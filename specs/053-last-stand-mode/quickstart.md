# Quickstart: Live Challenge Mode — Last Stand

**Branch**: `053-last-stand-mode`

## Overview

Last Stand adds a round-based elimination mode to the existing Live Challenge system. Players start with hearts; wrong or missed answers cost a heart; zero hearts = eliminated. The existing exam mode is untouched.

## Key Files to Modify

### Frappe (DocType + Validation)

| File | Change |
|------|--------|
| `memora_admin/doctype/memora_live_challenge_event/memora_live_challenge_event.json` | Add `mode`, `starting_hearts`, `result_window_duration` fields |
| `memora_admin/doctype/memora_live_challenge_event/memora_live_challenge_event.py` | Validate Last Stand fields, immutable mode, exam_duration calc |
| `memora_admin/doctype/memora_live_challenge_participation/memora_live_challenge_participation.json` | Add `final_hearts`, `is_eliminated`, `eliminated_at_question`, `avg_response_time_ms` |

### FastAPI (Service + Endpoints)

| File | Change |
|------|--------|
| `fastapi_app/services/last_stand_engine.py` | **NEW** — Round engine: async loop managing answer/result phases |
| `fastapi_app/services/live_challenge.py` | Mode branching in join/grade/reconcile, connection tracking with player_id |
| `fastapi_app/api/v1/endpoints/live_challenge.py` | New `POST /answer` endpoint, modified `/submit` (MODE_NOT_SUPPORTED), WS handler updates |
| `fastapi_app/models/live_challenge.py` | New request/response models for answer, round messages |
| `fastapi_app/core/redis_keys.py` | New key builders for round, hearts, alive, eliminated, round_answers, etc. |

### Scheduled Tasks

| File | Change |
|------|--------|
| `memora_admin/tasks/live_challenge_transitions.py` | Mode-aware transitions, Last Stand reconciliation with hearts/elimination data, 3-tier ranking |

### Admin API

| File | Change |
|------|--------|
| `memora_admin/api/live_challenge.py` | Dashboard: alive_count, eliminated_count, current_round for Active Last Stand |

## Implementation Order

### Phase 1: DocType Schema (no behavior change)
1. Add fields to Event DocType JSON
2. Add fields to Participation DocType JSON
3. Add validation in Event Python controller
4. Run `bench migrate` — existing events get `mode=exam` default

### Phase 2: Redis Key Layer
1. Add key builders to `redis_keys.py`
2. Add `mode` to meta HASH hydration (in transitions.py Draft→Waiting)
3. Extend `_hydrate_event_to_redis` with Last Stand keys

### Phase 3: Round Engine (core feature)
1. Create `last_stand_engine.py` with `LastStandEngine` class
2. Implement round loop: answer window → evaluate → result window → next
3. Add Lua scripts: atomic answer submission, heart deduction
4. Wire engine startup from `LiveChallengeService` when Waiting→Active for Last Stand events

### Phase 4: Endpoints + WebSocket
1. Add `POST /answer` endpoint
2. Modify `POST /submit` to reject Last Stand events
3. Modify join to reject late join + initialize hearts for Last Stand
4. Extend WebSocket handler: round_start, round_result (personalized), player_state on reconnect
5. Modify `register_connection` to track player_id per connection

### Phase 5: Reconciliation + Ranking
1. Extend reconciliation to persist hearts, elimination data from Redis
2. Add Last Stand ranking (3-tier sort)
3. Extend XP distribution (unchanged logic, just needs Last Stand participation data)

### Phase 6: Admin Dashboard + Finalization
1. Add alive/eliminated/round stats to `get_dashboard`
2. Add mode display to admin views
3. Recovery: startup scan for Active Last Stand events → resume round engines

## Critical Design Decisions

1. **Round engine is per-event async task** — like countdown_loop but manages phases. Full state in Redis for crash recovery.

2. **Personalized WebSocket broadcasts** — connection tracking maps `ws → player_id`. Round result messages include per-player fields (hearts, is_correct, is_eliminated).

3. **No DB writes during Active gameplay** — all state in Redis (hearts HASH, alive/eliminated SETs, round_answers HASHes). Persisted to DB during reconciliation after event ends.

4. **Early answer close** — answer endpoint checks if all alive answered; round engine also polls at 100ms. `asyncio.wait` on timeout OR signal.

5. **Lua atomicity for answers** — single Lua script validates status + alive + round_id + window + uniqueness atomically.

6. **Exam mode isolation** — new code is behind `if mode == "last_stand"` guards. Existing exam paths untouched. `/submit` returns error for Last Stand; `/answer` returns error for exam.

## Running Tests

```bash
# Frappe tests (DocType validation)
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.doctype.memora_live_challenge_event.test_live_challenge_event

# FastAPI tests (service + endpoints)
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_live_challenge_grading.py -v
python -m pytest fastapi_app/tests/test_live_challenge_ws.py -v
# New test files for Last Stand:
python -m pytest fastapi_app/tests/test_last_stand_engine.py -v
python -m pytest fastapi_app/tests/test_last_stand_answer.py -v
```
