# Player App Agent Documentation

This document provides comprehensive guidance for the AI agent responsible for building, maintaining, and extending the **Player App** (React frontend) for the Memora educational platform.

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [Tech Stack](#tech-stack)
4. [API Reference](#api-reference)
5. [Authentication & Authorization](#authentication--authorization)
6. [Key Features](#key-features)
7. [Development Setup](#development-setup)
8. [Common User Workflows](#common-user-workflows)
9. [Error Handling](#error-handling)
10. [Performance Targets](#performance-targets)
11. [Design Patterns](#design-patterns)

---

## Overview

### What is the Player App?

The **Player App** is a React-based mobile and web frontend that enables students to:
- **Learn**: Access educational content organized in subjects, tracks, units, topics, and lessons
- **Track Progress**: See real-time progress through interactive bitmap-based tracking
- **Earn Rewards**: Accumulate XP, maintain streaks, and earn hearts/bonuses
- **Compete**: View leaderboards (daily, weekly, all-time) and track their rank
- **Purchase Content**: Browse and buy products/bundles of subjects
- **Manage Sessions**: Start lessons, complete stages, track session state

### Core Value Proposition

**Students can track their learning progress and earn instant rewards with sub-second response times, even at 100K concurrent users.**

### Platform Maturity

- **v1.0 - v1.3**: Core MVP (authentication, progress, gamification, profiles, device management)
- **v1.4**: Product Store (catalog, purchases, approval workflow)
- **Status**: Actively shipping; 23 phases completed, 69 plans executed
- **Release Date**: Launched 2026-02-02 (v1.0)

---

## System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Player App (React)                       │
│  Mobile (iOS/Android) + Web • TypeScript • <500ms UI load   │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTPS
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              Nginx Reverse Proxy (Port 80/443)               │
│  Routes: /api/v1/* → FastAPI | /api/method/* → Frappe      │
└────────────────────┬────────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
┌──────────────────┐      ┌──────────────────┐
│  FastAPI (8002)  │      │  Frappe (8000)   │
│  Game API        │      │  Admin Panel     │
│  <20ms responses │      │  Content Mgmt    │
└────────┬─────────┘      └──────────────────┘
         │
    ┌────┴──────┐
    ▼           ▼
 ┌──────┐    ┌──────────┐
 │Redis │    │ MariaDB  │
 │Cache │    │ Database │
 │Hot   │    │Cold      │
 │Data  │    │Storage   │
 └──────┘    └──────────┘
```

### Data Flow for Key Operations

**1. Login & Authentication:**
```
Player → POST /api/v1/auth/login
  (email/mobile + password + device_id)
  → Frappe credential verification
  → Device registration (3-device max)
  → JWT tokens (access + refresh)
  → Response includes profile data & XP
```

**2. Lesson Completion:**
```
Player → POST /api/v1/sessions/{session_id}/complete
  (stage_id, attempts, hearts_remaining)
  → Lua script (4 Redis ops in <10ms):
     - Update progress bitmap
     - Check unlock state
     - Award XP + streak bonus
     - Update wallet
  → Event published to sync queue
  → 1-min background task persists to MariaDB
```

**3. Browse Store:**
```
Player → GET /api/v1/catalog
  → FastAPI queries Redis cache (per-plan)
  → Filters out purchased products
  → Filters out pending transactions
  → Returns <100ms (cached)
```

**4. Progress Tracking:**
```
Player → GET /api/v1/progress/{subject_id}
  → Frappe hierarchy API (1-hour cache)
  → Redis stats hash (O(1) completion counts)
  → Response includes tracks, units, topics, lessons
  → Shows unlock state for each lesson
```

---

## Tech Stack

### Frontend (Player App)

- **Framework**: React 18+ with TypeScript
- **State Management**: (Your choice - TBD)
- **HTTP Client**: Axios or Fetch
- **Styling**: (Your choice - TBD)
- **Mobile**: React Native or Expo (optional)
- **Authentication**: JWT tokens (access + refresh)

### Backend (Memora Platform)

**Game API:**
- **Framework**: FastAPI (Python 3.10+)
- **Async**: asyncio + asyncpg
- **Caching**: Redis async client
- **Serialization**: Pydantic models
- **Logging**: structlog (JSON structured)

**Admin/Content:**
- **Framework**: Frappe v15 (ERPNext ecosystem)
- **ORM**: Frappe Document Model
- **Database**: MariaDB (InnoDB)
- **DocTypes**: 32 custom types

**Infrastructure:**
- **Reverse Proxy**: Nginx (routes /api/v1/* → FastAPI, /api/method/* → Frappe)
- **Cache**: Redis 6+ (async connection pooling)
- **Database**: MariaDB 10.5+
- **CDN**: Mock filesystem layer (swappable for Cloudflare R2)

---

## API Reference

### Base URL

```
https://x.conanacademy.com/api/v1
```

All endpoints require `Authorization: Bearer {access_token}` header unless noted.

### 1. Authentication (`/auth`)

#### POST `/auth/login`

Login with email/mobile number and password. Creates a device record and returns JWT tokens.

**Request:**
```json
{
  "identifier": "student@example.com or +962791234567",
  "password": "securepassword"
}
```

**Headers:**
```
X-Device-ID: unique-device-fingerprint (required)
X-Platform: iOS | Android | Web (optional)
User-Agent: Standard browser/app user agent
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "profile": {
    "display_name": "أحمد محمد",
    "avatar": "https://cdn.example.com/avatar.jpg",
    "gender": "M",
    "xp": 1250
  }
}
```

**Errors:**
- `400 Bad Request`: Missing X-Device-ID header
- `401 Unauthorized`: Invalid credentials OR player has no plan assigned
- `429 Too Many Requests`: Rate limit exceeded (10/min per IP, 5/min per account)
  - Include `Retry-After` header

**Key Details:**
- Accepts email or mobile number as identifier
- Device registration is atomic; exceeding device limit returns `429`
- New login invalidates any previous session (single-session enforcement)
- Player MUST have a plan assigned in Frappe to login

#### POST `/auth/refresh`

Exchange refresh token for new access token.

**Request:**
```json
{
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc..." // Same token, not rotated
}
```

**Errors:**
- `401 Unauthorized`: Invalid, expired, or invalidated token

**Key Details:**
- Refresh token is reusable (NOT rotated on use)
- Validates session is still active (not invalidated by new login)
- plan_id sourced from session (reflects admin plan changes)
- Typical flow: refresh every 14 minutes (access token = 15 min TTL)

---

### 2. Progress Tracking (`/progress`)

#### GET `/progress/{subject_id}`

Get subject hierarchy with progress and unlock state.

**Response:** `200 OK`
```json
{
  "subject_id": "SUBJ-00015",
  "is_free": false,
  "is_linear": false,
  "tracks": [
    {
      "track_id": "TRACK-001",
      "title": "مقدمة العربية",
      "is_linear": true,
      "completed": 12,
      "total": 25,
      "unlocked": true,
      "percentage": 48,
      "units": [
        {
          "unit_id": "UNIT-001",
          "title": "الحروف والأصوات",
          "is_linear": false,
          "completed": 4,
          "total": 8,
          "unlocked": true,
          "percentage": 50,
          "topics": [
            {
              "topic_id": "TOPIC-001",
              "title": "الحروف الأبجدية",
              "is_free": false,
              "completed": 2,
              "total": 5,
              "unlocked": true,
              "percentage": 40,
              "lessons": [
                {
                  "lesson_id": "LESSON-001",
                  "bit_index": 0,
                  "title": "الحرف ا (ألف)",
                  "is_free": false,
                  "completed": true,
                  "unlocked": true
                }
              ]
            }
          ]
        }
      ]
    }
  ],
  "free_units": ["UNIT-002"],
  "free_topics": ["TOPIC-003", "TOPIC-004"]
}
```

**Errors:**
- `403 Forbidden`: Player lacks access (Double-Gate check failed)
- `404 Not Found`: Subject not found
- `503 Service Unavailable`: Cache/database error

**Key Details:**
- Full hierarchy with progress at all levels (track, unit, topic, lesson)
- `unlocked` field indicates if player can access (respects is_linear at each level)
- Free content bypass: lessons/topics with `is_free=true` are always unlocked
- Response cached 1 hour per player-subject combination

#### GET `/progress/{subject_id}/tracks`

Get all tracks for subject without nested units/topics (lightweight).

**Response:** `200 OK`
```json
{
  "tracks": [
    {
      "track_id": "TRACK-001",
      "completed": 12,
      "total": 25,
      "unlocked": true,
      "percentage": 48
    }
  ]
}
```

**Use Case**: Initial page load to show track cards; user clicks track for details.

#### GET `/progress/{subject_id}/tracks/{track_id}`

Get single track with units summary.

**Response:** `200 OK`
```json
{
  "track_id": "TRACK-001",
  "completed": 12,
  "total": 25,
  "unlocked": true,
  "percentage": 48,
  "units": [
    {
      "unit_id": "UNIT-001",
      "completed": 4,
      "total": 8,
      "unlocked": true,
      "percentage": 50
    }
  ]
}
```

#### GET `/progress/{subject_id}/tracks/{track_id}/units/{unit_id}`

Get single unit with topics and lessons.

**Response:** `200 OK` (same structure as nested topics/lessons from full hierarchy)

#### GET `/progress/{subject_id}/lessons/{lesson_id}/status`

Get completion status for specific lesson.

**Response:** `200 OK`
```json
{
  "lesson_id": "LESSON-001",
  "completed": true,
  "unlocked": true
}
```

**Key Details:**
- Ultra-fast (<5ms) — uses Redis pipeline GETBIT

---

### 3. Game Sessions (`/sessions`)

#### POST `/sessions`

Start a new game session (lesson).

**Request:**
```json
{
  "lesson_id": "LESSON-001"
}
```

**Response:** `201 Created`
```json
{
  "session_id": "SESSION-abc123",
  "lesson_id": "LESSON-001",
  "starts_at": "2026-02-08T10:30:00Z",
  "ttl_seconds": 3600,
  "hearts": 3
}
```

**Errors:**
- `403 Forbidden`: Lesson not accessible (unlock check failed)
- `404 Not Found`: Lesson not found
- `409 Conflict`: Active session already exists (force close previous)

**Key Details:**
- Max 1 active session per player
- TTL 1 hour (auto-cleanup)
- Hearts awarded based on gamification settings (typically 3)
- New start auto-closes previous session

#### GET `/sessions/{session_id}`

Get active session details.

**Response:** `200 OK`
```json
{
  "session_id": "SESSION-abc123",
  "lesson_id": "LESSON-001",
  "starts_at": "2026-02-08T10:30:00Z",
  "hearts": 2,
  "attempt_count": 1
}
```

#### POST `/sessions/{session_id}/complete`

Mark stage as complete within session.

**Request:**
```json
{
  "stage_id": "STAGE-001",
  "attempts": 2,
  "hearts_remaining": 2
}
```

**Response:** `200 OK`
```json
{
  "xp_awarded": 45,
  "streak_bonus": 15,
  "hearts_bonus": 10,
  "total_xp": 70,
  "new_streak": 5,
  "interaction_id": "INT-00420"
}
```

**Errors:**
- `400 Bad Request`: Session not found or not active
- `403 Forbidden`: Invalid stage for lesson
- `409 Conflict`: Stage already completed in this session

**Key Details:**
- Hot path: ~4 Redis round-trips, <10ms via Lua script
- XP calculation: `base_xp + hearts_bonus + streak_multiplier`
- Hearts bonus: `remaining_hearts * xp_per_heart`
- Streak tracks consecutive days (resets at midnight Asia/Amman)
- Replay detection: same stage completed 2nd time = 50% XP

#### POST `/sessions/{session_id}/end`

End session and trigger completion flow (lessons & wallet update).

**Request:**
```json
{
  "completed_stages": ["STAGE-001", "STAGE-002"]
}
```

**Response:** `200 OK`
```json
{
  "lesson_id": "LESSON-001",
  "total_xp": 140,
  "final_streak": 5,
  "progress_updated": true
}
```

**Key Details:**
- Triggers lesson completion (progress bitmap update, unlock next)
- Persists to MariaDB via 1-minute background sync

---

### 4. Wallet & XP (`/wallet`)

#### GET `/wallet`

Get authenticated player's wallet (XP and streak).

**Response:** `200 OK`
```json
{
  "xp": 3750,
  "streak": 5
}
```

**Errors:**
- `401 Unauthorized`: Not authenticated

**Key Details:**
- No `streak_date` in response (date is internal only)
- Streak = current consecutive days of activity
- XP = lifetime accumulation

#### GET `/wallet/{player_id}`

Get another player's wallet (admin only).

**Response:** `200 OK` (same structure)

**Errors:**
- `403 Forbidden`: Not admin (System Manager role required)
- `404 Not Found`: Player not found

---

### 5. Leaderboards (`/leaderboard`)

#### GET `/leaderboard/{type}`

Get top N players from leaderboard.

**Parameters:**
- `type`: `daily` | `weekly` | `alltime` (required)
- `limit`: 1-100, default 10
- `subject_id`: Optional (filter to specific subject/class)

**Response:** `200 OK`
```json
{
  "leaderboard_type": "daily",
  "subject_id": null,
  "entries": [
    {
      "rank": 1,
      "player_id": "usr-001",
      "display_name": "أحمد محمد",
      "xp": 520,
      "avatar": "https://cdn.example.com/avatar-001.jpg",
      "is_me": false
    },
    {
      "rank": 2,
      "player_id": "usr-002",
      "display_name": "فاطمة علي",
      "xp": 490,
      "avatar": "https://cdn.example.com/avatar-002.jpg",
      "is_me": true
    }
  ],
  "total_players": 1250
}
```

**Key Details:**
- **Dense ranking**: Tied scores share rank (1, 1, 3 not 1, 1, 2)
- **Daily**: Resets midnight Asia/Amman timezone
- **Weekly**: Resets Friday midnight
- **All-time**: Never resets
- Includes profile enrichment (display_name, avatar)
- Response cached; archival every 24h (90-day retention)

#### GET `/leaderboard/{type}/me`

Get user's rank with context (±2 neighbors).

**Parameters:**
- `type`: `daily` | `weekly` | `alltime`
- `subject_id`: Optional

**Response:** `200 OK`
```json
{
  "rank": 47,
  "xp": 380,
  "xp_to_next": 25,
  "total_players": 1250,
  "neighbors": [
    {
      "rank": 45,
      "player_id": "usr-045",
      "display_name": "محمود سامي",
      "xp": 410,
      "avatar": "https://cdn.example.com/avatar-045.jpg",
      "is_me": false
    },
    {
      "rank": 46,
      "player_id": "usr-046",
      "display_name": "ليلى أحمد",
      "xp": 405,
      "avatar": "https://cdn.example.com/avatar-046.jpg",
      "is_me": false
    },
    {
      "rank": 47,
      "player_id": "usr-me",
      "display_name": "أنت",
      "xp": 380,
      "avatar": "https://cdn.example.com/avatar-me.jpg",
      "is_me": true
    }
  ]
}
```

**Key Details:**
- `xp_to_next`: XP needed to pass the player above (or 0 if user is #1)
- Unranked users (0 XP): treated as tied for last place
- Neighbors: ±2 surrounding rank positions (up to 5 total)

---

### 6. Product Catalog & Purchase (`/catalog`, `/purchase`)

#### GET `/catalog`

Get available products for player's plan.

**Response:** `200 OK`
```json
{
  "products": [
    {
      "product_grant_id": "GRNT-00001",
      "bundle_name": "أساسيات اللغة العربية",
      "price": 29.99,
      "subjects": [
        {
          "subject_id": "SUBJ-00015",
          "alias_title": "مقدمة العربية",
          "notes": "يشمل الحروف والأصوات والقواعس الأساسية"
        },
        {
          "subject_id": "SUBJ-00016",
          "alias_title": "الكتابة والإملاء",
          "notes": null
        }
      ]
    }
  ]
}
```

**Filters Applied:**
- Excludes products player already purchased (has access to ALL subjects)
- Excludes products with pending transactions
- Empty catalog if player has no plan (returns 200 OK with empty array)

**Key Details:**
- Redis cache (infinite, invalidated by events)
- <100ms response (cached)
- Plan-specific catalog

#### POST `/purchase`

Submit purchase request for a product.

**Request:**
```json
{
  "product_grant_id": "GRNT-00001"
}
```

**Response:** `201 Created`
```json
{
  "transaction_id": "TRANS-00042",
  "product_grant_id": "GRNT-00001",
  "status": "Pending Approval",
  "created_at": "2026-02-08T10:30:00Z"
}
```

**Errors:**
- `400 Bad Request`: Player has no plan
- `404 Not Found`: Product grant or player not found
- `409 Conflict`: Duplicate pending purchase for this product
- `503 Service Unavailable`: Redis unavailable

**Key Details:**
- Creates Subscription Transaction in Frappe (status = "Pending Approval")
- Adds to player's Redis pending set (catalog immediately hides it)
- Requires plan assignment
- Manual approval by admin (except payment gateways can auto-approve)
- Admin notified via email on submission

---

### 7. Settings (`/settings`)

#### GET `/settings/gamification`

Get gamification configuration (XP values, streak rules, etc.).

**Response:** `200 OK`
```json
{
  "base_xp_per_stage": 30,
  "xp_per_heart": 5,
  "streak_multiplier_cap": 2.0,
  "max_devices_per_player": 3,
  "hearts_per_stage": 3
}
```

**Key Details:**
- Cached in Redis (1 hour TTL)
- Admin-configurable via Frappe Desk
- Used for XP calculations and device limits

---

### 8. Health & Status (`/health`)

#### GET `/health/live`

Check API availability.

**Response:** `200 OK`
```json
{
  "status": "alive",
  "api_version": "v1"
}
```

**No authentication required.** Use for health checks and monitoring.

---

## Authentication & Authorization

### JWT Token Structure

**Access Token (15 min TTL):**
```json
{
  "sub": "user-id",
  "email": "student@example.com",
  "name": "أحمد محمد",
  "plan_id": "PLAN-00052",
  "fid": "family-id-abc123",
  "iat": 1707384600,
  "exp": 1707385500
}
```

**Refresh Token (30 days TTL):**
```json
{
  "sub": "user-id",
  "type": "refresh",
  "fid": "family-id-abc123",
  "iat": 1707384600,
  "exp": 1738920600
}
```

### Token Placement

All requests (except `/auth/login` and `/auth/refresh`) require:
```
Authorization: Bearer {access_token}
```

### Refresh Token Flow

```
1. Player receives access_token (15 min) + refresh_token (30 days) on login
2. When access_token expires, use refresh endpoint:
   POST /auth/refresh
   {
     "refresh_token": "eyJ0eXA..."
   }
3. Get new access_token, keep same refresh_token
4. Repeat until refresh_token expires (user must re-login)
```

### Access Control (Double-Gate)

**Gate 1: Season Validation**
- Check if content season is active (status + end_ts)
- Cached in Redis per season

**Gate 2: Player Access Set**
- Check if player has subscription for subject
- Redis SISMEMBER check: `memora:access:{player_id}`
- Plan members get automatic access to free subjects

**Free Content Bypass**
- Lessons/topics with `is_free=true` bypass both gates
- Always accessible to everyone

### Role-Based Access

- **System Manager** (admin): Can view any player's wallet, manage devices, approve purchases
- **Default** (student): Limited to own data

---

## Key Features

### 1. Progress Tracking

**What the Player Sees:**
- Subject hierarchy: tracks → units → topics → lessons
- Progress bar at each level (% completed)
- Lock/unlock status for each lesson (grayed out if locked)
- Free content indicators (can access without subscription)

**How It Works (Backend):**
- Redis bitmap: 1 bit per lesson (O(1) completion check)
- Unlock calculation: respects `is_linear` at track/unit/topic levels
- Sequential unlock: complete all lessons in a level to unlock next
- Cached hierarchy (1 hour) + live stats (O(1) from hash)

**Example**: Unit has 3 topics with `is_linear=true`
- Student completes all lessons in Topic 1
- Topic 2 unlocks automatically
- Completion % updates real-time

### 2. Gamification (XP & Streaks)

**XP Calculation:**
```
total_xp = base_xp + hearts_bonus + (streak_bonus * streak_multiplier)
  where:
  - base_xp = 30 (configurable)
  - hearts_bonus = remaining_hearts * 5 (configurable)
  - streak_bonus = 15 (for consecutive days)
  - streak_multiplier = min(1 + (streak_days / 5), 2.0) [capped at 2x]
```

**Streak Tracking:**
- Increments on first completion of each day (UTC)
- Resets if no activity for 24 hours
- Bonus XP multiplied by streak (max 2x)
- Reset at midnight Asia/Amman timezone

**Hearts System:**
- 3 hearts per lesson (configurable)
- Each incorrect attempt costs 1 heart
- Remaining hearts × 5 XP bonus for completion
- All 3 hearts correct: 15 XP bonus

**Replay Detection:**
- Complete same stage 2nd time: 50% base XP
- Encourages new content, not replay farming

### 3. Leaderboards

**Three Leaderboard Types:**

| Type | Reset | Use Case |
|------|-------|----------|
| **Daily** | Midnight Asia/Amman | Daily competition |
| **Weekly** | Friday midnight | Weekly challenges |
| **All-time** | Never | Career achievements |

**Features:**
- Top 100 entries visible to all
- User's rank + ±2 neighbors
- Dense ranking (ties share rank)
- Optional subject filtering (class-specific competition)
- Composite score: XP + timestamp (tie-breaks favor earlier achiever)

### 4. Product Store

**Player Flow:**
1. Browse catalog (filtered by plan, excludes purchased/pending)
2. Click product → see details (subjects, price)
3. Submit purchase request → status = "Pending Approval"
4. Admin reviews in Frappe Desk
5. Admin approves → automatic subscription creation → access granted
6. Product removed from catalog

**Catalog Caching:**
- Per-plan cache (infinite TTL, event-invalidated)
- Updates instantly when Product Grant changes
- Player filtering (per-player, not cached)
- <100ms response

**Transaction Status:**
- **Pending Approval**: Awaiting admin decision
- **Approved**: Subscriptions created, access granted
- **Rejected**: Not purchased, reappears in catalog

### 5. Device Management

**Device Registration:**
- On login, device fingerprint registered with user agent
- Limit: 3 active devices per player (configurable)
- Exceeding limit returns `HTTP 429`
- New login on same device just updates metadata

**Device Fingerprinting:**
- Based on User-Agent hash (same device after app update)
- Optional X-Platform header (iOS, Android, Web)
- Unique per device, not per app installation

**Admin View:**
- Frappe Desk: Can see all player devices
- Can remove specific device (forces logout on next request)

### 6. Session Management

**Session Lifecycle:**
```
1. Player logs in
   → Creates Redis hash: memora:session:{user_id}:{family_id}
   → Stores plan_id, metadata, timestamps
   → TTL: 30 days (refresh token expiry)

2. Player starts lesson
   → Creates game session (separate from auth session)
   → TTL: 1 hour (auto cleanup of abandoned sessions)

3. Player completes stages within lesson
   → Updates session with stage data
   → Triggers XP/progress updates

4. Player logs in again
   → Invalidates previous auth session (single-session enforcement)
   → New session created with new family_id
   → Refresh token from old session becomes invalid
```

---

## Development Setup

### Prerequisites

- **Node.js**: 16+ (for React frontend)
- **npm/yarn**: Package manager
- **Git**: Version control
- **Postman** or **curl**: API testing

### API Server Access

**Development:**
```
http://127.0.0.1:8002/api/v1
```

**Staging/Production:**
```
https://x.conanacademy.com/api/v1
```

### Environment Setup

Create `.env.local` in React project root:

```env
# API Configuration
REACT_APP_API_URL=http://127.0.0.1:8002/api/v1
REACT_APP_API_TIMEOUT=30000

# Feature Flags (optional)
REACT_APP_DEBUG_MODE=false
REACT_APP_MOCK_API=false

# Analytics (optional)
REACT_APP_ANALYTICS_ID=
```

### Common Development Tasks

**Install Dependencies:**
```bash
npm install
```

**Start Dev Server:**
```bash
npm start  # Typically runs on http://localhost:3000
```

**Test API Endpoints:**
```bash
# Health check
curl http://127.0.0.1:8002/api/v1/health/live

# Login
curl -X POST http://127.0.0.1:8002/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -H "X-Device-ID: test-device-123" \
  -d '{
    "identifier": "student@example.com",
    "password": "password"
  }'
```

**Debug API Issues:**
- Check network tab in browser DevTools
- Verify `Authorization` header is present
- Check token expiry (decode JWT at jwt.io)
- Look at backend logs (structlog format in FastAPI)

---

## Common User Workflows

### Workflow 1: New Student Onboarding

```
1. Student enters email/mobile + password
   → Frontend sends: POST /auth/login
   → Backend: Verifies Frappe credentials, registers device
   → Response: JWT tokens + profile data

2. App displays profile (name, avatar, XP)
   → Built from login response

3. Student selects subject to start
   → GET /progress/{subject_id} (full hierarchy)
   → Shows tracks, units, topics, lessons with lock status

4. Browses tracks
   → GET /progress/{subject_id}/tracks (lightweight)
   → Shows cards for each track

5. Clicks track to see units
   → GET /progress/{subject_id}/tracks/{track_id}
   → Shows units in that track

6. Clicks unit to see topics
   → GET /progress/{subject_id}/tracks/{track_id}/units/{unit_id}
   → Shows lessons with lock status

7. Starts first lesson
   → POST /sessions (creates game session)
   → Displays lesson content

8. Completes stage
   → POST /sessions/{session_id}/complete
   → Awards XP, updates streak
   → Shows celebration (XP earned, new rank, etc.)

9. Completes lesson
   → POST /sessions/{session_id}/end
   → Unlocks next lesson
   → Returns to subject view, progress updated
```

### Workflow 2: Purchase & Access New Subject

```
1. Student views available products
   → GET /catalog
   → Filtered by plan, excludes purchased/pending

2. Clicks product to see details
   → Shows subjects included, price, description

3. Submits purchase
   → POST /purchase {product_grant_id}
   → Status: "Pending Approval"
   → Product hidden from catalog

4. Admin reviews purchase
   → Views in Frappe Desk
   → Clicks "Approve"

5. Backend creates subscriptions
   → One Memora Player Subscription per subject
   → Redis access set updated
   → Player logged out (plan change invalidates session)

6. Student re-logs in
   → New subjects now accessible
   → Appears in progress view
   → Can start lessons
```

### Workflow 3: Compete on Leaderboard

```
1. Student completes lesson
   → XP awarded, added to daily/weekly/all-time leaderboards

2. Views leaderboard
   → GET /leaderboard/daily?limit=10
   → Shows top 10 players with names, avatars, XP

3. Checks own rank
   → GET /leaderboard/daily/me
   → Shows rank, XP, distance to next tier
   → Shows ±2 neighbors for context

4. Competes to beat neighbor
   → Completes more lessons
   → XP updated in real-time
   → Rank changes automatically
```

---

## Error Handling

### Standard Error Response

All errors follow this format:

```json
{
  "detail": "Human-readable error message",
  "code": "ERROR_CODE (optional)"
}
```

### HTTP Status Codes

| Status | Meaning | Typical Cause |
|--------|---------|---------------|
| `200 OK` | Success | Normal operation |
| `201 Created` | Resource created | Purchase submitted |
| `400 Bad Request` | Invalid request | Missing/invalid parameters |
| `401 Unauthorized` | Auth failed/missing | Invalid/expired token |
| `403 Forbidden` | Access denied | No access to subject, not admin |
| `404 Not Found` | Resource missing | Subject/lesson not found |
| `409 Conflict` | State conflict | Duplicate purchase, session exists |
| `429 Too Many Requests` | Rate limited | Too many login attempts, device limit |
| `500 Internal Server Error` | Backend error | Bug/unhandled exception |
| `503 Service Unavailable` | Temp unavailable | Redis down, database unreachable |

### Handling Rate Limits (429)

```json
{
  "detail": "Too many login attempts",
  "retry_after": 45
}
```

**Headers:**
```
Retry-After: 45
```

**Client Action:**
1. Parse `retry_after` from response
2. Disable login button for X seconds
3. Show user countdown timer
4. Retry after wait time

### Handling Token Expiry (401)

```json
{
  "detail": "Invalid credentials"
}
```

**Client Action:**
1. Intercept 401 responses
2. Try to refresh token: `POST /auth/refresh`
3. If refresh succeeds: retry original request with new token
4. If refresh fails: redirect to login page

### Common Errors & Solutions

**"Player must have a plan assigned"**
- Admin must assign plan in Frappe Player Profile
- Contact support if unresolved

**"Device limit reached"**
- User has 3 active devices
- Can remove device from app settings or contact support
- Admin can remove from Frappe Desk

**"Product already has pending purchase"**
- Can't submit 2 purchases for same product simultaneously
- Wait for admin decision or contact support

**"Service temporarily unavailable"**
- Redis or database is down
- Retry in 30 seconds
- Contact ops if persists >5 min

---

## Performance Targets

The Player App must maintain these response times for optimal UX:

| Operation | Target | Actual | Status |
|-----------|--------|--------|--------|
| Login | <1000ms | ~800ms | ✓ Good |
| Token refresh | <500ms | ~300ms | ✓ Good |
| Fetch progress (full hierarchy) | <500ms | ~200ms (cached) | ✓ Good |
| Fetch progress (lightweight) | <100ms | ~30ms (cached) | ✓ Good |
| Lesson completion | <200ms | ~10ms (Lua script) | ✓ Excellent |
| Leaderboard fetch | <300ms | ~50ms | ✓ Good |
| Catalog fetch | <300ms | <100ms (cached) | ✓ Good |
| Access check | <20ms | <2ms (Redis) | ✓ Excellent |

### Optimization Strategies

1. **Token Refresh**: Use refresh token proactively at 10-min mark (before 15-min expiry)
2. **Batch Requests**: Combine related queries when possible
3. **Progressive Loading**:
   - Load lightweight progress first (tracks)
   - Load full hierarchy on demand
   - SSE streaming for real-time updates
4. **Caching Strategy**:
   - Cache full hierarchy 1 hour per player-subject
   - Cache leaderboards daily
   - Invalidate on mutations (events)
5. **Connection Pooling**: Reuse HTTP connections for multiple requests

---

## Design Patterns

### Pattern 1: Dependency Injection (FastAPI)

All endpoints use type hints for automatic dependency resolution:

```python
@router.get("/progress/{subject_id}")
async def get_progress(
    subject_id: str,
    user: CurrentUser,  # Injected from JWT
    progress_service: ProgressServiceDep,  # Injected from FastAPI Depends
    access_service: AccessServiceDep,  # Another service
):
    pass
```

**For Player App Frontend**: Not directly applicable, but understand that:
- Auth is handled via JWT in headers (automatic by HTTP client)
- Services are backend patterns (for reference/debugging)

### Pattern 2: Service Layer (Business Logic)

Backend services encapsulate logic:

```
Endpoint (FastAPI route)
  ↓ calls
Service (business logic)
  ↓ calls
Repository (Redis/Frappe data access)
```

**For Player App**: Just understand each endpoint maps to one service call (visible in docstrings).

### Pattern 3: Redis for Hot Data

All frequently-accessed data stored in Redis with TTL:

- **Progress bitmaps**: infinite (persistent)
- **Wallets (XP/streak)**: infinite (persistent)
- **Leaderboards**: daily reset
- **Profiles**: 1 hour TTL (invalidated on update)
- **Catalogs**: infinite (event-invalidated)
- **Settings**: 1 hour TTL

**For Player App**: Understand that data updates may have small delays (cache propagation ~1 second).

### Pattern 4: Event-Driven Invalidation

When admin changes data in Frappe:

```
Admin updates Product Grant
  → Frappe hook triggered
  → Redis pub/sub publishes message
  → FastAPI lifespan handler receives event
  → Calls CatalogService.invalidate()
  → Deletes Redis cache key
  → Next API call rebuilds cache from Frappe
```

**For Player App**: Catalog changes reflect within ~1 second.

### Pattern 5: Atomic Lua Scripts

Complex operations executed atomically in Redis:

```lua
-- Lesson completion: 4 operations in 1 round-trip
1. SETBIT progress bitmap
2. Calculate unlock
3. HINCRBY wallet (XP)
4. Update streak
```

**Result**: <10ms, no race conditions.

---

## References & Links

- **Main Platform Documentation**: `/CLAUDE.md`
- **Project Vision**: `.planning/PROJECT.md`
- **API Health Check**: `GET http://127.0.0.1:8002/api/v1/health/live`
- **Frappe Admin**: `https://x.conanacademy.com` (System Manager login)
- **Backend Code**: `fastapi_app/api/v1/endpoints/`
- **Data Models**: `fastapi_app/models/`

---

## FAQ for AI Agent

**Q: How do I know if my frontend is calling the API correctly?**
A: Open browser DevTools → Network tab → Check:
1. Request URL matches `http://127.0.0.1:8002/api/v1/...`
2. `Authorization` header contains `Bearer {token}`
3. Response status is 2xx (200, 201, etc.)
4. JSON payload matches documentation

**Q: What if API returns 401?**
A: Token expired or invalid. Frontend should:
1. Try POST /auth/refresh
2. If successful: retry original request
3. If fails: clear local storage, redirect to login

**Q: How do I test without breaking production?**
A: Always use local FastAPI (`http://127.0.0.1:8002`) during development. Production API is at `https://x.conanacademy.com/api/v1`.

**Q: Can I modify API behavior?**
A: No. Catalog endpoint is owned by backend. Create feature request if needed. That said, the backend team (AI agent for Memora Admin) owns API changes; work with them collaboratively.

**Q: Why is leaderboard returning different data?**
A: Leaderboard data updates:
- Every 1 minute (new entries added)
- Every 24 hours (daily reset)
- When user completes lesson (real-time)

**Q: What if user hits device limit?**
A: Return 429 with message: "Device limit reached. Contact support to manage devices." Frontend can offer link to Frappe device management (admin) or reset app.

---

**Last Updated**: 2026-02-08
**Status**: v1.4 shipped, actively maintained
**Next Milestone**: v1.5 (future roadmap)
