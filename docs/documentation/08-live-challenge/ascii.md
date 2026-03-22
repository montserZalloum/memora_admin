# Live Challenge — Flow Hierarchy

```
Live Challenge System
├── 1. EVENT LIFECYCLE (State Machine)
│   ├── Draft
│   │   ├── Admin creates event (LC-#####)
│   │   ├── Add questions (option A/B/C/D + correct answer)
│   │   ├── Import from Review Items (bulk)
│   │   ├── Set eligible plans (subscription gate)
│   │   ├── Optional: paid event settings (is_paid, price, currency)
│   │   ├── Configure XP rewards (1st/2nd/3rd/participation/default)
│   │   ├── Set schedule (start time, waiting duration, exam duration)
│   │   ├── Optional: enable question timer (question_time_limit → auto-calc exam_duration)
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
│   │   │   ├── lc:{id}:count        = "0"              (STRING, TTL 24h, SETNX)
│   │   │   └── joined/submitted/join_times/results keys are created lazily on player activity
│   │   └── MariaDB: status = "Waiting"
│   │
│   ├── Waiting → Active  [cron OR /status CAS OR WS countdown: exam_start_ts ≤ now]
│   │   ├── Redis: status = "active" (CAS in /status, direct SET in countdown loop)
│   │   ├── MariaDB: status = "Active"
│   │   └── WebSocket broadcast: exam_start + questions + timer metadata (no correct_answer)
│   │
│   ├── Active → Ended  [cron OR /status CAS OR WS countdown: exam_end_ts ≤ now]
│   │   ├── Redis: lc:{id}:status = "ended"
│   │   ├── MariaDB: status = "Ended"
│   │   ├── Trigger reconciliation (async in FastAPI, synchronous in cron)
│   │   └── WebSocket broadcast: event_ended (when countdown loop owns the transition)
│   │
│   ├── Post-Event Reconciliation  [triggered on transition OR cron retry]
│   │   ├── Distributed lock (lc:{id}:reconcile_lock, 3600s TTL)
│   │   ├── Read: joined set, count, meta, join_times, results hash
│   │   ├── Build Participation docs / rows (score pre-populated from Redis)
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
├── 2. SOURCE SELECTION (Event Detail Routing)
│   ├── Single criterion: lc:{id}:status key in Redis
│   │   ├── status ∈ {draft, waiting, active} → Redis ONLY (zero Frappe calls)
│   │   ├── status = "ended"                  → Frappe DB ONLY (no Redis fallback)
│   │   └── status missing                    → SETNX lc:{id}:hydrate_guard (30s) + one-time hydrate
│   ├── No silent fallback: missing Redis data during active = hard failure
│   ├── Post-reconciliation: ephemeral keys gone, status="ended" stays → DB path
│   └── Note: GET /status is Redis-first too, but hydrates directly on cold miss
│
├── 3. PLAYER FLOW
│   ├── GET /api/v1/live-challenge/{event_id}/status  [Status — Public]
│   │   ├── Auth: NONE (public, sub-2ms)
│   │   ├── Redis-first read (may hydrate from Frappe on cold start)
│   │   ├── Client-driven transitions: advances state if time thresholds met
│   │   │   └── Lua CAS: draft→waiting, waiting→active, active→ended
│   │   └── Return: status, participant_count
│   │
│   ├── GET /api/v1/live-challenge/{event_id}  [Event Detail]
│   │   ├── Auth: JWT  |  Rate limit: lc_read (20/window)
│   │   ├── Source selection (see §2):
│   │   │   ├── Draft / Waiting / Active: single Redis pipeline (zero Frappe calls)
│   │   │   └── Ended: Frappe DB read (event doc + participation record)
│   │   └── Return: metadata, capacity/current_count, timers, is_paid, question_count,
│   │              eligible_plans, player state, and top_players when ended
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
│   │   └── Return: position, countdown_remaining, waiting_room_duration, ws_url
│   │
│   ├── WebSocket /api/v1/live-challenge/{event_id}/ws  [Waiting Room]
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
│   ├── POST /api/v1/live-challenge/{event_id}/submit  [Submit Answers]
│   │   ├── Auth: JWT  |  Rate limit: lc_submit (2/window)
│   │   ├── Gate: status must be "active"
│   │   ├── Atomic SADD to lc:{id}:submitted   → 409 ALREADY_SUBMITTED
│   │   │   └── Rollback SREM if status changed mid-flight
│   │   ├── Verify: player in lc:{id}:joined
│   │   ├── Grade answers (server-side, from Redis questions)
│   │   │   └── score = (correct / total) × 100
│   │   ├── Store result in Redis: HSET lc:{id}:results
│   │   │   └── JSON: {score, correct_count, submitted_at, answers_json}
│   │   └── Return: score, correct_count, total_questions, submitted_at
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
├── 4. ADMIN DASHBOARD (Frappe Whitelist)
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
├── 5. REDIS KEY DESIGN (core event keys; most TTLs = 24h)
│   │
│   │  Key Pattern              Type         Purpose
│   │  ─────────────────────────────────────────────────────────────────
│   ├── lc:{id}:status          STRING       Routing signal + state machine
│   ├── lc:{id}:questions       STRING/JSON  Questions with correct_answer (server-only)
│   ├── lc:{id}:meta            HASH         All event metadata for Redis-only reads
│   ├── lc:{id}:count           STRING/INT   Participant counter (SETNX to avoid reset)
│   ├── lc:{id}:joined          SET          Player IDs who joined
│   ├── lc:{id}:submitted       SET          Player IDs who submitted
│   ├── lc:{id}:join_times      HASH         player_id → join timestamp
│   ├── lc:{id}:results         HASH         player_id → JSON {score, correct_count, ...}
│   ├── lc:{id}:reconcile_lock  STRING       Distributed lock (3600s TTL)
│   ├── lc:{id}:reconciled      STRING       "1" = reconciliation completed
│   └── lc:{id}:hydrate_guard   STRING       30s cold-start throttle for one-time DB hydration
│
├── 6. BUSINESS LOGIC (Pure Functions)
│   ├── grade_answers(questions, answers)
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
├── 7. SECURITY
│   ├── correct_answer never sent to client (stripped from WS + REST payloads)
│   ├── Lua scripts for atomic join + CAS transitions; submit uses atomic SADD + rollback
│   ├── Plan eligibility checked before join (from Redis meta)
│   ├── Paid events require premium bypass or active ticket access
│   ├── Per-player rate limits: lc_join (5), lc_submit (2), lc_read (20)
│   ├── Global per-IP rate limit: LC endpoints EXEMPT (school NAT scenario)
│   ├── WebSocket JWT auth before accept + participant gate
│   ├── Waiting-room reactions are Redis rate-limited and room-capped
│   ├── Client-driven transitions use Lua CAS (no stale overwrites)
│   └── Idempotent post-processing (xp_awarded > 0, leaderboard_json as marker)
│
├── 8. INFRASTRUCTURE
│   ├── Singleton: LiveChallengeService(redis, frappe) in app.state
│   │   ├── Startup: created in lifespan handler + reaction subscriber started
│   │   └── Shutdown: stop subscriber + cancel countdown loops
│   ├── Per-event countdown loop (1s tick)
│   │   ├── Starts on first WS connection during waiting/active
│   │   └── Can drive waiting→active and active→ended when clients are connected
│   ├── Cron: live_challenge_transitions (every 60s)
│   │   ├── Drives state machine for all non-terminal events
│   │   ├── Retries reconciliation on mismatch
│   │   └── Runs finalization (ranking + XP + leaderboard)
│   └── WebSocket broadcast: chunked concurrent send (2000) + 2s timeout + dead-socket cleanup
│
└── 9. ANALYTICS EXPORT
    ├── fact_live_challenge_event       → Parquet (full snapshot)
    └── fact_live_challenge_participation → Parquet (full snapshot)
```

## Chronological Timeline

```
T+0m    ┌─ Admin creates event in Draft
        │  (questions, plans, XP, schedule, optional question timer, optional paid settings)
        │  Redis draft snapshot populated on save (idempotent)
        │
T+Xm    ├─ scheduled_start reached
        │  ├─ Cron OR client /status: Draft → Waiting
        │  ├─ Redis refreshed (status, questions, meta, count via SETNX)
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

## Error Codes

```
Code                 HTTP   Trigger
─────────────────────────────────────────────────────
EVENT_NOT_FOUND      404    Event ID doesn't exist
EVENT_NOT_ACTIVE     400    Status not in expected state
EVENT_NOT_JOINABLE   400    Status not waiting/active
EVENT_NOT_ENDED      400    Leaderboard requested before active/ended data is available
ALREADY_JOINED       409    Player already in joined set (or had already submitted)
ALREADY_SUBMITTED    409    Player already in submitted set
CAPACITY_FULL        422    Participant count >= capacity
PLAN_NOT_ELIGIBLE    403    Player's plan not in eligible_plans
NO_EVENT_ACCESS      403    Paid event without premium bypass or active ticket
NOT_A_PARTICIPANT    403    Player not in joined set
NO_PARTICIPATION     404    No participation record found
SUBMISSION_FAILED    500    Grading or storage error
```
