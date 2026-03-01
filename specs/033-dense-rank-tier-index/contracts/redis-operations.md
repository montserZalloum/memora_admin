# Redis Operations Contract: Tier Index

**Feature Branch**: `033-dense-rank-tier-index`
**Date**: 2026-03-01

## Write Path: Atomic XP Award with Tier Maintenance

### Lua Script: `TIER_AWARE_ZINCRBY`

Called once per leaderboard variant (up to 8 times per XP award, pipelined in single RTT).

**Signature**:
```
EVALSHA <sha> 3 <lb_key> <tieridx_key> <tiercnt_key> <player_id> <xp_amount>
```

**Keys**:
| Index | Key | Type |
|-------|-----|------|
| KEYS[1] | Leaderboard ZSET | `memora:lb:{period}:{date}[:{scope}]` |
| KEYS[2] | Tier Index ZSET | `memora:lbmeta:{period}:{date}[:{scope}]:tieridx` |
| KEYS[3] | Tier Counts HASH | `memora:lbmeta:{period}:{date}[:{scope}]:tiercnt` |

**Args**:
| Index | Arg | Type | Description |
|-------|-----|------|-------------|
| ARGV[1] | player_id | string | Player identifier |
| ARGV[2] | xp_amount | integer | XP to add (always > 0) |

**Return**: `{old_score, new_score}` where old_score = -1 if player was new.

**Internal Operations** (server-side, no RTT):
1. `ZSCORE KEYS[1] ARGV[1]` → old_score (nil if new player)
2. `ZINCRBY KEYS[1] ARGV[2] ARGV[1]` → new_score
3. `old_tier = math.floor(old_score)` if old_score exists
4. `new_tier = math.floor(new_score)`
5. If old_tier exists AND old_tier ≠ new_tier:
   - `HINCRBY KEYS[3] tostring(old_tier) -1` → remaining
   - If remaining ≤ 0: `ZREM KEYS[2] tostring(old_tier)` + `HDEL KEYS[3] tostring(old_tier)`
6. `HINCRBY KEYS[3] tostring(new_tier) 1`
7. `ZADD KEYS[2] new_tier tostring(new_tier)`

**Complexity**: O(log N) for ZINCRBY + O(log T) for ZADD/ZREM where N = players, T = tiers.

**Post-Lua pipeline** (application layer, same RTT):
- `EXPIRE lb_key {ttl}`
- `EXPIRE tieridx_key {ttl}`
- `EXPIRE tiercnt_key {ttl}`

---

## Read Path: Dense Rank Query

### Indexed Path (tier metadata exists)

**Pipeline** (replaces `_RANK_LUA` eval in Stage 2):
```
ZCOUNT tieridx_key (xp) +inf             → distinct_above
ZRANGEBYSCORE tieridx_key (xp) +inf WITHSCORES LIMIT 0 1  → min_above_entry
```

**Derivation**:
- `dense_rank = distinct_above + 1`
- `xp_to_next = min_above_entry[1] - xp` if min_above_entry exists, else `None`

**Complexity**: O(log T) for ZCOUNT + O(log T) for ZRANGEBYSCORE.

### Fallback Path (tier metadata missing)

Same as current `_RANK_LUA` iterative approach. No changes.

**Detection**: `EXISTS tieridx_key` — checked in Stage 2 pipeline.

---

## Backfill: Build Metadata from Existing Leaderboard

### Per-Key Backfill Sequence

```
1. SET memora:lbmeta:lock:{suffix} "backfill:{ts}" NX EX 30
   → If fail: skip key (another backfill in progress or lock held)

2. ZSCAN lb_key 0 COUNT 1000
   → Iterate all (member, score) pairs
   → Build in-memory: tier_counts = defaultdict(int)
   → For each member: tier_counts[floor(score)] += 1

3. MULTI
     DEL tieridx_key tiercnt_key   -- clean slate
     For each tier, count in tier_counts:
       ZADD tieridx_key tier tostring(tier)
       HSET tiercnt_key tostring(tier) count
     EXPIRE tieridx_key {ttl}
     EXPIRE tiercnt_key {ttl}
   EXEC

4. DEL memora:lbmeta:lock:{suffix}
```

**Complexity**: O(N) scan + O(T) install where N = players, T = distinct tiers.

---

## Cleanup Extension

### New SCAN Patterns

Added to `cleanup_old_leaderboards()`:

```
SCAN 0 MATCH memora:lbmeta:daily:* COUNT 500    → daily_cutoff (30 days)
SCAN 0 MATCH memora:lbmeta:weekly:* COUNT 500   → weekly_cutoff (90 days)
```

Same `_extract_date()` regex and `_scan_and_delete()` function. No changes to existing logic.

---

## Key Builder Functions (redis_keys.py)

### New Exports

```python
LBMETA_PREFIX = "memora:lbmeta"

def lbmeta_tieridx_key(period: str, date_str: str, plan_id: str | None = None, subject_id: str | None = None) -> str
def lbmeta_tiercnt_key(period: str, date_str: str, plan_id: str | None = None, subject_id: str | None = None) -> str
def lbmeta_lock_key(lb_key_suffix: str) -> str

# Convenience: derive metadata keys from a leaderboard key
def lbmeta_keys_from_lb_key(lb_key: str) -> tuple[str, str]
```

### `lbmeta_tieridx_key` / `lbmeta_tiercnt_key`

**Parameters**:
| Param | Type | Description |
|-------|------|-------------|
| period | `"daily"` or `"weekly"` | Leaderboard period |
| date_str | str | Date string (YYYY-MM-DD) |
| plan_id | str or None | Plan ID for plan-scoped boards |
| subject_id | str or None | Subject ID for subject-scoped boards |

**Examples**:
```python
lbmeta_tieridx_key("daily", "2026-03-01")
# → "memora:lbmeta:daily:2026-03-01:tieridx"

lbmeta_tiercnt_key("daily", "2026-03-01", subject_id="SUBJ-001")
# → "memora:lbmeta:daily:2026-03-01:subject:SUBJ-001:tiercnt"

lbmeta_tieridx_key("weekly", "2026-02-28", plan_id="PLAN-001", subject_id="SUBJ-001")
# → "memora:lbmeta:weekly:2026-02-28:plan:PLAN-001:subject:SUBJ-001:tieridx"
```

### `lbmeta_keys_from_lb_key`

Convenience function that derives both metadata keys from an existing leaderboard key by replacing the prefix and appending suffixes.

```python
lbmeta_keys_from_lb_key("memora:lb:daily:2026-03-01:subject:SUBJ-001")
# → ("memora:lbmeta:daily:2026-03-01:subject:SUBJ-001:tieridx",
#    "memora:lbmeta:daily:2026-03-01:subject:SUBJ-001:tiercnt")
```

---

## Performance Characteristics

| Operation | Current | New (Indexed) | Improvement |
|-----------|---------|---------------|-------------|
| Dense rank read (bottom player, 5000 tiers) | O(5000 × log N) ≈ 200ms | O(log 5000) ≈ 0.2ms | ~1000× |
| Dense rank read (top player, 0 tiers above) | O(1) | O(log T) ≈ 0.1ms | ~Same |
| XP write (single variant) | 1 ZINCRBY | 1 Lua (7 internal ops) | ~7× more internal ops, same RTT |
| XP write (all 8 variants) | 8 ZINCRBY (1 pipeline) | 8 Lua evals (1 pipeline) | Same RTT count |
| Memory per leaderboard | 1 ZSET | 1 ZSET + 1 ZSET + 1 HASH | ~2× (tiers << members) |
