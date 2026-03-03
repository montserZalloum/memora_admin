# Memora Load Test Suite

Locust-based load testing suite for the Memora FastAPI sidecar. Simulates multiple player and optional admin/provider profiles with realistic traffic distribution to validate performance under load up to 100k concurrent users.

## Prerequisites

- **Python 3.11+**
- **Locust**: `pip install locust`
- **FastAPI sidecar** running on port 8002
- **Pre-created test player accounts** (minimum 3 for basic testing, 100-500 for 100k simulation)
- **Test subjects and lessons** with proper access grants for all test players

Verify the FastAPI sidecar is running:

```bash
curl http://127.0.0.1:8002/api/v1/health/live
# Expected: {"status":"alive","api_version":"v1"}
```

## Setup

```bash
cd apps/memora_admin/load_tests

# Copy example config and fill in real test data
cp config.example.py config.py
```

Edit `config.py` with your environment's test data:

```python
# Pre-created player accounts
TEST_PLAYERS = [
    {"mobile": "+201000000001", "password": "your_password", "player_id": "PLAYER-00001"},
    {"mobile": "+201000000002", "password": "your_password", "player_id": "PLAYER-00002"},
    {"mobile": "+201000000003", "password": "your_password", "player_id": "PLAYER-00003"},
    # Add more for larger tests
]

# Subjects accessible to test players (must have tracks/units/topics)
TEST_SUBJECTS = ["SUBJ-00001", "SUBJ-00002"]

# Lessons for session simulation (must be accessible to test players)
TEST_LESSONS = [
    {
        "lesson_id": "LESSON-00001",
        "subject_id": "SUBJ-00001",
        "topic_id": "TOPIC-00001",
        "stages": [
            {"stage_id": "STAGE-001", "min_time_ms": 3000, "max_time_ms": 8000, "max_fail_count": 2},
            {"stage_id": "STAGE-002", "min_time_ms": 2000, "max_time_ms": 6000, "max_fail_count": 2},
        ],
    },
]

# Optional fixtures used by added endpoint coverage
TEST_REVIEW_SUBJECTS = ["SUBJ-00001"]
TEST_AVATARS = ["avatar_01", "avatar_02"]
TEST_PLAN_MANIFEST_IDS = ["PLAN-00001"]

# Dangerous/state-changing flows are disabled unless explicitly enabled
ENABLE_MUTATION_ENDPOINTS = False
TEST_PLAN_CHANGE_IDS = ["PLAN-00002"]
TEST_PRODUCT_GRANTS = ["GRNT-00001"]
TEST_VOUCHERS = [{"pin": "VALID123", "grant_id": "GRNT-00001"}]

# Admin-only flows are also opt-in
ENABLE_ADMIN_ENDPOINTS = False
ADMIN_CREDENTIALS = {"email": "admin@example.com", "password": "CHANGE_ME"}
TEST_ACCESS_CONTENT_KEYS = ["SUB-MATH"]

# External provider webhook simulation is opt-in too
ENABLE_WEBHOOK_ENDPOINTS = False
TEST_WEBHOOK_EVENTS = [{"player_id": "PLAYER-00001", "product_grant_id": "GRNT-00001"}]
```

## Running Tests

### Sanity Check (10 users, 30 seconds)

```bash
cd apps/memora_admin/load_tests
locust --headless -u 10 -r 5 --run-time 30s --host http://127.0.0.1:8002
```

All always-on user profiles should appear in the stats output. The optional mutation/admin profiles only produce requests when enabled in `config.py`.

### Web UI Mode (Interactive)

```bash
locust --host http://127.0.0.1:8002
# Open http://localhost:8089 in your browser
```

### Output Options

```bash
# CSV output (creates results/stage1_stats.csv, results/stage1_stats_history.csv, etc.)
mkdir -p results
locust --headless -u 100 -r 10 --run-time 2m --csv results/stage1 --host http://127.0.0.1:8002

# HTML report
locust --headless -u 100 -r 10 --run-time 2m --html results/stage1.html --host http://127.0.0.1:8002
```

## 5-Stage Scaling Ladder

Progressively increase load to identify performance thresholds. Complete each stage before moving to the next.

| Stage | Users | Spawn Rate | Duration | Purpose |
|-------|-------|------------|----------|---------|
| 1 | 100 | 10/s | 2 min | Sanity check — all flows complete |
| 2 | 1,000 | 50/s | 5 min | Baseline — p99 < 500ms, error rate < 1% |
| 3 | 10,000 | 200/s | 10 min | Stress — monitor for degradation |
| 4 | 50,000 | 500/s | 15 min | High load — no cascade failures |
| 5 | 100,000 | 1,000/s | 15 min | Peak — no resource exhaustion (distributed mode required) |

### Stage Commands

```bash
# Stage 1: Sanity check (100 users)
locust --headless -u 100 -r 10 --run-time 2m --csv results/stage1 --host http://127.0.0.1:8002

# Stage 2: Baseline (1,000 users)
locust --headless -u 1000 -r 50 --run-time 5m --csv results/stage2 --host http://127.0.0.1:8002

# Stage 3: Stress (10,000 users)
locust --headless -u 10000 -r 200 --run-time 10m --csv results/stage3 --host http://127.0.0.1:8002

# Stage 4: High load (50,000 users)
locust --headless -u 50000 -r 500 --run-time 15m --csv results/stage4 --host http://127.0.0.1:8002

# Stage 5: Peak (100,000 users) — requires distributed mode, see below
locust --master --expect-workers=16 --host http://127.0.0.1:8002
```

### Success Criteria

| Stage | p99 Latency | Error Rate | Key Metric |
|-------|-------------|------------|------------|
| 1 (100u) | — | All flows complete | Functional validation |
| 2 (1ku) | < 500ms | < 1% | Baseline performance |
| 3 (10ku) | Monitored | < 1% | Degradation curve |
| 4 (50ku) | Monitored | No cascade failures | System resilience |
| 5 (100ku) | Monitored | No resource exhaustion | Peak capacity |

## Distributed Mode

A single machine can handle approximately 5,000-10,000 Locust users (depending on hardware and test complexity). For higher user counts, use Locust's built-in distributed mode. **No code changes are needed** — the same `locustfile.py` works in both modes.

### Single Machine (Multi-Process)

Auto-spawn one worker per CPU core:

```bash
locust --processes -1 --host http://127.0.0.1:8002
```

### Master/Worker on Same Machine

```bash
# Terminal 1: Start master (coordinates workers, runs web UI on :8089)
locust --master --host http://127.0.0.1:8002

# Terminal 2+: Start workers (one per CPU core)
locust --worker --master-host=127.0.0.1
```

### Multi-Machine Setup

For Stage 4-5, distribute workers across multiple machines:

```bash
# Machine A (master — coordinates workers, no load generation):
locust --master --expect-workers=16 --host http://TARGET_IP:8002

# Machine B-D (workers — each spawns processes matching CPU count):
locust --worker --master-host=MASTER_IP --processes -1
```

### Hardware Estimation

| Stage | Users | Workers Needed (est.) | Machine Count |
|-------|-------|-----------------------|---------------|
| 1-2 | 100-1k | 1 process | 1 |
| 3 | 10k | 2-4 workers | 1 |
| 4 | 50k | 8-12 workers | 2-3 |
| 5 | 100k | 16-24 workers | 4-6 |

Each worker process can handle roughly 2,000-5,000 users depending on the complexity of the test scenarios and the machine's resources.

## What Gets Tested

| Profile | Weight | Endpoints | Behavior |
|---------|--------|-----------|----------|
| DashboardUser | 40% | profile, stats, activity, mastery, wallet, progress, catalog, subscriptions, settings, announcements, plans | Polls dashboard and discovery APIs with weighted frequency |
| LessonPlayer | 35% | topic lessons, session current/start/end, wallet | Full lesson lifecycle with 3-10s think time |
| BrowserUser | 15% | progress, tracks, units (hierarchy drill-down) | Drills down subject → tracks → units |
| LeaderboardChecker | 10% | daily/weekly leaderboard, my rank | Checks rankings with weighted frequency |
| ReviewUser | 10% | reviews overview, due items, review submit | Runs review batches for a subject |
| PracticeUser | 12% | practice hierarchy/start/submit/continue | Runs practice session batches |
| MutationUser | 2% | avatar, voucher, purchase, reports, plan change | State-changing player flows, only when enabled |
| AdminAccessUser | 1% | access grants CRUD | Admin-only grant coverage, only when enabled |
| WebhookUser | 1% | payment webhook | External provider webhook simulation, only when enabled |

`MutationUser`, `AdminAccessUser`, and `WebhookUser` are opt-in. They are no-ops unless the related config flags and fixtures are populated.

### Endpoint Coverage

| Endpoint | Profile | Request Name |
|----------|---------|-------------|
| `GET /api/v1/profile` | DashboardUser | `/api/v1/profile` |
| `GET /api/v1/profile/stats` | DashboardUser | `/api/v1/profile/stats` |
| `GET /api/v1/profile/activity` | DashboardUser | `/api/v1/profile/activity` |
| `GET /api/v1/profile/mastery` | DashboardUser | `/api/v1/profile/mastery` |
| `PUT /api/v1/profile/avatar` | MutationUser | `/api/v1/profile/avatar` |
| `GET /api/v1/wallet` | DashboardUser, LessonPlayer | `/api/v1/wallet` |
| `GET /api/v1/subscriptions` | DashboardUser | `/api/v1/subscriptions` |
| `GET /api/v1/catalog/` | DashboardUser | `/api/v1/catalog/` |
| `GET /api/v1/settings/gamification` | DashboardUser | `/api/v1/settings/gamification` |
| `GET /api/v1/announcements/` | DashboardUser | `/api/v1/announcements/` |
| `GET /api/v1/plans/available` | DashboardUser | `/api/v1/plans/available` |
| `GET /api/v1/plans/{plan}/manifest` | DashboardUser | `/api/v1/plans/[plan]/manifest` |
| `GET /api/v1/progress` | DashboardUser, BrowserUser | `/api/v1/progress` |
| `GET /api/v1/progress/{subject}/topics/{topic}/lessons` | LessonPlayer | `/api/v1/progress/[subject]/topics/[topic]/lessons` |
| `GET /api/v1/sessions/current` | LessonPlayer | `/api/v1/sessions/current` |
| `POST /api/v1/sessions/start` | LessonPlayer | `/api/v1/sessions/start` |
| `POST /api/v1/sessions/end` | LessonPlayer | `/api/v1/sessions/end` |
| `GET /api/v1/progress/{subject}/tracks` | BrowserUser | `/api/v1/progress/[subject]/tracks` |
| `GET /api/v1/progress/{subject}/tracks/{track}` | BrowserUser | `/api/v1/progress/[subject]/tracks/[track]` |
| `GET /api/v1/progress/{subject}/tracks/{track}/units/{unit}` | BrowserUser | `/api/v1/progress/[subject]/tracks/[track]/units/[unit]` |
| `GET /api/v1/reviews` | ReviewUser | `/api/v1/reviews` |
| `GET /api/v1/reviews/{subject}` | ReviewUser | `/api/v1/reviews/[subject]` |
| `POST /api/v1/reviews/{subject}/submit` | ReviewUser | `/api/v1/reviews/[subject]/submit` |
| `GET /api/v1/practice/hierarchy` | PracticeUser | `/api/v1/practice/hierarchy` |
| `POST /api/v1/practice/start` | PracticeUser | `/api/v1/practice/start` |
| `POST /api/v1/practice/submit` | PracticeUser | `/api/v1/practice/submit` |
| `POST /api/v1/practice/continue` | PracticeUser | `/api/v1/practice/continue` |
| `GET /api/v1/leaderboard/daily` | LeaderboardChecker | `/api/v1/leaderboard/[type]` |
| `GET /api/v1/leaderboard/weekly` | LeaderboardChecker | `/api/v1/leaderboard/[type]` |
| `GET /api/v1/leaderboard/{type}/me` | LeaderboardChecker | `/api/v1/leaderboard/[type]/me` |
| `POST /api/v1/voucher/preview` | MutationUser | `/api/v1/voucher/preview` |
| `POST /api/v1/voucher/redeem` | MutationUser | `/api/v1/voucher/redeem` |
| `POST /api/v1/purchase/` | MutationUser | `/api/v1/purchase/` |
| `POST /api/v1/reports` | MutationUser | `/api/v1/reports` |
| `POST /api/v1/plans/change` | MutationUser | `/api/v1/plans/change` |
| `POST /api/v1/access/grants` | AdminAccessUser | `/api/v1/access/grants` |
| `GET /api/v1/access/grants/{player}` | AdminAccessUser | `/api/v1/access/grants/[player]` |
| `DELETE /api/v1/access/grants` | AdminAccessUser | `/api/v1/access/grants` |
| `POST /api/v1/webhooks/payment` | WebhookUser | `/api/v1/webhooks/payment` |

`WS /api/v1/notifications/ws` is not covered by this `HttpUser` suite. Test it separately with a websocket-capable client.

## Troubleshooting

### All logins fail (401)

- Verify test player accounts exist in the system
- Check passwords in `config.py` match the actual accounts
- Confirm accounts are not locked or deactivated

### Connection refused

- Verify FastAPI sidecar is running: `curl http://127.0.0.1:8002/api/v1/health/live`
- Check `--host` flag matches the actual server address
- If testing remotely, ensure firewall allows connections on port 8002

### High 429 rate at low user counts

- The server's global rate limiter may be too restrictive for testing
- Check `global_rate_limit` in FastAPI config
- Consider increasing the limit temporarily for load tests
- Spread load across more test player accounts (reduces per-account rate limiting)

### Session-end always fails

- Verify test lessons exist and have valid stage IDs
- Check that test players have access grants for the configured subjects
- Confirm no stale sessions: sessions have a 1-hour TTL

### Empty progress/hierarchy responses

- Verify test subjects have content (tracks, units, topics, lessons)
- Check that test players have subscription/access grants for the subjects
- The BrowserUser falls back to `config.TEST_SUBJECTS` if the response is empty

### Workers not connecting (distributed mode)

- Ensure master and workers use the same Locust version
- Check firewall allows port 5557 (Locust master-worker communication)
- Verify `--master-host` points to the correct IP address
