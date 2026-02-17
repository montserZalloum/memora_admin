# Phase 9: E2E Journey Tests — Complete Implementation Plan

## Executive Summary

26 end-to-end journey tests covering every business flow in the Memora system (excluding vouchers). Tests hit the REAL live server at `https://x.conanacademy.com`, use REAL Redis, and verify REAL Frappe DB records. Each test is a multi-step stateful journey where one actor (player or admin) goes through a complete business flow, with every step sharing state from the previous step.

---

## Architecture

### Execution Model

```
Test Process (pytest)
  │
  ├── httpx.AsyncClient ──→ https://x.conanacademy.com/api/v1/...
  │                           (real nginx → gunicorn → FastAPI)
  │
  ├── redis.asyncio ──→ redis://127.0.0.1:13000
  │                       (real Redis, direct verification)
  │
  └── frappe.client ──→ bench run-tests context
                          (real MariaDB verification via Frappe ORM)
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| HTTP client | Real HTTP to live server | Tests full stack (nginx, gunicorn, middleware) |
| OTP bypass | Hardcoded `"1111"` | `StaticOTPProvider` always returns `"1111"` (confirmed in `otp.py`) |
| Time mocking | Direct Redis `HSET streak_date` manipulation | Server runs in separate process, `freezegun` won't reach it |
| DB verification | Frappe API (`frappe.get_doc`, `frappe.db.get_value`) | Respects hooks, validation, and ORM layer |
| Test data | Fresh unique phone per test, cleanup after | Idempotent, isolated, no collision |
| Payment approval | Direct Frappe doc manipulation or instruct user | No admin-approve endpoint exists |
| Plan | `PLAN-00052` (existing) | Already has subjects, tracks, lessons in DB |
| Admin creds | `zmontsaer@gmail.com` / `admin` | Known test admin account |

---

## File Structure

```
fastapi_app/tests/e2e/
  __init__.py
  conftest.py                          # Shared fixtures, helpers, cleanup
  helpers.py                           # Reusable step functions
  constants.py                         # Test configuration constants

  # Player journeys
  test_j01_registration_to_first_lesson.py
  test_j02_login_and_replay.py
  test_j03_free_vs_paid_content.py
  test_j04_password_reset.py
  test_j05_token_refresh_and_session.py
  test_j06_profile_and_avatar.py
  test_j07_leaderboard_after_lessons.py
  test_j08_review_spaced_repetition.py
  test_j09_device_management.py
  test_j10_purchase_and_catalog.py

  # Multi-day scenarios
  test_j11_streak_three_consecutive_days.py
  test_j12_streak_break_and_reset.py
  test_j13_replay_does_not_maintain_streak.py

  # Admin journeys
  test_j14_admin_grant_revoke_access.py
  test_j15_admin_view_player_data.py
  test_j16_admin_role_enforcement.py
  test_j17_admin_purchase_approval.py

  # Consistency & sync
  test_j18_wallet_sync_redis_to_db.py
  test_j19_progress_sync_redis_to_db.py
  test_j20_interaction_buffer_flush.py

  # Edge cases & concurrency
  test_j21_concurrent_lesson_completions.py
  test_j22_double_end_session.py
  test_j23_session_force_close_on_new_start.py
  test_j24_hydration_after_key_eviction.py
  test_j25_webhook_idempotency.py
  test_j26_logout_and_session_invalidation.py
```

---

## Infrastructure: `conftest.py`

### Constants (`constants.py`)

```python
# Server
BASE_URL = "https://x.conanacademy.com"
API_V1 = f"{BASE_URL}/api/v1"
REDIS_URL = "redis://127.0.0.1:13000"

# Test plan (existing in DB)
TEST_PLAN_ID = "PLAN-00052"

# Admin credentials
ADMIN_EMAIL = "zmontsaer@gmail.com"
ADMIN_PASSWORD = "admin"

# OTP (StaticOTPProvider hardcodes "1111")
STATIC_OTP = "1111"

# Default test player fields
DEFAULT_PASSWORD = "TestPass123!"
DEFAULT_GENDER = "Male"
DEFAULT_AVATAR = "pre"

# Redis key prefixes (for verification)
REDIS_PREFIX = "memora:"
WALLET_KEY = f"{REDIS_PREFIX}wallet:"
SESSION_KEY = f"{REDIS_PREFIX}session:"
ACCESS_KEY = f"{REDIS_PREFIX}access:"
PROGRESS_KEY = f"{REDIS_PREFIX}progress:"
GAMESESSION_KEY = f"{REDIS_PREFIX}gamesession:"
DIRTY_WALLETS_KEY = "memora:dirty:wallets"
DIRTY_PROGRESS_KEY = "memora:dirty:progress"
INTERACTION_BUFFER_KEY = "memora:buffer:interactions"
```

### Core Fixtures (`conftest.py`)

```python
import pytest
import httpx
import redis.asyncio as aioredis
import uuid
import json
import asyncio


# ── HTTP client ──────────────────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def http():
    """Real HTTP client to live server. Session-scoped for connection reuse."""
    async with httpx.AsyncClient(
        base_url=API_V1,
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        # Smoke test
        r = await client.get("/health/live")
        assert r.status_code == 200, f"Server not reachable: {r.text}"
        yield client


# ── Redis client ─────────────────────────────────────────────────
@pytest.fixture(scope="session")
async def redis_client():
    """Real Redis client for direct verification."""
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


# ── Unique phone generator ──────────────────────────────────────
@pytest.fixture
def unique_phone():
    """Generate unique Jordanian phone number (9 digits)."""
    return f"7{uuid.uuid4().hex[:8]}"  # 9 digits, starts with 7


# ── Registration options (cached per session) ────────────────────
@pytest.fixture(scope="session")
async def reg_options(http):
    """Fetch registration options once (grades, plans, seasons)."""
    r = await http.get("/auth/registration-options")
    assert r.status_code == 200
    return r.json()


# ── Discover test subject & lesson (cached per session) ──────────
@pytest.fixture(scope="session")
async def test_content(http, admin_token):
    """Discover a real subject_id and lesson_id from PLAN-00052's hierarchy.
    
    Steps:
    1. GET /plans/PLAN-00052/manifest → subjects list
    2. Pick first subject → GET hierarchy from Frappe
    3. Extract first lesson_id, bit_index, track_id, etc.
    
    Returns dict with: subject_id, lesson_id, lesson_id_2 (for replay),
    free_lesson_id (if exists), track_id, bit_index, hierarchy
    """
    # Get plan manifest
    r = await http.get(
        f"/plans/{TEST_PLAN_ID}/manifest",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    # NOTE: plan manifest may not require auth - agent should handle both cases
    # If 401, use admin token. If 200 without auth, fine.
    
    manifest = r.json()
    subjects = manifest.get("subjects", [])
    assert len(subjects) > 0, f"PLAN-00052 has no subjects"
    
    subject_id = subjects[0]["id"]
    
    # Fetch hierarchy via FastAPI (needs auth)
    # The hierarchy is cached or fetched from Frappe
    # We need to get it to find lesson IDs
    # Use admin token to access progress endpoint which loads hierarchy
    
    return {
        "plan_id": TEST_PLAN_ID,
        "subject_id": subject_id,
        "subjects": subjects,
        # lesson_id, bit_index etc. will be discovered at runtime
        # by the helper that navigates the hierarchy
    }


# ── Player registration helper ──────────────────────────────────
@pytest.fixture
async def registered_player(http, redis_client, unique_phone, reg_options):
    """Register a fresh player and return full context.
    
    Yields dict with:
      player_id, phone, token, refresh_token, family_id,
      plan_id, grade, display_name
    
    Cleanup: deletes player from Frappe + cleans Redis keys.
    """
    phone = unique_phone
    
    # Discover grade and plan from reg_options
    grades = reg_options.get("grades", [])
    plans = reg_options.get("plans", [])
    
    # Use PLAN-00052 if available in options, else first plan
    plan_id = TEST_PLAN_ID
    grade_id = None
    major_id = None
    
    for p in plans:
        if p["name"] == TEST_PLAN_ID:
            grade_id = p.get("grade")
            major_id = p.get("major")
            break
    
    if not grade_id and grades:
        grade_id = grades[0]["name"]
    
    display_name = f"E2E-Test-{phone[-4:]}"
    
    # Step 1: Register
    r = await http.post("/auth/player/register", json={
        "mobile": phone,
        "password": DEFAULT_PASSWORD,
        "display_name": display_name,
        "gender": DEFAULT_GENDER,
        "grade": grade_id,
        "plan": plan_id,
        "major": major_id,
    })
    assert r.status_code == 200, f"Register failed: {r.text}"
    pending_id = r.json()["pending_id"]
    
    # Step 2: Verify OTP (hardcoded "1111")
    device_id = f"e2e-device-{uuid.uuid4().hex[:8]}"
    r = await http.post("/auth/player/register/verify", json={
        "pending_id": pending_id,
        "otp": STATIC_OTP,
    }, headers={"X-Device-ID": device_id})
    assert r.status_code == 200, f"Verify failed: {r.text}"
    
    data = r.json()
    player_id = None
    
    # Extract player_id from JWT (decode without verification for extraction)
    import jwt as pyjwt
    token_payload = pyjwt.decode(
        data["access_token"], options={"verify_signature": False}
    )
    player_id = token_payload["sub"]
    
    ctx = {
        "player_id": player_id,
        "phone": phone,
        "token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "device_id": device_id,
        "plan_id": plan_id,
        "grade": grade_id,
        "display_name": display_name,
    }
    
    yield ctx
    
    # ── Cleanup ──
    # Delete Redis keys
    keys_to_clean = [
        f"{WALLET_KEY}{player_id}",
        f"{SESSION_KEY}{player_id}",
        f"{ACCESS_KEY}{player_id}",
        f"{GAMESESSION_KEY}{player_id}",
    ]
    for key in keys_to_clean:
        await redis_client.delete(key)
    
    # Remove from dirty sets
    await redis_client.srem(DIRTY_WALLETS_KEY, player_id)
    await redis_client.srem(DIRTY_PROGRESS_KEY, f"{player_id}:*")
    
    # Delete from Frappe (via bench API or direct)
    # NOTE: Agent should implement this via Frappe whitelisted API
    # or instruct user if no API exists


# ── Admin token (session-scoped) ─────────────────────────────────
@pytest.fixture(scope="session")
async def admin_token(http):
    """Login as admin and return access token."""
    r = await http.post("/auth/admin/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
    })
    assert r.status_code == 200, f"Admin login failed: {r.text}"
    return r.json()["access_token"]


# ── Authed HTTP helper ───────────────────────────────────────────
def authed(http_client, token):
    """Create headers dict with Bearer token."""
    return {"Authorization": f"Bearer {token}"}
```

### Reusable Step Functions (`helpers.py`)

```python
"""Reusable step functions for E2E journeys.

Each function performs one real HTTP call and returns parsed result.
These are the building blocks that journey tests compose.
"""

async def register_player(http, phone, password, display_name, gender, 
                          grade, plan, major=None):
    """Register → returns pending_id."""
    r = await http.post("/auth/player/register", json={
        "mobile": phone, "password": password,
        "display_name": display_name, "gender": gender,
        "grade": grade, "plan": plan, "major": major,
    })
    return r


async def verify_otp(http, pending_id, otp="1111", device_id=None):
    """Verify OTP → returns tokens + profile."""
    headers = {}
    if device_id:
        headers["X-Device-ID"] = device_id
    r = await http.post("/auth/player/register/verify", 
                        json={"pending_id": pending_id, "otp": otp},
                        headers=headers)
    return r


async def login_player(http, phone, password, device_id):
    """Login → returns tokens + profile."""
    r = await http.post("/auth/player/login",
                        json={"mobile": phone, "password": password},
                        headers={"X-Device-ID": device_id})
    return r


async def start_session(http, token, subject_id, lesson_id, device_id=None):
    """Start game session → returns session_id."""
    headers = {"Authorization": f"Bearer {token}"}
    if device_id:
        headers["X-Device-ID"] = device_id
    r = await http.post("/sessions/start",
                        json={"lesson_id": lesson_id, "subject_id": subject_id},
                        headers=headers)
    return r


async def end_session(http, token, stages=None):
    """End game session → returns xp_awarded, streak, is_replay."""
    if stages is None:
        stages = [{"stage_id": "stage-1", "time_spent": 5000, 
                    "fail_count": 0, "completed_at": "2026-02-17T10:00:00Z"}]
    r = await http.post("/sessions/end",
                        json={"stages": stages},
                        headers={"Authorization": f"Bearer {token}"})
    return r


async def get_wallet(http, token):
    """GET /wallet → returns {xp, streak}."""
    r = await http.get("/wallet",
                       headers={"Authorization": f"Bearer {token}"})
    return r


async def get_progress_summary(http, token):
    """GET /progress/ → returns subject summaries."""
    r = await http.get("/progress/",
                       headers={"Authorization": f"Bearer {token}"})
    return r


async def admin_grant_access(http, admin_token, player_id, content_keys):
    """Admin grants access to player."""
    r = await http.post("/access/grants",
                        json={"player_id": player_id, "content_keys": content_keys},
                        headers={"Authorization": f"Bearer {admin_token}"})
    return r


async def admin_revoke_access(http, admin_token, player_id, content_keys):
    """Admin revokes access from player."""
    r = await http.request("DELETE", "/access/grants",
                           json={"player_id": player_id, "content_keys": content_keys},
                           headers={"Authorization": f"Bearer {admin_token}"})
    return r
```

---

## Player Journeys (J01–J10)

---

### J01: Registration → First Lesson Completion

**File:** `test_j01_registration_to_first_lesson.py`

**Story:** A brand new student downloads the app, registers, and completes their very first lesson. We verify every side effect across the full stack.

```
Step | Action                              | Endpoint                        | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Fetch registration options           | GET /auth/registration-options   | 200, has grades[], plans[], seasons[]
 2   | Register with unique phone           | POST /auth/player/register       | 200, returns pending_id (string)
 3   | Verify OTP "1111"                    | POST /auth/player/register/verify| 200, returns access_token, refresh_token, profile
     |                                      |                                  | profile.xp == 0, profile.display_name matches
     |                                      |                                  | Headers: X-Device-ID sent
 4   | [Redis] Verify session created       | DIRECT REDIS                     | EXISTS memora:session:{player_id} == 1
     |                                      |                                  | JSON has fid (family_id) and plan_id
 5   | [Redis] Verify wallet initialized    | DIRECT REDIS                     | HGETALL memora:wallet:{player_id} → xp=0
 6   | [Redis] Verify device registered     | DIRECT REDIS                     | EXISTS memora:devices:{player_id} == 1
 7   | [Frappe] Verify player doc created   | frappe.db.exists("Memora Player Profile", player_id) | True
     |                                      | frappe.get_value(..., "mobile")  | matches phone
 8   | [Frappe] Verify wallet doc created   | frappe.db.exists("Memora Player Wallet", {"player": player_id}) | True
     |                                      | frappe.get_value(..., "total_xp")| == 0
 9   | Get plan manifest                    | GET /plans/{plan_id}/manifest    | 200, subjects[] not empty
10   | Start lesson session                 | POST /sessions/start             | 200, returns session_id, lesson_id
     |                                      | body: {lesson_id, subject_id}    | session_id is UUID format
11   | [Redis] Verify game session hash     | DIRECT REDIS                     | HGETALL memora:gamesession:{player_id}
     |                                      |                                  | has lesson_id, subject_id, started_at
     |                                      |                                  | TTL ≤ 3600s
12   | End lesson session                   | POST /sessions/end               | 200, success=True
     |                                      | body: {stages: [{stage_id,       | xp_awarded > 0
     |                                      |   time_spent, fail_count,        | is_replay == False
     |                                      |   completed_at}]}                | streak == 1 (first completion today)
13   | [Redis] Verify game session deleted  | DIRECT REDIS                     | EXISTS memora:gamesession:{player_id} == 0
14   | [Redis] Verify wallet updated        | DIRECT REDIS                     | HGET memora:wallet:{player_id} xp > 0
     |                                      |                                  | HGET streak == 1
     |                                      |                                  | HGET streak_date == today (Asia/Amman)
15   | [Redis] Verify progress bit set      | DIRECT REDIS                     | GETBIT memora:progress:{player}:{subject}:v{ver} {bit_idx} == 1
16   | [Redis] Verify dirty wallet flagged  | DIRECT REDIS                     | SISMEMBER memora:dirty:wallets {player_id} == 1
17   | [Redis] Verify dirty progress flagged| DIRECT REDIS                     | SISMEMBER memora:dirty:progress "{player}:{subject}:v{ver}" == 1
18   | [Redis] Verify interaction buffer    | DIRECT REDIS                     | LLEN memora:buffer:interactions >= 1
     |                                      |                                  | Last item JSON has player == player_id
19   | Get wallet via API                   | GET /wallet                      | 200, xp matches Redis, streak == 1
20   | Get progress summary                 | GET /progress/                   | 200, subject shows completed >= 1
```

**Shared State Flow:**
```
registration_options → {grade, plan, season}
  → register(phone, grade, plan) → {pending_id}
    → verify(pending_id, "1111") → {access_token, player_id}
      → start_session(subject_id, lesson_id) → {session_id}
        → end_session(stages) → {xp_awarded, streak}
          → get_wallet() → verify consistency
```

---

### J02: Login and Replay Detection

**File:** `test_j02_login_and_replay.py`

**Story:** Player logs in (not registers), completes a lesson they already did, system correctly detects replay and awards reduced XP.

**Prerequisite:** Uses `registered_player` fixture (already has 1 completed lesson from J01 pattern).

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Login with phone + password          | POST /auth/player/login          | 200, access_token, profile
     |                                      | Headers: X-Device-ID             | profile.xp == current XP (not 0)
 2   | [Redis] Old session replaced         | DIRECT REDIS                     | session:{player_id} has NEW family_id
 3   | Complete a lesson (first time)        | POST /sessions/start + end       | xp_awarded = normal, is_replay=False
 4   | Record XP after first completion     | GET /wallet                      | xp = xp_after_first
 5   | Start SAME lesson again              | POST /sessions/start             | 200 (allowed)
 6   | End SAME lesson again                | POST /sessions/end               | 200, is_replay == True
     |                                      |                                  | xp_awarded == replay_xp (reduced)
 7   | Verify replay XP is less             | GET /wallet                      | xp == xp_after_first + replay_xp
     |                                      |                                  | replay_xp < first_xp
 8   | [Redis] Streak unchanged on replay   | DIRECT REDIS                     | streak same as after step 3
     |                                      |                                  | streak_date unchanged
```

---

### J03: Free vs Paid Content Access

**File:** `test_j03_free_vs_paid_content.py`

**Story:** New player without any purchased subscriptions can access free content but gets 403 on paid content. Admin grants access, then player can access.

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register fresh player                | (fixture)                        | player has NO grants
 2   | [Redis] Verify empty access set      | DIRECT REDIS                     | SCARD memora:access:{player_id} == 0
 3   | Discover a FREE lesson               | via hierarchy / plan manifest     | lesson where is_free=True
 4   | Start free lesson                    | POST /sessions/start             | 200 OK (free content bypasses Gate 2)
 5   | End free lesson                      | POST /sessions/end               | 200 OK
 6   | Discover a PAID lesson               | via hierarchy / plan manifest     | lesson where is_free=False
 7   | Try start paid lesson                | POST /sessions/start             | 403 NO_ACCESS
 8   | Admin grants subject access          | POST /access/grants              | 200, granted >= 1
     |                                      | body: {player_id, ["SUB-{subj}"]}|
 9   | [Redis] Verify grant in access set   | DIRECT REDIS                     | SISMEMBER memora:access:{player_id} "SUB-{subject}" == 1
10   | Start paid lesson (now granted)      | POST /sessions/start             | 200 OK (access check passes)
11   | End paid lesson                      | POST /sessions/end               | 200 OK, xp_awarded > 0
12   | Check subscriptions endpoint         | GET /subscriptions               | 200, grants includes "SUB-{subject}"
```

**NOTE for agent:** If PLAN-00052 has no free content, the free lesson steps (3-5) should be skipped with `pytest.mark.skipif`, or the agent should create a test subject with `is_free` content.

---

### J04: Password Reset Flow

**File:** `test_j04_password_reset.py`

**Story:** Player forgets password, goes through the 3-step OWASP reset flow, then logs in with new password.

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register player (fixture)            |                                  | Has phone, password
 2   | Request password reset               | POST /auth/player/password-reset/request | 200, generic message
     |                                      | body: {mobile: phone}            | (anti-enumeration: same msg always)
 3   | Verify reset OTP "1111"              | POST /auth/player/password-reset/verify | 200, returns reset_token
     |                                      | body: {mobile, otp: "1111"}      |
 4   | Confirm with new password            | POST /auth/player/password-reset/confirm | 200, success message
     |                                      | body: {reset_token, new_password}|
 5   | Try login with OLD password          | POST /auth/player/login          | 401 Invalid credentials
 6   | Login with NEW password              | POST /auth/player/login          | 200, tokens returned
 7   | Try reuse reset_token                | POST /auth/player/password-reset/confirm | 401 (single-use token consumed)
 8   | [Redis] Old session invalidated      | DIRECT REDIS                     | session:{player_id} has new fid
 9   | Request reset for UNREGISTERED phone | POST /auth/player/password-reset/request | 200 (SAME generic msg)
     |                                      | body: {mobile: "999999999"}      | anti-enumeration verified
```

---

### J05: Token Refresh and Session Management

**File:** `test_j05_token_refresh_and_session.py`

**Story:** Player logs in, uses refresh token to get new access token, verifies session continuity, then second login invalidates first session.

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register + login player              | (fixture)                        | token_1, refresh_1, fid_1
 2   | Use access_token for wallet          | GET /wallet                      | 200 OK
 3   | Refresh token                        | POST /auth/refresh               | 200, new access_token_2
     |                                      | body: {refresh_token}            | same refresh_token returned (not rotated)
 4   | Use NEW access token                 | GET /wallet                      | 200 OK
 5   | Login AGAIN (new device)             | POST /auth/player/login          | 200, token_3, fid_2
     |                                      | headers: X-Device-ID = new-dev   |
 6   | [Redis] Session has new fid          | DIRECT REDIS                     | session:{player_id} → fid == fid_2 (not fid_1)
 7   | Try refresh with OLD refresh_token   | POST /auth/refresh               | 401 (fid mismatch → SESSION_SUPERSEDED)
 8   | Use OLD access_token for wallet      | GET /wallet                      | 401 (session fid doesn't match)
 9   | Use NEW access_token for wallet      | GET /wallet                      | 200 OK
```

---

### J06: Profile and Avatar Update

**File:** `test_j06_profile_and_avatar.py`

**Story:** Player views profile sections and updates avatar.

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register player (fixture)            |                                  |
 2   | Get hero section                     | GET /profile                     | 200, display_name matches
     |                                      |                                  | level >= 1, xp >= 0
 3   | Get stats                            | GET /profile/stats               | 200, streak, items_learned, total_xp
 4   | Get weekly activity                  | GET /profile/activity            | 200, days[] has 7 entries
 5   | Get mastery                          | GET /profile/mastery             | 200, mature/learning/new_items
 6   | Update avatar to "blonde"            | PUT /profile/avatar              | 200, avatar="blonde", success=True
     |                                      | body: {avatar: "blonde"}         |
 7   | [Frappe] Verify avatar in DB         | frappe.get_value("Memora Player Profile", player_id, "avatar") | == "blonde"
 8   | Get hero again                       | GET /profile                     | avatar == "blonde"
 9   | Try invalid avatar                   | PUT /profile/avatar              | 400 Invalid avatar option
     |                                      | body: {avatar: "nonexistent"}    |
```

---

### J07: Leaderboard After Multiple Lessons

**File:** `test_j07_leaderboard_after_lessons.py`

**Story:** Player completes 2 lessons, appears on leaderboard with correct XP.

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register + grant access (fixture)    |                                  |
 2   | Complete lesson 1                    | start + end session              | xp_1 awarded
 3   | Complete lesson 2 (different lesson) | start + end session              | xp_2 awarded
 4   | Get daily leaderboard               | GET /leaderboard/daily           | 200, player appears with xp_1 + xp_2
     |                                      | query: ?limit=10                 |
 5   | Get alltime leaderboard             | GET /leaderboard/alltime         | 200, player appears
 6   | Get my rank (daily)                  | GET /leaderboard/daily/me        | 200, rank >= 1
 7   | [Redis] Verify ZSET entries          | DIRECT REDIS                     | ZSCORE memora:lb:daily:{date} {player_id}
     |                                      |                                  | == xp_1 + xp_2
```

---

### J08: Review / Spaced Repetition Flow

**File:** `test_j08_review_spaced_repetition.py`

**Story:** Player completes a lesson (creating FSRS items), then checks review overview and submits reviews.

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register + complete a lesson         | (combined fixture)               | Has interaction items
 2   | [Wait] FSRS processor runs           | (trigger or wait ~2 min)         | Memory State records created
 3   | Get review overview                  | GET /reviews                     | 200, subjects[] may include test subject
 4   | Get due items for subject            | GET /reviews/{subject}           | 200, items[] (may be empty if not due)
 5   | Submit review batch                  | POST /reviews/{subject}/submit   | 200, xp_awarded == 3 (per session)
     |                                      | body: {items: [{item_id, fail_count}]} |
 6   | Verify wallet has review XP          | GET /wallet                      | xp increased by 3
```

**NOTE for agent:** FSRS processing is async (scheduled task). If items are not immediately due, the test should either trigger `process_fsrs_reviews()` directly or assert on the review overview structure only.

---

### J09: Device Management

**File:** `test_j09_device_management.py`

**Story:** Player logs in from multiple devices, hits device limit.

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register player (fixture)            |                                  | device-1 registered
 2   | Login with device-2                  | POST /auth/player/login          | 200 OK
     |                                      | X-Device-ID: device-2            |
 3   | Login with device-3                  | POST /auth/player/login          | 200 OK
     |                                      | X-Device-ID: device-3            |
 4   | [Redis] Check device count           | DIRECT REDIS                     | HLEN memora:devices:{player_id} == 3
 5   | Get max_devices from settings        | GET /settings/gamification       | max_devices_per_player value
 6   | Login beyond limit (if max < 5)      | POST /auth/player/login          | 429 DEVICE_LIMIT_EXCEEDED
     |                                      | X-Device-ID: device-N+1          | (or 200 if limit > current count)
 7   | Logout from device-2                 | POST /profile/logout             | 200, success=True
     |                                      | X-Device-ID: device-2            |
 8   | [Redis] Device removed               | DIRECT REDIS                     | device-2 no longer in hash
```

---

### J10: Purchase and Catalog Flow

**File:** `test_j10_purchase_and_catalog.py`

**Story:** Player views catalog, submits purchase request, admin approves, player gets access.

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register player (fixture)            |                                  |
 2   | Get product catalog                  | GET /catalog/                    | 200, products[] list
 3   | Pick first product                   | (from catalog response)          | product_grant_id, subjects[]
 4   | Submit purchase                      | POST /purchase/                  | 201, purchase request created
     |                                      | body: {product_grant_id,         |
     |                                      |  payment_method: "Manual-Admin"} |
 5   | [Redis] Pending set updated          | DIRECT REDIS                     | SISMEMBER memora:pending:{player_id} grant_id == 1
 6   | Catalog hides pending product        | GET /catalog/                    | product_grant_id NOT in response
 7   | Submit DUPLICATE purchase            | POST /purchase/                  | 409 already pending
 8   | [Manual/Frappe] Admin approves       | **NOTE: Instruct user or call    | Transaction status → Approved
     |                                      | Frappe API directly**            | Subscription created
 9   | [Redis] Access granted               | DIRECT REDIS                     | SISMEMBER memora:access:{player_id} "SUB-{subj}" == 1
10   | Player can start paid lesson         | POST /sessions/start             | 200 OK (was 403 before)
```

**NOTE for agent:** Step 8 (admin approval) requires either:
- A Frappe whitelisted API to approve transactions, OR
- Direct `frappe.get_doc("Memora Subscription Transaction", name)` + `.submit()`, OR
- Print instructions for the user to approve manually in Frappe desk

The agent should check if such an API exists. If not, mark step 8 as a manual intervention point and document what the user needs to do.

---

## Multi-Day Streak Scenarios (J11–J13)

### Time Manipulation Strategy

Since we can't freeze the server's clock, we manipulate Redis directly:

```python
async def simulate_streak_date(redis_client, player_id, date_str):
    """Set the player's streak_date to simulate time passing.
    
    The Lua STREAK_UPDATE_SCRIPT reads streak_date from the hash
    and compares it to 'today' and 'yesterday' (Asia/Amman timezone).
    By setting streak_date, we control the Lua script's branching.
    """
    key = f"memora:wallet:{player_id}"
    await redis_client.hset(key, "streak_date", date_str)
```

---

### J11: Streak — Three Consecutive Days

**File:** `test_j11_streak_three_consecutive_days.py`

```
Step | Action                              | Redis Manipulation               | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register + complete lesson           | (natural, today)                 | streak == 1, streak_date == today
 2   | Set streak_date to yesterday         | HSET wallet streak_date {yesterday} | —
 3   | Complete another lesson              | POST sessions/start + end        | streak == 2 (incremented from 1)
 4   | Set streak_date to yesterday again   | HSET wallet streak_date {yesterday} | —
 5   | Complete another lesson              | POST sessions/start + end        | streak == 3
 6   | Verify wallet                        | GET /wallet                      | streak == 3
 7   | [Redis] Verify streak_date           | HGET wallet streak_date          | == today
```

---

### J12: Streak Break and Reset

**File:** `test_j12_streak_break_and_reset.py`

```
Step | Action                              | Redis Manipulation               | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register + build streak to 5         | (simulate 5 consecutive days)    | streak == 5
 2   | Set streak_date to 3 days ago        | HSET wallet streak_date {3_days_ago} | —
 3   | Complete lesson                      | POST sessions/start + end        | streak == 1 (RESET, not 6)
 4   | Verify wallet                        | GET /wallet                      | streak == 1
 5   | [Redis] streak_date is today         | HGET wallet streak_date          | == today
```

---

### J13: Replay Does NOT Maintain Streak

**File:** `test_j13_replay_does_not_maintain_streak.py`

```
Step | Action                              | Redis Manipulation               | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register + complete lesson           | (natural)                        | streak == 1, streak_date == today
 2   | Set streak_date to yesterday         | HSET wallet streak_date {yesterday} | Simulates "tomorrow"
 3   | REPLAY same lesson                   | POST sessions/start + end        | is_replay == True
     |                                      |                                  | streak stays at 1 (NOT incremented)
 4   | [Redis] streak_date unchanged        | HGET wallet streak_date          | == yesterday (NOT today)
 5   | Complete NEW lesson (non-replay)      | POST sessions/start + end        | streak == 2 (NOW increments)
     |                                      |                                  | streak_date == today
```

---

## Admin Journeys (J14–J17)

### J14: Admin Grant and Revoke Access

**File:** `test_j14_admin_grant_revoke_access.py`

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register player (no grants)          | (fixture)                        |
 2   | Player tries paid lesson             | POST /sessions/start             | 403 NO_ACCESS
 3   | Admin grants SUB-{subject}           | POST /access/grants              | 200, granted == 1
     |                                      | auth: admin_token                |
 4   | Admin lists player grants            | GET /access/grants/{player_id}   | 200, grants includes "SUB-{subject}"
 5   | Player starts paid lesson            | POST /sessions/start             | 200 OK
 6   | Admin revokes SUB-{subject}          | DELETE /access/grants            | 200, revoked == 1
 7   | [Redis] Grant removed                | DIRECT REDIS                     | SISMEMBER == 0
 8   | Player tries paid lesson again       | POST /sessions/start             | 403 NO_ACCESS
 9   | Admin grants track-level             | POST /access/grants              | 200, "TRK-{track_id}" granted
     |                                      | body: ["TRK-{track}"]            |
10   | Player starts lesson in that track   | POST /sessions/start             | 200 OK (track grant works)
```

---

### J15: Admin Views Player Data

**File:** `test_j15_admin_view_player_data.py`

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register player + complete lesson    | (fixture)                        | Has XP > 0
 2   | Admin views player wallet            | GET /wallet/{player_id}          | 200, xp > 0, streak >= 0
     |                                      | auth: admin_token                |
 3   | Admin views player grants            | GET /access/grants/{player_id}   | 200, returns grants list
 4   | Values match player's own wallet     | GET /wallet (player's token)     | Same xp, same streak
```

---

### J16: Admin Role Enforcement

**File:** `test_j16_admin_role_enforcement.py`

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register player (fixture)            |                                  | Non-admin token
 2   | Player tries admin grant             | POST /access/grants              | 403 Forbidden
     |                                      | auth: player_token               |
 3   | Player tries admin revoke            | DELETE /access/grants            | 403 Forbidden
 4   | Player tries view other wallet       | GET /wallet/{other_player_id}    | 403 Forbidden
 5   | Player tries admin list grants       | GET /access/grants/{other_id}    | 403 Forbidden
 6   | Admin CAN do all above               | (same endpoints, admin_token)    | 200 OK for all
```

---

### J17: Admin Purchase Approval Flow

**File:** `test_j17_admin_purchase_approval.py`

```
Step | Action                              | Endpoint                         | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register player                      | (fixture)                        |
 2   | Player submits purchase              | POST /purchase/                  | 201
 3   | [Frappe] Transaction created         | frappe.db.exists("Memora Subscription Transaction", ...) | True
     |                                      | status == "Pending Approval"     |
 4   | [Frappe] Admin approves transaction  | **Direct Frappe doc manipulation**| status → "Approved"
     |                                      | doc.status = "Approved"          | Triggers on_update hook
     |                                      | doc.save()                       | Which creates Subscription
 5   | [Redis] Access key granted           | SISMEMBER memora:access:{player} | == 1 (hook fired)
 6   | Player can now access content        | POST /sessions/start             | 200 OK
 7   | [WebSocket] Notification sent        | (verify via Redis pubsub or skip)| subscription_update message
```

**NOTE for agent:** If step 4 cannot be done programmatically (no API), print instructions:
```
⚠️ MANUAL STEP REQUIRED:
Go to https://x.conanacademy.com/app/memora-subscription-transaction/{name}
Set status to "Approved" and save.
Press Enter to continue...
```

---

## Consistency & Sync Tests (J18–J20)

### J18: Wallet Sync Redis → MariaDB

**File:** `test_j18_wallet_sync_redis_to_db.py`

```
Step | Action                              | Target                           | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register + complete lesson           |                                  | xp > 0 in Redis
 2   | [Redis] Confirm dirty flag           | SISMEMBER dirty:wallets          | == 1
 3   | Trigger sync_dirty_wallets()         | from memora_admin.tasks.sync import sync_dirty_wallets | Function call
     |                                      | sync_dirty_wallets()             |
 4   | [Frappe] Verify DB updated           | frappe.get_value("Memora Player Wallet", | total_xp == Redis xp
     |                                      |   {"player": player_id}, "total_xp")    | current_streak == Redis streak
 5   | [Redis] Dirty flag cleared           | SISMEMBER dirty:wallets          | == 0
 6   | [Frappe] dirty_flag cleared          | frappe.get_value(..., "dirty_flag") | == 0
 7   | [Frappe] last_sync_at populated      | frappe.get_value(..., "last_sync_at") | not None
```

---

### J19: Progress Sync Redis → MariaDB

**File:** `test_j19_progress_sync_redis_to_db.py`

```
Step | Action                              | Target                           | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register + complete lesson           |                                  | Progress bit set in Redis
 2   | [Redis] Get bitmap hex               | GET memora:progress:{p}:{s}:v{v} | Non-empty bytes
 3   | [Redis] Confirm dirty progress       | SISMEMBER dirty:progress         | == 1
 4   | Trigger sync_dirty_progress()        | sync_dirty_progress()            |
 5   | [Frappe] Verify Structure Progress   | frappe.get_value("Memora Structure Progress", | passed_lessons_bitset == hex
     |                                      |   {"player": p, "subject": s}, "passed_lessons_bitset") |
 6   | [Frappe] Completion % correct        | completion_percentage            | > 0
 7   | [Redis] Dirty flag cleared           | SISMEMBER dirty:progress         | == 0
```

---

### J20: Interaction Buffer Flush

**File:** `test_j20_interaction_buffer_flush.py`

```
Step | Action                              | Target                           | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────── | ──────────────────────────────────────
 1   | Register + complete lesson with stages|                                 | Interactions pushed to buffer
 2   | [Redis] Buffer has items             | LLEN memora:buffer:interactions  | >= 1
 3   | Count buffer before flush            |                                  | count_before
 4   | Trigger flush_interaction_buffer()   | flush_interaction_buffer()        |
 5   | [Redis] Buffer trimmed              | LLEN memora:buffer:interactions  | < count_before
 6   | [Frappe] Interaction Log created     | frappe.get_all("Memora Interaction Log", | count >= 1
     |                                      |   filters={"player": player_id}) | lesson matches
```

---

## Edge Cases & Concurrency (J21–J26)

### J21: Concurrent Lesson Completions (Two Players)

**File:** `test_j21_concurrent_lesson_completions.py`

```
Step | Action                              | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────────────
 1   | Register player_A and player_B       | Two separate players
 2   | Both start sessions (different lessons)| Both get 200
 3   | Both end sessions concurrently       | asyncio.gather(end_A, end_B)
     |                                      | Both return 200
 4   | Verify player_A wallet               | xp_A correct, not cross-contaminated
 5   | Verify player_B wallet               | xp_B correct, not cross-contaminated
 6   | Verify leaderboard                   | Both appear with correct individual XP
```

---

### J22: Double End Session

**File:** `test_j22_double_end_session.py`

```
Step | Action                              | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────────────
 1   | Register + start session              |
 2   | End session                           | 200, success
 3   | End session AGAIN (no active session) | 403 NO_ACTIVE_SESSION
 4   | XP not double-awarded                 | Wallet xp same as after step 2
```

---

### J23: Session Force-Close on New Start

**File:** `test_j23_session_force_close_on_new_start.py`

```
Step | Action                              | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────────────
 1   | Register + start session (lesson_A)   | session_id_1
 2   | [Redis] Session exists               | gamesession:{player} has lesson_A
 3   | Start NEW session (lesson_B)          | 200, session_id_2 (different from _1)
 4   | [Redis] Session is now lesson_B      | gamesession:{player} has lesson_B
 5   | End session                           | 200, lesson_id matches lesson_B (not A)
 6   | Get current session                   | 404 (session ended)
```

---

### J24: Hydration After Redis Key Eviction

**File:** `test_j24_hydration_after_key_eviction.py`

```
Step | Action                              | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────────────
 1   | Register + complete lesson            | xp > 0 in Redis
 2   | Trigger sync (wallet to DB)           | DB has correct xp
 3   | DELETE Redis wallet key               | HGETALL returns empty
 4   | GET /wallet                           | 200, xp == DB value (hydrated from Frappe)
 5   | [Redis] Wallet re-populated           | HGET xp == DB value
 6   | Complete another lesson               | xp correctly ADDED to hydrated base (not reset to 0)
 7   | Final wallet check                    | xp == original + new (FINDING-01 regression test)
```

---

### J25: Webhook Idempotency

**File:** `test_j25_webhook_idempotency.py`

```
Step | Action                              | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────────────
 1   | Register player                       |
 2   | Send payment webhook                  | POST /webhooks/payment
     |                                      | body: {event_id, player_id, product_grant_id, ...}
     |                                      | 200, status=="accepted"
 3   | Wait for background processing        | sleep(2)
 4   | [Redis] Idempotency key exists        | EXISTS memora:webhook:{event_id} == 1
 5   | Send SAME webhook again              | 200, status=="already_processed"
 6   | Verify no duplicate subscriptions     | Only 1 subscription created in Frappe
```

---

### J26: Logout and Session Invalidation

**File:** `test_j26_logout_and_session_invalidation.py`

```
Step | Action                              | Assertions
──── | ─────────────────────────────────── | ──────────────────────────────────────
 1   | Register player (fixture)             | Has valid token
 2   | Verify session works                  | GET /wallet → 200
 3   | Logout                                | POST /profile/logout → 200
     |                                      | X-Device-ID: device-1            |
 4   | [Redis] Session may be deleted        | Session key may be cleared
 5   | Try using old token                   | GET /wallet → 401 (session gone)
 6   | Login again                           | POST /auth/player/login → 200
 7   | New token works                       | GET /wallet → 200
```

---

## Test Discovery & Content Resolution

### Problem
Tests need real `subject_id`, `lesson_id`, and `bit_index` values that exist in the hierarchy for `PLAN-00052`. These cannot be hardcoded because they depend on content that may change.

### Solution: Runtime Discovery Helper

```python
async def discover_test_content(http, token, plan_id="PLAN-00052"):
    """Discover usable content from the plan hierarchy.
    
    Returns dict with:
      - subject_id: First subject in plan
      - lesson_id: First lesson in first track/unit/topic
      - lesson_id_2: Second lesson (for replay vs non-replay tests)
      - free_lesson_id: A free lesson (or None)
      - paid_lesson_id: A paid lesson (or None)
      - track_id: Track containing lesson_id
      - bit_index: Bit index for lesson_id in progress bitmap
      - hierarchy_version: Bitmap version
    """
    # 1. Get plan manifest
    r = await http.get(f"/plans/{plan_id}/manifest",
                       headers={"Authorization": f"Bearer {token}"})
    manifest = r.json()
    subject = manifest["subjects"][0]
    subject_id = subject["id"]
    
    # 2. Get hierarchy for subject (via progress endpoint which loads it)
    r = await http.get(f"/progress/{subject_id}/tracks",
                       headers={"Authorization": f"Bearer {token}"})
    # Parse tracks → units → topics → lessons
    # Extract first two lesson IDs and their metadata
    
    return {
        "subject_id": subject_id,
        "lesson_id": "...",  # Discovered at runtime
        "lesson_id_2": "...",
        # etc.
    }
```

**Agent instruction:** The implementing agent must:
1. First call `GET /plans/PLAN-00052/manifest` to discover subjects
2. For each subject, navigate the hierarchy to find lesson IDs
3. If PLAN-00052 lacks sufficient content (need at minimum 2 lessons, 1 free + 1 paid ideally), create a minimal test subject with the required structure

---

## pyproject.toml Addition

```toml
[tool.pytest.ini_options]
testpaths = ["fastapi_app/tests"]
asyncio_mode = "auto"
markers = [
    "e2e: end-to-end journey tests (real server, real Redis, real DB)",
    "slow: marks tests as slow (>30s)",
    "smoke: critical subset for CI smoke tests",
    "admin: admin journey tests",
    "streak: multi-day streak tests",
    "sync: Redis-to-DB sync tests",
]
```

---

## Execution Commands

```bash
# All E2E tests
cd /home/corex/aurevia-bench/apps/memora_admin
python3 -m pytest fastapi_app/tests/e2e/ -v --tb=short -m e2e

# Player journeys only
python3 -m pytest fastapi_app/tests/e2e/ -v -k "j01 or j02 or j03 or j04 or j05 or j06 or j07 or j08 or j09 or j10"

# Admin journeys only
python3 -m pytest fastapi_app/tests/e2e/ -v -m admin

# Streak tests only
python3 -m pytest fastapi_app/tests/e2e/ -v -m streak

# Sync/consistency tests
python3 -m pytest fastapi_app/tests/e2e/ -v -m sync

# Smoke (critical subset for CI)
python3 -m pytest fastapi_app/tests/e2e/ -v -m smoke
# Smoke = J01, J04, J14, J18

# Single journey
python3 -m pytest fastapi_app/tests/e2e/test_j01_registration_to_first_lesson.py -v
```

---

## Dependencies to Install

```bash
pip install pytest "pytest-asyncio>=0.23,<1.0" httpx PyJWT --break-system-packages
```

---

## Summary Table

| # | Journey | Category | Steps | Key Risk Tested |
|---|---------|----------|-------|-----------------|
| J01 | Registration → First Lesson | Player | 20 | Full registration + lesson flow, all side effects |
| J02 | Login + Replay | Player | 8 | Replay detection, reduced XP, streak non-increment |
| J03 | Free vs Paid Content | Player | 12 | Gate 2 access control, admin grant enables access |
| J04 | Password Reset | Player | 9 | 3-step OWASP flow, single-use token, anti-enumeration |
| J05 | Token Refresh + Session | Player | 9 | Refresh rotation, session supersede on re-login |
| J06 | Profile + Avatar | Player | 9 | Profile sections, avatar update + Frappe sync |
| J07 | Leaderboard | Player | 7 | XP accumulation on ZSET, rank calculation |
| J08 | Reviews / FSRS | Player | 6 | Spaced repetition flow, review XP |
| J09 | Device Management | Player | 8 | Multi-device login, limit enforcement, removal |
| J10 | Purchase + Catalog | Player | 10 | Purchase → pending → approval → access flow |
| J11 | Streak 3 Days | Streak | 7 | Consecutive day increment via Redis manipulation |
| J12 | Streak Break | Streak | 5 | Missed day resets streak to 1 |
| J13 | Replay No Streak | Streak | 5 | Replay does not advance streak_date |
| J14 | Admin Grant/Revoke | Admin | 10 | Subject + track level grants, immediate effect |
| J15 | Admin View Player | Admin | 4 | Cross-verify admin vs player wallet reads |
| J16 | Role Enforcement | Admin | 6 | Players blocked from all admin endpoints |
| J17 | Purchase Approval | Admin | 7 | Full purchase → approve → access lifecycle |
| J18 | Wallet Sync | Sync | 7 | Redis dirty flag → DB write → flag clear |
| J19 | Progress Sync | Sync | 7 | Bitmap → hex → Structure Progress upsert |
| J20 | Interaction Flush | Sync | 6 | Buffer → Interaction Log → LTRIM |
| J21 | Concurrent Players | Edge | 6 | Two players complete simultaneously, no cross-contamination |
| J22 | Double End | Edge | 4 | Second end_session gets 403, no double XP |
| J23 | Force Close | Edge | 6 | New session silently closes old one |
| J24 | Hydration | Edge | 7 | FINDING-01 regression: XP not reset after key eviction |
| J25 | Webhook Idempotency | Edge | 6 | Duplicate webhook doesn't create duplicate grants |
| J26 | Logout | Edge | 7 | Session invalidation, token rejection post-logout |
| **Total** | | | **~198 steps** | **26 journeys** |

---

## Risks & Findings to Watch

| ID | Finding | Test That Catches It |
|----|---------|---------------------|
| FINDING-01 | XP resets to 0 when hydration fails | J24 (hydration after eviction) |
| FINDING-03 | Stats double-count on cold start race | J21 (concurrent completions) |
| Race | Two sessions ending simultaneously | J21 |
| Data Loss | Dirty flag cleared before DB write succeeds | J18, J19 |
| Anti-Enum | Password reset leaks phone existence | J04 step 9 |
| Idempotency | Webhook processed twice | J25 |
| Session Hijack | Old token works after re-login | J05 steps 7-8 |

---

## Implementation Order (Recommended)

```
Phase 9a: Infrastructure (conftest, helpers, constants, content discovery)
Phase 9b: J01 (registration → first lesson) — validates entire setup works
Phase 9c: J04, J05 (auth flows — password reset, token refresh)
Phase 9d: J02, J03, J14 (lesson replay, access control, admin grants)
Phase 9e: J11-J13 (streak scenarios)
Phase 9f: J18-J20 (sync consistency)
Phase 9g: J21-J26 (edge cases)
Phase 9h: J06-J10, J15-J17 (remaining journeys)
```

Start with J01 because it validates the entire infrastructure (HTTP client, Redis, OTP, registration, sessions, wallet, progress). If J01 passes, everything else builds on proven foundations.