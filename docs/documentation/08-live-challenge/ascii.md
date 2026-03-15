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
│   │   └── Set schedule (start time, waiting duration, exam duration)
│   │
│   ├── Draft → Waiting  [cron: scheduled_start ≤ now]
│   │   ├── Populate Redis (atomic pipeline)
│   │   │   ├── lc:status:{id}      = "waiting"       (STRING, TTL 24h)
│   │   │   ├── lc:questions:{id}   = JSON             (STRING, TTL 24h)
│   │   │   ├── lc:meta:{id}        = {timestamps...}  (HASH,   TTL 24h)
│   │   │   ├── lc:count:{id}       = "0"              (STRING, TTL 24h)
│   │   │   ├── lc:joined:{id}      = {}               (SET,    TTL 24h)
│   │   │   └── lc:submitted:{id}   = {}               (SET,    TTL 24h)
│   │   └── MariaDB: status = "Waiting"
│   │
│   ├── Waiting → Active  [cron OR WebSocket countdown: exam_start_ts ≤ now]
│   │   ├── Redis: lc:status = "active"
│   │   ├── MariaDB: status = "Active"
│   │   └── WebSocket broadcast: exam_start + questions (no correct_answer)
│   │
│   ├── Active → Ended  [cron OR WebSocket countdown: exam_end_ts ≤ now]
│   │   ├── Redis: lc:status = "ended"
│   │   ├── Drain in-memory submission queue (flush last batch)
│   │   ├── MariaDB: status = "Ended"
│   │   └── WebSocket broadcast: event_ended
│   │
│   └── Post-Event Processing  [cron: Ended + no leaderboard_json]
│       ├── Wait for queue flush (submitted count match or 5-min timeout)
│       ├── Compute ranking (standard competition: ties share rank)
│       ├── Distribute XP (participation + rank bonus → Redis wallet)
│       └── Save leaderboard_json (top 20) → completion marker
│
├── 2. PLAYER FLOW
│   ├── GET /api/v1/live-challenge/{event_id}  [Event Detail]
│   │   ├── Auth: JWT (player_id from user.sub)
│   │   ├── Fetch event metadata from MariaDB
│   │   ├── Fetch live data from Redis (status, count, flags)
│   │   └── Return: metadata, capacity, timers, player state (has_joined, has_submitted)
│   │
│   ├── POST /api/v1/live-challenge/{event_id}/join  [Join]
│   │   ├── Auth: JWT  |  Rate limit: lc_join
│   │   ├── Gate: status must be "waiting" or "active"
│   │   ├── Plan eligibility check (FrappeClient, authoritative)
│   │   ├── Atomic join via Lua script
│   │   │   ├── Check: not in lc:joined       → 409 ALREADY_JOINED
│   │   │   ├── Check: not in lc:submitted    → 409 ALREADY_JOINED
│   │   │   ├── Check: lc:count < capacity    → 422 CAPACITY_FULL
│   │   │   └── INCR count + SADD joined → return position
│   │   ├── Create Participation record (FrappeClient, async)
│   │   │   └── On failure: rollback (DECR + SREM) → 500
│   │   ├── Sync participant_count to MariaDB
│   │   └── Return: position, countdown_remaining, ws_url
│   │
│   ├── WebSocket /api/v1/live-challenge/{event_id}/ws  [Waiting Room]
│   │   ├── Auth: JWT decoded before accept
│   │   ├── Gate: player must be in lc:joined
│   │   ├── Waiting phase (1s interval)
│   │   │   └── → {type: "countdown", remaining, participant_count}
│   │   ├── Exam start (broadcast once)
│   │   │   └── → {type: "exam_start", questions[], exam_end_ts, total_questions}
│   │   ├── Late join during Active
│   │   │   └── → immediate exam_start to new client
│   │   └── Exam end (broadcast once)
│   │       └── → {type: "event_ended"}
│   │
│   ├── POST /api/v1/live-challenge/{event_id}/submit  [Submit Answers]
│   │   ├── Auth: JWT  |  Rate limit: lc_submit
│   │   ├── Gate: status must be "active"
│   │   ├── Atomic SADD to lc:submitted         → 409 ALREADY_SUBMITTED
│   │   ├── Verify: player in lc:joined
│   │   ├── Grade answers (server-side, from Redis questions)
│   │   │   └── score = (correct / total) × 100
│   │   ├── Queue submission (in-memory async queue)
│   │   │   └── Consumer: batch 50 items or 30s → FrappeClient flush
│   │   └── Return: score, correct_count, total_questions, corrections
│   │
│   ├── GET /api/v1/live-challenge/{event_id}/result  [Result]
│   │   ├── Auth: JWT
│   │   ├── Query Participation (event + player) from MariaDB
│   │   └── Return: score, rank, xp_awarded, total_participants, corrections
│   │
│   └── GET /api/v1/live-challenge/{event_id}/leaderboard  [Leaderboard]
│       ├── Auth: JWT
│       ├── Gate: status must be "Ended"
│       ├── Parse leaderboard_json (top 20)
│       └── Return: leaderboard[], my_rank, my_score (if show_student_rank)
│
├── 3. ADMIN DASHBOARD (Frappe Whitelist)
│   ├── GET  get_dashboard(event_id)
│   │   ├── Active: participant_count, submitted_count, still_taking, time_remaining
│   │   ├── Ended:  average_score, highest_score, completion_rate, leaderboard
│   │   └── Draft/Waiting: basic counts
│   │
│   └── POST import_review_items(event_id, review_item_ids)
│       ├── Gate: Draft only
│       └── Maps correct_choice (1-4) → A-D
│
├── 4. BUSINESS LOGIC (Pure Functions)
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
├── 5. SECURITY
│   ├── correct_answer never sent to client (stripped from WebSocket payload)
│   ├── Lua scripts for atomic join/submit (no race conditions)
│   ├── Plan eligibility via FrappeClient (not JWT, which may be stale)
│   ├── Rate limiting on join + submit endpoints
│   ├── WebSocket JWT auth before accept + participant gate
│   └── Idempotent post-processing (xp_awarded > 0, leaderboard_json as marker)
│
└── 6. ANALYTICS EXPORT
    ├── fact_live_challenge_event       → Parquet (full snapshot)
    └── fact_live_challenge_participation → Parquet (full snapshot)
```

## Chronological Timeline

```
T+0m    ┌─ Admin creates event in Draft
        │  (questions, plans, XP, schedule)
        │
T+Xm    ├─ scheduled_start reached
        │  ├─ Cron: Draft → Waiting
        │  └─ Redis populated (status, questions, meta, counters)
        │
        ├─ Players join waiting room
        │  ├─ POST /join → atomic Lua (position assigned)
        │  ├─ WebSocket connects → countdown every 1s
        │  └─ Participant count grows
        │
T+Xm+W  ├─ exam_start_ts reached (W = waiting_room_duration)
        │  ├─ Waiting → Active
        │  ├─ WebSocket broadcast: exam_start + questions
        │  └─ Late joiners get immediate exam_start
        │
        ├─ Players submit answers
        │  ├─ POST /submit → grade → score returned immediately
        │  ├─ Submissions queued (batch 50 / 30s)
        │  └─ Queue consumer flushes to MariaDB
        │
T+Xm+W+E├─ exam_end_ts reached (E = exam_duration)
        │  ├─ Active → Ended
        │  ├─ Final queue drain
        │  └─ WebSocket broadcast: event_ended
        │
T+5m     ├─ Post-event processing
        │  ├─ Compute ranks (standard competition)
        │  ├─ Distribute XP → Redis wallets
        │  └─ Save leaderboard_json (top 20)
        │
T+∞     └─ Players fetch results + leaderboard
           ├─ GET /result → score, rank, xp, corrections
           └─ GET /leaderboard → top 20 + own rank
```
