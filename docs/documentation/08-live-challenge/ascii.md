# Live Challenge — Flow Hierarchy

```
Live Challenge System
├── 1. EVENT LIFECYCLE (State Machine)
│   ├── Draft
│   │   ├── Admin creates event (LC-#####)
│   │   ├── Add questions (option A/B/C/D + correct answer)
│   │   ├── Import from Review Items (bulk)
│   │   ├── Set eligible plans (subscription gate)
│   │   ├── Configure XP rewards (1st/2nd/3rd/participation/default)
│   │   ├── Set schedule (start time, waiting duration, exam duration)
│   │   ├── Optional: enable question timer (per-question time limit)
│   │   ├── Validation: schedule overlap detection (5-min buffer)
│   │   ├── Validation: capacity 1–10,000; waiting 30–600s; exam 1–180 min
│   │   └── after_save: populate Redis idempotently (respects client-driven advances)
│   │
│   ├── Draft → Waiting  [cron OR client-driven: scheduled_start ≤ now]
│   │   ├── Populate Redis (atomic pipeline)
│   │   │   ├── lc:{id}:status       = "waiting"       (STRING, TTL 24h)
│   │   │   ├── lc:{id}:questions    = JSON             (STRING, TTL 24h)
│   │   │   ├── lc:{id}:meta         = {all fields...}  (HASH,   TTL 24h)
│   │   │   ├── lc:{id}:count        = "0"              (STRING, TTL 24h, SETNX)
│   │   │   ├── lc:{id}:joined       = {}               (SET,    TTL 24h)
│   │   │   └── lc:{id}:submitted    = {}               (SET,    TTL 24h)
│   │   └── MariaDB: status = "Waiting"
│   │
│   ├── Waiting → Active  [cron OR client-driven: exam_start_ts ≤ now]
│   │   ├── Redis: Lua CAS script (compare-and-swap "waiting" → "active")
│   │   ├── MariaDB: status = "Active"
│   │   └── WebSocket broadcast: exam_start + questions (no correct_answer)
│   │
│   ├── Active → Ended  [cron OR client-driven: exam_end_ts ≤ now]
│   │   ├── Redis: lc:{id}:status = "ended"
│   │   ├── MariaDB: status = "Ended"
│   │   ├── Trigger async reconciliation (Redis → DB flush)
│   │   └── WebSocket broadcast: event_ended
│   │
│   ├── Post-Event Reconciliation  [triggered on transition OR cron retry]
│   │   ├── Distributed lock (lc:{id}:reconcile_lock, 3600s TTL)
│   │   ├── Read: joined set, count, meta, join_times, results hash
│   │   ├── Build Participation docs (score pre-populated from Redis)
│   │   ├── Batch insert_many (500/batch, fallback to sequential)
│   │   ├── Sync participant_count + submitted_count to event
│   │   ├── On success: delete ephemeral keys, keep status="ended" (24h)
│   │   ├── Set lc:{id}:reconciled = "1"
│   │   └── On failure: keys preserved for retry (no data loss)
│   │
│   └── Finalization  [cron: Ended + no leaderboard_json]
│       ├── Check data completeness (Redis vs DB submitted counts)
│       ├── Retry reconciliation if mismatch detected
│       ├── Compute ranking (standard competition: ties share rank)
│       ├── Distribute XP (participation + rank bonus → Redis wallet + dirty set)
│       └── Save leaderboard_json (top 20) → completion marker
│
├── 2. SOURCE SELECTION (Explicit Routing)
│   ├── Single criterion: lc:{id}:status key in Redis
│   │   ├── status ∈ {draft, waiting, active} → Redis ONLY (zero Frappe calls)
│   │   ├── status = "ended"                  → Frappe DB ONLY (no Redis fallback)
│   │   └── status missing                    → cold-start guard (30s TTL, hydrate)
│   ├── No silent fallback: missing Redis data during active = hard failure
│   └── Post-reconciliation: ephemeral keys gone, status="ended" stays → DB path
│
├── 3. PLAYER FLOW
│   ├── GET /api/v1/live-challenge/{event_id}/status  [Status — Public]
│   │   ├── Auth: NONE (public, sub-2ms)
│   │   ├── Redis-only read (status + meta fields)
│   │   ├── Client-driven transitions: advances state if time thresholds met
│   │   │   └── Lua CAS: draft→waiting, waiting→active, active→ended
│   │   └── Return: status, timestamps, participant_count
│   │
│   ├── GET /api/v1/live-challenge/{event_id}  [Event Detail]
│   │   ├── Auth: JWT  |  Rate limit: lc_read (20/window)
│   │   ├── Source selection (see §2):
│   │   │   ├── Active: single Redis pipeline (zero Frappe calls)
│   │   │   └── Ended: Frappe DB read (event doc + participation record)
│   │   └── Return: metadata, capacity, timers, player state (has_joined, has_submitted)
│   │
│   ├── POST /api/v1/live-challenge/{event_id}/join  [Join]
│   │   ├── Auth: JWT  |  Rate limit: lc_join (5/window)
│   │   ├── Gate: status must be "waiting" or "active"
│   │   ├── Plan eligibility check (Redis meta: eligible_plans)
│   │   ├── Atomic join via Lua script (7 ops in 1 call)
│   │   │   ├── Check: status ∈ {waiting, active}   → 409 EVENT_NOT_JOINABLE
│   │   │   ├── Check: not in lc:{id}:joined         → 409 ALREADY_JOINED
│   │   │   ├── Check: not in lc:{id}:submitted      → 409 ALREADY_JOINED
│   │   │   ├── Check: lc:{id}:count < capacity       → 422 CAPACITY_FULL
│   │   │   └── INCR count + SADD joined → return position
│   │   ├── Record join timestamp: HSET lc:{id}:join_times
│   │   └── Return: position, countdown_remaining, waiting_room_duration
│   │
│   ├── WebSocket /api/v1/live-challenge/{event_id}/ws  [Waiting Room]
│   │   ├── Auth: JWT via query parameter (decoded before accept)
│   │   ├── Gate: player must be in lc:{id}:joined
│   │   ├── Waiting phase (1s interval)
│   │   │   └── → {type: "countdown", remaining, participant_count}
│   │   ├── Exam start (broadcast once)
│   │   │   └── → {type: "exam_start", questions[], exam_end_ts, total_questions}
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
│   │   └── Return: questions[], exam_end_ts, total_questions
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
│   │   └── Return: score, correct_count, total_questions, corrections
│   │
│   ├── GET /api/v1/live-challenge/{event_id}/result  [Result]
│   │   ├── Auth: JWT  |  Rate limit: lc_read
│   │   ├── Query Participation (event + player) from MariaDB
│   │   └── Return: score, rank, xp_awarded, total_participants, corrections
│   │
│   └── GET /api/v1/live-challenge/{event_id}/leaderboard  [Leaderboard]
│       ├── Auth: JWT  |  Rate limit: lc_read
│       ├── Gate: status must be "Ended"
│       ├── Parse leaderboard_json (top 20)
│       └── Return: leaderboard[], my_rank, my_score (if show_student_rank)
│
├── 4. ADMIN DASHBOARD (Frappe Whitelist)
│   ├── GET  get_dashboard(event_id)
│   │   ├── Active: participant_count, submitted_count, still_taking, time_remaining
│   │   ├── Ended:  average_score, highest_score, completion_rate, leaderboard
│   │   └── Draft/Waiting: basic counts
│   │
│   └── POST import_review_items(event_id, review_item_ids)
│       ├── Gate: Draft only
│       └── Maps correct_choice (1-4) → A-D
│
├── 5. REDIS KEY DESIGN (all keys: TTL 24h = 86400s)
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
│   └── lc:{id}:reconciled      STRING       "1" = reconciliation completed
│
├── 6. BUSINESS LOGIC (Pure Functions)
│   ├── grade_answers(questions, answers, show_correct_answers)
│   │   ├── Score = (correct_count / total) × 100
│   │   ├── null selected → treated as wrong
│   │   └── Corrections list (if show_correct_answers enabled)
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
│   ├── Lua scripts for atomic join/submit (no race conditions)
│   ├── Plan eligibility checked before join (from Redis meta)
│   ├── Per-player rate limits: lc_join (5), lc_submit (2), lc_read (20)
│   ├── Global per-IP rate limit: LC endpoints EXEMPT (school NAT scenario)
│   ├── WebSocket JWT auth before accept + participant gate
│   ├── Client-driven transitions use Lua CAS (no stale overwrites)
│   └── Idempotent post-processing (xp_awarded > 0, leaderboard_json as marker)
│
├── 8. INFRASTRUCTURE
│   ├── Singleton: LiveChallengeService(redis, frappe) in app.state
│   │   ├── Startup: created in lifespan handler
│   │   └── Shutdown: flush + cancel countdown loops
│   ├── Cron: live_challenge_transitions (every 60s)
│   │   ├── Drives state machine for all non-terminal events
│   │   ├── Retries reconciliation on mismatch
│   │   └── Runs finalization (ranking + XP + leaderboard)
│   └── WebSocket broadcast: configurable semaphore (0=sequential, >0=parallel)
│
└── 9. ANALYTICS EXPORT
    ├── fact_live_challenge_event       → Parquet (full snapshot)
    └── fact_live_challenge_participation → Parquet (full snapshot)
```

## Chronological Timeline

```
T+0m    ┌─ Admin creates event in Draft
        │  (questions, plans, XP, schedule, optional question timer)
        │  Redis populated on save (idempotent)
        │
T+Xm    ├─ scheduled_start reached
        │  ├─ Cron OR client /status: Draft → Waiting
        │  ├─ Redis hydrated (status, questions, meta, counters)
        │  └─ MariaDB: status = "Waiting"
        │
        ├─ Players join waiting room
        │  ├─ POST /join → Lua atomic (position + join_time recorded)
        │  ├─ WebSocket connects → countdown every 1s
        │  └─ Participant count grows (Redis counter)
        │
T+Xm+W  ├─ exam_start_ts reached (W = waiting_room_duration)
        │  ├─ Cron OR client /status: Waiting → Active (Lua CAS)
        │  ├─ WebSocket broadcast: exam_start + questions
        │  └─ Late joiners / reconnects get immediate exam_start
        │
        ├─ Players submit answers
        │  ├─ POST /submit → grade → score returned immediately
        │  ├─ Results stored in Redis (lc:{id}:results hash)
        │  └─ No real-time DB writes (deferred to reconciliation)
        │
T+Xm+W+E├─ exam_end_ts reached (E = exam_duration)
        │  ├─ Cron OR client /status: Active → Ended
        │  ├─ WebSocket broadcast: event_ended
        │  └─ Reconciliation triggered (async, distributed lock)
        │
        ├─ Reconciliation (Redis → MariaDB)
        │  ├─ Build Participation docs from Redis join_times + results
        │  ├─ Batch insert (500/batch) with duplicate handling
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
           ├─ GET /result → score, rank, xp, corrections (from DB)
           └─ GET /leaderboard → top 20 + own rank (from DB)
```

## Error Codes

```
Code                 HTTP   Trigger
─────────────────────────────────────────────────────
EVENT_NOT_FOUND      404    Event ID doesn't exist
EVENT_NOT_ACTIVE     400    Status not in expected state
EVENT_NOT_JOINABLE   409    Status not waiting/active
EVENT_NOT_ENDED      400    Leaderboard/result before ended
ALREADY_JOINED       409    Player already in joined set
ALREADY_SUBMITTED    409    Player already in submitted set
CAPACITY_FULL        422    Participant count >= capacity
PLAN_NOT_ELIGIBLE    403    Player's plan not in eligible_plans
NOT_A_PARTICIPANT    403    Player not in joined set
NO_PARTICIPATION     404    No participation record found
SUBMISSION_FAILED    500    Grading or storage error
```
