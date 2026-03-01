# Implementation Plan: Exact Dense Rank at Scale (Tier Index)

**Branch**: `033-dense-rank-tier-index` | **Date**: 2026-03-01 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/033-dense-rank-tier-index/spec.md`

## Summary

Replace the O(T × log N) iterative tier-walking Lua script (`_RANK_LUA`) in `leaderboard.py` with O(log T) indexed tier lookups using a maintained tier ZSET + tier counts HASH per leaderboard. The write path uses a new atomic Lua script that maintains tier metadata alongside score updates. A fallback to the legacy approach ensures zero-downtime deployment, and a backfill command populates metadata for existing leaderboards.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, redis.asyncio, structlog, Frappe Framework (for backfill command)
**Storage**: Redis at `redis://127.0.0.1:13001` (dedicated Memora instance) — no schema changes
**Testing**: pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio (real Redis, no mocks)
**Target Platform**: Linux server (x.conanacademy.com)
**Project Type**: Dual architecture (Frappe admin + FastAPI sidecar)
**Performance Goals**: Dense rank read < 20ms for any player position on 100k-player board; write latency budget unchanged
**Constraints**: No API response changes, no new DocTypes, metadata prefix `memora:lbmeta:*` separate from `memora:lb:*`
**Scale/Scope**: 100k concurrent users, up to 50 active leaderboard keys, ~8 variants updated per XP award

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache Architecture | PASS | Tier metadata is derived from leaderboard ZSETs. If metadata is lost (restart/eviction), the read path falls back to legacy `_RANK_LUA`. A re-backfill restores full metadata. No Redis-only state that can't be rebuilt. |
| II. Sub-20ms Game API Performance | PASS | Core goal of this feature. Reduces worst-case rank read from ~200ms to <1ms. Write path stays within budget (same pipeline RTT count). |
| III. Content Hierarchy Integrity | N/A | No content hierarchy changes. |
| IV. Double-Gate Access Control | N/A | No access control changes. |
| V. Cryptographic Voucher Security | N/A | No voucher changes. |
| VI. Financial Precision | N/A | No financial calculations. |
| VII. Auditable State Machines | N/A | No state machine changes. |
| VIII. Test-First Coverage | PASS | Tests against real Redis (no mocks per constitution). Will cover: write path atomicity, read path correctness, fallback behavior, backfill integrity, cleanup extension. |

### Post-Design Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache Architecture | PASS | Fallback = self-healing. Missing metadata → legacy path. Backfill = manual rehydration command. No data loss possible — metadata is always derivable from the leaderboard ZSET. |
| II. Sub-20ms Game API Performance | PASS | `ZCOUNT` + `ZRANGEBYSCORE` are O(log T). For T=5000 distinct tiers: ~0.2ms vs current ~200ms. Write path: Lua eval is server-side (no extra RTT). |
| VIII. Test-First Coverage | PASS | Test plan covers: atomic tier maintenance (concurrent XP awards), empty tier cleanup, dense rank correctness at scale, fallback detection, backfill integrity check, cleanup of metadata keys. |

## Project Structure

### Documentation (this feature)

```text
specs/033-dense-rank-tier-index/
├── plan.md              # This file
├── research.md          # Phase 0 output — all design decisions with rationale
├── data-model.md        # Phase 1 output — tier index, tier counts, lock entities
├── quickstart.md        # Phase 1 output — testing guide and deployment order
├── contracts/
│   └── redis-operations.md  # Lua script contracts, read/write paths, key builders
└── tasks.md             # Phase 2 output (not created by /speckit.plan)
```

### Source Code (repository root)

```text
fastapi_app/
├── core/
│   └── redis_keys.py          # MODIFIED: Add LBMETA_PREFIX, lbmeta_*_key() builders
├── services/
│   └── leaderboard.py         # MODIFIED: New Lua script, indexed read, tier-aware write

memora_admin/
├── tasks/
│   ├── leaderboard_cleanup.py # MODIFIED: Add lbmeta scan patterns
│   └── leaderboard_backfill.py # NEW: Backfill management command
└── hooks.py                   # MODIFIED: Register backfill command (if applicable)
```

**Structure Decision**: No new modules or packages. All changes are modifications to existing service/task files plus one new task file for backfill. Follows the established pattern in `memora_admin/tasks/`.

## Complexity Tracking

No constitution violations. No complexity justifications needed.

---

## Implementation Summary

### Phase 1: Redis Key Builders (`redis_keys.py`)
- Add `LBMETA_PREFIX = "memora:lbmeta"`
- Add `lbmeta_tieridx_key(period, date_str, plan_id?, subject_id?)` → `memora:lbmeta:{period}:{date}[:{scope}]:tieridx`
- Add `lbmeta_tiercnt_key(period, date_str, plan_id?, subject_id?)` → `memora:lbmeta:{period}:{date}[:{scope}]:tiercnt`
- Add `lbmeta_lock_key(lb_key_suffix)` → `memora:lbmeta:lock:{suffix}`
- Add `lbmeta_keys_from_lb_key(lb_key)` → convenience derivation

### Phase 2: Atomic Write Path (`leaderboard.py`)
- New Lua script `_TIER_AWARE_ZINCRBY_LUA`: ZSCORE → ZINCRBY → old tier HINCRBY(-1) → conditional ZREM/HDEL → new tier HINCRBY(+1) → ZADD
- Modify `update_leaderboards()`: replace `ZINCRBY` pipeline commands with `eval` of Lua script per variant, add `EXPIRE` for tieridx/tiercnt keys
- Keep `_RANK_LUA` as fallback (renamed to `_LEGACY_RANK_LUA`)

### Phase 3: Indexed Read Path (`leaderboard.py`)
- Modify `get_my_rank()` Stage 2: check `EXISTS tieridx_key`, if yes → `ZCOUNT` + `ZRANGEBYSCORE` pipeline, else → legacy `_LEGACY_RANK_LUA`
- Log `fallback_used=True/False` for monitoring
- `xp_to_next` derivation unchanged (uses `min_above` from ZRANGEBYSCORE instead of Lua return)
- Neighbor dense rank computation unchanged (uses `my_rank` regardless of source)

### Phase 4: Backfill Command (`leaderboard_backfill.py`)
- Frappe command: `bench execute memora_admin.tasks.leaderboard_backfill.backfill_tier_metadata`
- SCAN for `memora:lb:*`, skip archive keys
- Per key: acquire lock → ZSCAN to build tier counts → MULTI/EXEC install → release lock
- Progress logging every 10 keys
- Integrity check: sum(tier_counts) == ZCARD(lb_key)

### Phase 5: Cleanup Extension (`leaderboard_cleanup.py`)
- Add two scan patterns: `memora:lbmeta:daily:*` (30-day retention) and `memora:lbmeta:weekly:*` (90-day retention)
- Reuse existing `_scan_and_delete()` function unchanged

### Phase 6: Tests
- Write path: atomic tier creation, tier movement, empty tier removal, new player first XP
- Read path: indexed dense rank correctness, top-tier xp_to_next=None, ties share rank
- Fallback: missing metadata → legacy path, logging verification
- Backfill: integrity check (sum == ZCARD), lock prevents race
- Cleanup: metadata keys deleted alongside leaderboard keys
- Scale: 100k synthetic players, rank query at bottom < 20ms
