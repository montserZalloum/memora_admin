# Live Challenge — Flow Hierarchy

```
Live Challenge System
├── 1. EVENT MODES
│   ├── Exam (default): all questions at once, timed, bulk submit
│   └── Last Stand: round-by-round elimination, hearts system, server-driven
│
├── 2. EVENT LIFECYCLE (State Machine — shared by both modes)
│   ├── Draft
│   │   ├── Admin creates event (LC-#####)
│   │   ├── Set mode: "exam" (default) or "last_stand"
│   │   ├── Add questions (option A/B/C/D + correct answer)
│   │   ├── Import from Review Items (bulk)
│   │   ├── Set eligible plans (subscription gate)
│   │   ├── Optional: paid event settings (is_paid, price, currency)
│   │   ├── Configure XP rewards (1st/2nd/3rd/participation/default)
│   │   ├── Set schedule (start time, waiting duration, exam duration)
│   │   ├── Exam mode: optional question_time_limit → auto-calc exam_duration
│   │   ├── Last Stand mode: set starting_hearts (default 3), result_window_duration (default 3s)
│   │   ├── Validation: overlap detection vs non-Draft events (5-min buffer)
│   │   ├── Validation: capacity 0–10,000 (0 = unlimited); waiting 30–600s; exam 1–180 min
│   │   ├── Validation: paid events require price > 0
│   │   ├── Validation: at least 1 question before leaving Draft
│   │   ├── Validation: after Draft, only status/counters/leaderboard remain mutable
│   │   └── after_save: populate Redis draft snapshot idempotently (respects client-driven advances)
│   │
│   ├── Draft → Waiting  [cron OR client-driven: scheduled_start ≤ now]
│   │   ├── Populate / refresh Redis (atomic pipeline)
│   │   │   ├── lc:{id}:status       = "waiting"       (STRING, TTL 24h)
│   │   │   ├── lc:{id}:questions    = JSON             (STRING, TTL 24h)
│   │   │   ├── lc:{id}:meta         = {all fields...}  (HASH,   TTL 24h)
│   │   │   ├── lc:{id}:mode         = "exam"|"last_stand" (STRING, TTL 24h)
│   │   │   ├── lc:{id}:count        = "0"              (STRING, TTL 24h, SETNX)
│   │   │   └── joined/submitted/join_times/results keys are created lazily on player activity
│   │   └── MariaDB: status = "Waiting"
│   │
│   ├── Waiting → Active  [cron OR /status CAS OR WS countdown: exam_start_ts ≤ now]
│   │   ├── Redis: status = "active" (CAS in /status, direct SET in countdown loop)
│   │   ├── MariaDB: status = "Active"
│   │   ├── Exam mode: WebSocket broadcast exam_start + questions + timer metadata
│   │   └── Last Stand mode: _start_last_stand_engine(event_id) → round loop begins
│   │
│   ├── Active → Ended  [cron OR /status CAS OR WS countdown OR engine callback]
│   │   ├── Redis: lc:{id}:status = "ended"
│   │   ├── MariaDB: status = "Ended"
│   │   ├── Trigger reconciliation (async in FastAPI, synchronous in cron)
│   │   ├── Exam mode: WebSocket broadcast event_ended
│   │   └── Last Stand mode: broadcast event_ended with reason + final_alive_count + total_rounds_played
│   │
│   ├── Post-Event Reconciliation  [triggered on transition OR cron retry]
│   │   ├── Distributed lock (lc:{id}:reconcile_lock, 3600s TTL)
│   │   ├── Read: joined set, count, meta, join_times
│   │   ├── Mode-specific data reads (see §4 Exam / §5 Last Stand)
│   │   ├── Build Participation docs / rows
│   │   ├── FastAPI path: insert_many (500/batch, fallback to sequential updates)
│   │   ├── Cron path: batched SQL INSERT/UPDATE (500/batch)
│   │   ├── Sync participant_count + submitted_count to event
│   │   ├── On success: set lc:{id}:reconciled = "1"
│   │   ├── On success: delete ephemeral keys, keep status="ended" (24h)
│   │   └── On failure: keys preserved for retry (no data loss)
│   │
│   └── Finalization  [cron: Ended + no leaderboard_json]
│       ├── Check data completeness (Redis vs DB submitted counts)
│       ├── Retry reconciliation if mismatch detected
│       ├── Compute ranking (standard competition: ties share rank)
│       ├── Distribute XP (participation + rank bonus → Redis wallet + dirty set)
│       └── Save leaderboard_json (top 20) → completion marker
│
├── 3. SOURCE SELECTION (Event Detail Routing — shared)
│   ├── Single criterion: lc:{id}:status key in Redis
│   │   ├── status ∈ {draft, waiting, active} → Redis ONLY (zero Frappe calls)
│   │   ├── status = "ended"                  → Frappe DB ONLY (no Redis fallback)
│   │   └── status missing                    → SETNX lc:{id}:hydrate_guard (30s) + one-time hydrate
│   ├── No silent fallback: missing Redis data during active = hard failure
│   ├── Post-reconciliation: ephemeral keys gone, status="ended" stays → DB path
│   └── Note: GET /status is Redis-first too, but hydrates directly on cold miss
│
├── 4. EXAM MODE — Player Flow
│   ├── GET /api/v1/live-challenge/{event_id}/status  [Status — Public]
│   │   ├── Auth: NONE (public, sub-2ms)
│   │   ├── Redis-first read (may hydrate from Frappe on cold start)
│   │   ├── Client-driven transitions: advances state if time thresholds met
│   │   │   └── Lua CAS: draft→waiting, waiting→active, active→ended
│   │   └── Return: status, participant_count, mode
│   │
│   ├── GET /api/v1/live-challenge/{event_id}  [Event Detail]
│   │   ├── Auth: JWT  |  Rate limit: lc_read (20/window)
│   │   ├── Source selection (see §3):
│   │   │   ├── Draft / Waiting / Active: single Redis pipeline (zero Frappe calls)
│   │   │   └── Ended: Frappe DB read (event doc + participation record)
│   │   └── Return: metadata, capacity/current_count, timers, is_paid, question_count,
│   │              eligible_plans, mode, player state, and top_players when ended
│   │
│   ├── POST /api/v1/live-challenge/{event_id}/join  [Join]
│   │   ├── Auth: JWT  |  Rate limit: lc_join (5/window)
│   │   ├── Gate: status must be "waiting" or "active"
│   │   ├── Plan eligibility check (Redis meta: eligible_plans)
│   │   ├── Paid gate (if is_paid): premium bypass OR active event ticket
│   │   ├── Atomic join via Lua script
│   │   │   ├── Check: status ∈ {waiting, active}   → 400 EVENT_NOT_JOINABLE
│   │   │   ├── Check: not in lc:{id}:joined         → 409 ALREADY_JOINED
│   │   │   ├── Check: not in lc:{id}:submitted      → 409 ALREADY_JOINED
│   │   │   ├── Check: lc:{id}:count < capacity      → 422 CAPACITY_FULL
│   │   │   └── INCR count + SADD joined → return position
│   │   ├── Record join timestamp: HSET lc:{id}:join_times
│   │   └── Return: position, countdown_remaining, waiting_room_duration, mode, ws_url
│   │
│   ├── WebSocket /api/v1/live-challenge/{event_id}/ws  [Waiting Room + Exam]
│   │   ├── Auth: JWT via query parameter (decoded before accept)
│   │   ├── Gate: player must be in lc:{id}:joined
│   │   ├── Waiting phase (1s interval)
│   │   │   └── → {type: "countdown", remaining, participant_count}
│   │   ├── Waiting-room reactions
│   │   │   ├── Client tap: {type: "waiting_room_reaction_tap", reaction: heart|fire|clap}
│   │   │   └── Server burst: {type: "waiting_room_reaction_burst", reactions, degraded, server_ts}
│   │   ├── Exam start (broadcast once)
│   │   │   └── → {type: "exam_start", questions[], exam_end_ts, total_questions, timer fields}
│   │   ├── Late join / reconnect during Active
│   │   │   └── → immediate exam_start to new client
│   │   └── Exam end (broadcast once)
│   │       └── → {type: "event_ended"}
│   │
│   ├── GET /api/v1/live-challenge/{event_id}/questions  [Questions — REST Fallback]
│   │   ├── Auth: JWT  |  Rate limit: lc_read
│   │   ├── Gate: status must be "active"
│   │   ├── Gate: player must be in lc:{id}:joined
│   │   ├── Gate: player must NOT be in lc:{id}:submitted
│   │   ├── Strips correct_answer from each question
│   │   └── Return: questions[], exam_end_ts, total_questions, timer fields
│   │
│   ├── POST /api/v1/live-challenge/{event_id}/submit  [Submit Answers — Exam Only]
│   │   ├── Auth: JWT  |  Rate limit: lc_submit (2/window)
│   │   ├── Gate: status must be "active"
│   │   ├── Gate: mode must be "exam" (returns MODE_NOT_SUPPORTED for last_stand)
│   │   ├── Atomic SADD to lc:{id}:submitted   → 409 ALREADY_SUBMITTED
│   │   │   └── Rollback SREM if status changed mid-flight
│   │   ├── Verify: player in lc:{id}:joined
│   │   ├── Grade answers (server-side, from Redis questions)
│   │   │   └── score = (correct / total) × 100
│   │   ├── Store result in Redis: HSET lc:{id}:results
│   │   │   └── JSON: {score, correct_count, submitted_at, answers_json}
│   │   └── Return: score, correct_count, total_questions, submitted_at
│   │
│   ├── Reconciliation data (Exam):
│   │   ├── Reads lc:{id}:results hash → score, correct_count, submitted_at, answers_json
│   │   └── Participation fields: score, submitted_at, answers_json
│   │
│   ├── GET /api/v1/live-challenge/{event_id}/result  [Result]
│   │   ├── Auth: JWT  |  Rate limit: lc_read
│   │   ├── Query Participation (event + player) from MariaDB
│   │   └── Return: score, rank, xp_awarded, total_participants, submitted_at
│   │
│   └── GET /api/v1/live-challenge/{event_id}/leaderboard  [Leaderboard]
│       ├── Auth: JWT  |  Rate limit: lc_read
│       ├── Active: return empty leaderboard + exam_end_ts (poll-later response)
│       ├── Ended: parse leaderboard_json (top 20)
│       └── Return: leaderboard[], my_rank, my_score, total_participants, exam_end_ts
│
├── 5. LAST STAND MODE — Player Flow
│   │
│   ├── 5A. JOIN (differences from Exam)
│   │   ├── During Waiting: allows join (same as Exam)
│   │   ├── During Active: REJECTED → 400 NO_LATE_JOIN
│   │   │   └── Reason: can't add player mid-elimination (missed rounds)
│   │   ├── On join, initialize player:
│   │   │   ├── HSET lc:{id}:hearts player_id starting_hearts
│   │   │   └── SADD lc:{id}:alive player_id
│   │   └── Return: position, countdown_remaining, mode="last_stand", starting_hearts
│   │
│   ├── 5B. LAST STAND ENGINE (Server-Driven Round Loop)
│   │   ├── Architecture:
│   │   │   ├── One engine instance per active Last Stand event
│   │   │   ├── Runs as asyncio Task managed by LiveChallengeService
│   │   │   ├── Fully server-driven (no client request-response pattern for rounds)
│   │   │   ├── All game state lives in Redis (zero MariaDB during Active — FR-022)
│   │   │   └── Supports crash recovery by resuming from stored round state
│   │   │
│   │   ├── Engine Lock (multi-worker safety):
│   │   │   ├── lc:{id}:engine_lock (SETNX, 24h TTL)
│   │   │   ├── Only ONE worker across the cluster runs the engine
│   │   │   ├── Other workers skip silently on lock contention
│   │   │   └── Lock released when event ends or on crash recovery
│   │   │
│   │   ├── Round Loop (repeats for each question):
│   │   │   │
│   │   │   ├── 1. ANSWER PHASE
│   │   │   │   ├── Duration: question_time_limit seconds (from meta, default 30s)
│   │   │   │   ├── Store round state: HSET lc:{id}:round
│   │   │   │   │   └── {round_id, question_idx, phase:"answer", phase_end_ts, alive_count}
│   │   │   │   ├── Broadcast via pub/sub: round_start message (see §5C)
│   │   │   │   ├── Players answer via POST /answer (see §5D)
│   │   │   │   ├── Early close: triggered when all alive players have answered
│   │   │   │   │   └── Pub/sub signal on memora:lc:{id}:round_signal ("all_answered")
│   │   │   │   └── Phase ends: timeout OR early close
│   │   │   │
│   │   │   ├── 2. EVALUATION (instant, between phases)
│   │   │   │   ├── Read all answers for this round from lc:{id}:round_answers:{round_id}
│   │   │   │   ├── For each alive player:
│   │   │   │   │   ├── Correct answer → no change
│   │   │   │   │   ├── Wrong answer → deduct 1 heart
│   │   │   │   │   └── No answer (timeout) → deduct 1 heart
│   │   │   │   ├── Eliminate players with hearts ≤ 0:
│   │   │   │   │   ├── SREM lc:{id}:alive
│   │   │   │   │   ├── SADD lc:{id}:eliminated
│   │   │   │   │   └── HSET lc:{id}:eliminated_at player_id → question_idx
│   │   │   │   ├── Update hearts: HSET lc:{id}:hearts player_id → new_hearts
│   │   │   │   ├── Update stats: HINCRBY lc:{id}:correct_counts, lc:{id}:answered_counts
│   │   │   │   ├── Record response times: append ms to lc:{id}:response_times
│   │   │   │   └── Set hearts_deducted flag (crash recovery guard — S-1)
│   │   │   │
│   │   │   ├── 3. RESULT PHASE
│   │   │   │   ├── Duration: result_window_duration seconds (from meta, default 3s)
│   │   │   │   ├── Broadcast via pub/sub: round_result (personalized per player, see §5C)
│   │   │   │   ├── Broadcast via pub/sub: alive_count_update (lightweight, for spectators)
│   │   │   │   └── Phase ends: timeout
│   │   │   │
│   │   │   └── 4. LOOP CONTROL
│   │   │       ├── Continue if: more questions AND alive players > 0
│   │   │       ├── End (all_finished): all questions played, ≥1 player alive
│   │   │       ├── End (all_eliminated): all players eliminated before all questions
│   │   │       └── End (time_ceiling): exam_end_ts safety net reached
│   │   │
│   │   └── Event End:
│   │       ├── Store end metadata in lc:{id}:round HASH:
│   │       │   └── {phase:"ended", end_reason, final_alive_count, total_rounds_played}
│   │       ├── Set lc:{id}:status = "ended"
│   │       ├── Broadcast event_ended with reason + final_alive_count + total_rounds_played
│   │       └── Trigger reconciliation
│   │
│   ├── 5C. WEBSOCKET MESSAGES (Last Stand)
│   │   │
│   │   ├── Waiting phase: same as Exam (countdown + reactions)
│   │   │
│   │   ├── round_start  [broadcast at each answer phase start]
│   │   │   ├── round_id: unique ID (e.g., "LC-00123-R3")
│   │   │   ├── question_idx: 0-based question index
│   │   │   ├── question: {text, options} — NO correct_answer
│   │   │   ├── time_limit: answer window duration (seconds)
│   │   │   ├── alive_count: current alive players
│   │   │   ├── total_rounds: total questions in event
│   │   │   └── is_alive: personalized boolean (true only for alive players)
│   │   │
│   │   ├── round_result  [personalized broadcast after answer window]
│   │   │   ├── round_id, question_idx
│   │   │   ├── alive_count: alive AFTER evaluation
│   │   │   ├── eliminated_this_round: count of newly eliminated
│   │   │   ├── result_duration: result window duration (seconds)
│   │   │   ├── hearts_remaining: player's hearts after this round (personalized)
│   │   │   ├── heart_lost: boolean — did this player lose a heart? (personalized)
│   │   │   ├── is_correct: true / false / null (unanswered) (personalized)
│   │   │   ├── is_eliminated: eliminated this round? (personalized)
│   │   │   └── is_alive: still alive? (personalized)
│   │   │   └── Spectator defaults: hearts=0, heart_lost=false, is_correct=null, is_eliminated=false, is_alive=false
│   │   │
│   │   ├── alive_count_update  [lightweight broadcast after each evaluation]
│   │   │   ├── alive_count: current alive
│   │   │   ├── eliminated_count: total eliminated so far
│   │   │   └── current_round: current question index (0-based)
│   │   │
│   │   ├── player_state  [sent to reconnecting player on WS accept]
│   │   │   ├── hearts_remaining, is_alive
│   │   │   ├── current_round_id: current round_id or null (between rounds)
│   │   │   ├── question_idx: current question index
│   │   │   ├── phase: "answer" or "result"
│   │   │   ├── phase_remaining_ms: milliseconds until phase ends
│   │   │   ├── question: current question (if alive + answer phase), else null
│   │   │   ├── alive_count: current alive players
│   │   │   └── eliminated_at_question: question index where eliminated (null if alive)
│   │   │
│   │   └── event_ended  [broadcast when event ends]
│   │       ├── reason: "all_finished" | "all_eliminated" | "time_ceiling"
│   │       ├── final_alive_count: players alive at event end
│   │       └── total_rounds_played: questions actually played (may be < total_questions)
│   │
│   ├── 5D. ANSWER ENDPOINT (Last Stand Only)
│   │   ├── POST /api/v1/live-challenge/{event_id}/answer
│   │   ├── Auth: JWT  |  Rate limit: lc_answer (2/window)
│   │   ├── Request: {round_id: str, selected: "A"|"B"|"C"|"D"}
│   │   ├── Atomic Lua script validates (R-007):
│   │   │   ├── status = "active"              → -1 EVENT_NOT_ACTIVE
│   │   │   ├── player in alive set            → -2 NOT_ALIVE
│   │   │   ├── round_id matches current       → -3 ROUND_MISMATCH
│   │   │   ├── answer window still open        → -4 WINDOW_CLOSED
│   │   │   └── not already answered            → -5 ALREADY_ANSWERED
│   │   ├── Store: HSET lc:{id}:round_answers:{round_id} player_id → {selected, ts}
│   │   ├── Return: {accepted: bool, round_id: str}
│   │   │   └── Note: correctness NOT revealed here — only via round_result WS message
│   │   └── If all alive players answered → publish early-close signal
│   │
│   ├── 5E. RECONCILIATION (Last Stand)
│   │   ├── Reads from Redis:
│   │   │   ├── lc:{id}:hearts         → final_hearts per player
│   │   │   ├── lc:{id}:eliminated     → is_eliminated (0/1)
│   │   │   ├── lc:{id}:eliminated_at  → eliminated_at_question (index)
│   │   │   ├── lc:{id}:correct_counts → correct answers per player
│   │   │   ├── lc:{id}:answered_counts → answered count per player
│   │   │   └── lc:{id}:response_times → JSON array of ms values per player
│   │   ├── Score = (correct_count / total_questions) × 100
│   │   ├── submitted_at = reconciliation timestamp (all players marked submitted)
│   │   ├── avg_response_time_ms = mean of per-player response times
│   │   └── Participation fields:
│   │       ├── score, submitted_at
│   │       ├── final_hearts
│   │       ├── is_eliminated (0/1)
│   │       ├── eliminated_at_question (0-based index, 0 if not eliminated)
│   │       └── avg_response_time_ms
│   │
│   └── 5F. RESULT / LEADERBOARD (Last Stand)
│       ├── GET /result returns: score, rank, xp_awarded, final_hearts, is_eliminated,
│       │   eliminated_at_question, avg_response_time_ms
│       └── GET /leaderboard entries include: final_hearts, is_eliminated
│
├── 6. MULTI-WORKER PUB/SUB BROADCASTING
│   ├── Problem: engine runs in one worker, WS connections may be in other workers
│   ├── Engine publishes to: memora:lc_round:{event_id}
│   ├── All workers subscribe to relevant event channels
│   ├── Envelope format: {event_id, broadcast_type, message/base_msg/player_states}
│   ├── Two broadcast types:
│   │   ├── "json": round_start, alive_count_update, event_ended (same for all)
│   │   └── "personalized": round_result (base message + per-player state overrides)
│   ├── Round subscriber loop: _round_subscriber_loop() polls Redis pub/sub
│   ├── Fallback: if publish fails, engine broadcasts locally
│   └── Lifecycle: subscribe on first WS connection, unsubscribe when no WS clients
│
├── 7. CRASH RECOVERY & RESUME
│   ├── Startup scan: resume_active_last_stand_events() called once on FastAPI startup
│   ├── Queries Frappe for all Active events with mode=last_stand
│   ├── For each event:
│   │   ├── Check Redis round state exists (lc:{id}:round HASH)
│   │   ├── Determine resume point from stored question_idx, phase, phase_end_ts
│   │   ├── If phase_end_ts < now: fast-forward missed round
│   │   │   ├── Deduct hearts for alive players who didn't answer
│   │   │   ├── Eliminate players with hearts ≤ 0
│   │   │   └── Guard: hearts_deducted flag prevents double-penalization (S-1)
│   │   ├── If phase = "result": resume from NEXT round
│   │   └── If in answer window: resume THIS round (remaining time)
│   └── Lock cleanup: allows another worker to claim engine_lock and resume
│
├── 8. ADMIN DASHBOARD (Frappe Whitelist)
│   ├── GET  get_dashboard(event_id)
│   │   ├── Active: participant_count, submitted_count, still_taking, time_remaining
│   │   ├── Ended:  average_score, highest_score, completion_rate, leaderboard
│   │   └── Draft/Waiting: basic counts
│   │
│   ├── GET  get_live_participants(event_id)
│   │   ├── Active only: reads joined/submitted/join_times/results from Redis
│   │   └── Return: joined_count, submitted_count, still_taking, participants[]
│   │
│   ├── GET  get_full_leaderboard(event_id)
│   │   └── Ended only: returns full ranked leaderboard from Participation rows
│   │
│   └── POST import_review_items(event_id, review_item_ids)
│       ├── Gate: Draft only
│       └── Maps correct_choice (1-4) → A-D
│
├── 9. REDIS KEY DESIGN
│   │
│   │  ── Shared Keys (both modes, most TTLs = 24h) ──
│   │
│   │  Key Pattern              Type         Purpose
│   │  ─────────────────────────────────────────────────────────────────
│   ├── lc:{id}:status          STRING       Routing signal + state machine
│   ├── lc:{id}:mode            STRING       Fast mode lookup ("exam"|"last_stand")
│   ├── lc:{id}:questions       STRING/JSON  Questions with correct_answer (server-only)
│   ├── lc:{id}:meta            HASH         All event metadata for Redis-only reads
│   ├── lc:{id}:count           STRING/INT   Participant counter (SETNX to avoid reset)
│   ├── lc:{id}:joined          SET          Player IDs who joined
│   ├── lc:{id}:submitted       SET          Player IDs who submitted
│   ├── lc:{id}:join_times      HASH         player_id → join timestamp
│   ├── lc:{id}:reconcile_lock  STRING       Distributed lock (3600s TTL)
│   ├── lc:{id}:reconciled      STRING       "1" = reconciliation completed
│   ├── lc:{id}:hydrate_guard   STRING       30s cold-start throttle for one-time DB hydration
│   │
│   │  ── Exam-Only Keys ──
│   │
│   ├── lc:{id}:results         HASH         player_id → JSON {score, correct_count, submitted_at, answers_json}
│   │
│   │  ── Last Stand Keys ──
│   │
│   ├── lc:{id}:round                    HASH    Round state: round_id, question_idx, phase,
│   │                                             phase_end_ts, alive_count, hearts_deducted,
│   │                                             end_reason, final_alive_count, total_rounds_played
│   ├── lc:{id}:hearts                   HASH    player_id → hearts_remaining
│   ├── lc:{id}:alive                    SET     Player IDs still in competition
│   ├── lc:{id}:eliminated               SET     Player IDs who lost all hearts
│   ├── lc:{id}:eliminated_at            HASH    player_id → question_idx where eliminated
│   ├── lc:{id}:round_answers:{round_id} HASH    player_id → JSON {selected, ts}
│   ├── lc:{id}:correct_counts           HASH    player_id → count of correct answers
│   ├── lc:{id}:answered_counts          HASH    player_id → count of questions answered
│   ├── lc:{id}:response_times           HASH    player_id → JSON array of ms values
│   ├── lc:{id}:engine_lock              STRING  Distributed lock for single-engine guarantee (24h)
│   │
│   │  ── Last Stand Pub/Sub Channels ──
│   │
│   ├── memora:lc_round:{id}             PUB/SUB  Round broadcast envelope (cross-worker)
│   └── memora:lc:{id}:round_signal      PUB/SUB  Early-close signal ("all_answered")
│
├── 10. BUSINESS LOGIC (Pure Functions)
│   ├── grade_answers(questions, answers)  [Exam mode]
│   │   ├── Score = (correct_count / total) × 100
│   │   ├── missing / null selected → treated as wrong
│   │   └── Returns score, correct_count, total_questions
│   │
│   ├── compute_ranking(participants, display_names)
│   │   ├── Sort by score DESC
│   │   └── Standard competition ranking (1, 1, 3, 4...)
│   │
│   └── compute_xp_awards(ranked, xp_config)
│       ├── participation_xp (flat, all submitters)
│       └── Rank bonus: 1st / 2nd / 3rd / default
│
├── 11. SECURITY
│   ├── correct_answer never sent to client (stripped from WS + REST payloads)
│   ├── Lua scripts for atomic join + CAS transitions; submit uses atomic SADD + rollback
│   ├── Last Stand answer: Lua script validates 5 conditions atomically (R-007)
│   ├── Plan eligibility checked before join (from Redis meta)
│   ├── Paid events require premium bypass or active ticket access
│   ├── Per-player rate limits: lc_join (5), lc_submit (2), lc_read (20), lc_answer (2)
│   ├── Global per-IP rate limit: LC endpoints EXEMPT (school NAT scenario)
│   ├── WebSocket JWT auth before accept + participant gate
│   ├── Waiting-room reactions are Redis rate-limited and room-capped
│   ├── Client-driven transitions use Lua CAS (no stale overwrites)
│   ├── Engine lock prevents multi-worker engine duplication
│   ├── hearts_deducted guard prevents double-penalization on crash recovery
│   └── Idempotent post-processing (xp_awarded > 0, leaderboard_json as marker)
│
├── 12. INFRASTRUCTURE
│   ├── Singleton: LiveChallengeService(redis, frappe) in app.state
│   │   ├── Startup: created in lifespan handler + reaction subscriber started
│   │   ├── Startup: resume_active_last_stand_events() for crash recovery
│   │   └── Shutdown: stop subscriber + cancel countdown loops + cancel engine tasks
│   ├── Per-event countdown loop (1s tick)
│   │   ├── Starts on first WS connection during waiting/active
│   │   └── Can drive waiting→active and active→ended when clients are connected
│   ├── Per-event Last Stand engine (asyncio Task)
│   │   ├── Started on waiting→active transition (if mode=last_stand)
│   │   ├── Lock-guarded: one engine per event across all workers
│   │   └── Broadcasts via pub/sub for cross-worker delivery
│   ├── Cron: live_challenge_transitions (every 60s)
│   │   ├── Drives state machine for all non-terminal events
│   │   ├── Sets mode key in Redis on active transition
│   │   ├── Retries reconciliation on mismatch
│   │   └── Runs finalization (ranking + XP + leaderboard)
│   └── WebSocket broadcast: chunked concurrent send (2000) + 2s timeout + dead-socket cleanup
│
└── 13. ANALYTICS EXPORT
    ├── fact_live_challenge_event       → Parquet (full snapshot)
    └── fact_live_challenge_participation → Parquet (full snapshot)
```

## Mode Comparison

```
┌──────────────────────────────┬──────────────────────────────────┐
│         Exam Mode             │        Last Stand Mode            │
├──────────────────────────────┼──────────────────────────────────┤
│                              │                                  │
│ All questions at once        │ One question per round           │
│ (bulk delivery)              │ (server-driven)                  │
│                              │                                  │
│ Player submits all answers   │ Player answers per round         │
│ via POST /submit             │ via POST /answer                 │
│                              │                                  │
│ Score revealed on submit     │ Result revealed via WS           │
│ (immediate REST response)    │ (round_result message)           │
│                              │                                  │
│ Late join allowed (Active)   │ Late join BLOCKED (Active)       │
│                              │                                  │
│ No elimination               │ Hearts system:                   │
│                              │  ── wrong/timeout = -1 heart     │
│                              │  ── hearts ≤ 0 = eliminated      │
│                              │                                  │
│ REST + WebSocket             │ WebSocket mandatory              │
│ (WS optional for real-time)  │ (server drives all rounds)       │
│                              │                                  │
│ Client controls pacing       │ Server controls pacing           │
│                              │                                  │
│ Reconciliation:              │ Reconciliation:                  │
│  ── score + answers_json     │  ── score + final_hearts         │
│                              │  ── is_eliminated                │
│                              │  ── eliminated_at_question       │
│                              │  ── avg_response_time_ms         │
│                              │                                  │
│ Single worker sufficient     │ Engine lock + pub/sub for        │
│                              │ multi-worker broadcasting        │
│                              │                                  │
└──────────────────────────────┴──────────────────────────────────┘
```

## Chronological Timeline — Exam Mode

```
T+0m    ┌─ Admin creates event in Draft (mode="exam")
        │  (questions, plans, XP, schedule, optional question timer, optional paid settings)
        │  Redis draft snapshot populated on save (idempotent)
        │
T+Xm    ├─ scheduled_start reached
        │  ├─ Cron OR client /status: Draft → Waiting
        │  ├─ Redis refreshed (status, questions, meta, mode, count via SETNX)
        │  └─ MariaDB: status = "Waiting"
        │
        ├─ Players join waiting room
        │  ├─ POST /join → Lua atomic (position + join_time recorded)
        │  ├─ Paid events also check premium OR ticket access
        │  ├─ WebSocket connects → countdown every 1s
        │  ├─ Optional reaction taps aggregate into burst broadcasts
        │  └─ Participant count grows (Redis counter)
        │
T+Xm+W  ├─ exam_start_ts reached (W = waiting_room_duration)
        │  ├─ Cron OR /status OR WS countdown: Waiting → Active
        │  ├─ WebSocket broadcast: exam_start + questions + timer metadata
        │  └─ Late joiners / reconnects get immediate exam_start
        │
        ├─ Players submit answers
        │  ├─ POST /submit → grade → score + submitted_at returned immediately
        │  ├─ Results stored in Redis (lc:{id}:results hash)
        │  └─ No real-time DB writes (deferred to reconciliation)
        │
T+Xm+W+E├─ exam_end_ts reached (E = exam_duration)
        │  ├─ Cron OR /status OR WS countdown: Active → Ended
        │  ├─ Reconciliation triggered (async or sync, lock-guarded)
        │  └─ WebSocket broadcast: event_ended if clients are connected
        │
        ├─ Reconciliation (Redis → MariaDB)
        │  ├─ Build Participation docs from Redis join_times + results
        │  ├─ FastAPI path uses insert_many; cron path uses SQL batches
        │  ├─ Sync counts to event doc
        │  └─ Delete ephemeral keys, keep status="ended" for routing
        │
T+~5m   ├─ Finalization (cron, idempotent)
        │  ├─ Verify reconciliation completeness
        │  ├─ Compute ranks (standard competition)
        │  ├─ Distribute XP → Redis wallets + dirty set
        │  └─ Save leaderboard_json (top 20) → completion marker
        │
T+∞     └─ Players fetch results + leaderboard
           ├─ GET /result → score, rank, xp, submitted_at (from DB)
           └─ GET /leaderboard → active poll-later OR ended top 20 + own rank
```

## Chronological Timeline — Last Stand Mode

```
T+0m    ┌─ Admin creates event in Draft (mode="last_stand")
        │  (questions, plans, XP, schedule, starting_hearts=3, result_window_duration=3s)
        │  Redis draft snapshot populated on save (idempotent)
        │
T+Xm    ├─ scheduled_start reached
        │  ├─ Cron OR client /status: Draft → Waiting
        │  ├─ Redis refreshed (status, questions, meta, mode="last_stand", count via SETNX)
        │  └─ MariaDB: status = "Waiting"
        │
        ├─ Players join waiting room
        │  ├─ POST /join → Lua atomic + initialize hearts + add to alive set
        │  ├─ Return includes mode="last_stand", starting_hearts
        │  ├─ WebSocket connects → countdown every 1s + reactions
        │  └─ Late join BLOCKED once event goes Active
        │
T+Xm+W  ├─ exam_start_ts reached (W = waiting_room_duration)
        │  ├─ Waiting → Active transition
        │  ├─ _start_last_stand_engine() acquires engine_lock
        │  └─ Engine starts round loop as asyncio Task
        │
        ├─ Round 1
        │  ├─ WS broadcast: round_start {question, time_limit, round_id, alive_count}
        │  ├─ Players answer: POST /answer {round_id, selected} → {accepted}
        │  ├─ [Answer phase ends: timeout OR all alive answered]
        │  ├─ Evaluate: deduct hearts for wrong/unanswered, eliminate hearts≤0
        │  ├─ WS broadcast: round_result (personalized: hearts, is_correct, is_eliminated)
        │  ├─ WS broadcast: alive_count_update
        │  └─ [Result phase ends: result_window_duration]
        │
        ├─ Round 2...N (repeat until end condition)
        │  ├─ End: all questions played (all_finished)
        │  ├─ End: all players eliminated (all_eliminated)
        │  └─ End: exam_end_ts safety ceiling (time_ceiling)
        │
T+end   ├─ Event ends
        │  ├─ Engine stores end metadata in lc:{id}:round HASH
        │  ├─ lc:{id}:status = "ended"
        │  ├─ WS broadcast: event_ended {reason, final_alive_count, total_rounds_played}
        │  └─ Reconciliation triggered
        │
        ├─ Reconciliation (Redis → MariaDB)
        │  ├─ Build Participation: score, final_hearts, is_eliminated,
        │  │   eliminated_at_question, avg_response_time_ms
        │  ├─ Sync counts to event doc
        │  └─ Delete ephemeral keys, keep status="ended" for routing
        │
T+~5m   ├─ Finalization (cron, idempotent)
        │  ├─ Compute ranks + Distribute XP + Save leaderboard_json
        │  └─ Leaderboard includes final_hearts, is_eliminated per entry
        │
T+∞     └─ Players fetch results + leaderboard
           ├─ GET /result → score, rank, xp, final_hearts, is_eliminated, avg_response_time_ms
           └─ GET /leaderboard → entries with final_hearts, is_eliminated
```

## Error Codes

```
Code                 HTTP   Trigger                                    Mode
──────────────────────────────────────────────────────────────────────────────
EVENT_NOT_FOUND      404    Event ID doesn't exist                     Both
EVENT_NOT_ACTIVE     400    Status not in expected state                Both
EVENT_NOT_JOINABLE   400    Status not waiting/active                  Both
EVENT_NOT_ENDED      400    Leaderboard before active/ended data       Both
ALREADY_JOINED       409    Player already in joined set               Both
ALREADY_SUBMITTED    409    Player already in submitted set            Exam
CAPACITY_FULL        422    Participant count >= capacity               Both
PLAN_NOT_ELIGIBLE    403    Player's plan not in eligible_plans        Both
NO_EVENT_ACCESS      403    Paid event without premium/ticket          Both
NOT_A_PARTICIPANT    403    Player not in joined set                   Both
NO_PARTICIPATION     404    No participation record found               Both
SUBMISSION_FAILED    500    Grading or storage error                   Both
MODE_NOT_SUPPORTED   400    POST /submit on last_stand event           Last Stand
NO_LATE_JOIN         400    POST /join during Active                   Last Stand
NOT_ALIVE            400    POST /answer after elimination             Last Stand
ROUND_MISMATCH       400    POST /answer with wrong round_id           Last Stand
WINDOW_CLOSED        400    POST /answer after phase expires           Last Stand
ALREADY_ANSWERED     400    POST /answer twice in same round           Last Stand
```
