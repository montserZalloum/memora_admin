# Roadmap: Memora Platform

## Overview

This roadmap delivers a gamified educational platform backend with sub-20ms game API responses. The journey starts with infrastructure foundation (FastAPI + Redis), progresses through authentication and access control (Double-Gate pattern), implements core game mechanics (bitmap progress tracking, XP, streaks), and completes with content build pipeline and data synchronization. Each phase builds on the previous, with the game loop (progress -> wallet -> sync) forming the critical path.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Infrastructure Foundation** - FastAPI sidecar with Redis connection and Nginx routing
- [x] **Phase 2: Authentication** - JWT-based authentication with stateless verification
- [ ] **Phase 3: Access Control** - Double-Gate pattern (season + player grants) for content access
- [ ] **Phase 4: Progress Tracking** - Bitmap-based lesson completion with unlock states
- [ ] **Phase 5: Wallet & Gamification** - XP accumulation and streak tracking
- [ ] **Phase 6: Build Pipeline** - Content JSON generation and CDN upload
- [ ] **Phase 7: Sync Mechanisms** - Redis to MariaDB persistence with scheduled tasks

## Phase Details

### Phase 1: Infrastructure Foundation
**Goal**: FastAPI sidecar runs alongside Frappe with shared Redis and proper routing
**Depends on**: Nothing (first phase)
**Requirements**: INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05
**Success Criteria** (what must be TRUE):
  1. FastAPI server starts with lifespan events and responds to health check at /api/v1/health
  2. Redis async connection pool connects to shared Frappe Redis instance with memora:* key prefix
  3. Nginx routes /api/v1/* requests to FastAPI (port 8001) and /api/method/* to Frappe (port 8000)
  4. Configuration loads from .env file (Redis URL, JWT secret, paths) without hardcoded values
  5. Server migration documentation exists with steps to relocate Redis/FastAPI to separate server
**Plans**: 4 plans in 3 waves

Plans:
- [x] 01-01-PLAN.md — FastAPI project scaffold with configuration and health endpoint (Wave 1)
- [x] 01-02-PLAN.md — Redis async connection pooling and readiness check (Wave 2)
- [x] 01-03-PLAN.md — Nginx reverse proxy configuration (Wave 2)
- [x] 01-04-PLAN.md — Server migration documentation (Wave 3)

### Phase 2: Authentication
**Goal**: Players can authenticate via JWT tokens verified statelessly
**Depends on**: Phase 1
**Requirements**: AUTH-01, AUTH-02, AUTH-03
**Success Criteria** (what must be TRUE):
  1. Player can login with Frappe credentials and receive JWT access token (15 min) + refresh token (30 days)
  2. Player can exchange refresh token for new access token without re-entering credentials
  3. FastAPI middleware validates JWT tokens without database lookup (stateless verification)
  4. Invalid/expired tokens are rejected with 401 response
  5. New login invalidates previous session (single-session per player)
**Plans**: 3 plans in 2 waves

Plans:
- [x] 02-01-PLAN.md — Security foundation: JWT utilities, auth models, Frappe service (Wave 1)
- [x] 02-02-PLAN.md — Session management and rate limiting services (Wave 1)
- [x] 02-03-PLAN.md — Auth endpoints (login, refresh) and JWT middleware (Wave 2)

### Phase 3: Access Control
**Goal**: Content access validated through Double-Gate pattern (season status + player grants)
**Depends on**: Phase 2
**Requirements**: ACCESS-01, ACCESS-02, ACCESS-03, ACCESS-04, ACCESS-05
**Success Criteria** (what must be TRUE):
  1. Gate 1 rejects access when season is inactive or expired (status check + end_ts comparison)
  2. Gate 2 rejects access when player lacks direct grant or plan membership for requested content
  3. Units/Topics with is_free=true are accessible without Gate 2 check (free preview)
  4. Payment webhook creates subscription record in MariaDB and adds grant to Redis access set
  5. Admin can grant player access from Frappe Desk and change is reflected in Redis within 1 second
**Plans**: TBD

Plans:
- [ ] 03-01: Season meta sync (Frappe hook to Redis hash)
- [ ] 03-02: Player access set management (direct grants + plan membership)
- [ ] 03-03: Double-Gate middleware and free preview logic
- [ ] 03-04: Payment webhook and admin grant endpoints

### Phase 4: Progress Tracking
**Goal**: Lesson completion tracked via Redis bitmaps with linear unlock enforcement
**Depends on**: Phase 3
**Requirements**: PROG-01, PROG-02, PROG-03
**Success Criteria** (what must be TRUE):
  1. Completing a lesson sets corresponding bit in player-subject bitmap (O(1) operation)
  2. Progress endpoint returns lesson completion states with <20ms response time
  3. Unlock state calculation respects is_linear flags at Track/Unit/Topic levels (locked lessons show but cannot be started)
  4. Player cannot mark lesson complete without proper access (Double-Gate validated first)
**Plans**: TBD

Plans:
- [ ] 04-01: Bitmap slot allocation system and progress data structures
- [ ] 04-02: Lesson completion endpoint with bitmap SETBIT
- [ ] 04-03: Progress fetch endpoint with unlock state calculation

### Phase 5: Wallet & Gamification
**Goal**: Players earn XP and maintain streaks on lesson completion
**Depends on**: Phase 4
**Requirements**: WALLET-01, WALLET-02, WALLET-03
**Success Criteria** (what must be TRUE):
  1. Completing a lesson awards XP to player wallet (stored in Redis hash)
  2. Streak increments when player completes first lesson of a new calendar day (user timezone)
  3. Replaying already-completed lessons awards 50% XP (replay detection works)
  4. Wallet endpoint returns current XP and streak with <10ms response time
**Plans**: TBD

Plans:
- [ ] 05-01: Wallet data structure and XP award logic
- [ ] 05-02: Streak calculation with timezone handling
- [ ] 05-03: Replay detection and reduced XP awards

### Phase 6: Build Pipeline
**Goal**: Content changes trigger JSON generation and CDN upload with cache invalidation
**Depends on**: Phase 4
**Requirements**: BUILD-01, BUILD-02, BUILD-03, BUILD-04, BUILD-05, BUILD-06, BUILD-07
**Success Criteria** (what must be TRUE):
  1. Saving content DocType in Frappe queues a build (debounced for 2 minutes)
  2. Build generates hierarchy JSON (_h.json) with tracks, units, topics structure
  3. Build generates bitmap JSON (_b.json) with bit_range and excluded_bits per entity
  4. Build generates unit content JSON (*_c.json) and lesson JSON with stages
  5. Generated JSON files upload to mock CDN (abstraction layer ready for R2 swap)
  6. FastAPI bitmap cache invalidates via Redis pub/sub when build completes
**Plans**: TBD

Plans:
- [ ] 06-01: Frappe doc_events hooks for build queue
- [ ] 06-02: Hierarchy and bitmap JSON generation
- [ ] 06-03: Unit content and lesson JSON generation
- [ ] 06-04: Mock CDN upload and pub/sub cache invalidation

### Phase 7: Sync Mechanisms
**Goal**: Redis game state persists to MariaDB via scheduled background sync
**Depends on**: Phase 5, Phase 6
**Requirements**: SYNC-01, SYNC-02, SYNC-03, TASK-01
**Success Criteria** (what must be TRUE):
  1. Progress sync converts Redis bitmaps to hex strings and updates Structure Progress records
  2. Wallet sync copies Redis hash values (XP, streak, streak_date) to Player Wallet records
  3. Interaction buffer flushes Redis list to Interaction Log via batch INSERT
  4. Build worker processes pending builds every 2 minutes via Frappe scheduler
  5. Sync Log DocType records each sync run with success/failure status and record counts
**Plans**: TBD

Plans:
- [ ] 07-01: Dirty set tracking for progress and wallets
- [ ] 07-02: Progress sync task (bitmap to hex)
- [ ] 07-03: Wallet sync task and interaction buffer flush
- [ ] 07-04: Build worker scheduled task and Sync Log integration

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Infrastructure Foundation | 4/4 | ✓ Complete | 2026-02-01 |
| 2. Authentication | 3/3 | ✓ Complete | 2026-02-02 |
| 3. Access Control | 0/4 | Not started | - |
| 4. Progress Tracking | 0/3 | Not started | - |
| 5. Wallet & Gamification | 0/3 | Not started | - |
| 6. Build Pipeline | 0/4 | Not started | - |
| 7. Sync Mechanisms | 0/4 | Not started | - |

---
*Roadmap created: 2026-02-01*
*Total phases: 7 | Total plans: 25 (estimated)*
*Coverage: 30/30 v1 requirements mapped*
