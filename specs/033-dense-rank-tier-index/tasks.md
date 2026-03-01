# Tasks: Exact Dense Rank at Scale (Tier Index)

**Input**: Design documents from `/specs/033-dense-rank-tier-index/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/redis-operations.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Exact file paths included in descriptions

---

## Phase 1: Setup (Redis Key Builders)

**Purpose**: Add all new Redis key builders and constants to the centralized key registry before any service code references them.

- [X] T001 Add `LBMETA_PREFIX`, TTL constants, `lbmeta_tieridx_key()`, `lbmeta_tiercnt_key()`, `lbmeta_lock_key()`, and `lbmeta_keys_from_lb_key()` builder functions in `fastapi_app/core/redis_keys.py`

**Checkpoint**: All key builders importable; no service code yet.

---

## Phase 2: Foundational (Atomic Write Lua Script)

**Purpose**: The Lua script is used by both US1 (read path needs metadata to exist) and US2 (write path atomicity). Must be defined before either story.

**⚠️ CRITICAL**: US1 and US2 both depend on this phase.

- [X] T002 Define `_TIER_AWARE_ZINCRBY_LUA` Lua script constant in `fastapi_app/services/leaderboard.py` implementing the atomic ZSCORE → ZINCRBY → old tier HINCRBY(-1) → conditional ZREM/HDEL → new tier HINCRBY(+1) → ZADD logic per contract in `contracts/redis-operations.md`

**Checkpoint**: Lua script defined as a string constant, not yet wired into any method.

---

## Phase 3: User Story 2 — Atomic Tier Maintenance on XP Award (Priority: P1)

**Goal**: When a player earns XP, atomically update both leaderboard score and tier metadata per variant. Empty tiers are removed immediately.

**Independent Test**: Award XP to players, verify tier counts match actual distributions, verify abandoned tiers are removed.

### Implementation for User Story 2

- [X] T003 [US2] Modify `update_leaderboards()` in `fastapi_app/services/leaderboard.py` to replace each `pipe.zincrby()` call with `pipe.eval(_TIER_AWARE_ZINCRBY_LUA, 3, lb_key, tieridx_key, tiercnt_key, player_id, xp_amount)` using key builders from T001, and add `pipe.expire()` for both `tieridx_key` and `tiercnt_key` after each eval with the same TTL as the corresponding leaderboard key
- [X] T004 [US2] Verify `update_leaderboards()` handles all 8 leaderboard variants correctly: global daily, global weekly, subject daily, subject weekly, plan daily, plan weekly, plan+subject daily, plan+subject weekly — each with its own tier metadata keys and correct TTL in `fastapi_app/services/leaderboard.py`

**Checkpoint**: XP awards create/maintain tier metadata. Existing read path still works (uses `_RANK_LUA`).

---

## Phase 4: User Story 1 — Dense Rank Read Performance at Scale (Priority: P1)

**Goal**: Dense rank reads use O(log T) indexed lookups via ZCOUNT + ZRANGEBYSCORE on the tier index ZSET, with fallback to legacy `_RANK_LUA` when metadata is missing.

**Independent Test**: Populate a 100k-player leaderboard, query rank of lowest player, verify correct result in under 20ms.

### Implementation for User Story 1

- [X] T005 [US1] Rename `_RANK_LUA` to `_LEGACY_RANK_LUA` in `fastapi_app/services/leaderboard.py` (update all references)
- [X] T006 [US1] Modify `get_my_rank()` Stage 2 in `fastapi_app/services/leaderboard.py` to: check `EXISTS tieridx_key` in the pipeline; if exists, use `ZCOUNT tieridx_key (xp+1) +inf` and `ZRANGEBYSCORE tieridx_key (xp) +inf WITHSCORES LIMIT 0 1` for dense rank and xp_to_next; if not exists, fall back to `_LEGACY_RANK_LUA`
- [X] T007 [US1] Add structured log field `fallback_used=True/False` to the `leaderboard_rank_fetched` log event in `get_my_rank()` in `fastapi_app/services/leaderboard.py`

**Checkpoint**: Rank reads use indexed path when metadata exists, legacy path otherwise. All response shapes unchanged.

---

## Phase 5: User Story 4 — Graceful Fallback During Rollout (Priority: P2)

**Goal**: Missing tier metadata triggers legacy path with monitoring. No errors during transition.

**Independent Test**: Query rank on a leaderboard with no metadata keys, verify correct result and fallback log emitted.

### Implementation for User Story 4

- [X] T008 [US4] Verify fallback behavior is correctly implemented in `get_my_rank()` from T006 — no additional code needed if T006 is complete; validate that the `fallback_used` log field from T007 serves as the monitoring counter per FR-015 in `fastapi_app/services/leaderboard.py`

**Checkpoint**: Fallback path validated. Safe to deploy before backfill.

---

## Phase 6: User Story 3 — Race-Free Backfill of Existing Leaderboards (Priority: P2)

**Goal**: A management command populates tier metadata for all existing leaderboard keys using per-key locking to prevent races with live writes.

**Independent Test**: Populate leaderboards, run backfill while concurrent writer awards XP, verify final metadata matches leaderboard state.

### Implementation for User Story 3

- [X] T009 [US3] Create `memora_admin/tasks/leaderboard_backfill.py` with `backfill_tier_metadata()` function that: SCANs for `memora:lb:*` keys (skipping archive keys), acquires per-key lock via `SET NX EX 30` using `lbmeta_lock_key()`, ZSCANs the leaderboard to build tier counts dict, atomically installs tieridx ZSET + tiercnt HASH via MULTI/EXEC with correct TTL, releases lock, and logs progress every 10 keys
- [X] T010 [US3] Add integrity check at end of `backfill_tier_metadata()` in `memora_admin/tasks/leaderboard_backfill.py` that verifies `sum(tier_counts) == ZCARD(lb_key)` for each backfilled key and logs mismatches as errors
- [X] T011 [US3] Use synchronous `redis` client (not `redis.asyncio`) in `memora_admin/tasks/leaderboard_backfill.py` via `get_memora_redis()` from `memora_admin.utils.redis_connection` since Frappe management commands run synchronously

**Checkpoint**: `bench --site x.conanacademy.com execute memora_admin.tasks.leaderboard_backfill.backfill_tier_metadata` works correctly.

---

## Phase 7: User Story 5 — Metadata Cleanup Prevents Orphan Accumulation (Priority: P3)

**Goal**: The cleanup job deletes expired tier metadata keys using the same date-based retention policy as leaderboard keys.

**Independent Test**: Create old leaderboard + metadata keys, run cleanup, verify all three key types deleted together.

### Implementation for User Story 5

- [X] T012 [US5] Add `memora:lbmeta:daily:*` and `memora:lbmeta:weekly:*` scan patterns to `cleanup_old_leaderboards()` in `memora_admin/tasks/leaderboard_cleanup.py`, using the same `_extract_date()` regex and retention thresholds (30 days daily, 90 days weekly) and the existing `_scan_and_delete()` function

**Checkpoint**: Cleanup removes all three key types (leaderboard, tieridx, tiercnt) based on date retention.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Logging, monitoring, and deployment validation

- [X] T013 Add tier cardinality logging to the write path in `fastapi_app/services/leaderboard.py` — log number of Lua evals executed per `update_leaderboards()` call for operational monitoring per FR-015
- [X] T014 Run quickstart.md validation steps (health check, write path verification, read path performance, backfill, integrity check) to confirm end-to-end functionality

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 (needs key builders)
- **Phase 3 (US2 — Write Path)**: Depends on Phase 2 (needs Lua script)
- **Phase 4 (US1 — Read Path)**: Depends on Phase 2 (needs Lua script constant defined); can run in parallel with Phase 3
- **Phase 5 (US4 — Fallback)**: Depends on Phase 4 (validation of fallback in read path)
- **Phase 6 (US3 — Backfill)**: Depends on Phase 1 (needs key builders); can run in parallel with Phases 3-5
- **Phase 7 (US5 — Cleanup)**: Depends on Phase 1 (needs LBMETA_PREFIX); can run in parallel with Phases 3-6
- **Phase 8 (Polish)**: Depends on all previous phases

### User Story Dependencies

- **US2 (Write Path)**: Independent after Phase 2
- **US1 (Read Path)**: Independent after Phase 2; benefits from US2 being complete (metadata exists)
- **US4 (Fallback)**: Subset of US1 — validated as part of US1 implementation
- **US3 (Backfill)**: Independent after Phase 1; uses `get_memora_redis()` (sync), not FastAPI code
- **US5 (Cleanup)**: Fully independent after Phase 1

### Parallel Opportunities

- T003 and T004 (US2 write path) are sequential (same file, dependent logic)
- T005, T006, T007 (US1 read path) are sequential (same file, dependent logic)
- Phase 3 (US2) and Phase 6 (US3) can run in parallel (different files: `leaderboard.py` vs `leaderboard_backfill.py`)
- Phase 7 (US5) can run in parallel with any other phase after Phase 1 (different file: `leaderboard_cleanup.py`)

---

## Implementation Strategy

### MVP First (US2 + US1 + US4)

1. Complete Phase 1: Key builders in `redis_keys.py`
2. Complete Phase 2: Lua script definition
3. Complete Phase 3: Write path (US2) — tier metadata now created on XP awards
4. Complete Phase 4: Read path (US1) — indexed dense rank with fallback
5. **STOP and VALIDATE**: Deploy code, verify fallback works, monitor `fallback_used` logs
6. Complete Phase 6: Backfill (US3) — populate metadata for existing leaderboards
7. Monitor: `fallback_used` should drop to 0 after backfill

### Incremental Delivery

1. Setup + Foundational → Key builders and Lua script ready
2. US2 (Write Path) → New XP awards maintain tier metadata → Deploy
3. US1 (Read Path) → Indexed reads with fallback → Deploy
4. US3 (Backfill) → Populate existing boards → Run command
5. US5 (Cleanup) → Prevent orphan accumulation → Deploy
6. Polish → Logging and validation → Complete
