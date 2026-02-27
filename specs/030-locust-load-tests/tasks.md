# Tasks: Locust Load Test Suite

**Input**: Design documents from `/specs/030-locust-load-tests/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Not applicable — this feature IS a test suite. No test tasks generated.

**Organization**: Tasks grouped by user story. US1 and US2 are both P1 but split into incremental phases: US1 delivers a working test, US2 makes it realistic.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Exact file paths included in descriptions

## Path Conventions

All paths relative to repository root (`apps/memora_admin/`):

```text
load_tests/
├── locustfile.py        # Main entry point with all 4 user classes
├── config.py            # Real test data (gitignored)
├── config.example.py    # Placeholder config for VCS
├── helpers.py           # Shared auth helper, response validators
└── README.md            # Usage guide, scaling ladder, distributed mode
```

---

## Phase 1: Setup

**Purpose**: Create directory structure and version control configuration

- [x] T001 Create `load_tests/` directory and add `load_tests/config.py` to `.gitignore`

---

## Phase 2: Foundational (Config + Helpers)

**Purpose**: Shared infrastructure that ALL user profiles depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 [P] Create `load_tests/config.example.py` with placeholder config structure: `HOST`, `TEST_PLAYERS` (3 entries with mobile/password), `TEST_SUBJECTS` (2 entries), `TEST_LESSONS` (1 entry with lesson_id/subject_id/topic_id/stages), `SCALING_LADDER` (5 stages: 100/1k/10k/50k/100k)
- [x] T003 [P] Create `load_tests/helpers.py` with: (1) `AuthMixin` class providing `on_start()` that authenticates via `POST /api/v1/auth/player/login` with random player from config, stores `self.token` and `self.device_id`, handles 429 gracefully (FR-002, FR-007); (2) `api_get(self, path, name, **kwargs)` helper with Bearer auth header, 429→success, 401→success+clear token, aggregated naming (FR-006, FR-007, FR-008); (3) `api_post(self, path, json, name, **kwargs)` helper with same error handling

**Checkpoint**: Config + helpers ready — user profile implementation can begin

---

## Phase 3: User Story 1 — Run Basic Load Test (Priority: P1) MVP

**Goal**: All 4 user behavior profiles execute their flows and produce per-endpoint stats output

**Independent Test**: `cd load_tests && locust --headless -u 10 -r 5 --run-time 30s --host http://127.0.0.1:8002` — all 4 profile types appear in stats output

### Implementation

- [x] T004 [US1] Create `load_tests/locustfile.py` with imports (locust, config, helpers) and DashboardUser class: inherits `AuthMixin` + `HttpUser`, `weight=40`, `wait_time=between(3, 8)`, single `@task` method that sequentially calls `GET /profile`, `GET /profile/stats`, `GET /profile/activity`, `GET /wallet`, `GET /progress` via `api_get()`
- [x] T005 [US1] Add LessonPlayer class to `load_tests/locustfile.py`: inherits `AuthMixin` + `HttpUser`, `weight=35`, `wait_time=between(5, 15)`, single `@task` method that picks a random lesson from `config.TEST_LESSONS`, calls `POST /sessions/start` with `lesson_id`/`subject_id`, sleeps 3-5s, calls `POST /sessions/end` with minimal stage payload (1 stage, randomized time_spent), then `GET /wallet`
- [x] T006 [US1] Add BrowserUser class to `load_tests/locustfile.py`: inherits `AuthMixin` + `HttpUser`, `weight=15`, `wait_time=between(2, 6)`, single `@task` method that calls `GET /progress` then `GET /progress/{subject}/tracks` for a random config subject, using aggregated request names
- [x] T007 [US1] Add LeaderboardChecker class to `load_tests/locustfile.py`: inherits `AuthMixin` + `HttpUser`, `weight=10`, `wait_time=between(5, 10)`, single `@task` method that calls `GET /leaderboard/daily` and `GET /leaderboard/daily/me` via `api_get()` with aggregated names

**Checkpoint**: Basic load test runs — all 4 profiles execute and produce stats. US1 acceptance scenarios pass.

---

## Phase 4: User Story 2 — Realistic Traffic Distribution (Priority: P1)

**Goal**: Traffic distribution matches production patterns — weighted tasks within profiles, realistic think times, full session lifecycle, hierarchy drill-down

**Independent Test**: `locust --headless -u 100 -r 20 --run-time 2m --host http://127.0.0.1:8002` — per-endpoint request counts roughly match expected distribution (dashboard ~40%, lesson ~35%, browser ~15%, leaderboard ~10%) within 10% margin

### Implementation

- [x] T008 [US2] Refactor DashboardUser in `load_tests/locustfile.py`: split single task into 6 weighted methods — `check_profile` `@task(3)`, `check_stats` `@task(2)`, `check_activity` `@task(2)`, `check_mastery` `@task(1)` (GET /profile/mastery), `check_wallet` `@task(1)`, `check_progress` `@task(1)`
- [x] T009 [US2] Implement full LessonPlayer session lifecycle in `load_tests/locustfile.py`: (1) pick a random lesson from `config.TEST_LESSONS` (which provides `lesson_id`, `subject_id`, and `topic_id`), (2) `GET /progress/{subject}/topics/{topic}/lessons` to simulate browsing (name: `/api/v1/progress/[subject]/topics/[topic]/lessons`), using `topic_id` from config, (3) `POST /sessions/start` with `lesson_id`/`subject_id`, (4) `time.sleep(random.uniform(3, 10))` to simulate student thinking, (5) `POST /sessions/end` with 1-3 stages containing randomized `time_spent` (3000-10000ms), `fail_count` (0-2), ISO `completed_at`, empty `items` list, (6) `GET /wallet` to verify XP; handle 409 on session start by skipping to next iteration
- [x] T010 [US2] Implement full BrowserUser hierarchy drill-down in `load_tests/locustfile.py`: (1) `GET /progress` → pick random subject, (2) `GET /progress/{subject}/tracks` → extract `track_id` from response, stop if empty, (3) `GET /progress/{subject}/tracks/{track}` → extract `unit_id` from units array, stop if empty, (4) `GET /progress/{subject}/tracks/{track}/units/{unit}` → final level; all requests use aggregated names with `[subject]`, `[track]`, `[unit]` placeholders
- [x] T011 [US2] Refactor LeaderboardChecker in `load_tests/locustfile.py`: split into 3 weighted methods — `check_daily` `@task(2)` (GET /leaderboard/daily), `check_weekly` `@task(1)` (GET /leaderboard/weekly), `check_my_rank` `@task(2)` (randomly GET /leaderboard/daily/me or /leaderboard/weekly/me); all use aggregated name `/api/v1/leaderboard/[type]` or `/api/v1/leaderboard/[type]/me`

**Checkpoint**: Realistic traffic distribution — endpoint request counts match configured weights at steady state. US2 acceptance scenarios pass.

---

## Phase 5: User Story 4 — Configure Test Data Without Code Changes (Priority: P2)

**Goal**: Test data defined in a separate config file; no hardcoded values in test code; only placeholders committed to VCS

**Independent Test**: Modify `config.py` to use different player accounts/subjects, re-run the load test, verify it uses the new configuration

### Implementation

- [x] T012 [US4] Add startup config validation at module level in `load_tests/locustfile.py`: assert `config.TEST_PLAYERS` is non-empty (with clear error message "No test players configured — copy config.example.py to config.py"), assert `config.TEST_SUBJECTS` is non-empty, assert `config.TEST_LESSONS` is non-empty; fail fast before Locust spawns users
- [x] T013 [US4] Audit `load_tests/locustfile.py` and `load_tests/helpers.py` for hardcoded test data — ensure ALL player accounts, subject IDs, lesson IDs, and stage templates read from `config` module; verify `HOST` is passed via Locust CLI `--host` flag (not hardcoded)

**Checkpoint**: Config externalization complete — suite is portable across environments by editing a single file. US4 acceptance scenarios pass.

---

## Phase 6: User Story 3 — Scale to 100k Simulated Users (Priority: P2)

**Goal**: Documentation with 5-stage scaling ladder and distributed mode instructions for reaching 100k concurrent users

**Independent Test**: Follow README Stage 1 instructions (100 users, 2 min) and verify it runs successfully

### Implementation

- [x] T014 [US3] Create `load_tests/README.md` with: project overview, prerequisites (Python 3.11+, Locust, running FastAPI sidecar, pre-created test accounts), setup instructions (copy config.example.py, edit config.py), and basic CLI examples for headless mode and web UI mode (FR-010)
- [x] T015 [US3] Add 5-stage scaling ladder section to `load_tests/README.md`: table with Stage 1 (100u, rate 10, 2min) through Stage 5 (100ku, rate 1000, 15min), CLI commands for each stage, CSV/HTML output flags, and success criteria per stage (FR-012)
- [x] T016 [US3] Add distributed mode section to `load_tests/README.md`: master/worker CLI commands, auto-spawn workers with `--processes -1`, multi-machine setup with `--expect-workers`, hardware estimation table (Stage 1-2: 1 process, Stage 3: 2-4 workers, Stage 4: 8-12 workers, Stage 5: 16-24 workers) (FR-013)
- [x] T017 [US3] Add troubleshooting section to `load_tests/README.md`: common issues (all logins fail, connection refused, high 429 rate, session-end failures, empty responses) with resolution steps; add "What Gets Tested" table showing profile weights and endpoint coverage

**Checkpoint**: README complete — a developer can follow it end-to-end from setup through 100k simulation. US3 acceptance scenarios pass.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup across all files

- [x] T018 Verify all parameterized endpoints use aggregated request names per FR-006 naming convention in `load_tests/locustfile.py`: `/api/v1/progress/[subject]`, `/api/v1/progress/[subject]/tracks`, `/api/v1/progress/[subject]/tracks/[track]`, `/api/v1/progress/[subject]/tracks/[track]/units/[unit]`, `/api/v1/progress/[subject]/topics/[topic]/lessons`, `/api/v1/leaderboard/[type]`, `/api/v1/leaderboard/[type]/me`
- [x] T019 Run quickstart.md sanity validation: `cd load_tests && locust --headless -u 10 -r 5 --run-time 30s --host http://127.0.0.1:8002` — verify all 4 profiles appear in stats, no unexpected failures, rate-limited responses counted as success

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — creates the working test suite
- **US2 (Phase 4)**: Depends on Phase 3 — enhances flows for realism
- **US4 (Phase 5)**: Depends on Phase 3 — validates config separation
- **US3 (Phase 6)**: No code dependencies — can start after Phase 2 (documentation only)
- **Polish (Phase 7)**: Depends on Phases 3-6 complete

### User Story Dependencies

- **US1 (P1)**: Depends on Foundational only — no cross-story dependencies
- **US2 (P1)**: Depends on US1 (refactors the user classes created in US1)
- **US4 (P2)**: Depends on US1 (audits code written in US1)
- **US3 (P2)**: Independent of other stories (documentation) — can run in parallel with US2/US4

### Within Each User Story

- US1: T004 → T005 → T006 → T007 (sequential — same file, each builds on previous)
- US2: T008 → T009 → T010 → T011 (sequential — same file, each refactors a class)
- US4: T012 → T013 (sequential — add validation before audit)
- US3: T014 → T015 → T016 → T017 (sequential — each adds sections to README)

### Parallel Opportunities

- **Phase 2**: T002 (config.example.py) and T003 (helpers.py) — different files, no dependencies
- **Cross-phase**: US3 (Phase 6) can start in parallel with US2 (Phase 4) and US4 (Phase 5) since README is independent of code changes

---

## Parallel Example: Phase 2 (Foundational)

```bash
# Launch both foundational tasks together (different files):
Task: "Create config.example.py with placeholder config structure" → load_tests/config.example.py
Task: "Create helpers.py with auth mixin and request helpers"      → load_tests/helpers.py
```

## Parallel Example: Cross-Phase

```bash
# After Phase 3 (US1) is complete, these can run in parallel:
Task: Phase 4 (US2) — Refactor user classes for realistic flows   → load_tests/locustfile.py
Task: Phase 6 (US3) — Create README with scaling documentation    → load_tests/README.md
```

---

## Implementation Strategy

### MVP First (Phase 1-3: US1 Only)

1. Complete Phase 1: Setup (create directory)
2. Complete Phase 2: Foundational (config + helpers)
3. Complete Phase 3: US1 (4 basic user classes)
4. **STOP and VALIDATE**: `locust --headless -u 10 -r 5 --run-time 30s`
5. All 4 profiles execute — MVP delivered

### Incremental Delivery

1. Setup + Foundational → Infrastructure ready
2. Add US1 → Basic test works → **MVP!**
3. Add US2 → Realistic traffic patterns → Production-grade test
4. Add US4 → Config externalization validated → Portable across environments
5. Add US3 → README + scaling docs → Complete documentation
6. Polish → Final validation → Feature complete

### Solo Developer Path (Recommended)

Since all code lives in 5 files with strong sequential dependencies:

1. T001 → T002 + T003 (parallel) → T004-T007 → validate US1
2. T008-T011 → validate US2
3. T012-T013 → validate US4
4. T014-T017 → validate US3
5. T018-T019 → final validation

---

## Notes

- [P] tasks = different files, no dependencies within same phase
- [Story] label maps task to specific user story for traceability
- No test tasks generated — this feature IS the test suite
- All 4 user classes live in `locustfile.py` — within-story tasks are sequential (same file)
- `config.py` is never committed — only `config.example.py` goes to VCS
- FR-011 compliance: zero production code modifications — all files in `load_tests/` directory
