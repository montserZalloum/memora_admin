# Quickstart: Locust Load Test Suite

## Prerequisites

1. **Python 3.11+** installed
2. **Locust** installed: `pip install locust`
3. **FastAPI sidecar** running on port 8002
4. **Test player accounts** pre-created in the system (minimum 3)
5. **Test subjects/lessons** with proper access grants for test players

## Setup

```bash
cd apps/memora_admin/load_tests

# Copy example config and fill in real test data
cp config.example.py config.py

# Edit config.py with your test player accounts, subjects, and lessons
# See "Configuration" section below
```

## Configuration

Edit `load_tests/config.py`:

```python
# Target server
HOST = "http://127.0.0.1:8002"

# Test player accounts (pre-created, with access grants)
TEST_PLAYERS = [
    {"mobile": "+201000000001", "password": "test123"},
    {"mobile": "+201000000002", "password": "test123"},
    {"mobile": "+201000000003", "password": "test123"},
    # Add more for larger tests (100-500 recommended for 100k simulation)
]

# Subjects accessible to test players
TEST_SUBJECTS = ["SUBJ-00001", "SUBJ-00002"]

# Lessons for session simulation (must be accessible to test players)
TEST_LESSONS = [
    {
        "lesson_id": "LESSON-00001",
        "subject_id": "SUBJ-00001",
        "stages": [
            {"stage_id": "STAGE-001", "min_time_ms": 3000, "max_time_ms": 8000},
            {"stage_id": "STAGE-002", "min_time_ms": 2000, "max_time_ms": 6000},
        ],
    },
]
```

## Running Tests

### Sanity Check (10 users, 30 seconds)
```bash
cd apps/memora_admin/load_tests
locust --headless -u 10 -r 5 --run-time 30s --host http://127.0.0.1:8002
```

### Web UI Mode (interactive)
```bash
locust --host http://127.0.0.1:8002
# Open http://localhost:8089 in browser
```

### 5-Stage Scaling Ladder

| Stage | Command |
|-------|---------|
| 1 (100 users) | `locust --headless -u 100 -r 10 --run-time 2m --host http://127.0.0.1:8002` |
| 2 (1,000 users) | `locust --headless -u 1000 -r 50 --run-time 5m --host http://127.0.0.1:8002` |
| 3 (10,000 users) | `locust --headless -u 10000 -r 200 --run-time 10m --host http://127.0.0.1:8002` |
| 4 (50,000 users) | `locust --headless -u 50000 -r 500 --run-time 15m --host http://127.0.0.1:8002` |
| 5 (100,000 users) | Requires distributed mode (see below) |

### Output Options
```bash
# CSV output
locust --headless -u 100 -r 10 --run-time 2m --csv results/stage1 --host http://127.0.0.1:8002

# HTML report
locust --headless -u 100 -r 10 --run-time 2m --html results/stage1.html --host http://127.0.0.1:8002
```

## Distributed Mode (for 100k users)

A single machine can handle ~5,000-10,000 virtual users. For higher counts, use Locust's distributed mode:

```bash
# Terminal 1: Master (runs web UI, coordinates workers)
locust --master --host http://127.0.0.1:8002

# Terminal 2-N: Workers (one per CPU core)
locust --worker --master-host=127.0.0.1

# Or auto-spawn workers matching CPU count:
locust --worker --master-host=127.0.0.1 --processes -1
```

**Multi-machine setup**:
```bash
# Machine A (master):
locust --master --expect-workers=8 --host http://TARGET_IP:8002

# Machine B-D (workers, 2-4 per machine):
locust --worker --master-host=MASTER_IP --processes -1
```

**Estimated workers for each stage**:

| Stage | Users | Workers (est.) |
|-------|-------|----------------|
| 1-2 | 100-1k | 1 process |
| 3 | 10k | 2-4 workers |
| 4 | 50k | 8-12 workers |
| 5 | 100k | 16-24 workers |

## What Gets Tested

| Profile | Weight | Endpoints |
|---------|--------|-----------|
| DashboardUser | 40% | profile, stats, activity, mastery, wallet, progress summary |
| LessonPlayer | 35% | session start/end, wallet, progress (full lesson lifecycle) |
| BrowserUser | 15% | hierarchy drill-down: subjects → tracks → units |
| LeaderboardChecker | 10% | daily/weekly leaderboard, my rank |

## Success Criteria

| Stage | p99 Latency | Error Rate | Duration |
|-------|-------------|------------|----------|
| 1 (100u) | Sanity check | All flows complete | 2 min |
| 2 (1ku) | < 500ms | < 1% | 5 min |
| 3 (10ku) | Monitored | < 1% | 10 min |
| 4 (50ku) | Monitored | No cascade failures | 15 min |
| 5 (100ku) | Monitored | No resource exhaustion | 15 min |

## Troubleshooting

- **All logins fail (401)**: Check that test player accounts exist and passwords are correct
- **All requests fail (connection refused)**: Verify FastAPI is running on port 8002: `curl http://127.0.0.1:8002/api/v1/health/live`
- **High 429 rate at low user counts**: Check `global_rate_limit` setting in FastAPI config (default: 100 req/min/IP for tests)
- **Session-end always fails**: Verify test lessons exist and are accessible to test players
- **Empty progress/hierarchy responses**: Verify test subjects have content and test players have access grants
