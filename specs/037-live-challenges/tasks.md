# Tasks: Live Challenges

**Input**: Design documents from `/specs/037-live-challenges/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: Included per Constitution Principle VIII (Test-First Coverage). Unit tests for pure logic (grading, ranking, XP), integration test for full flow (real Redis), WebSocket test.

**Organization**: Tasks grouped by user story. 7 user stories (3x P1, 3x P2, 1x P3).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Frappe DocTypes**: `memora_admin/memora_admin/doctype/`
- **Frappe API**: `memora_admin/memora_admin/api/`
- **Frappe Tasks**: `memora_admin/memora_admin/tasks/`
- **Frappe Hooks**: `memora_admin/hooks.py`
- **FastAPI Endpoints**: `fastapi_app/api/v1/endpoints/`
- **FastAPI Services**: `fastapi_app/services/`
- **FastAPI Models**: `fastapi_app/models/`
- **FastAPI Core**: `fastapi_app/core/`
- **FastAPI Tests**: `fastapi_app/tests/`
- **Frappe Tests**: `memora_admin/memora_admin/tests/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create all DocType schemas, Redis key builders, and Pydantic models so downstream phases have the data layer ready.

**Note**: Each new DocType directory must include an `__init__.py` file alongside the `.json` and `.py` files (Frappe requires this for module loading).

- [x] T001 [P] Create Event DocType JSON schema with all fields (event_name, status, scheduled_start, waiting_room_duration, exam_duration, exam_start_ts, exam_end_ts, enable_question_timer, question_time_limit, capacity, is_paid, show_correct_answers, show_student_rank, participation_xp, first/second/third_place_xp, default_xp, questions child table, eligible_plans child table, leaderboard_json, participant_count, submitted_count) with autoname "LC-.#####." and `__init__.py` in memora_admin/memora_admin/doctype/memora_live_challenge_event/
- [x] T002 [P] Create Question child table JSON schema (istable=1) with fields: question_text (Small Text), option_a/b/c/d (Data), correct_answer (Select: A/B/C/D), source_review_item (Link: Memora Review Item) and `__init__.py` in memora_admin/memora_admin/doctype/memora_live_challenge_question/
- [x] T003 [P] Create Eligible Plan child table JSON schema (istable=1) with field: plan (Link: Memora Academic Plan) and `__init__.py` in memora_admin/memora_admin/doctype/memora_live_challenge_eligible_plan/
- [x] T004 [P] Create Participation DocType JSON schema with fields: event (Link: Memora Live Challenge Event), player (Link: Memora Player Profile), score (Float), rank (Int), joined_at (Datetime), submitted_at (Datetime), answers_json (JSON), xp_awarded (Int, default 0) with autoname "hash" and `__init__.py` in memora_admin/memora_admin/doctype/memora_live_challenge_participation/
- [x] T005 [P] Add LC Redis key builders (lc_status_key, lc_questions_key, lc_count_key, lc_submitted_key, lc_meta_key) and LC_KEY_TTL (24h) constant with full docstrings documenting type, producers, consumers, and TTL in fastapi_app/core/redis_keys.py
- [x] T006 [P] Create Pydantic request/response models: EventDetailResponse, JoinResponse, SubmitAnswerItem, SubmitRequest, SubmitResponse, CorrectionItem, ResultResponse, LeaderboardEntryItem, LeaderboardResponse, and WebSocket message models per contracts/api.md in fastapi_app/models/live_challenge.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create DocType Python classes, service skeleton, FastAPI wiring, and run database migration.

**CRITICAL**: No user story work can begin until this phase is complete.

- [x] T007 [P] Create Question child table Python class (minimal Document subclass) in memora_admin/memora_admin/doctype/memora_live_challenge_question/memora_live_challenge_question.py
- [x] T008 [P] Create Eligible Plan child table Python class (minimal Document subclass) in memora_admin/memora_admin/doctype/memora_live_challenge_eligible_plan/memora_live_challenge_eligible_plan.py
- [x] T009 [P] Create Participation DocType Python class (minimal Document subclass) in memora_admin/memora_admin/doctype/memora_live_challenge_participation/memora_live_challenge_participation.py
- [x] T010 [P] Create LiveChallengeService skeleton class with redis dependency injection (matching existing service pattern from services/wallet.py or services/access.py) in fastapi_app/services/live_challenge.py
- [x] T011 [P] Create endpoint file skeleton with APIRouter(prefix="/live-challenge", tags=["live-challenge"]) and register it via include_router in fastapi_app/api/v1/endpoints/live_challenge.py and fastapi_app/api/v1/router.py
- [x] T012 [P] Add LiveChallengeService dependency factory (Annotated + Depends pattern) and LiveChallengeServiceDep type alias in fastapi_app/api/deps.py
- [x] T013 Run bench --site x.conanacademy.com migrate to create database tables for all 4 new DocTypes

**Checkpoint**: Foundation ready -- database tables exist, service skeleton registered, endpoints wired up.

---

## Phase 3: User Story 1 -- Admin Creates and Schedules a Live Challenge Event (Priority: P1)

**Goal**: Admins can create exam events with all configuration fields, questions, and eligible plans. Events automatically transition through lifecycle states at the scheduled times.

**Independent Test**: Create an event in the admin panel, verify all fields persist, confirm automatic Draft -> Waiting -> Active -> Ended transitions at the scheduled times. Verify overlap detection rejects conflicting schedules.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T014 [US1] Unit test for Event DocType: validate VALID_TRANSITIONS rejects invalid transitions, computed fields (exam_start_ts, exam_end_ts) are set correctly, overlap detection rejects conflicting schedules with 5-minute buffer, at-least-one-question validation, XP field non-negative validation in memora_admin/memora_admin/tests/test_live_challenge_event.py

### Implementation for User Story 1

- [x] T015 [US1] Implement Event DocType Python class with VALID_TRANSITIONS dict (Draft->Waiting, Waiting->Active, Active->Ended), computed fields (exam_start_ts, exam_end_ts in validate()), overlap validation against existing non-Draft events with 5-minute buffer, min/max range validation for durations/capacity/XP fields, at-least-one-question check before leaving Draft, and freeze-on-non-Draft (prevent edits after Draft) in memora_admin/memora_admin/doctype/memora_live_challenge_event/memora_live_challenge_event.py
- [x] T016 [P] [US1] Create Event DocType JS form handlers: conditional visibility for question_time_limit (depends_on enable_question_timer), read-only indicators for computed fields (exam_start_ts, exam_end_ts), form freeze when status != Draft, status indicator colors in memora_admin/memora_admin/doctype/memora_live_challenge_event/memora_live_challenge_event.js
- [x] T017 [US1] Create scheduled task process_live_challenge_transitions that queries pending transitions (Draft with scheduled_start <= now -> Waiting, Waiting with exam_start_ts <= now -> Active, Active with exam_end_ts <= now -> Ended), populates Redis keys (status, questions JSON, meta hash including eligible_plans as JSON array, count) on Waiting transition via get_memora_redis(), and triggers post-event processing on Ended transition in memora_admin/memora_admin/tasks/live_challenge_transitions.py
- [x] T018 [US1] Register scheduled job in memora_admin/hooks.py under scheduler_events cron "* * * * *" (every 60 seconds; WebSocket handles sub-minute precision for Waiting->Active, scheduled task is the safety net for all transitions)
- [x] T019 [P] [US1] Create import_review_items @frappe.whitelist() API that copies Review Item questions (question_text, choice_1..4 -> option_a..d, correct_choice 1-4 -> A/B/C/D) into event's child table, rejecting if event is not in Draft status in memora_admin/memora_admin/api/live_challenge.py

**Checkpoint**: Admin can create, configure, and schedule events. State transitions happen automatically. Questions can be imported from Review Items.

---

## Phase 4: User Story 2 -- Student Joins, Takes Exam, and Receives Instant Score (Priority: P1)

**Goal**: Students can join an event (during Waiting or Active status), answer questions, submit answers, and receive their score immediately. Duplicate submissions are rejected. Submissions are batched for persistence.

**Independent Test**: Have a student join via the join endpoint, submit answers via the submit endpoint, verify correct score is returned immediately, verify duplicate submission is rejected.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T020 [US2] Unit test for grading logic: verify score = (correct/total) x 100, verify corrections list only includes wrong answers, verify null/unanswered selections count as incorrect, verify show_correct_answers=false returns null corrections in fastapi_app/tests/test_live_challenge_grading.py

### Implementation for User Story 2

- [x] T021 [US2] Implement join logic in LiveChallengeService: validate event status from Redis (Waiting or Active -- Active allows late joins per US2-7), check not already joined (Redis submitted set + participation lookup via FrappeClient), atomic capacity check via Lua script (INCR only if under capacity, avoiding INCR+DECR race condition per Constitution Principle II Lua atomicity), create Participation record via FrappeClient, return countdown_remaining (server-authoritative, 0 if Active) in fastapi_app/services/live_challenge.py
- [x] T022 [US2] Implement grading logic in LiveChallengeService: load questions with correct answers from Redis, compare each submitted answer against correct_answer, calculate score = (correct_count / total_questions) * 100, build corrections list (only incorrect answers with correct_answer if show_correct_answers enabled, null otherwise), mark submitted via SADD to submitted set in fastapi_app/services/live_challenge.py
- [x] T023 [US2] Implement submission batch queue in LiveChallengeService: asyncio.Queue, background consumer task that flushes to MariaDB via FrappeClient every 50 items or 30 seconds (whichever first), mandatory drain on event end and on shutdown, each flush writes Participation records (score, submitted_at, answers_json) and increments event submitted_count in fastapi_app/services/live_challenge.py
- [x] T024 [P] [US2] Create GET /live-challenge/{event_id} endpoint returning EventDetailResponse with public event details (no correct answers), player-specific flags (has_joined, has_submitted) via Redis lookups, question_count (not questions), and current_count from Redis counter in fastapi_app/api/v1/endpoints/live_challenge.py
- [x] T025 [US2] Create POST /live-challenge/{event_id}/join endpoint using LiveChallengeService.join(), returning JoinResponse with position, countdown_remaining, ws_url; apply rate limit require_rate_limit("lc_join"); handle errors: EVENT_NOT_JOINABLE (400), ALREADY_JOINED (409), PLAN_NOT_ELIGIBLE (403), CAPACITY_FULL (422) in fastapi_app/api/v1/endpoints/live_challenge.py
- [x] T026 [US2] Create POST /live-challenge/{event_id}/submit endpoint using LiveChallengeService.grade() and queue_submission(), returning SubmitResponse with immediate score; apply rate limit require_rate_limit("lc_submit"); handle errors: EVENT_NOT_ACTIVE (400), NOT_A_PARTICIPANT (403), ALREADY_SUBMITTED (409) in fastapi_app/api/v1/endpoints/live_challenge.py
- [x] T027 [US2] Wire up LiveChallengeService initialization and queue consumer background task in FastAPI lifespan (start on startup, drain on shutdown) in fastapi_app/main.py

**Checkpoint**: Full exam flow works: join -> submit -> instant score. Batch queue persists results to MariaDB.

---

## Phase 5: User Story 3 -- Waiting Room with WebSocket Start Signal (Priority: P1)

**Goal**: Students connect via WebSocket during Waiting Room, see live countdown, and receive a synchronized start signal with questions when the exam begins. Late joiners during Active receive questions immediately.

**Independent Test**: Connect multiple WebSocket clients to waiting room, verify all receive countdown updates and the exam_start signal simultaneously when countdown reaches zero.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T028 [US3] Integration test for WebSocket: verify countdown messages include remaining seconds and participant_count, verify exam_start message contains questions without correct_answer, verify reconnection during Active receives exam_start immediately, verify event_ended message is broadcast in fastapi_app/tests/test_live_challenge_ws.py

### Implementation for User Story 3

- [x] T029 [US3] Implement event-scoped WebSocket connection tracking (dict of event_id -> set[WebSocket]) in LiveChallengeService with methods: register_connection, remove_connection, get_connected_count, and periodic countdown broadcast loop (send remaining seconds + participant_count every 1-2 seconds) in fastapi_app/services/live_challenge.py
- [x] T030 [US3] Create WebSocket /live-challenge/{event_id}/ws endpoint with JWT auth via query parameter token (auth-before-accept pattern from notifications.py), validate event is Waiting or Active, register connection, if Waiting send countdown state, if Active send exam_start with questions (sans correct_answer) immediately (late join / reconnect), handle disconnect cleanup in fastapi_app/api/v1/endpoints/live_challenge.py
- [x] T031 [US3] Add exam_start broadcast logic: FastAPI countdown loop is authoritative for the start signal -- when server time >= exam_start_ts, set Redis status to "active" and broadcast exam_start to all connected clients with questions (without correct_answer), exam_end_ts, and timer settings; the Frappe scheduled task transitions MariaDB status independently as a safety net (idempotent). Also add event_ended broadcast when Redis status changes to "ended" in fastapi_app/services/live_challenge.py

**Checkpoint**: Real-time waiting room works. Students get synchronized start signal and questions via WebSocket. FastAPI owns real-time transitions; scheduled task owns MariaDB persistence.

---

## Phase 6: User Story 7 -- Eligible Study Plans Restriction (Priority: P2)

**Goal**: Events can be restricted to specific study plans. Only students on eligible plans can join.

**Independent Test**: Create an event with eligible plans, verify matching-plan student can join, non-matching student is rejected with PLAN_NOT_ELIGIBLE, and an event with empty eligible plans allows all students.

### Implementation for User Story 7

- [x] T032 [US7] Add plan eligibility validation to join flow in LiveChallengeService: read eligible_plans JSON array from Redis meta hash (populated during Waiting transition in T017), if non-empty check player's plan (from FrappeClient player profile lookup) against eligible set, raise PLAN_NOT_ELIGIBLE if not matched, skip check if eligible_plans is empty array in fastapi_app/services/live_challenge.py

**Checkpoint**: Plan-based access control works for event joins.

---

## Phase 7: User Story 4 -- Leaderboard Calculation and Display After Event Ends (Priority: P2)

**Goal**: After event ends, compute ranked leaderboard using standard competition ranking, store top 20 on event and individual rank on each participation. Students can view leaderboard and their own result.

**Independent Test**: End an event with multiple submissions, verify standard competition ranking (tied scores share rank, next rank = count of players ranked above; e.g., 1, 1, 3), top 20 stored on event, individual rank on each participation record, result and leaderboard endpoints return correct data.

### Tests for User Story 4

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T033 [US4] Unit test for ranking computation: verify standard competition ranking (scores [100, 100, 95, 90] -> ranks [1, 1, 3, 4]), verify single participant gets rank 1, verify all same scores share rank 1, verify top 20 truncation, verify display_name is resolved in leaderboard entries in memora_admin/memora_admin/tests/test_live_challenge_ranking.py

### Implementation for User Story 4

- [x] T034 [US4] Implement leaderboard computation in the scheduled task: query all Participation records for the event ordered by score DESC, compute standard competition ranking (same score = same rank, next rank = position number), resolve display_name for each participant from Memora Player Profile, store top 20 as leaderboard_json on Event (including rank, player, display_name, score), store individual rank on each Participation record, update participant_count and submitted_count on Event in memora_admin/memora_admin/tasks/live_challenge_transitions.py
- [x] T035 [P] [US4] Create GET /live-challenge/{event_id}/result endpoint returning ResultResponse with student's score, correct_count, rank (null if not computed yet), xp_awarded (null if not distributed yet), corrections (if show_correct_answers enabled); read from Participation record via FrappeClient in fastapi_app/api/v1/endpoints/live_challenge.py
- [x] T036 [P] [US4] Create GET /live-challenge/{event_id}/leaderboard endpoint returning LeaderboardResponse with top 20 from event's leaderboard_json, student's own rank and score (if show_student_rank enabled, null otherwise); return EVENT_NOT_ENDED (400) if event status != Ended in fastapi_app/api/v1/endpoints/live_challenge.py
- [x] T037 [US4] Integrate leaderboard computation into post-event processing: ensure it runs after Ended transition in T017's process_live_challenge_transitions, only compute once (check if leaderboard_json already populated) in memora_admin/memora_admin/tasks/live_challenge_transitions.py

**Checkpoint**: Leaderboard computed after event ends. Students can view their result and the top 20 leaderboard.

---

## Phase 8: User Story 5 -- XP Rewards Distribution (Priority: P2)

**Goal**: After leaderboard computation, distribute XP: participation XP to all submitters, rank-based bonus XP (1st/2nd/3rd/default) to ranked participants.

**Independent Test**: After event completion, verify each student's wallet reflects correct XP (participation + rank bonus). Tied-rank students both receive that rank's XP.

### Tests for User Story 5

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T038 [US5] Unit test for XP calculation: verify participation_xp added to all submitters, verify rank 1 gets first_place_xp, rank 2 gets second_place_xp, rank 3 gets third_place_xp, rank 4+ gets default_xp, verify tied rank 1 students both get first_place_xp, verify total = participation_xp + rank_bonus in memora_admin/memora_admin/tests/test_live_challenge_xp.py

### Implementation for User Story 5

- [x] T039 [US5] Implement XP distribution in the scheduled task: iterate ranked Participation records, calculate total XP per player (participation_xp + rank-based bonus: first_place_xp for rank 1, second_place_xp for rank 2, third_place_xp for rank 3, default_xp for rank 4+), award via Redis HINCRBY on wallet hash + SADD to dirty:wallets set using get_memora_redis(), update xp_awarded on each Participation record in memora_admin/memora_admin/tasks/live_challenge_transitions.py
- [x] T040 [US5] Integrate XP distribution into post-event processing: run after leaderboard computation (T037), only distribute once (check if any Participation.xp_awarded > 0), handle tied ranks (both get same rank's XP) in memora_admin/memora_admin/tasks/live_challenge_transitions.py

**Checkpoint**: XP correctly distributed to all participants based on rank.

---

## Phase 9: User Story 6 -- Admin Monitors Active Event via Dashboard (Priority: P3)

**Goal**: Admin can view real-time dashboard during active events (connected/submitted/still-taking counts, time remaining) and post-event analytics (full leaderboard, aggregate stats, drill-down).

**Independent Test**: Start an event, have students join and submit, verify dashboard counters. After event ends, verify aggregate statistics and leaderboard display.

### Implementation for User Story 6

- [x] T041 [US6] Create get_dashboard @frappe.whitelist() Frappe API: for Active events return participant_count, submitted_count, still_taking_count (participant - submitted), time_remaining (exam_end_ts - now), exam_end_ts; for Ended events return participant_count, submitted_count, completion_rate, average_score, highest_score (computed from Participation records), and full leaderboard from leaderboard_json in memora_admin/memora_admin/api/live_challenge.py

**Checkpoint**: Admin has operational visibility into active and completed events.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Integration test, rate limit config, end-to-end validation.

- [x] T042 Integration test for full flow: create event (Frappe), transition to Waiting (scheduled task), join via FastAPI, submit answers, verify immediate score, transition to Ended, verify leaderboard computed with correct ranking, verify XP distributed to wallets, all against real Redis in fastapi_app/tests/test_live_challenge_integration.py
- [x] T043 [P] Add rate limit configuration entries for lc_join (5 per minute) and lc_submit (2 per minute) to rate limiting setup if not handled inline by require_rate_limit dependency
- [x] T044 Verify end-to-end flow per quickstart.md: create event, migrate, add questions, wait for transitions, join via FastAPI, submit answers, verify score, verify leaderboard and XP after event ends

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies -- can start immediately
- **Foundational (Phase 2)**: Depends on Setup (Phase 1) -- BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 -- creates events and state machine
- **US2 (Phase 4)**: Depends on Phase 2 + US1 (events must exist with Redis keys populated)
- **US3 (Phase 5)**: Depends on Phase 2 + US1 (needs events in Waiting status with Redis data)
- **US7 (Phase 6)**: Depends on US2 (adds plan check to existing join flow)
- **US4 (Phase 7)**: Depends on US2 (needs submissions to compute leaderboard)
- **US5 (Phase 8)**: Depends on US4 (needs ranks for XP calculation)
- **US6 (Phase 9)**: Depends on US1 + US2 (dashboard reads event and participation data)
- **Polish (Phase 10)**: Depends on all user stories

### User Story Dependencies

```
Phase 1 (Setup) -> Phase 2 (Foundational)
                        |
                        v
                   Phase 3 (US1: Admin Creates Event)
                   /           \
                  v             v
   Phase 4 (US2: Exam Flow)   Phase 5 (US3: WebSocket)
         |          \
         v           v
  Phase 6 (US7)   Phase 7 (US4: Leaderboard)
                        |
                        v
                   Phase 8 (US5: XP Rewards)
                        |
                        v
         Phase 9 (US6: Admin Dashboard) -- also depends on US1+US2
                        |
                        v
                   Phase 10 (Polish)
```

### Within Each User Story

- Tests MUST be written first and verified to FAIL before implementation
- Models/schemas before service logic
- Service logic before endpoints
- Core implementation before integration wiring
- Frappe-side (DocType, scheduled task) before FastAPI-side where cross-process dependency exists

### Parallel Opportunities

**Phase 1**: All 6 tasks (T001-T006) are fully parallel -- different files, no dependencies.

**Phase 2**: T007-T012 are all parallel (different files). T013 (migrate) depends on T001-T009.

**Phase 3 (US1)**: T014 (test) runs first. Then T015 (passes the test). T016 (JS) and T019 (import API) are parallel with each other. T017 depends on T015. T018 depends on T017.

**Phase 4 (US2)**: T020 (test) runs first. T024 (GET endpoint) is parallel with T021-T023 (service methods). T025 and T026 depend on T021-T023.

**Phase 5 (US3)**: T028 (test) runs first. T029 must complete before T030 and T031.

**US2 and US3 can run in parallel** after US1 completes (independent of each other).

**Phase 7 (US4)**: T033 (test) runs first. T035 and T036 (GET endpoints) are parallel with each other and with T034 (different files).

---

## Parallel Example: Phase 1

```
# All 6 setup tasks run simultaneously:
T001: Event DocType JSON + __init__.py
T002: Question child table JSON + __init__.py
T003: Eligible Plan child table JSON + __init__.py
T004: Participation DocType JSON + __init__.py
T005: Redis key builders
T006: Pydantic models
```

## Parallel Example: After US1 Completes

```
# US2 and US3 can start simultaneously:
Thread A: T020 (test) -> T021 -> T022 -> T023 -> T025 -> T026 -> T027  (US2: Exam flow)
Thread B: T028 (test) -> T029 -> T030 -> T031                          (US3: WebSocket)
```

---

## Implementation Strategy

### MVP First (US1 + US2 Only)

1. Complete Phase 1: Setup (all DocTypes + Redis keys + models)
2. Complete Phase 2: Foundational (skeletons + migrate)
3. Complete Phase 3: US1 (event creation + state transitions)
4. Complete Phase 4: US2 (join + submit + score)
5. **STOP and VALIDATE**: Test full exam flow end-to-end without WebSocket or leaderboard
6. This delivers a functional exam system where students join, answer, and get scores

### Incremental Delivery

1. Setup + Foundational -> Data layer ready
2. US1 -> Admin can create and schedule events
3. US2 -> Students can take exams and get scores (MVP!)
4. US3 -> Real-time waiting room with synchronized start
5. US7 -> Plan-based access restriction
6. US4 -> Post-event leaderboard
7. US5 -> XP rewards
8. US6 -> Admin dashboard with live stats
9. Polish -> Integration test, rate limits, end-to-end validation

---

## Notes

- [P] tasks = different files, no dependencies within the same phase
- [Story] label maps task to specific user story for traceability
- All Redis keys MUST use builders from `fastapi_app/core/redis_keys.py` -- never inline f-strings
- All Redis key writes MUST set TTL via LC_KEY_TTL (24h) constant
- The submission batch queue has a documented 30s max data loss window (per spec A-009)
- Server-authoritative timing: never trust client clocks for deadlines (per spec FR-023)
- WebSocket is server-push only; no client-to-server messages required
- Post-event processing order: flush queue -> compute leaderboard -> distribute XP
- XP distribution uses existing wallet Redis pattern (HINCRBY + dirty set SADD)
- Ranking uses standard competition ranking (1, 1, 3) -- NOT dense ranking (1, 1, 2)

### Transition Ownership (FastAPI vs Scheduled Task)

The Waiting->Active transition has two actors:
- **FastAPI (primary, real-time)**: The WebSocket countdown loop detects `now >= exam_start_ts`, sets Redis status to "active", and broadcasts `exam_start` to all connected clients immediately.
- **Frappe scheduled task (secondary, safety net)**: Runs every 60 seconds, transitions MariaDB status, and sets Redis status (idempotent SET -- same value if FastAPI already set it).

Both are idempotent. FastAPI provides sub-second precision for the start signal; the scheduled task ensures MariaDB state consistency.

### Constitutional Exceptions

Two documented deviations from Constitution Principle I (Self-Healing Cache):

1. **No `ensure_hydrated()` for LC Redis keys**: LC keys are ephemeral (24h TTL), created at event start, consumed during event, and auto-expire. If Redis restarts mid-event, the event is effectively lost (accepted -- events are 1-180 minutes). MariaDB remains source of truth; data IS reconstructable but auto-recovery is deferred as a future improvement. Plan gate: PASS.

2. **In-memory submission queue**: The asyncio.Queue has a 30s worst-case data loss window if the FastAPI process crashes (spec A-009). Acknowledged submissions (score returned to student) may not reach MariaDB, affecting leaderboard/XP accuracy. This trade-off is explicitly accepted for performance (avoiding 1000 concurrent MariaDB writes). The queue is NOT Redis-only state -- it's transient write buffering.
