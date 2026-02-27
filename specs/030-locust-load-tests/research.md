# Research: Locust Load Test Suite

**Phase 0 Output** | **Date**: 2026-02-27

## Research Tasks & Findings

### R-001: Locust User Class Architecture for Weighted Profiles

**Decision**: Use 4 separate `HttpUser` subclasses with `weight` attribute for traffic distribution.

**Rationale**: Locust natively supports `weight` on user classes to control spawn ratio. This is simpler and more maintainable than a single class with weighted `@task` decorators, because each profile has a distinct flow pattern (not just different endpoint weights).

**Alternatives Considered**:
- Single user class with weighted `@task` decorators — rejected because DashboardUser and LessonPlayer have fundamentally different flow structures (stateless polling vs. stateful session lifecycle)
- `TaskSet` nesting — rejected because Locust docs recommend flat `@task` methods on `HttpUser` for simpler scenarios; TaskSets add complexity without benefit here

**Implementation**:
```python
class DashboardUser(HttpUser):
    weight = 40
    wait_time = between(3, 8)

class LessonPlayer(HttpUser):
    weight = 35
    wait_time = between(5, 15)

class BrowserUser(HttpUser):
    weight = 15
    wait_time = between(2, 6)

class LeaderboardChecker(HttpUser):
    weight = 10
    wait_time = between(5, 10)
```

---

### R-002: Authentication Flow for Simulated Users

**Decision**: Authenticate once in `on_start()` using `POST /api/v1/auth/player/login`, store `access_token` on `self`, and set `Authorization` header for all subsequent requests.

**Rationale**: The spec requires "authenticate once on startup and reuse the token" (FR-002). The login endpoint returns `access_token` (60 min TTL) and `refresh_token`. For most test durations (<60 min), the access token won't expire. For longer runs, add a refresh mechanism.

**Key Details**:
- **Endpoint**: `POST /api/v1/auth/player/login`
- **Request**: `{"mobile": "+201234567890", "password": "test_password"}`
- **Required Header**: `X-Device-ID: <unique-per-virtual-user>`
- **Response**: `{"access_token": "...", "refresh_token": "...", "profile": {...}}`
- **Auth header**: `Authorization: Bearer <access_token>`
- **Rate limit**: 10 attempts/min per IP, 5/min per account — use random player from pool to spread load

**Edge Case**: If login is rate-limited (429), the user should log a warning, set `self.token = None`, and skip authenticated endpoints for that cycle. On next `on_start` retry (Locust respawns), it tries again.

**Implementation**:
```python
def on_start(self):
    player = random.choice(config.TEST_PLAYERS)
    self.device_id = f"locust-{uuid4().hex[:12]}"
    with self.client.post(
        "/api/v1/auth/player/login",
        json={"mobile": player["mobile"], "password": player["password"]},
        headers={"X-Device-ID": self.device_id},
        catch_response=True,
    ) as resp:
        if resp.status_code == 200:
            data = resp.json()
            self.token = data["access_token"]
            self.player_id = data.get("profile", {}).get("display_name", "unknown")
            resp.success()
        elif resp.status_code == 429:
            resp.success()  # Rate limit is expected under load
            self.token = None
        else:
            resp.failure(f"Login failed: {resp.status_code}")
            self.token = None
```

---

### R-003: Handling Rate-Limited Responses (HTTP 429)

**Decision**: Use Locust's `catch_response=True` context manager to intercept 429 responses and mark them as success (FR-007).

**Rationale**: By default, Locust counts any non-2xx response as a failure. The spec explicitly requires 429s to be treated as expected behavior. Using `catch_response=True` gives manual control over success/failure classification.

**Alternatives Considered**:
- Global event listener on `request` event to reclassify 429s — rejected because it's harder to maintain and less explicit per-endpoint
- Wrapper function around `self.client` — viable, chosen as the DRY approach (see helpers.py)

**Implementation**: A shared helper that wraps requests:
```python
def api_get(self, path, name=None, **kwargs):
    """GET with 429 tolerance and auth header."""
    if not self.token:
        return None
    headers = {"Authorization": f"Bearer {self.token}"}
    with self.client.get(
        path,
        name=name or path,
        headers=headers,
        catch_response=True,
        **kwargs,
    ) as resp:
        if resp.status_code == 429:
            resp.success()  # Expected under load (FR-007)
            return None
        elif resp.status_code == 401:
            resp.success()  # Session expired under load (FR-008)
            self.token = None
            return None
        elif resp.status_code >= 400:
            resp.failure(f"{resp.status_code}: {resp.text[:200]}")
            return None
        resp.success()
        return resp
```

---

### R-004: Aggregated Request Naming (FR-006)

**Decision**: Use the `name` parameter on all requests with parameterized paths to group stats by endpoint pattern.

**Rationale**: Without `name`, Locust creates separate stats entries for `/progress/SUBJ-001`, `/progress/SUBJ-002`, etc. The `name="/api/v1/progress/[subject]"` parameter groups them.

**Implementation**:
```python
# Without name: stats scattered across 100s of entries
self.client.get(f"/api/v1/progress/{subject_id}")

# With name: aggregated under one entry
self.client.get(
    f"/api/v1/progress/{subject_id}",
    name="/api/v1/progress/[subject]"
)
```

Naming convention for all parameterized endpoints:
| Endpoint | Name |
|----------|------|
| `/progress/{subject}` | `/api/v1/progress/[subject]` |
| `/progress/{subject}/tracks` | `/api/v1/progress/[subject]/tracks` |
| `/progress/{subject}/tracks/{track}` | `/api/v1/progress/[subject]/tracks/[track]` |
| `/progress/{subject}/tracks/{track}/units/{unit}` | `/api/v1/progress/[subject]/tracks/[track]/units/[unit]` |
| `/progress/{subject}/topics/{topic}/lessons` | `/api/v1/progress/[subject]/topics/[topic]/lessons` |
| `/leaderboard/{type}` | `/api/v1/leaderboard/[type]` |
| `/leaderboard/{type}/me` | `/api/v1/leaderboard/[type]/me` |

---

### R-005: Lesson Flow Simulation (LessonPlayer Profile)

**Decision**: Simulate full lesson lifecycle: start session → think time (3-10s) → end session with stage results → check wallet.

**Rationale**: FR-004 requires the lesson player to simulate the complete flow. The session endpoints are:
1. `POST /sessions/start` — body: `{"lesson_id": "...", "subject_id": "..."}`
2. Wait 3-10 seconds (simulating student completing lesson)
3. `POST /sessions/end` — body: `{"stages": [{"stage_id": "...", "time_spent": N, "fail_count": 0, "completed_at": "ISO", "items": [...]}]}`
4. `GET /wallet` — check XP update

**Key Detail**: `EndSessionRequest.stages` requires realistic stage data. For load testing, we send 1-3 stages with plausible `time_spent` values (3000-10000ms) and `fail_count` (0-2).

**Edge Case (FR-008)**: If session-end returns 401 (session expired during think time), treat as expected behavior.

---

### R-006: Config File Structure (FR-009)

**Decision**: Python file (`config.py`) with dicts/lists, as specified. Separate `config.example.py` for version control.

**Rationale**: Spec explicitly requires Python config format. This allows direct import by Locust user classes without parsing overhead.

**Structure**:
```python
# config.py — NOT committed to version control
HOST = "http://127.0.0.1:8002"

TEST_PLAYERS = [
    {"mobile": "+201000000001", "password": "test123"},
    {"mobile": "+201000000002", "password": "test123"},
    {"mobile": "+201000000003", "password": "test123"},
]

TEST_SUBJECTS = ["SUBJ-001", "SUBJ-002"]

TEST_LESSONS = [
    {"lesson_id": "LESSON-001", "subject_id": "SUBJ-001", "topic_id": "TOPIC-001", "stages": [...]},
    {"lesson_id": "LESSON-002", "subject_id": "SUBJ-001", "topic_id": "TOPIC-002", "stages": [...]},
]

# Scaling ladder stages (for documentation/reference)
SCALING_LADDER = [
    {"stage": 1, "users": 100, "spawn_rate": 10, "duration": "2m"},
    {"stage": 2, "users": 1000, "spawn_rate": 50, "duration": "5m"},
    {"stage": 3, "users": 10000, "spawn_rate": 200, "duration": "10m"},
    {"stage": 4, "users": 50000, "spawn_rate": 500, "duration": "15m"},
    {"stage": 5, "users": 100000, "spawn_rate": 1000, "duration": "15m"},
]
```

---

### R-007: Distributed Mode for 100k Users (FR-013)

**Decision**: Document distributed mode in README.md but do not implement orchestration. The suite works identically in both modes (Locust handles distribution transparently).

**Rationale**: A single machine can typically handle 5,000-10,000 Locust users (depending on hardware). For 100k, distributed mode is needed. Locust's built-in master/worker distribution requires no code changes — just CLI flags.

**Documentation to include**:
```bash
# Master (coordinates workers, runs web UI)
locust --master -f locustfile.py

# Workers (one per CPU core, on same or different machines)
locust --worker --master-host=<master-ip> -f locustfile.py

# Auto-spawn workers matching CPU count
locust --worker --master-host=<master-ip> -f locustfile.py --processes -1
```

**Hardware Estimation** (for README):
| Stage | Users | Workers Needed (est.) |
|-------|-------|-----------------------|
| 1-2   | 100-1k | 1 (single process) |
| 3     | 10k   | 2-4 workers |
| 4     | 50k   | 8-12 workers |
| 5     | 100k  | 16-24 workers |

---

### R-008: Think Time Configuration

**Decision**: Use `between(min, max)` for wait times, with profile-specific ranges reflecting real user behavior.

| Profile | Wait Time | Rationale |
|---------|-----------|-----------|
| DashboardUser | `between(3, 8)` | Dashboard polling — users check stats every few seconds |
| LessonPlayer | `between(5, 15)` | Lesson flow with in-task think time (3-10s session duration) |
| BrowserUser | `between(2, 6)` | Browsing is faster — users scan content hierarchy |
| LeaderboardChecker | `between(5, 10)` | Casual check-ins, less frequent interaction |

In-task think times (within a single `@task` method) use `time.sleep(random.uniform(min, max))` for the session duration simulation.

---

### R-009: Session-Expired Handling (FR-008)

**Decision**: Treat 401 responses on session-end as expected behavior under load, using `catch_response=True` + `resp.success()`.

**Rationale**: Under heavy load, a session's 1-hour TTL may expire between session-start and session-end if the think time + request queue delay exceeds the TTL. This is a valid production scenario, not a test failure.

**Implementation**: Handled in the shared `api_post()` helper alongside 429 handling.

---

### R-010: Endpoint Coverage by User Profile

**DashboardUser (40%)**:
- `GET /api/v1/profile` — hero section (level, XP, avatar)
- `GET /api/v1/profile/stats` — streak, items learned, total XP
- `GET /api/v1/profile/activity` — 7-day XP chart
- `GET /api/v1/profile/mastery` — FSRS mature/learning counts
- `GET /api/v1/wallet` — current XP and streak
- `GET /api/v1/progress` — subject summaries

**LessonPlayer (35%)**:
- `GET /api/v1/progress` — pick a subject
- `GET /api/v1/progress/{subject}/topics/{topic}/lessons` — pick a lesson
- `POST /api/v1/sessions/start` — start lesson
- (think time 3-10s)
- `POST /api/v1/sessions/end` — submit stage results
- `GET /api/v1/wallet` — check XP reward

**BrowserUser (15%)**:
- `GET /api/v1/progress` — subject list
- `GET /api/v1/progress/{subject}/tracks` — tracks in subject
- `GET /api/v1/progress/{subject}/tracks/{track}` — units in track
- `GET /api/v1/progress/{subject}/tracks/{track}/units/{unit}` — topics in unit

**LeaderboardChecker (10%)**:
- `GET /api/v1/leaderboard/daily` — top 20
- `GET /api/v1/leaderboard/daily/me` — my rank + neighbors
- `GET /api/v1/leaderboard/weekly` — top 20
- `GET /api/v1/leaderboard/weekly/me` — my rank + neighbors
