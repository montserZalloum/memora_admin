# Tasks: Challenge Hub (مركز التحدي)

**Input**: Design documents from `/specs/038-challenge-hub/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/challenge-hub-api.md, quickstart.md

**Tests**: Minimal test tasks included per Constitution VIII (pure logic + key integration tests).

**Organization**: Tasks grouped by user story. US2 + US3 + US4 share the same endpoint (POST /attempt) and service method — combined into one phase with per-story labels for traceability.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Redis keys, Pydantic models, and settings — everything downstream depends on these.

- [x] T001 Add 6 Redis key builders (`ch_progress_key`, `ch_leaderboard_key`, `ch_leaderboard_subject_key`, `ch_idem_key`, `dirty_ch_progress_key`, `ch_attempt_buffer_key`) and TTL constants (`CH_PROGRESS_KEY_TTL = 48h`, `CH_IDEM_KEY_TTL = 300s`, `CH_SETTINGS_KEY_TTL = 300s`) to `fastapi_app/core/redis_keys.py`. Note: challenge settings reuse the existing `memora:settings` key pattern (no separate key builder needed)
- [x] T002 [P] Create Pydantic request/response models (`AttemptRequest`, `QuestionDetail`, `AttemptResponse`, `ChallengeSubjectSummary`, `ChallengeHierarchyResponse`, `TopicState`, `UnitState`, `TrackState`, `LeaderboardEntry`, `LeaderboardResponse`, `MyRankResponse`) in `fastapi_app/models/challenge.py`
- [x] T003 [P] Add Challenge Hub section to Memora Settings DocType: `challenge_xp_per_question` (Int, default 5), `challenge_pass_threshold` (Int, default 50), `challenge_lb_top_count` (Int, default 20), `challenge_lb_refresh_interval` (Int, default 300) in `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json`

---

## Phase 2: Foundational (DocTypes + Routing)

**Purpose**: Data layer and API scaffolding that ALL user stories depend on.

**CRITICAL**: No user story work can begin until this phase is complete.

- [x] T004 [P] Create `Memora Challenge Progress` DocType with fields: `player` (Link → Memora Player Profile), `topic` (Link → Memora Topic), `subject` (Link → Memora Subject), `season` (Link → Memora Season), `stamped` (Check, default 0), `best_correct` (Int, default 0), `best_score_pct` (Percent, default 0), `best_passing_pct` (Percent, default 0), `total_xp_earned` (Int, default 0), `attempt_count` (Int, default 0). Autoname: `hash`. Files: `memora_admin/memora_admin/doctype/memora_challenge_progress/memora_challenge_progress.json` and `memora_challenge_progress.py`
- [x] T005 [P] Create `Memora Challenge Attempt Detail` child table DocType with fields: `item_id` (Data), `correct` (Check), `time_spent` (Int), `chosen_answer` (Int). Files: `memora_admin/memora_admin/doctype/memora_challenge_attempt_detail/memora_challenge_attempt_detail.json` and `memora_challenge_attempt_detail.py`
- [x] T006 [P] Create `Memora Challenge Attempt` DocType with fields: `player` (Link → Memora Player Profile), `topic` (Link → Memora Topic), `subject` (Link → Memora Subject), `season` (Link → Memora Season), `attempt_number` (Int), `total_questions` (Int), `correct_count` (Int), `score_pct` (Percent), `passed` (Check), `time_spent` (Int), `xp_earned` (Int), `submitted_at` (Datetime), `details` (Table → Memora Challenge Attempt Detail). Autoname: `naming_series` (`CHA-.#####`). Files: `memora_admin/memora_admin/doctype/memora_challenge_attempt/memora_challenge_attempt.json` and `memora_challenge_attempt.py`
- [x] T007 Create `ChallengeService` skeleton in `fastapi_app/services/challenge.py` with `__init__` accepting `redis: Redis`, `frappe_client: FrappeClient`, and `settings: dict`. Include `ensure_hydrated()` stub and structured logging setup via `structlog`
- [x] T008 Add `ChallengeServiceDep` (Annotated + Depends) to `fastapi_app/api/deps.py`, create empty router in `fastapi_app/api/v1/endpoints/challenge.py`, and mount `challenge.router` with prefix `/challenge` in `fastapi_app/api/v1/router.py`

**Checkpoint**: Foundation ready — DocTypes installable, router mounted, service injectable.

---

## Phase 3: User Story 1 — Browse Challenge Hierarchy (Priority: P1) 🎯 MVP

**Goal**: Students can open Challenge Hub, see their plan subjects, and browse tracks/units/topics with correct lock/open/stamped states.

**Independent Test**: Load Challenge Hub, verify subjects from plan appear, drill into a subject and confirm topic states match expected unlock chain logic (including hidden empty topics).

### Implementation for User Story 1

- [x] T009 [US1] Extend `generate_plan_json()` in `memora_admin/services/build/plan_generator.py` to embed `mcq_count` per topic by querying `Memora Review Item` where `stage_type = "QUESTION"` grouped by topic
- [x] T010 [US1] Implement `ensure_hydrated()` in `ChallengeService` in `fastapi_app/services/challenge.py` — on Redis cache miss for `ch_progress_key(player, subject)`, load all `Memora Challenge Progress` records for that player+subject from MariaDB via FrappeClient and populate the Redis HASH
- [x] T011 [US1] Implement `get_challenge_subjects()` in `ChallengeService` in `fastapi_app/services/challenge.py` — load player's plan subjects, for each subject load hierarchy + challenge progress, compute `total_topics` (excluding empty), `stamped_topics`, `total_challenge_xp`, return list of `ChallengeSubjectSummary`
- [x] T012 [US1] Implement `get_challenge_hierarchy()` in `ChallengeService` in `fastapi_app/services/challenge.py` — load hierarchy for subject, walk tracks → units → topics, for each topic evaluate 3 unlock conditions (access via `AccessService.check_access_with_plan()`, normal path complete via stats cache, predecessor stamped via challenge progress), handle empty topic auto-stamp chain (FR-009: when predecessor is stamped, empty topics with `mcq_count == 0` auto-stamp and propagate through the chain), filter hidden topics from response, attach `lock_reason`
- [x] T013 [US1] Implement GET `/challenge/hierarchy` endpoint in `fastapi_app/api/v1/endpoints/challenge.py` — depends on `ActiveSeasonDep`, `PlayerDep`, `ChallengeServiceDep`, returns `{"subjects": [ChallengeSubjectSummary]}`
- [x] T014 [US1] Implement GET `/challenge/hierarchy/{subject_id}` endpoint in `fastapi_app/api/v1/endpoints/challenge.py` — depends on `ActiveSeasonDep`, `PlayerDep`, `ChallengeServiceDep`, validates subject in player's plan (404 if not), returns `ChallengeHierarchyResponse` with tracks/units/topics and states

**Checkpoint**: Students can browse Challenge Hub hierarchy with correct topic states. No gameplay yet.

---

## Phase 4: User Story 2 + 3 + 4 — Core Gameplay Loop (Priority: P1)

**Goal**: Students can play a topic challenge, submit results, earn XP (delta-only), track best scores, retry, and have answers sent to FSRS. Covers: playing (US2), retrying with improvement tracking (US3), and isolated XP system (US4).

**Independent Test**: Select an open topic → answer all questions → verify grading, stamping, XP delta, best score tracking, FSRS push. Replay the same topic → verify delta XP only for improvement, best score updates correctly, attempt_count increments.

### Implementation for User Stories 2, 3, 4

- [x] T015 [US2] Implement `_grade_attempt()` pure method in `ChallengeService` in `fastapi_app/services/challenge.py` — calculate `score_pct = round(correct_count / total_questions * 100, 2)`, determine `passed = score_pct >= pass_threshold`, validate `correct_count == sum(q.correct for q in questions)` and `total_questions == len(questions)`
- [x] T016 [US3] Implement `_update_best_scores()` in `ChallengeService` in `fastapi_app/services/challenge.py` — compare current `correct_count` with `best_correct`, update `best_correct`, `best_score_pct` if improved, update `best_passing_pct` if passed and score > previous best passing, return `is_new_best` flag
- [x] T017 [US4] Implement `_calculate_xp_delta()` in `ChallengeService` in `fastapi_app/services/challenge.py` — `xp_delta = max(0, current_correct - previous_best_correct) * xp_per_question`, return `xp_delta` (0 if no improvement)
- [x] T018 [US2] Implement `_push_fsrs_interactions()` in `ChallengeService` in `fastapi_app/services/challenge.py` — for each question result, RPUSH to `interaction_buffer_key()` with FSRS interaction format: `{player, lesson, stage_id, item_id, event_type: "Completed", errors_count: 0 if correct else 1, time_spent, timestamp, metadata: {source: "challenge_hub"}}`. The `lesson` and `stage_id` per question are read from the cached topic question JSON file (generated by T028, which includes these fields per question item)
- [x] T019 [US2] Implement `submit_attempt()` orchestration in `ChallengeService` in `fastapi_app/services/challenge.py` — validate topic is open (3 conditions), load topic question file for `lesson`/`stage_id` mapping, grade → update best scores → calculate XP delta → update Redis progress HASH → SADD dirty set → RPUSH serialized attempt payload (attempt_number, total_questions, correct_count, score_pct, passed, time_spent, xp_earned, submitted_at, per-question details) to `ch_attempt_buffer_key()` → push FSRS interactions → compute `next_topic` (if this stamp unlocked the next topic, evaluate its unlock state and return `{topic_id, state}`, else null) → return `AttemptResponse`. Use Redis pipeline for atomic progress update + dirty set + attempt buffer + FSRS push
- [x] T020 [US2] Implement POST `/challenge/attempt` endpoint in `fastapi_app/api/v1/endpoints/challenge.py` — depends on `ActiveSeasonDep`, `PlayerDep`, `ChallengeServiceDep`. Implement idempotency: check `ch_idem_key(player, attempt_key)` via GET, if exists return cached response (409), else process and SET NX EX 300 with response. Validate request body against `AttemptRequest` model
- [x] T021 [US2] Add `sync_dirty_challenge_progress()` function to `memora_admin/tasks/sync.py` — two jobs in one function: (1) SPOP members from `dirty_ch_progress_key()`, for each `{player}:{subject}` load Redis HASH, upsert `Memora Challenge Progress` records in MariaDB (follow existing MERGE pattern, do not replace); (2) LPOP entries from `ch_attempt_buffer_key()` (batch up to 100), deserialize each payload, create `Memora Challenge Attempt` + child `Memora Challenge Attempt Detail` records in MariaDB
- [x] T022 [US2] Register `sync_dirty_challenge_progress` as scheduled job (every 1 min) in `memora_admin/hooks.py` under `scheduler_events.cron`

**Checkpoint**: Full gameplay loop works — play, score, stamp, retry, XP delta, FSRS push. All P1 stories complete.

---

## Phase 5: User Story 5 — Challenge Leaderboard (Priority: P2)

**Goal**: Students view Challenge XP rankings among peers in the same plan, with per-subject filtering and own-rank display.

**Independent Test**: Earn XP in challenges → open leaderboard → verify ranking position, filter by subject → verify subject-specific ranking, check own rank with neighbors.

### Implementation for User Story 5

- [x] T023 [US5] Integrate leaderboard ZSET update into `submit_attempt()` in `fastapi_app/services/challenge.py` — when `xp_delta > 0`, pipeline `ZINCRBY` on `ch_leaderboard_key(season, plan)` and `ch_leaderboard_subject_key(season, plan, subject)`. Update tier metadata (tieridx/tiercnt) following existing `LeaderboardService` pattern
- [x] T024 [P] [US5] Implement GET `/challenge/leaderboard` endpoint in `fastapi_app/api/v1/endpoints/challenge.py` — depends on `ActiveSeasonDep`, `PlayerDep`. Accept optional `subject_id`, `limit` (default 20, max 100), `offset` (default 0). Query ZREVRANGE on appropriate key, resolve player profiles (display_name, avatar), mark `is_me`, return `LeaderboardResponse`
- [x] T025 [US5] Implement GET `/challenge/leaderboard/me` endpoint in `fastapi_app/api/v1/endpoints/challenge.py` — depends on `ActiveSeasonDep`, `PlayerDep`. Accept optional `subject_id`. Get own score + rank via ZSCORE/ZREVRANK, get neighbors via ZREVRANGE around own rank, compute `xp_to_next`, return `MyRankResponse`. Handle unranked case (rank=null, xp=0)

**Checkpoint**: Leaderboard fully functional with plan-scope, subject filter, and own-rank.

---

## Phase 6: User Story 6 — Season Reset (Priority: P3)

**Goal**: When a season ends, all Challenge Hub data is cleared from Redis and archived in MariaDB.

**Independent Test**: Trigger season end → verify Redis challenge keys deleted, leaderboard ZSETs cleared, new season shows fresh state.

### Implementation for User Story 6

- [x] T026 [US6] Implement `reset_challenge_data(season_id)` in `ChallengeService` or as standalone function — SCAN and DELETE all `memora:ch:progress:*` keys, DELETE all `memora:lb:ch:{season_id}:*` keys (plan + subject leaderboards + tier metadata), DELETE `memora:dirty:ch_progress` entries for the season. MariaDB records are preserved as archive (no deletion)
- [x] T027 [US6] Hook `reset_challenge_data` into existing season expiry event — extend `on_season_updated` in `memora_admin/events/build_trigger.py` or `access_sync.py` to call challenge cleanup when season status changes to expired

**Checkpoint**: Season lifecycle complete — data clears on expiry, fresh start on new season.

---

## Phase 7: Build Pipeline (Content Delivery)

**Purpose**: Generate per-topic question JSON files for CDN delivery (zero DB load on challenge start).

- [x] T028 [P] Create topic question JSON file generator in `memora_admin/services/build/challenge_questions.py` — query `Memora Review Item` where `stage_type = "QUESTION"` per topic, generate `challenges/{subject_id}/topics/{topic_id}_q.json` with structure: `{topic_id, subject_id, total_questions, questions: [{item_id, lesson, stage_id, question_text, choices, correct_choice}]}`. The `lesson` and `stage_id` fields per question are required by the FSRS interaction push (T018) — including them in the cached file avoids MariaDB lookups in the hot path
- [x] T029 Add build trigger for question file rebuild on Review Item sync in `memora_admin/events/build_trigger.py` — when `on_content_updated` fires for a topic with Review Item changes, queue rebuild of that topic's question JSON file

**Checkpoint**: Question files auto-generated and served from CDN. Teachers add/edit questions → files rebuild automatically.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Logging, validation, and final verification across all stories.

- [x] T030 Add structured logging for challenge operations (attempt submissions, XP delta, stamps, cache misses, FSRS pushes) using `structlog` in `fastapi_app/services/challenge.py`
- [x] T031 Register rate limit scopes (`ch_hierarchy`: 10/min, `ch_attempt`: 30/min, `ch_leaderboard`: 10/min) in `fastapi_app/api/deps.py` and apply to all 5 challenge endpoints
- [x] T032 Run quickstart.md verification checklist — validate all 10 items pass end-to-end

---

## Phase 9: Tests (Constitution VIII)

**Purpose**: Pure logic tests and key integration tests to validate core challenge mechanics.

- [x] T033 [P] Pure tests for `_grade_attempt()` in `fastapi_app/tests/test_challenge_service.py` — test pass/fail at threshold boundary (49% fail, 50% pass, 51% pass), 0/N score, N/N score, validation that `correct_count` matches sum of `q.correct`
- [x] T034 [P] Pure tests for `_calculate_xp_delta()` in `fastapi_app/tests/test_challenge_service.py` — test first attempt (full XP), improvement (delta XP), regression (0 XP), same score (0 XP), configurable `xp_per_question`
- [x] T035 [P] Pure tests for `_update_best_scores()` in `fastapi_app/tests/test_challenge_service.py` — test new best overall, new best passing, regression (no update), first passing attempt after failures, `is_new_best` flag
- [x] T036 Integration test for empty topic auto-stamp chain in `fastapi_app/tests/test_challenge_service.py` — test chain of [stamped, empty, empty, real] resolves to [stamped, auto-stamped, auto-stamped, open], single empty topic, all-empty unit
- [x] T037 Integration test for challenge XP isolation in `fastapi_app/tests/test_challenge_service.py` — verify Challenge XP does not appear in main wallet hash (`memora:wallet:{player}`), main leaderboard ZSETs (`memora:lb:*` excluding `memora:lb:ch:*`), or profile stats
- [x] T038 Integration test for FSRS push in `fastapi_app/tests/test_challenge_service.py` — submit attempt, verify `memora:buffer:interactions` contains one entry per question with `metadata.source == "challenge_hub"`, verify abandoned attempt (no submission) produces zero buffer entries

**Checkpoint**: Core mechanics verified — grading, XP delta, unlock chain, isolation, FSRS push.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — entry point to feature, MVP
- **US2+US3+US4 (Phase 4)**: Depends on Phase 2. Depends on Phase 3 for unlock validation in submit_attempt
- **US5 (Phase 5)**: Depends on Phase 4 (leaderboard update is part of submit_attempt)
- **US6 (Phase 6)**: Depends on Phase 2 (DocTypes). Can run in parallel with Phases 4-5
- **Build Pipeline (Phase 7)**: Depends on Phase 1 (no service deps). Can run in parallel with Phases 3-6
- **Tests (Phase 9)**: Pure logic tests (T033-T035) can run after Phase 4. Integration tests (T036-T038) require Phases 4-5
- **Polish (Phase 8)**: Depends on all previous phases

### User Story Dependencies

- **US1 (Browse)**: Independent after Foundational — no other story deps
- **US2+US3+US4 (Gameplay)**: Depends on US1 (uses hierarchy + unlock logic to validate attempts)
- **US5 (Leaderboard)**: Depends on US2+US3+US4 (leaderboard ZINCRBY hooks into submit_attempt)
- **US6 (Season Reset)**: Independent after Foundational — can be built in parallel with gameplay

### Within Each Phase

- Redis keys → models → service → endpoints (sequential dependency)
- DocTypes are parallel (different directories)
- Pure logic methods (_grade, _update_best, _calc_xp) are parallel within service
- Endpoints depend on service methods being ready

### Parallel Opportunities

**Setup (Phase 1)**: T002 and T003 are [P] — can run with T001 in parallel after T001 starts (T002/T003 don't import from redis_keys)

**Foundational (Phase 2)**: T004, T005, T006 are [P] — all DocTypes can be created in parallel

**Phase 3**: T013 and T014 are independent endpoints (can parallelize if service is ready)

**Phase 7**: T028 is [P] — can run in parallel with any API-side work

**Cross-phase**: Phase 6 (Season Reset) and Phase 7 (Build Pipeline) can run in parallel with Phases 3-5

---

## Parallel Example: Phase 2 (Foundational)

```bash
# Launch all DocType creation in parallel:
Task T004: "Create Memora Challenge Progress DocType"
Task T005: "Create Memora Challenge Attempt Detail child table"
Task T006: "Create Memora Challenge Attempt DocType"

# Then sequentially:
Task T007: "Create ChallengeService skeleton" (needs redis_keys from T001)
Task T008: "Mount router + deps" (needs T007 service class)
```

## Parallel Example: Phase 4 (Core Gameplay)

```bash
# Launch pure logic methods in parallel:
Task T015: "Grading logic (_grade_attempt)"
Task T016: "Best score tracking (_update_best_scores)"
Task T017: "XP delta calculation (_calculate_xp_delta)"
Task T018: "FSRS push (_push_fsrs_interactions)"

# Then sequentially:
Task T019: "submit_attempt() orchestration" (depends on T015-T018, T028 for question file)
Task T020: "POST /attempt endpoint" (depends on T019)
Task T021: "sync_dirty_challenge_progress + attempt buffer flush" (depends on T004 DocType, T001 buffer key)
Task T022: "Register sync in hooks.py" (depends on T021)
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (3 tasks)
2. Complete Phase 2: Foundational (5 tasks)
3. Complete Phase 3: US1 — Browse Hierarchy (6 tasks)
4. **STOP and VALIDATE**: Students can browse Challenge Hub with correct topic states
5. Deploy/demo — hierarchy browsing is the entry point to the entire feature

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US1 (Hierarchy) → Browse works → **MVP deployed**
3. US2+US3+US4 (Gameplay) → Full play loop → **Core product deployed**
4. US5 (Leaderboard) → Competitive feature → **Engagement driver deployed**
5. US6 (Season Reset) + Build Pipeline → Lifecycle + CDN → **Production-ready**
6. Tests → Pure logic + integration verification → **Verified**
7. Polish → Logging, rate limits, verification → **Launch-ready**

### Suggested MVP Scope

Phase 1 (Setup) + Phase 2 (Foundational) + Phase 3 (US1) = **14 tasks**. This delivers a browsable Challenge Hub that students can open and navigate, proving the hierarchy integration, unlock chain logic, and empty topic handling work correctly.

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- US2 + US3 + US4 are combined in Phase 4 because they share the same endpoint (POST /attempt) and service method — splitting them into separate phases would create artificial dependencies
- Challenge XP is completely isolated from main XP/wallet/leaderboard (FR-022) — no modifications to existing XP or leaderboard code
- The build pipeline (Phase 7) generates CDN files for question delivery — independent of API implementation
- Topic question files include `lesson` and `stage_id` per question item (needed by FSRS push, avoids MariaDB lookups in hot path)
- Attempt details are buffered in Redis (`ch_attempt_buffer_key()`) and flushed to MariaDB by the sync task — same pattern as `memora:buffer:interactions`
- Protected keys (`dirty_ch_progress`, `ch_attempt_buffer`) must NEVER receive TTL (per CLAUDE.md)
- All Redis keys MUST use builders from `redis_keys.py` — no inline f-strings (per CLAUDE.md)
- Follow `decode_responses=True` convention — treat Redis responses as strings, no `.encode()` calls
