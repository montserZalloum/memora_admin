# Research: Live Challenges

**Feature**: `037-live-challenges` | **Date**: 2026-03-07

## R-001: DocType Patterns for Event + Child Tables

**Decision**: Use Frappe standard DocType with child tables for questions and eligible plans.

**Rationale**: The project consistently uses this pattern (Lesson -> Lesson Stage, Academic Plan -> Plan Subject, Announcement -> Announcement Target Plan). Child tables use `"istable": 1` in JSON schema and `"fieldtype": "Table"` in the parent.

**Alternatives considered**:
- Standalone DocType with Link fields: rejected because questions belong exclusively to one event and should be managed inline (same as Lesson Stages).
- JSON field for questions: rejected because Frappe child tables provide better admin UX (inline editing, reordering via `idx`).

## R-002: State Machine Validation Pattern

**Decision**: Use `VALID_TRANSITIONS` dict with `validate()` hook, matching the Voucher Card pattern.

**Rationale**: `memora_voucher_card.py` implements exactly this pattern with a `VALID_TRANSITIONS` dict and `_validate_status_transition()` method. It handles `is_new()`, `has_value_changed("status")`, and `get_doc_before_save()` correctly.

**Live Challenge transitions**:
- Draft -> Waiting (scheduled job or manual trigger)
- Waiting -> Active (scheduled job when countdown ends)
- Active -> Ended (scheduled job when exam expires, or admin manual end)

**Alternatives considered**:
- Frappe Workflow: rejected because state transitions are time-based (automated), not user-approval-based.

## R-003: WebSocket Architecture for Waiting Room

**Decision**: Reuse existing `ConnectionManager` from `fastapi_app/core/ws_manager.py` with a new event-scoped broadcast pattern.

**Rationale**: The existing WebSocket infrastructure handles per-user connections with multi-device support, async locks, and configurable broadcast concurrency. The notification endpoint (`notifications.py`) demonstrates the auth-before-accept pattern with JWT query parameter.

**Key difference from notifications**: Waiting room needs event-scoped broadcast (all participants of one event), not user-scoped messaging. We'll maintain a dict of `event_id -> set[WebSocket]` in the service layer, separate from the per-user ConnectionManager.

**Alternatives considered**:
- Redis pub/sub only (no WebSocket manager): rejected because we need to track connected count for admin dashboard.
- SSE (Server-Sent Events): rejected because WebSocket provides bidirectional communication and the infrastructure already exists.

## R-004: Redis Key Design for Live Challenges

**Decision**: Add key builders to `redis_keys.py` following the established pattern with docstrings documenting type, producers, consumers, and TTL.

**Keys needed**:

| Key | Type | Purpose | TTL |
|-----|------|---------|-----|
| `memora:lc:{event_id}:status` | STRING | Event state (waiting/active/ended) | 24h (cleanup after event) |
| `memora:lc:{event_id}:questions` | STRING | JSON array of questions with correct answers | 24h |
| `memora:lc:{event_id}:count` | STRING | Atomic participant counter (INCR) | 24h |
| `memora:lc:{event_id}:submitted` | SET | Player IDs who submitted (duplicate prevention) | 24h |
| `memora:lc:{event_id}:meta` | HASH | exam_start_ts, exam_end_ts, capacity, show_correct_answers, etc. | 24h |

All keys use 24h TTL as cleanup safety net. Keys are written when event transitions to Waiting and naturally expire after event ends.

**Alternatives considered**:
- Single HASH for all event data: rejected because questions JSON can be large and should be a separate key for independent access.
- No TTL (protected keys): rejected because live challenge data is ephemeral — MariaDB is source of truth and data doesn't need to survive beyond the event.

## R-005: Submission Batch Queue

**Decision**: Use `asyncio.Queue` with a background consumer task in FastAPI lifespan, flushing via FrappeClient API calls.

**Rationale**: The project uses `asyncio.create_task()` for background work in lifespan (see pubsub listener, notification listener). The queue consumer will flush every 30 seconds or when 50 items accumulate, with a mandatory drain on shutdown.

**Write path**: Submissions go to the in-memory queue after grading (score already returned to student). The consumer batches writes to MariaDB via FrappeClient.

**Alternatives considered**:
- Redis LIST as queue (RPUSH/BLPOP): more durable but adds Redis round-trip on every submission. In-memory queue is acceptable given the 30s data loss window is documented.
- Direct DB write per submission: rejected because 1000 concurrent writes would overwhelm MariaDB.

## R-006: XP Distribution via Wallet Service

**Decision**: Use existing `WalletService.award_xp()` for XP distribution after leaderboard computation.

**Rationale**: `award_xp()` atomically increments XP via `HINCRBY`, refreshes TTL, and marks dirty set for sync to MariaDB. This is exactly what's needed for challenge rewards.

**Flow**: Post-event processing (background task) computes leaderboard, then iterates through ranked participants calling `award_xp()` with the appropriate amount (participation + rank bonus).

## R-007: Scheduled Job for State Transitions

**Decision**: Add a scheduled job running every 30 seconds to check for pending state transitions.

**Rationale**: The project uses cron-based scheduling via `hooks.py` `scheduler_events`. The announcement cleanup pattern (`announcement_cleanup.py`) demonstrates querying by date filters and transitioning states. However, live challenge transitions need higher frequency (30s) than the typical daily/hourly jobs.

**Implementation**: A single task `process_live_challenge_transitions` that:
1. Queries events where `status=Draft AND scheduled_start <= now()` -> transition to Waiting
2. Queries events where `status=Waiting AND scheduled_start + waiting_room_duration <= now()` -> transition to Active
3. Queries events where `status=Active AND exam_end_ts <= now()` -> transition to Ended

**Alternatives considered**:
- APScheduler with precise timers: rejected because it adds a new dependency and doesn't survive process restarts.
- Celery ETA tasks: rejected because Frappe uses its own background job system (RQ), not Celery.

## R-008: Player Identity and Plan Reference

**Decision**: Use `Memora Player Profile` as the student entity (linked via `name` field, format `PLAYER-#####`). Plan eligibility checks use the `plan` field (Link to `Memora Academic Plan`).

**Rationale**: `Memora Player Profile` has `plan` (Link -> Memora Academic Plan) and `mobile` (unique identifier). The `name` field is the primary key used across all existing services (wallet, progress, access).

**Review Item question fields**: `question_text`, `choice_1` through `choice_4`, `correct_choice` (Int, 1-4). When importing to Live Challenge Question, map `choice_N` to `option_a/b/c/d` and `correct_choice` (1-4) to `correct_answer` (A/B/C/D).

## R-009: Announcement Model as Reference for Admin-Facing Events

**Decision**: Follow `Memora Announcement` patterns for admin UI: computed fields, conditional visibility via `depends_on`, and child table for target plans.

**Rationale**: Announcements have `target_plans` child table (Announcement Target Plan) linking to academic plans — identical pattern needed for eligible study plans. They also have computed dates and status fields that inform the live challenge status display.

## R-010: FastAPI Endpoint Registration

**Decision**: Create `fastapi_app/api/v1/endpoints/live_challenge.py` with a dedicated router, registered in `router.py`.

**Rationale**: Every feature has its own endpoint file (wallet.py, sessions.py, progress.py, etc.) with a router included via `router.include_router()`. Dependencies are injected via `Annotated[Service, Depends(factory)]` pattern.

**Auth**: Use `CurrentUser` dependency for student endpoints. Admin dashboard endpoints don't need FastAPI auth (served via Frappe admin panel).
