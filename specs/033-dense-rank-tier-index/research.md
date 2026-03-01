# Research: Exact Dense Rank at Scale (Tier Index)

**Feature Branch**: `033-dense-rank-tier-index`
**Date**: 2026-03-01

## R-001: Optimal Redis Data Structures for Tier Index

**Decision**: ZSET for tier index + HASH for tier counts per leaderboard key.

**Rationale**:
- **Tier Index (ZSET)**: `ZCOUNT key (xp+1) +inf` returns the count of distinct tiers above a player's XP in O(log T) — exactly what dense rank needs. Member = tier string (e.g., "193"), score = same tier as integer (193). Only tiers with ≥1 player exist.
- **Tier Counts (HASH)**: `HGET key "193"` returns the player count at tier 193 in O(1). `HDEL` removes a tier when count drops to 0. This is needed to know when a tier should be removed from the tier index.
- Combined: Dense rank = `ZCOUNT tierIdx (xp+1) +inf` + 1. xp_to_next = `ZRANGEBYSCORE tierIdx (xp) +inf LIMIT 0 1` minus player XP.

**Alternatives considered**:
- **Sorted Set only (no HASH)**: Cannot track per-tier player counts without the HASH. Would need to walk the ZSET to determine if a tier is empty after a player moves. Rejected.
- **HyperLogLog per tier**: Approximate counts, not exact. Spec requires exact dense rank. Rejected.
- **Bitmap of active tiers**: XP values can be large (100k+), bitmap wastes memory for sparse sets. ZSET is memory-efficient for sparse integer sets. Rejected.

## R-002: Atomic Tier Maintenance in Lua

**Decision**: Single Lua script per leaderboard variant that atomically: reads old score, ZINCRBYs the leaderboard, decrements old tier count (HDECRs + conditionally ZREMs + HDELs if 0), increments new tier count (HINCRBYs + ZADDs).

**Rationale**:
- Atomicity: Redis Lua scripts execute without interleaving. No race between reading old score and updating metadata.
- No loops: The Lua script has a fixed number of operations regardless of leaderboard size (~7 commands).
- `ZINCRBY` returns the new score. Old score = new_score - xp_amount. This avoids a separate `ZSCORE` call.
- The Lua script does NOT set TTL (per FR-008: TTL is set outside atomic operations). The application layer handles TTL via pipeline `EXPIRE` after the Lua call.

**Alternatives considered**:
- **Pipeline without Lua**: Race condition between `ZSCORE` (read old score) and `ZINCRBY` (update score). If two concurrent XP awards arrive, both read the same old score, and one tier decrement is lost. Rejected.
- **WATCH/MULTI**: Requires retry logic, higher latency under contention. Lua is simpler and guaranteed atomic. Rejected.
- **Post-hoc consistency check**: Let writes be non-atomic, run a periodic job to fix metadata. Violates FR-005 (must maintain exact counts) and accumulates errors. Rejected.

**Lua Script Design**:
```
KEYS[1] = leaderboard ZSET key
KEYS[2] = tier index ZSET key
KEYS[3] = tier counts HASH key
ARGV[1] = player_id
ARGV[2] = xp_amount (integer)

1. old_score = ZSCORE(KEYS[1], player_id) -- nil if new player
2. ZINCRBY(KEYS[1], xp_amount, player_id)
3. new_score = old_score + xp_amount (or xp_amount if new)
4. old_tier = floor(old_score) if old_score ~= nil
5. new_tier = floor(new_score)
6. IF old_tier ~= nil AND old_tier ~= new_tier THEN
     count = HINCRBY(KEYS[3], old_tier, -1)
     IF count <= 0 THEN
       ZREM(KEYS[2], tostring(old_tier))
       HDEL(KEYS[3], tostring(old_tier))
     END
   END
7. HINCRBY(KEYS[3], tostring(new_tier), 1)
8. ZADD(KEYS[2], new_tier, tostring(new_tier))
9. RETURN {old_score or -1, new_score}
```

## R-003: Dense Rank Read Path — O(log T)

**Decision**: Replace the iterative `_RANK_LUA` Lua script with two simple Redis commands via pipeline.

**Rationale**:
- `ZCOUNT tierIdx (xp+1) +inf` returns distinct tier count above player in O(log T) — exactly `distinct_above`.
- `ZRANGEBYSCORE tierIdx (xp) +inf LIMIT 0 1 WITHSCORES` returns the lowest tier above player for `xp_to_next`.
- Both are O(log T) where T = distinct tiers (typically ≤5000 for 100k players).
- The existing pipeline structure (Stage 1: position/total/score, Stage 2: neighbors + rank) remains unchanged. The Lua eval in Stage 2 is replaced by ZCOUNT + ZRANGEBYSCORE on the tier index.

**Alternatives considered**:
- **Keep Lua but use ZCOUNT inside it**: Possible, but pure pipeline commands are simpler, more maintainable, and don't block the Redis event loop at all. Rejected.
- **Precompute ranks in a hash**: Requires O(T) updates on every XP write. Current system has up to 100k writes/day. Rejected.

## R-004: Fallback Strategy

**Decision**: Check for tier index key existence before using the indexed path. If missing, fall back to existing `_RANK_LUA` iterative approach. Log fallback usage.

**Rationale**:
- Zero-downtime deployment: Code can be deployed before backfill runs.
- The fallback is the current production code — proven correct.
- A simple `EXISTS` check (O(1)) determines which path to take.
- Logging allows operators to track migration progress.

**Implementation**:
- In `get_my_rank()`, after Stage 1, check `EXISTS tierIdxKey`.
- If exists → indexed path (ZCOUNT + ZRANGEBYSCORE).
- If not exists → legacy `_RANK_LUA`.
- Counter: `structlog` with `fallback_used=True/False` on every rank read.

## R-005: Backfill Strategy

**Decision**: Frappe management command that iterates all `memora:lb:*` ZSET keys, acquires a per-key short-lived lock, builds metadata from `ZSCAN`, installs atomically, releases lock.

**Rationale**:
- Per-key lock (Redis `SET NX EX 30`) prevents concurrent XP writes from racing with backfill.
- During lock acquisition by backfill, live XP writes will retry briefly (the Lua script can return a "locked" signal, or the application can check the lock and retry with a short sleep).
- `ZSCAN` with `COUNT 1000` iterates the leaderboard efficiently without blocking.
- Atomic install: `MULTI`/`EXEC` to set both tier index and tier counts in one transaction.
- Progress logging: emit count every 10 keys.

**Alternatives considered**:
- **No lock, post-hoc reconciliation**: Writes during backfill corrupt metadata. Requires a second pass. Slower and more complex. Rejected.
- **Maintenance window (freeze writes)**: Requires downtime. Spec requires zero-downtime. Rejected.
- **Shadow keys + atomic swap**: Build metadata under temp keys, rename atomically. But RENAME is not safe across slots in Redis Cluster (not currently used, but unnecessary complexity). Rejected.

## R-006: Metadata Key Naming and TTL

**Decision**: Separate prefix `memora:lbmeta:` with two suffixes per leaderboard: `:tieridx` (ZSET) and `:tiercnt` (HASH). Same scope identifiers (date, subject, plan, plan+subject) mirrored from leaderboard keys.

**Key patterns**:
| Leaderboard Key | Tier Index Key | Tier Counts Key |
|---|---|---|
| `memora:lb:daily:2026-03-01` | `memora:lbmeta:daily:2026-03-01:tieridx` | `memora:lbmeta:daily:2026-03-01:tiercnt` |
| `memora:lb:daily:2026-03-01:subject:math` | `memora:lbmeta:daily:2026-03-01:subject:math:tieridx` | `memora:lbmeta:daily:2026-03-01:subject:math:tiercnt` |
| `memora:lb:daily:2026-03-01:plan:PLAN-001` | `memora:lbmeta:daily:2026-03-01:plan:PLAN-001:tieridx` | `memora:lbmeta:daily:2026-03-01:plan:PLAN-001:tiercnt` |
| `memora:lb:weekly:2026-02-28:plan:PLAN-001:subject:math` | `memora:lbmeta:weekly:2026-02-28:plan:PLAN-001:subject:math:tieridx` | `memora:lbmeta:weekly:2026-02-28:plan:PLAN-001:subject:math:tiercnt` |

**TTL**: Same as corresponding leaderboard key. Set via `EXPIRE` in the application layer after the Lua atomic write, same as the existing EXPIRE for leaderboard keys. Not inside the Lua script.

**Rationale**:
- Separate prefix ensures archive jobs scanning `memora:lb:*` never encounter metadata keys (FR-007).
- Mirrored scope identifiers make it easy to derive metadata keys from leaderboard keys (just replace prefix and append suffix).
- Centralized key builders in `redis_keys.py` per project convention.

## R-007: Cleanup Extension

**Decision**: Add two new SCAN patterns to `cleanup_old_leaderboards()` for `memora:lbmeta:daily:*` and `memora:lbmeta:weekly:*`, using the same date extraction regex and retention thresholds.

**Rationale**:
- Metadata keys follow the same date format as leaderboard keys, so `_extract_date()` works unchanged.
- Same retention policy: daily metadata = 30 days, weekly metadata = 90 days.
- The existing `_scan_and_delete()` function works directly for both tier index and tier counts keys since they share the same date segment.

## R-008: Write Amplification Analysis

**Decision**: Acceptable. ~56 Redis commands per XP award (8 variants × 7 ops each) is within capacity.

**Analysis**:
- **Current**: 8 `ZINCRBY` + 8 `EXPIRE` + 1 `HINCRBY` (daily_xp) + 1 `EXPIRE` = 18 commands per pipeline.
- **New**: 8 Lua evals (each ~7 internal commands) + 8 `EXPIRE` (lb) + 8 `EXPIRE` (tieridx) + 8 `EXPIRE` (tiercnt) + 1 `HINCRBY` (daily_xp) + 1 `EXPIRE` = 34 pipeline commands, but each Lua eval handles 1 LB + metadata atomically.
- Per-Lua internal: `ZSCORE` + `ZINCRBY` + `HINCRBY (old tier)` + conditional `ZREM` + conditional `HDEL` + `HINCRBY (new tier)` + `ZADD` = 5-7 Redis commands (server-side, no RTT).
- **Latency impact**: All 8 Lua evals can be pipelined in a single RTT (Redis pipelines Lua eval commands). Total RTT count stays at 1 for the write path.
- **Memory impact**: Each leaderboard now has 2 additional keys. For 50 active leaderboards, that's 100 additional keys — negligible.

## R-009: Neighbor Dense Rank Computation

**Decision**: No change needed. The existing neighbor rank derivation from `window_tiers` set remains correct with the indexed path.

**Rationale**:
- The `ZRANGE` neighbor window already provides all distinct tiers in the contiguous position range.
- The relative rank computation (my_rank ± tiers_between) works the same regardless of how `my_rank` was obtained.
- The only change is how `my_rank` and `min_above` are obtained: from tier index ZCOUNT instead of from the `_RANK_LUA` iterative walk.

## R-010: Lock Approach for Backfill Write-Path Interaction

**Decision**: The Lua write script does NOT acquire or check a lock. Instead, the backfill process acquires a per-key lock, and during the lock period, the write-path Lua script operates normally (it's always atomic). The backfill atomically overwrites metadata after computing it from the ZSET. Since the Lua write path is atomic and the backfill MULTI/EXEC is atomic, there is no race.

**Rationale**:
- The backfill reads the ZSET state, then atomically installs metadata. If a write occurs between read and install, the metadata might be slightly stale for that one player. The lock prevents this.
- The write path doesn't need to know about the lock — it just does its atomic Lua update. If backfill hasn't run, metadata doesn't exist, and the read path falls back.
- After backfill installs metadata, subsequent writes maintain it correctly.
- Lock TTL: 30 seconds per key. Backfill of a 100k-player ZSET takes <5 seconds.
