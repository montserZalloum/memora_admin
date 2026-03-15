# Tasks: Practice Arena V2

**Input**: Design documents from `/specs/049-practice-arena-v2/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Not included (not explicitly requested). Test files listed in plan.md can be added later.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **FastAPI sidecar**: `fastapi_app/` (gameplay endpoints, services, models)
- **Frappe admin**: `memora_admin/memora_admin/` (DB setup, content pipeline, scheduler tasks)
- **Shared**: `fastapi_app/core/` (Redis key patterns)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create V2 file structure and shared foundational types

- [x] T001 Create V2 file structure per plan.md — create directories `fastapi_app/api/v2/endpoints/`, `fastapi_app/services/`, `fastapi_app/models/`, `memora_admin/memora_admin/services/build/`, `memora_admin/memora_admin/tasks/`, `memora_admin/memora_admin/events/`, `memora_admin/memora_admin/api/` with `__init__.py` files as needed
- [x] T002 [P] Define V2 Redis key patterns in fastapi_app/core/redis_keys.py — add constants for `memora:practice:summary:{player_id}:{track_id}`, `memora:practice:v2:session:{player_id}`, `memora:practice:rate:{player_id}:sessions`, `memora:practice:write_queue`, `memora:practice:write_queue:dead`, `memora:practice:map_invalidation` per data-model.md section 5
- [x] T003 [P] Create Pydantic request/response models in fastapi_app/models/practice_v2.py — define StartSessionRequest (subject_id, track_ids, unit_ids?, topic_ids?), BatchResponse (session_active, batch_seq, question_ids, chunk_refs, total_available, all_seen_warning), SubmitRequest (batch_seq, results[]), ResultItem (item_id, is_correct), SubmitResponse (accepted, batch_seq, correct_count, total_count, accuracy_percent, is_duplicate), ContinueRequest (batch_seq), SessionStatusResponse (session_active, subject_id, track_ids, batch_seq, submitted, question_ids, chunk_refs), ErrorResponse (detail) per contracts/practice-v2.yaml schemas

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**Warning**: No user story work can begin until this phase is complete

- [x] T004 [P] Add `tabPlayer Practice Summary` table creation to memora_admin/memora_admin/setup.py — CREATE TABLE IF NOT EXISTS with composite PK `(player_id, track_id)`, columns: player_id VARCHAR(140), track_id VARCHAR(140), subject_id VARCHAR(140), question_history LONGTEXT DEFAULT '{}', total_seen INT UNSIGNED DEFAULT 0, total_correct INT UNSIGNED DEFAULT 0, last_session_at DATETIME NULL, updated_at DATETIME with ON UPDATE CURRENT_TIMESTAMP; indexes: idx_player_subject (player_id, subject_id), idx_updated_at (updated_at) per data-model.md section 1
- [x] T005 [P] Implement practice map file loader with in-process cache and Redis pubsub invalidation in fastapi_app/services/practice_map.py — load map JSON from local storage path `practice/maps/{subject_id}.json`, cache parsed dict in process-level dict keyed by subject_id with 1h TTL safety net, subscribe to Redis pubsub channel `memora:practice:map_invalidation` to evict cache entries on content changes, expose `get_map(subject_id)` that returns the parsed map data structure per data-model.md section 3 schema and research.md R2
- [x] T006 [P] Register V2 practice API router and create endpoint stubs in fastapi_app/api/v2/endpoints/practice.py — create APIRouter with prefix `/api/v2/practice`, add stub handlers for POST /start, POST /submit, POST /continue, GET /session that return 501 Not Implemented; wire router into FastAPI app
- [x] T007 [P] Implement Redis Streams consumer group initialization in fastapi_app/services/practice_writer.py — create `ensure_consumer_group()` function that runs XGROUP CREATE memora:practice:write_queue practice-writers 0 MKSTREAM (idempotent — catch BUSYGROUP error), call on FastAPI startup per research.md R1

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Start a Practice Session Instantly (Priority: P1) MVP

**Goal**: A student selects a subject and tracks, receives their first batch of 20 prioritized questions within 200ms. Prior history is reflected immediately via cached Player Summary.

**Independent Test**: Start a session for a player with known history and verify returned question IDs match expected priority order (unseen > last incorrect > lowest correct ratio > oldest seen), are drawn from the correct scope, and include correct chunk references.

### Implementation for User Story 1

- [x] T008 [US1] Implement player summary cache service in fastapi_app/services/practice_v2.py — `get_player_summary(redis, player_id, track_id, db_conn)`: read from Redis key `memora:practice:summary:{player_id}:{track_id}` (JSON string of question_history); on cache miss, SELECT question_history FROM `tabPlayer Practice Summary` WHERE player_id=%s AND track_id=%s, populate Redis with 7200s TTL; return dict of `{item_id: {lr, ac, cc, ls}}` or empty dict for new players. Include `set_player_summary(redis, player_id, track_id, history)` to write back to cache.
- [x] T009 [US1] Implement question selection algorithm in fastapi_app/services/practice_v2.py — `select_questions(map_data, track_ids, unit_ids, topic_ids, player_history, served_ids, batch_size=20)`: (1) extract all question IDs from map_data matching the scope filter (tracks, optionally units/topics per FR-012), (2) exclude served_ids (in-session repeat avoidance per FR-015), (3) classify each remaining question using player_history: unseen (not in history), incorrect (lr="I"), seen-correct (lr="C") with correct_ratio = cc/ac and last_seen = ls, (4) sort by priority: unseen first, then incorrect, then lowest correct_ratio, then oldest ls (per FR-013), (5) return top batch_size question IDs + their chunk refs from map_data, (6) set all_seen_warning=true if all in-scope questions have been served at least once and wrapping around
- [x] T010 [US1] Implement session creation with Redis hash in fastapi_app/services/practice_v2.py — `create_session(redis, player_id, subject_id, track_ids, scope_hash, question_ids, chunk_refs)`: generate UUID session_id, delete any existing session key `memora:practice:v2:session:{player_id}` (session replacement per FR-008), HSET new session hash with fields: session_id, subject_id, track_ids (JSON), scope_hash, batch_seq=0, current_batch (JSON array of question IDs), submitted="0", batch_stats="", served_ids (JSON array = current_batch), created_at, last_activity_at; set TTL 3600s per data-model.md section 5.2
- [x] T011 [US1] Wire POST /api/v2/practice/start endpoint in fastapi_app/api/v2/endpoints/practice.py — accept StartSessionRequest, validate scope (if len(track_ids)>1: unit_ids and topic_ids must be null; if unit_ids and len(unit_ids)>1: topic_ids must be null per FR-035/FR-036), validate all IDs exist in map file, load map via practice_map.get_map(subject_id), load player summary per track via T008, call select_questions (T009), call create_session (T010), return BatchResponse with session_active=true, batch_seq=0, question_ids, chunk_refs (deduplicated), total_available (count of in-scope questions in map), all_seen_warning

**Checkpoint**: At this point, a player can start a practice session and receive a personalized batch of 20 questions. The core read path (CDN map + cached history + selection) is validated end-to-end.

---

## Phase 4: User Story 2 — Submit Answers and See Results Immediately (Priority: P1)

**Goal**: A student submits answers for their current batch and receives accuracy stats within 100ms. The database write happens asynchronously via the write queue.

**Independent Test**: Submit a batch of results and verify the response contains correct stats, the player's cached summary is updated in Redis, and a write message is enqueued to the Redis Stream.

### Implementation for User Story 2

- [x] T012 [US2] Implement submission validation and stats computation in fastapi_app/services/practice_v2.py — `validate_submission(redis, player_id, batch_seq, results)`: read session hash, verify batch_seq matches session.batch_seq (else 400), verify all item_ids exist in session.current_batch (else 400 "item_id not in current batch"), verify no duplicate item_ids in payload (else 400), verify len(results)==len(current_batch); `compute_stats(results)`: return correct_count, total_count, accuracy_percent (correct/total * 100.0)
- [x] T013 [US2] Implement submit flow in fastapi_app/services/practice_v2.py — `submit_results(redis, player_id, batch_seq, results, db_conn)`: (1) check if session.submitted=="1" and batch_seq matches — if so, return cached batch_stats with is_duplicate=true (FR-019), (2) compute stats via T012, (3) update player summary cache — for each result, merge into history dict: set lr="C"/"I", increment ac, conditionally increment cc, set ls=now (FR-020), write back to Redis via set_player_summary, (4) XADD to memora:practice:write_queue MAXLEN ~100000 with fields: player_id, track_id (from session), subject_id, submitted_at (ISO now), batch_seq, session_id, results JSON (FR-021), (5) HSET session: submitted="1", batch_stats=JSON stats, (6) return SubmitResponse
- [x] T014 [US2] Wire POST /api/v2/practice/submit endpoint in fastapi_app/api/v2/endpoints/practice.py — accept SubmitRequest, read session (404 if none), call validate_submission, call submit_results, return SubmitResponse with accepted=true, batch_seq, correct_count, total_count, accuracy_percent, is_duplicate

**Checkpoint**: Players can now start sessions AND submit answers. The core gameplay loop (minus continue) is functional. Results are queued for background persistence.

---

## Phase 5: User Story 3 — Continue to Next Batch Without Repeats (Priority: P1)

**Goal**: After submitting, a student requests the next batch of 20 new questions with no repeats within the session, reflecting their updated history.

**Independent Test**: Submit batch 0, request continue, verify batch 1 contains different question IDs that reflect updated priority ordering.

### Implementation for User Story 3

- [x] T015 [US3] Implement continue logic in fastapi_app/services/practice_v2.py — `continue_session(redis, player_id, db_conn)`: (1) read session hash, verify submitted=="1" (else 400 "current batch not submitted" per FR-023), (2) load updated player summary from cache (reflects just-submitted answers per FR-024), (3) load map data, (4) call select_questions with served_ids from session (ensures no repeats per FR-015), (5) increment batch_seq, (6) HSET session: batch_seq=new, current_batch=new question IDs JSON, submitted="0", batch_stats="", append new question IDs to served_ids JSON, (7) return BatchResponse with new batch
- [x] T016 [US3] Wire POST /api/v2/practice/continue endpoint in fastapi_app/api/v2/endpoints/practice.py — accept ContinueRequest, read session (404 if none), validate batch_seq matches session.batch_seq, call continue_session, return BatchResponse

**Checkpoint**: The full gameplay loop (start -> submit -> continue -> submit -> ...) is now complete. This is the functional MVP.

---

## Phase 6: User Story 4 — Content Updates Propagate Quickly (Priority: P2)

**Goal**: When a content editor publishes, edits, or deletes a question, the change is reflected in new sessions within 60 seconds. Only affected chunks are regenerated.

**Independent Test**: Modify a question, wait up to 60 seconds, verify the map file and content chunks reflect the change. Verify only the affected chunk was regenerated.

### Implementation for User Story 4

- [x] T017 [US4] Implement practice content generator in memora_admin/memora_admin/services/build/practice_content.py — `generate_practice_content(subject_id)`: (1) query all Review Items for the subject via content hierarchy (Subject > Track > Unit > Topic), (2) build map file JSON per data-model.md section 3 schema (tracks > units > topics > questions with chunk refs), (3) generate content chunks per R4 algorithm: iterate tracks/units/topics sorted by sort_order, group ~100 questions per chunk respecting topic boundaries, write chunk JSON per data-model.md section 4 schema, (4) write map file to `practice/maps/{subject_id}.json` and chunks to `practice/chunks/{subject_id}/chunk_{N}.json` using existing StorageBackend abstraction, (5) expose `generate_all_practice_content()` for bulk generation
- [x] T018 [US4] Implement selective chunk regeneration and CDN invalidation in memora_admin/memora_admin/services/build/practice_content.py — `regenerate_for_item(item_id)`: (1) identify the topic of the changed Review Item, (2) find which chunk(s) contain questions from that topic, (3) regenerate only those chunks + the map file, (4) upload via StorageBackend atomic swap (temp > swap > cleanup per existing publisher.py pattern), (5) invalidate CDN cache for affected chunk files + map file via existing CloudflarePurgeService, (6) publish invalidation message to Redis pubsub channel `memora:practice:map_invalidation` with subject_id so FastAPI workers evict their in-process map cache
- [x] T019 [P] [US4] Implement content change event hooks in memora_admin/memora_admin/events/practice_content_trigger.py — create handlers for Review Item on_update, after_insert, on_trash doc_events that call `regenerate_for_item(item_id)` with Redis debounce (SET NX EX pattern per R8 shared infrastructure, 10s debounce window to batch rapid edits)
- [x] T020 [US4] Register content trigger hooks in memora_admin hooks.py — add doc_events entry for "Memora Review Item" mapping on_update, after_insert, on_trash to practice_content_trigger handlers

**Checkpoint**: Content pipeline is operational. Changes to Review Items propagate to CDN content and FastAPI map caches within 60 seconds.

---

## Phase 7: User Story 5 — Session Lifecycle Management (Priority: P2)

**Goal**: Sessions auto-expire after 1h inactivity. Only one active session per player. Rate-limited to 5 sessions/hour. Players can query session status.

**Independent Test**: Create sessions and verify TTL expiry, replacement behavior, rate limit rejection at 6th session, and session status response.

### Implementation for User Story 5

- [x] T021 [US5] Implement rate limiting for session creation in fastapi_app/services/practice_v2.py — `check_rate_limit(redis, player_id)`: INCR key `memora:practice:rate:{player_id}:sessions`, if first increment (result==1) set TTL 3600s, if counter > 5 raise 429 with Retry-After header (TTL of rate key per FR-010); integrate into start endpoint flow before session creation
- [x] T022 [US5] Implement session TTL refresh on submit and continue in fastapi_app/services/practice_v2.py — after successful submit (T013) and continue (T015), call EXPIRE on session key `memora:practice:v2:session:{player_id}` with 3600s to refresh inactivity timer; also update last_activity_at field in session hash
- [x] T023 [US5] Wire GET /api/v2/practice/session status endpoint in fastapi_app/api/v2/endpoints/practice.py — read session hash for authenticated player, return SessionStatusResponse (session_active, subject_id, track_ids, batch_seq, submitted, question_ids from current_batch, chunk_refs) or 404 if no active session

**Checkpoint**: Session lifecycle is fully managed. Rate limiting prevents abuse, TTL prevents resource leaks, status endpoint enables client reconnection.

---

## Phase 8: User Story 6 — Player History Persists Across Sessions (Priority: P2)

**Goal**: A player's practice history persists across sessions. Starting a new session the next day reflects all prior practice activity. Existing V1 players have their history available on V2 launch.

**Independent Test**: Have a player complete a session (US1+US2), ensure the background writer processes results (US7), start a new session, and verify question selection reflects prior history. Run backfill on existing data and verify row counts match.

**Note**: Most of US6 is satisfied by US1 (summary cache hydration from DB) + US7 (background writer persists to DB). The unique deliverable is the one-time backfill for existing players.

### Implementation for User Story 6

- [x] T024 [US6] Implement one-time backfill script in memora_admin/memora_admin/api/practice_summary.py — `backfill_player_summaries(batch_size=1000)`: (1) get distinct player_ids from tabMemora Practice Log, (2) for each batch of players: JOIN Practice Log with Review Item to resolve track_id per item, GROUP BY (player_id, track_id), (3) build question_history JSON per item (lr from last_result, ac from attempt_count, cc from correct_count, ls from last_seen_at), compute total_seen/total_correct, (4) UPSERT into tabPlayer Practice Summary with ON DUPLICATE KEY UPDATE, (5) log progress (players processed / total); function must be idempotent (safe to re-run per R9)

**Checkpoint**: Existing V1 players will see their full practice history when V2 launches. Combined with US1 (cache hydration) and US7 (ongoing persistence), long-term history continuity is guaranteed.

---

## Phase 9: User Story 7 — Background Write Worker Processes Results (Priority: P2)

**Goal**: Results submitted by players are persisted to the database by a background worker. Idempotent processing, retry with backoff, dead-letter for unprocessable messages.

**Independent Test**: Enqueue write messages to the Redis Stream, run the worker, verify Practice Log rows are upserted and Player Summary JSON is updated. Enqueue the same message twice and verify no double-counting.

### Implementation for User Story 7

- [x] T025 [US7] Implement write worker core in fastapi_app/services/practice_writer.py — `process_write_queue(redis, db_conn)`: (1) XREADGROUP GROUP practice-writers writer-{instance_id} COUNT 10 BLOCK 5000 STREAMS memora:practice:write_queue >, (2) for each message: parse results JSON, (3) for each result: execute idempotent Practice Log UPSERT per contracts/write-queue.md — INSERT INTO tabMemora Practice Log ... ON DUPLICATE KEY UPDATE with IF(VALUES(last_seen_at) > last_seen_at, ...) timestamp guard to prevent double-counting, (4) read current tabPlayer Practice Summary row for (player_id, track_id), merge results into question_history JSON with same timestamp guard (skip if existing ls >= submitted_at), update total_seen/total_correct/last_session_at, write back, (5) XACK on success
- [x] T026 [US7] Implement retry, backoff, dead-letter, and stale message reclaim in fastapi_app/services/practice_writer.py — (1) wrap message processing in try/except, on failure: log error with message_id, do NOT XACK (message stays in PEL), (2) add `reclaim_stale_messages(redis)`: XAUTOCLAIM with 60s visibility timeout to pick up messages from crashed consumers, (3) check delivery count via XPENDING for each reclaimed message: if delivery_count >= 5, XADD to dead-letter stream memora:practice:write_queue:dead with original_id, error, delivery_count, all original fields, then XACK original, (4) implement exponential backoff between retries (base 2s, max 32s per FR-033)
- [x] T027 [P] [US7] Create Frappe scheduler task wrapper in memora_admin/memora_admin/tasks/practice_writer.py — `process_write_queue()`: create Redis and DB connections, call fastapi_app worker's process_write_queue + reclaim_stale_messages, handle connection cleanup; register in memora_admin hooks.py scheduler_events under cron "* * * * *" (every minute per quickstart.md section 5)

**Checkpoint**: Background persistence pipeline is complete. Results flow from Redis Stream to Practice Log + Player Summary tables reliably with idempotent, retryable processing.

---

## Phase 10: User Story 8 — Operational Observability (Priority: P3)

**Goal**: Operations engineers can monitor write queue depth, worker health, and cache hit/miss ratios. Admin utilities for emergency operations.

**Independent Test**: Verify metrics/logs are emitted during normal operations. Verify admin utilities (force-expire, dead-letter reprocess) function correctly.

### Implementation for User Story 8

- [x] T028 [P] [US8] Add observability logging to practice services — in fastapi_app/services/practice_writer.py: log queue depth (XLEN), pending count (XPENDING summary), dead-letter count (XLEN dead stream), messages processed per cycle, processing errors; in fastapi_app/services/practice_v2.py: log cache hits/misses for player summary (hit when Redis has data, miss when DB fallback), session creation count, selection time; use structured logging with practice_v2 logger
- [x] T029 [P] [US8] Implement admin utilities in memora_admin/memora_admin/api/practice_summary.py — `force_expire_all_practice_sessions()`: SCAN with pattern `memora:practice:v2:session:*`, delete in batches of 100, log count expired (per R10); `reprocess_dead_letters(redis, db_conn)`: XRANGE dead-letter stream, for each message: re-XADD to main write queue with original fields (minus error/delivery_count metadata), XDEL from dead-letter stream, log count reprocessed
- [x] T030 [US8] Implement scheduled session cleanup task in memora_admin/memora_admin/tasks/practice_writer.py — add `cleanup_orphaned_sessions()`: SCAN for session keys, check TTL, delete any with TTL <= 0 or missing TTL (safety net per R6); register in hooks.py scheduler_events under cron "0 * * * *" (hourly)

**Checkpoint**: Operations team has full visibility into the practice system. Emergency admin tools are available.

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and end-to-end validation

- [x] T031 [P] Update practice arena flow documentation in docs/documentation/06-practice-arena-flow/ascii.md — add V2 architecture diagram showing CDN content flow, FastAPI endpoints, Redis state, write queue, background worker, and DB tables
- [x] T032 Run quickstart.md validation end-to-end — execute every command in specs/049-practice-arena-v2/quickstart.md sections 1-9 against a development environment, verify all commands succeed and outputs match expected results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (T001 creates directories) — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — core entry point, must complete first
- **US2 (Phase 4)**: Depends on US1 (session must exist to submit)
- **US3 (Phase 5)**: Depends on US2 (batch must be submitted to continue)
- **US4 (Phase 6)**: Depends on Phase 2 only — can run in parallel with US1-US3 (different codepath: Frappe content pipeline)
- **US5 (Phase 7)**: Depends on US1 (adds lifecycle controls to existing session/start flow)
- **US6 (Phase 8)**: Depends on Phase 2 only — backfill script is standalone
- **US7 (Phase 9)**: Depends on Phase 2 — write worker is standalone consumer
- **US8 (Phase 10)**: Depends on US7 (monitors the write worker) and US1 (monitors cache)
- **Polish (Phase 11)**: Depends on all desired user stories being complete

### User Story Dependencies

```
Phase 1 (Setup)
    │
    ▼
Phase 2 (Foundational)
    │
    ├──────────────────────────────────────────────┐
    │                                              │
    ▼                                              ▼
US1 (Start) ──► US2 (Submit) ──► US3 (Continue)   US4 (Content) [parallel]
    │               │                              US6 (Backfill) [parallel]
    │               │                              US7 (Writer)   [parallel]
    ▼               ▼
US5 (Lifecycle)  US8 (Observability)
                    │
                    ▼
              Phase 11 (Polish)
```

### Within Each User Story

- Models/schemas before services
- Services before endpoints
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

**Phase 2** (all 4 tasks are [P] — different files):
```
T004 (DB table) | T005 (Map loader) | T006 (API router) | T007 (Stream init)
```

**Cross-story parallelism** (after Phase 2):
```
US1-US2-US3 (sequential — gameplay loop)  |  US4 (content pipeline)
                                          |  US6 (backfill script)
                                          |  US7 (write worker)
```

**Phase 10** (T028 and T029 are [P] — different files):
```
T028 (observability logging) | T029 (admin utilities)
```

---

## Parallel Example: Phase 2 (Foundational)

```bash
# All four foundational tasks can run in parallel (different files, no dependencies):
Task T004: "Add tabPlayer Practice Summary table creation to memora_admin/memora_admin/setup.py"
Task T005: "Implement practice map file loader in fastapi_app/services/practice_map.py"
Task T006: "Register V2 API router in fastapi_app/api/v2/endpoints/practice.py"
Task T007: "Implement Redis Streams consumer group init in fastapi_app/services/practice_writer.py"
```

## Parallel Example: Cross-Story (after Foundational)

```bash
# These story tracks can run in parallel (independent codepaths):
Track A: US1 → US2 → US3 (gameplay loop — FastAPI endpoints + services)
Track B: US4 (content pipeline — Frappe services + hooks)
Track C: US6 (backfill — Frappe admin script)
Track D: US7 (write worker — FastAPI service + Frappe scheduler)
```

---

## Implementation Strategy

### MVP First (User Stories 1-3 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: Foundational (T004-T007) — CRITICAL, blocks all stories
3. Complete Phase 3: User Story 1 — Start Session (T008-T011)
4. **STOP and VALIDATE**: Test session start independently — verify question selection, priority order, cache hydration
5. Complete Phase 4: User Story 2 — Submit (T012-T014)
6. Complete Phase 5: User Story 3 — Continue (T015-T016)
7. **STOP and VALIDATE**: Test full gameplay loop (start → submit → continue → submit → ...)
8. Deploy/demo if ready — MVP is functional!

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US1 + US2 + US3 → Core gameplay loop (MVP)
3. US7 → Background persistence (data durability)
4. US4 → Content pipeline (editor workflow)
5. US5 → Session lifecycle (abuse prevention)
6. US6 → Backfill (V1 migration)
7. US8 → Observability (operational readiness)
8. Polish → Documentation + validation

### Parallel Team Strategy

With multiple developers after Phase 2:

- **Developer A**: US1 → US2 → US3 → US5 (gameplay loop + lifecycle)
- **Developer B**: US4 → US6 (content pipeline + backfill — Frappe side)
- **Developer C**: US7 → US8 (write worker + observability)

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Zero DB queries during gameplay (warm cache) is validated by US1
- Idempotency is validated by US7 (timestamp guard on UPSERT)
- Existing `tabMemora Practice Log` schema is NEVER modified (constraint C-001)
