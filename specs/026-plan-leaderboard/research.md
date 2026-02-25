# Research: Plan-Scoped Leaderboard

**Date**: 2026-02-24 | **Branch**: `026-plan-leaderboard`

## R1: How to Resolve Player's Plan Without MariaDB

**Decision**: Read `plan_id` from JWT access token (`user.plan`)

**Rationale**: The access token already contains the `plan` field, set at login from the player's `Memora Player Profile.plan` Link field. Zero additional Redis or MariaDB queries needed.

**Fallback**: If `user.plan` is None (shouldn't happen for player tokens), fall back to `memora:session:{user_id}` hash which also stores `plan`.

**Alternatives considered**:
- Redis `memora:player_plan:{player_id}` cache (24h TTL) — unnecessary extra query when JWT already has it
- MariaDB lookup via FrappeClient — too slow for hot path (<20ms target)

---

## R2: Redis Key Design for Plan-Scoped Leaderboards

**Decision**: Insert `:plan:{plan_id}` segment into existing key patterns

**New key patterns**:

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `memora:lb:daily:{date}:plan:{plan_id}` | ZSET | 48h | Daily overall for plan |
| `memora:lb:daily:{date}:plan:{plan_id}:subject:{subject_id}` | ZSET | 48h | Daily per-subject for plan |
| `memora:lb:weekly:{friday}:plan:{plan_id}` | ZSET | 8d | Weekly overall for plan |
| `memora:lb:weekly:{friday}:plan:{plan_id}:subject:{subject_id}` | ZSET | 8d | Weekly per-subject for plan |

**Rationale**:
- Key structure mirrors existing patterns but adds plan dimension
- Shorter TTLs than global keys (48h vs 30d daily, 8d vs 90d weekly) since plan-scoped data is transient
- ZINCRBY for accumulation (same as existing daily/weekly)
- No composite score needed — daily/weekly don't need tie-breaking by timestamp (simple XP accumulation)

**Global keys kept** (write-only, no read endpoints):

| Key | Status |
|-----|--------|
| `memora:lb:daily:{date}` | Keep writing |
| `memora:lb:daily:{date}:subject:{subject_id}` | Keep writing |
| `memora:lb:weekly:{friday}` | Keep writing |
| `memora:lb:weekly:{friday}:subject:{subject_id}` | Keep writing |
| `memora:lb:alltime` | Keep writing |
| `memora:lb:alltime:subject:{subject_id}` | Keep writing |

**Alternatives considered**:
- Separate prefix (`memora:plb:` for plan leaderboards) — rejected because it fragments key discovery and the existing `memora:lb:` prefix with added `:plan:` segment is clear enough
- Single ZSET per plan with member encoding (`{player_id}:{subject_id}`) — rejected because it prevents per-subject filtering with ZRANGE

---

## R3: Write Path Changes

**Decision**: Add `plan_id` parameter to `update_leaderboards()` and add plan-scoped writes to the existing pipeline

**Current pipeline** (6-12 commands in 1 RTT):
1. ZADD alltime (global)
2. ZINCRBY daily (global) + EXPIRE
3. ZINCRBY weekly (global) + EXPIRE
4. HINCRBY daily_xp hash + EXPIRE
5. Optionally: ZADD/ZINCRBY for subject-specific variants

**New pipeline** (10-18 commands in 1 RTT — same 1 RTT):
- Keep all existing global writes unchanged
- ADD: ZINCRBY daily plan-scoped + EXPIRE 48h
- ADD: ZINCRBY weekly plan-scoped + EXPIRE 8d
- ADD: Optionally ZINCRBY for plan+subject variants + EXPIRE

**Rationale**: Single pipeline means the added plan-scoped writes add zero extra round-trips. Marginal increase in pipeline size is negligible.

**Alternatives considered**:
- Two separate pipelines (global + plan) — rejected, unnecessary extra RTT
- Remove global writes — rejected per spec FR-007 (keep as backup)

---

## R4: Read Path Changes

**Decision**: Endpoints read exclusively from plan-scoped keys. Plan resolved from JWT.

**Changes**:
1. `_get_key()` method gains `plan_id` parameter — returns plan-scoped key when provided
2. `get_top()` always receives `plan_id` — reads from plan-scoped ZSET
3. `get_my_rank()` always receives `plan_id` — reads from plan-scoped ZSET
4. `lb_type` enum changes from `Literal["daily", "weekly", "alltime"]` to `Literal["daily", "weekly"]`
5. `limit` parameter removed from top endpoint — hardcoded to 20

**Rationale**: Endpoints become simpler (fewer parameters) while scoping automatically provides fair competition.

---

## R5: Archive Jobs Impact

**Decision**: No archival for plan-scoped keys. Rely on TTL auto-expiry.

**Rationale**:
- Plan-scoped daily keys expire in 48h, weekly in 8d — data is transient by design
- There's no use case for historical per-plan leaderboard data
- Archiving plan-scoped keys would multiply SCAN scope significantly (many plans × daily/weekly × subjects)
- Global keys continue to be archived as before (unchanged)

**Alternatives considered**:
- Archive plan-scoped keys too — rejected, no consumer and large storage overhead

---

## R6: Profile Cache Warming

**Decision**: Keep warming from global leaderboards (unchanged)

**Rationale**: The same players who appear on plan-scoped boards also appear on global boards (dual-write). The profile cache only stores `{display_name, avatar}` which is plan-agnostic. No changes needed to `warm_profile_cache()`.

---

## R7: Subject Dropdown Data

**Decision**: Use existing plan manifest endpoint for subject list. No new endpoint.

**Rationale**: The plan manifest (`GET /api/v1/plans/{plan_id}/manifest`) already returns `subjects: list[PlanSubject]` with `id`, `title`, `alias_title`. The mobile app loads this at startup. Adding a dedicated `/leaderboard/subjects` endpoint would be redundant.

**Alternatives considered**:
- New `/api/v1/leaderboard/subjects` endpoint — rejected, plan manifest already serves this data
- Query `memora:plan:{plan_id}:free_subjects` — insufficient, only contains free subjects, not all plan subjects

---

## R8: Plan Change Mid-Season

**Decision**: No migration of historical XP. Player sees new plan's leaderboard immediately.

**Rationale**: Plan-scoped keys use `plan_id` at write time. Old XP was written to old plan's ZSETs and stays there. New XP (after plan change) goes to new plan's ZSETs. The JWT `plan` field is set at login — so the player must re-login after a plan change for the new plan to take effect in the token. The session hash is also updated on plan change via `on_player_profile_plan_changed()` event hook.

**Edge case**: If the player's plan changes but they don't re-login, their JWT still has the old plan. This is acceptable — the session invalidation hook already forces re-authentication on plan change.
