# Tasks: Live Challenge Waiting Room Reactions (Backend Only)

**Input**: Design documents from `/specs/050-waiting-room-reactions/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/websocket-messages.md, quickstart.md

**Tests**: Included — plan.md constitution check marks "Test-First Coverage: COMPLIANT" and quickstart.md lists dedicated test files.

**Organization**: Tasks grouped by user story (5 stories: 3× P1, 2× P2). Each story is independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US5)
- File paths are relative to repository root

---

## Phase 1: Setup & Foundational Infrastructure

**Purpose**: Configuration and Redis key infrastructure required by all user stories

- [x] T001 Add 7 reaction configuration settings (`reaction_flush_interval_ms`, `reaction_sustained_rate`, `reaction_burst_allowance`, `reaction_room_cap_per_sec`, `reaction_rl_ttl_sec`, `reaction_counter_ttl_sec`, `reaction_enabled`) with defaults to `Settings` class in fastapi_app/core/config.py
- [x] T002 [P] Add `lc_reaction_rl_key(event_id, player_id)` key builder function and `REACTION_RL_TTL` constant to fastapi_app/core/redis_keys.py

**Checkpoint**: Configuration and key infrastructure ready — user story implementation can begin

---

## Phase 2: User Story 1 — Tap a Reaction in Waiting Room (Priority: P1) 🎯 MVP

**Goal**: Accept reaction taps (heart/fire/clap) over WebSocket, aggregate into 300ms windows, broadcast anonymous burst messages to all room participants

**Independent Test**: Send `waiting_room_reaction_tap` over WS while room is in `waiting` state → receive `waiting_room_reaction_burst` within flush window with correct counts and intensity tiers

### Tests for User Story 1

> **Write these tests FIRST, ensure they FAIL before implementation**

- [x] T003 [P] [US1] Write unit tests for ReactionEngine: tap acceptance (valid/invalid reaction types), counter aggregation, flush-and-reset, burst message structure (counts, intensity tiers, server_ts, room_id), empty window suppression, and `reaction_enabled` feature flag bypass in fastapi_app/tests/test_waiting_room_reactions.py

### Implementation for User Story 1

- [x] T004 [US1] Create `ReactionEngine` class with `RoomReactionState` dataclass, `VALID_REACTIONS` set, intensity tier thresholds, `accept_tap(event_id, player_id, reaction)` method (validation + counter increment), async flush loop (300ms interval, snapshot-and-reset counters, build burst message, call broadcast callback), `_compute_intensity(count)` helper, and `reaction_enabled` guard in fastapi_app/services/waiting_room_reactions.py
- [x] T005 [US1] Wire `ReactionEngine` into `LiveChallengeService.__init__()`: instantiate engine with Redis connection and `_broadcast_json` callback; add `handle_reaction_tap(event_id, player_id, msg)` method that validates room status == `"waiting"` and delegates to engine; start flush loop on first tap in fastapi_app/services/live_challenge.py
- [x] T006 [US1] Add JSON message parsing to WS receive loop: parse `receive_text()`, check `msg.get("type") == "waiting_room_reaction_tap"`, call `service.handle_reaction_tap(event_id, user_id, msg)`, silently drop malformed JSON and unknown types in fastapi_app/api/v1/endpoints/live_challenge.py
- [x] T007 [US1] Write integration test for full WS flow: connect client, send tap, verify burst broadcast with correct schema (`type`, `room_id`, `reactions.{type}.count`, `reactions.{type}.intensity`, `degraded`, `window_duration_ms`, `server_ts`), verify empty windows suppressed, verify invalid reaction types silently dropped in fastapi_app/tests/test_waiting_room_reactions_ws.py

**Checkpoint**: Core reaction loop works — taps accepted, aggregated, broadcast. MVP deliverable.

---

## Phase 3: User Story 2 — Per-User Rate Limiting (Priority: P1)

**Goal**: Enforce token bucket rate limiting (3 taps/sec sustained, 6-tap burst allowance) per user per room via Redis Lua script — excess taps silently dropped, no disconnect

**Independent Test**: Send 10 taps in 1 second → at most 3 counted in burst broadcasts; send 6 taps in under 2 seconds → all 6 accepted; no disconnect or error events

### Tests for User Story 2

- [x] T008 [P] [US2] Write unit tests for token bucket rate limiting: burst allowance (6 taps accepted), sustained limit (3/sec), token refill after pause, excess taps return rejected, no error/disconnect side effects in fastapi_app/tests/test_waiting_room_reactions.py

### Implementation for User Story 2

- [x] T009 [US2] Implement `_RATE_LIMIT_LUA` script (token bucket: HMGET tokens/last_ms, refill based on elapsed time, consume or reject) and `check_rate_limit(event_id, player_id)` async method using `redis.evalsha()` with config-driven parameters in fastapi_app/services/waiting_room_reactions.py
- [x] T010 [US2] Integrate rate limit check into `accept_tap()` — call `check_rate_limit()` before counter increment, silently drop on rejection in fastapi_app/services/waiting_room_reactions.py
- [x] T011 [US2] Write integration test with real Redis: rapid-fire taps exceed limit → verify only allowed count appears in burst, verify no WS disconnect or error message sent in fastapi_app/tests/test_waiting_room_reactions_ws.py

**Checkpoint**: Rate limiting enforced — abusive clients silently throttled without disruption

---

## Phase 4: User Story 4 — Immediate Cutoff on Room Transition (Priority: P1)

**Goal**: When room transitions from `waiting` to `active`/`ended`, immediately stop flush loop, clear counters, reject new taps, and let Redis keys auto-expire (5s TTL)

**Independent Test**: Transition room to `active` while taps are flowing → no burst messages after transition, new taps silently rejected, counters cleared

### Tests for User Story 4

- [x] T012 [P] [US4] Write unit tests for `stop_room()`: flush task cancelled, counters cleared, subsequent `accept_tap()` returns rejected, no burst emitted after stop in fastapi_app/tests/test_waiting_room_reactions.py

### Implementation for User Story 4

- [x] T013 [US4] Implement `stop_room(event_id)` in `ReactionEngine` — cancel flush task, delete `RoomReactionState` entry, ensure `accept_tap()` short-circuits for stopped/unknown rooms in fastapi_app/services/waiting_room_reactions.py
- [x] T014 [US4] Wire `engine.stop_room(event_id)` call into `LiveChallengeService` room transition handler where status changes from `waiting` to `active` or `ended` in fastapi_app/services/live_challenge.py
- [x] T015 [US4] Write integration test: start taps, transition room, verify no burst after transition, verify new taps silently dropped, verify Redis rate limit keys expire within TTL in fastapi_app/tests/test_waiting_room_reactions_ws.py

**Checkpoint**: All P1 stories complete — core reactions, rate limiting, and transition cutoff all working

---

## Phase 5: User Story 3 — Room-Level Degradation Under Load (Priority: P2)

**Goal**: When room-wide tap volume exceeds 250/sec cap, silently drop excess taps, set `degraded: true` in burst messages, maintain stable broadcast cadence

**Independent Test**: Simulate 500 taps/sec from many users → burst messages continue at flush interval with `degraded: true` and capped counts; reduce volume → `degraded` returns to `false`

### Tests for User Story 3

- [x] T016 [P] [US3] Write unit tests for room-level cap: per-second counter reset, taps dropped above cap, `degraded` flag set in burst message, degradation clears when volume drops in fastapi_app/tests/test_waiting_room_reactions.py

### Implementation for User Story 3

- [x] T017 [US3] Add `room_tap_count` and `room_tap_second` fields to `RoomReactionState`; implement per-second cap check in `accept_tap()` — increment counter, drop taps when `room_tap_count >= room_cap_per_sec`, reset counter on new second in fastapi_app/services/waiting_room_reactions.py
- [x] T018 [US3] Add `degraded` flag computation to burst message builder — set `true` when room cap was hit during the window, reflect capped counts in burst payload in fastapi_app/services/waiting_room_reactions.py
- [x] T019 [US3] Write integration test: high-volume taps from multiple simulated users → verify burst has `degraded: true` and capped counts, reduce volume → verify `degraded: false` returns in fastapi_app/tests/test_waiting_room_reactions_ws.py

**Checkpoint**: Graceful degradation under load — production stability ensured

---

## Phase 6: User Story 5 — Resilience to Backend Failure (Priority: P2)

**Goal**: All reaction processing wrapped in error isolation — Redis failure = fail-open (accept tap, skip rate limit), engine error = silently drop tap. No reaction failure propagates to countdown, transitions, or exam flow.

**Independent Test**: Simulate Redis unavailability → room transitions still work, taps silently dropped, no client errors; restore Redis → reactions resume automatically

### Tests for User Story 5

- [x] T020 [P] [US5] Write unit tests for error isolation: Redis connection error in rate limit → tap accepted (fail-open), engine error in accept_tap → silently dropped, flush loop error → restarts on next cycle in fastapi_app/tests/test_waiting_room_reactions.py

### Implementation for User Story 5

- [x] T021 [US5] Wrap `check_rate_limit()` in try/except — on `RedisError` log warning and return allowed (fail-open); wrap `accept_tap()` outer boundary in try/except — log and silently drop; wrap flush loop body in try/except — log and continue loop in fastapi_app/services/waiting_room_reactions.py
- [x] T022 [US5] Wrap `handle_reaction_tap()` call in `LiveChallengeService` with try/except to isolate reaction errors from WS connection and countdown logic in fastapi_app/services/live_challenge.py
- [x] T023 [US5] Write integration test: patch Redis to raise `ConnectionError`, send taps → verify no WS errors, transition room → verify countdown and exam start succeed normally in fastapi_app/tests/test_waiting_room_reactions_ws.py

**Checkpoint**: Reactions are fully non-critical — any failure degrades gracefully to no-op

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Logging, observability, and validation across all stories

- [x] T024 [P] Add structlog logging for reaction processing: tap accepted/dropped (debug), rate limit hit (debug), room cap hit (info), flush loop emit (debug), stop_room (info), errors (warning/error) in fastapi_app/services/waiting_room_reactions.py
- [x] T025 Run quickstart.md validation — manual WS test of full tap → burst flow with all stories active

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **US1 (Phase 2)**: Depends on Phase 1 — BLOCKS all other stories (engine doesn't exist yet)
- **US2 (Phase 3)**: Depends on Phase 2 (needs accept_tap to integrate rate limiting into)
- **US4 (Phase 4)**: Depends on Phase 2 (needs engine + flush loop to stop)
- **US3 (Phase 5)**: Depends on Phase 2 (needs accept_tap + burst builder to add cap logic)
- **US5 (Phase 6)**: Depends on Phases 2–5 (wraps error handling around all existing logic)
- **Polish (Phase 7)**: Depends on all stories complete

### User Story Dependencies

- **US1 (P1)**: Foundation — must complete first (creates ReactionEngine)
- **US2 (P1)**: Depends on US1 only — adds rate limiting to existing accept_tap()
- **US4 (P1)**: Depends on US1 only — adds stop_room() to existing engine
- **US3 (P2)**: Depends on US1 only — adds room cap to existing accept_tap() and burst builder
- **US5 (P2)**: Depends on US1–US4 — wraps error handling around all paths

Note: US2, US4, and US3 can run **in parallel** after US1 completes (they modify different methods/sections of the same file with minimal overlap).

### Within Each User Story

1. Tests written FIRST (FAIL before implementation)
2. Core logic implementation
3. Integration/wiring into LiveChallengeService and WS endpoint
4. Integration tests (verify end-to-end)

### Parallel Opportunities

- **Phase 1**: T001 and T002 can run in parallel (different files)
- **Phase 2**: T003 in parallel with nothing (write tests first)
- **After US1**: US2 (T008–T011), US4 (T012–T015), and US3 (T016–T019) can run in parallel
- **Phase 7**: T024 in parallel with any remaining work

---

## Parallel Example: After US1 Completes

```bash
# These three story phases can start simultaneously (different concerns, minimal file overlap):
# Developer A: US2 — Rate Limiting (T008–T011)
# Developer B: US4 — Room Transition Cutoff (T012–T015)
# Developer C: US3 — Room-Level Degradation (T016–T019)
# Then: US5 wraps all of the above with error handling
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (T001–T002)
2. Complete Phase 2: US1 — Core Tap → Aggregate → Broadcast (T003–T007)
3. **STOP and VALIDATE**: Run unit + integration tests, manual WS test
4. Deploy/demo core reaction feature

### Incremental Delivery

1. Setup → **Foundation ready**
2. US1 → Test → Deploy **(MVP! Reactions work)**
3. US2 → Test → Deploy **(Rate limiting active — safe for production)**
4. US4 → Test → Deploy **(Transition cutoff — exam integrity guaranteed)**
5. US3 → Test → Deploy **(Degradation — production stability at scale)**
6. US5 → Test → Deploy **(Full resilience — reactions can never break core flow)**
7. Polish → Validate → **Feature complete**

### File Touch Map

| File | Stories | Tasks |
|------|---------|-------|
| `fastapi_app/core/config.py` | Setup | T001 |
| `fastapi_app/core/redis_keys.py` | Setup | T002 |
| `fastapi_app/services/waiting_room_reactions.py` | US1–US5, Polish | T004, T009, T010, T013, T017, T018, T021, T024 |
| `fastapi_app/services/live_challenge.py` | US1, US4, US5 | T005, T014, T022 |
| `fastapi_app/api/v1/endpoints/live_challenge.py` | US1 | T006 |
| `fastapi_app/tests/test_waiting_room_reactions.py` | US1–US5 | T003, T008, T012, T016, T020 |
| `fastapi_app/tests/test_waiting_room_reactions_ws.py` | US1–US5 | T007, T011, T015, T019, T023 |

---

## Notes

- All reaction data is ephemeral — zero MariaDB writes (FR-004)
- Burst messages are anonymous — zero user-identifying fields (FR-003)
- `reaction_enabled` config flag is the global kill-switch (checked in T004/T006)
- Redis Lua script provides atomic token bucket — plan.md has the full script
- Intensity tiers: low (1–10), medium (11–50), high (51+) — configurable thresholds
- Room cap: 250 taps/sec default — in-memory per-second counter, not Redis
