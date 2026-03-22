# Tasks: Live Challenge Mode — Last Stand

**Input**: Design documents from `/specs/053-last-stand-mode/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/api.yaml, research.md, quickstart.md

**Tests**: Not explicitly requested — test tasks omitted.

**Organization**: Tasks grouped by user story. US2 and US3 are tightly coupled (join + round engine) but kept separate per spec priority.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Schema Changes)

**Purpose**: Add new fields to DocType JSON definitions — no behavioral changes, just schema

- [x] T001 Add `mode` (Select: exam/last_stand, default exam), `starting_hearts` (Int, default 3), and `result_window_duration` (Int, default 3) fields to memora_admin/memora_admin/doctype/memora_live_challenge_event/memora_live_challenge_event.json
- [x] T002 [P] Add `final_hearts` (Int, default 0), `is_eliminated` (Check, default 0), `eliminated_at_question` (Int, default 0), and `avg_response_time_ms` (Int, default 0) fields to memora_admin/memora_admin/doctype/memora_live_challenge_participation/memora_live_challenge_participation.json

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared infrastructure that all user stories depend on — Redis keys, Pydantic models, event hydration

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 Add Last Stand Redis key builders (`round`, `hearts`, `alive`, `eliminated`, `eliminated_at`, `round_answers`, `response_times`, `correct_counts`, `answered_counts`, `mode`) to fastapi_app/core/redis_keys.py
- [x] T004 [P] Add `AnswerRequest`, `AnswerResponse`, and WS message models (`WSRoundStart`, `WSRoundResult`, `WSPlayerState`, `WSAliveCountUpdate`, `WSEventEnded`) to fastapi_app/models/live_challenge.py
- [x] T005 [P] Extend meta HASH hydration with `mode`, `starting_hearts`, `result_window_duration` when Waiting→Active in memora_admin/tasks/live_challenge_transitions.py

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Admin Creates a Last Stand Event (Priority: P1) MVP

**Goal**: Admins can create Last Stand events with starting_hearts and question_time_limit. Mode is immutable after creation.

**Independent Test**: Create a Last Stand event via admin, verify field validation and immutability.

- [x] T006 [US1] Implement Last Stand validation in `validate()`: require `starting_hearts` (1-10), require `enable_question_timer`, require `result_window_duration` (1-10) when `mode=last_stand`; enforce immutable mode in `before_save()`; auto-calculate `exam_duration` for Last Stand in memora_admin/memora_admin/doctype/memora_live_challenge_event/memora_live_challenge_event.py

**Checkpoint**: Admins can create and save Last Stand events with all constraints enforced

---

## Phase 4: User Story 7 — Exam Mode Remains Unchanged (Priority: P1)

**Goal**: All exam mode functionality continues to work. /submit rejects Last Stand events. Default mode is exam.

**Independent Test**: Run all existing exam mode workflows and verify no regressions. Call /submit for Last Stand and verify MODE_NOT_SUPPORTED.

- [x] T007 [US7] Add MODE_NOT_SUPPORTED early return in POST /submit when event mode is `last_stand` (read mode from Redis meta HASH) in fastapi_app/api/v1/endpoints/live_challenge.py

**Checkpoint**: Exam mode fully isolated — existing events unaffected, /submit gates Last Stand

---

## Phase 5: User Story 2 — Players Join and Play a Last Stand Event (Priority: P1)

**Goal**: Players join during Waiting phase, receive starting hearts. Late join rejected during Active. Hearts initialized in Redis.

**Independent Test**: Multiple players join a Last Stand event in Waiting phase, verify hearts initialization and Active-phase join rejection.

**Dependencies**: Requires US1 (event exists with mode field)

- [x] T008 [US2] Modify join logic: reject Active-phase joins for Last Stand (NO_LATE_JOIN error), initialize player in hearts HASH (`HSET player_id → starting_hearts`) and alive SET (`SADD player_id`) on join in fastapi_app/services/live_challenge.py
- [x] T009 [US2] Add `starting_hearts` and `mode` fields to join response in fastapi_app/api/v1/endpoints/live_challenge.py

**Checkpoint**: Players can join Last Stand events with hearts assigned; Active-phase join blocked

---

## Phase 6: User Story 3 — Round-Based Synchronized Gameplay (Priority: P1)

**Goal**: Server-driven round loop delivers questions one at a time. Each round: answer window → evaluate → result window → next. Lua script for atomic answer validation. Early close when all alive answered.

**Independent Test**: Run a Last Stand event through multiple rounds, verify phase transitions, round_id validation, heart deduction, elimination, and early close.

**Dependencies**: Requires US2 (players joined with hearts in Redis)

- [x] T010 [US3] Create `LastStandEngine` class: Lua atomic answer script (5 validations: status, alive, round_id, window, uniqueness), round loop coroutine (answer window → evaluate → result window → next/end), answer evaluation with heart deduction and elimination (`HINCRBY -1`, `SMOVE alive→eliminated`), early answer window close (100ms poll + Redis signal channel) in fastapi_app/services/last_stand_engine.py
- [x] T011 [US3] Add `ws → player_id` connection tracking in `register_connection` and `_broadcast_personalized(event_id, base_msg, player_states)` method for per-player WS messages in fastapi_app/services/live_challenge.py
- [x] T012 [US3] Wire `LastStandEngine` startup on Waiting→Active transition for Last Stand events; integrate `round_start` and `round_result` (personalized) broadcasts via `_broadcast_personalized`; handle event end (all questions done OR all eliminated) with `event_ended` broadcast in fastapi_app/services/live_challenge.py
- [x] T013 [US3] Add `POST /{event_id}/answer` endpoint: parse `AnswerRequest`, call engine's Lua answer script, return `AnswerResponse` or error (ROUND_MISMATCH, WINDOW_CLOSED, ALREADY_ANSWERED, NOT_ALIVE, MODE_NOT_SUPPORTED for exam) in fastapi_app/api/v1/endpoints/live_challenge.py

**Checkpoint**: Full round-based gameplay works — questions synchronized, answers validated atomically, hearts deducted, players eliminated, early close functional

---

## Phase 7: User Story 5 — Event Ends and Results Are Persisted (Priority: P1)

**Goal**: After event ends, reconcile Redis runtime state into Participation records with hearts, elimination, score, ranking, and response time data.

**Independent Test**: Complete a Last Stand event, verify Participation records have correct scores, hearts, elimination data, and 3-tier ranking.

**Dependencies**: Requires US3 (round engine creates runtime state in Redis)

- [x] T014 [US5] Extend reconciliation for Last Stand: read `hearts`, `eliminated_at`, `correct_counts`, `answered_counts`, `response_times` from Redis; compute score as `(correct_count / total_questions) * 100`; compute avg_response_time from answered questions only; persist `final_hearts`, `is_eliminated`, `eliminated_at_question`, `avg_response_time_ms` to Participation in memora_admin/tasks/live_challenge_transitions.py
- [x] T015 [US5] Implement 3-tier Last Stand ranking: sort by score DESC → `final_hearts` DESC → `avg_response_time_ms` ASC; use competition ranking (1,1,3 for ties) in memora_admin/tasks/live_challenge_transitions.py
- [x] T016 [P] [US5] Add Last Stand fields (`final_hearts`, `is_eliminated`, `eliminated_at_question`, `avg_response_time_ms`) to result and leaderboard response models and endpoints; leaderboard entries include `final_hearts` and `is_eliminated` in fastapi_app/api/v1/endpoints/live_challenge.py

**Checkpoint**: Event results fully persisted with correct 3-tier ranking; leaderboard available

---

## Phase 8: User Story 4 — Disconnect and Reconnect Handling (Priority: P2)

**Goal**: Disconnected players lose hearts for missed rounds (automatic — no answer = heart deduction). Reconnecting players receive current state via player_state WS message.

**Independent Test**: Simulate disconnect mid-round, verify heart deduction. Reconnect alive/eliminated players and verify correct state delivery.

**Dependencies**: Requires US3 (round engine handles unanswered rounds; connection tracking exists)

- [x] T017 [US4] Implement WS reconnect handler for Active Last Stand: check `alive`/`eliminated` SET membership; send `player_state` message with `hearts_remaining`, `is_alive`, `current_round_id`, `question_idx`, `phase`, `phase_remaining_ms`, current question (if alive + answer phase); eliminated players get `eliminated_at_question` in fastapi_app/services/live_challenge.py

**Checkpoint**: Disconnected players lose hearts automatically; reconnecting players resume seamlessly

---

## Phase 9: User Story 6 — Admin Monitors Active Last Stand Event (Priority: P2)

**Goal**: Admin dashboard shows alive count, eliminated count, and current round during Active events. Post-event: final ranking and leaderboard (already available from US5).

**Independent Test**: Run a Last Stand event, verify admin dashboard shows live stats updating each round.

**Dependencies**: Requires US3 (round engine provides runtime data), US5 (post-event leaderboard)

- [x] T018 [US6] Add `mode`, `alive_count`, `eliminated_count`, `current_round`, `total_rounds` to `GET /{event_id}/status` response for Active Last Stand (read from Redis alive/eliminated SETs and round HASH) in fastapi_app/api/v1/endpoints/live_challenge.py
- [x] T019 [P] [US6] Add `alive_count_update` WS broadcast (alive_count, eliminated_count, current_round) after each round evaluation in fastapi_app/services/last_stand_engine.py
- [x] T020 [P] [US6] Add Last Stand stats (alive_count, eliminated_count, current_round) to admin dashboard endpoint in memora_admin/api/live_challenge.py

**Checkpoint**: Admins see live stats during Active events and full results after Ended

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Crash recovery, safety nets, cleanup

- [x] T021 Implement FastAPI startup scan: query Active Last Stand events from MariaDB, check Redis round state, resume `LastStandEngine` from stored state (fast-forward missed rounds if `phase_end_ts < now`) in fastapi_app/services/live_challenge.py
- [x] T022 [P] Add mode-aware cron safety net: if `exam_end_ts` passed for Active Last Stand event and no round engine running, transition to Ended and trigger reconciliation in memora_admin/tasks/live_challenge_transitions.py
- [x] T023 [P] Add Redis key cleanup for Last Stand keys (`round`, `hearts`, `alive`, `eliminated`, `eliminated_at`, `round_answers:*`, `response_times`, `correct_counts`, `answered_counts`) after successful reconciliation in memora_admin/tasks/live_challenge_transitions.py
- [x] T024 Run quickstart.md validation — verify all key files exist, endpoints respond, Last Stand event can be created and played through

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — can start immediately after
- **US7 (Phase 4)**: Depends on Phase 2 — can run in parallel with US1
- **US2 (Phase 5)**: Depends on Phase 2 + US1 (event with mode field must exist)
- **US3 (Phase 6)**: Depends on US2 (players must join with hearts)
- **US5 (Phase 7)**: Depends on US3 (round engine creates runtime state)
- **US4 (Phase 8)**: Depends on US3 (connection tracking + round engine)
- **US6 (Phase 9)**: Depends on US3 + US5 (runtime stats + post-event leaderboard)
- **Polish (Phase 10)**: Depends on all user stories complete

### User Story Dependencies

```
Phase 1 (Setup) ──→ Phase 2 (Foundation) ──┬──→ US1 (Phase 3) ──→ US2 (Phase 5) ──→ US3 (Phase 6) ──┬──→ US5 (Phase 7)
                                            │                                                          ├──→ US4 (Phase 8)
                                            └──→ US7 (Phase 4)                                         └──→ US6 (Phase 9)
                                                                                                              │
                                                                                                              ▼
                                                                                                       Polish (Phase 10)
```

### Within Each User Story

- Models/schema before services
- Services before endpoints
- Core implementation before integration
- Story complete before moving to dependent stories

### Parallel Opportunities

- **Phase 1**: T001 ∥ T002 (different DocType JSON files)
- **Phase 2**: T003 ∥ T004 ∥ T005 (different source files)
- **Phase 3-4**: US1 ∥ US7 (independent — different files, no dependency)
- **Phase 7**: T014/T015 ∥ T016 (transitions.py ∥ endpoints)
- **Phase 9**: T018 ∥ T019 ∥ T020 (three different files)
- **Phase 10**: T022 ∥ T023 (same file but independent functions)

---

## Parallel Example: Phase 2 (Foundational)

```bash
# Launch all foundational tasks in parallel (three different files):
Task T003: "Redis key builders in fastapi_app/core/redis_keys.py"
Task T004: "Pydantic models in fastapi_app/models/live_challenge.py"
Task T005: "Meta HASH hydration in memora_admin/tasks/live_challenge_transitions.py"
```

## Parallel Example: US1 + US7

```bash
# These two stories are fully independent — run in parallel:
Task T006: "Event validation in memora_live_challenge_event.py"
Task T007: "Submit mode gate in fastapi_app/api/v1/endpoints/live_challenge.py"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (schema)
2. Complete Phase 2: Foundational (Redis keys, models, hydration)
3. Complete Phase 3: US1 — Admin Creates Last Stand Event
4. **STOP and VALIDATE**: Create a Last Stand event, verify all validation rules
5. Optionally deploy — admins can configure events while gameplay is built

### Incremental Delivery

1. Setup + Foundation → schema ready
2. US1 + US7 (parallel) → events configurable, exam isolated
3. US2 → players can join with hearts
4. US3 → full gameplay loop functional (**core milestone**)
5. US5 → results persisted, leaderboard available (**feature complete for P1**)
6. US4 + US6 (parallel) → reconnection + admin monitoring (**P2 complete**)
7. Polish → crash recovery, safety nets

### Critical Path

**Setup → Foundation → US1 → US2 → US3 → US5** — this is the minimum path to a working Last Stand feature with results. All P1 stories.

---

## Notes

- US2 and US3 are tightly coupled: US2 initializes hearts; US3 uses them. They cannot be independently tested in full without each other. US2's join behavior (rejection, hearts init) CAN be tested in isolation.
- The round engine (T010) is the largest single task — it's a new file with ~400-500 lines covering Lua scripts, async round loop, evaluation, and early close.
- All modifications to `fastapi_app/api/v1/endpoints/live_challenge.py` (T007, T009, T013, T016, T018) touch different endpoints/functions — no merge conflicts if done sequentially.
- All modifications to `memora_admin/tasks/live_challenge_transitions.py` (T005, T014, T015, T022, T023) touch different functions — no merge conflicts if done sequentially.
- FR-022 (no DB writes during Active) is enforced by design: round engine operates purely on Redis. DB writes happen only in reconciliation (T014).
