# Roadmap: Memora Platform

## Milestones

- ✅ **v1.0 MVP** - Phases 1-7 (shipped 2026-02-02)
- 🚧 **v1.1 Feature Expansion** - Phases 8-11 (current)

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1-7) - SHIPPED 2026-02-02</summary>

### Phase 1: Project Foundation
**Goal**: FastAPI sidecar with Redis integration ready for game API
**Plans**: 3 plans

Plans:
- [x] 01-01: Project structure and environment setup
- [x] 01-02: Redis connection pooling
- [x] 01-03: Nginx routing configuration

### Phase 2: Authentication
**Goal**: Stateless JWT authentication with rate limiting
**Plans**: 4 plans

Plans:
- [x] 02-01: Login endpoint with JWT tokens
- [x] 02-02: Refresh endpoint
- [x] 02-03: JWT verification middleware
- [x] 02-04: Rate limiting

### Phase 3: Access Control
**Goal**: Double-Gate access validation (season + player grants)
**Plans**: 4 plans

Plans:
- [x] 03-01: Season validation (Gate 1)
- [x] 03-02: Player access set check (Gate 2)
- [x] 03-03: Payment webhook integration
- [x] 03-04: Admin grant UI

### Phase 4: Progress Tracking
**Goal**: Bitmap-based progress tracking with unlock enforcement
**Plans**: 3 plans

Plans:
- [x] 04-01: Bitmap progress storage
- [x] 04-02: Unlock state calculation
- [x] 04-03: Lesson completion endpoint

### Phase 5: Gamification
**Goal**: XP and streak mechanics with replay detection
**Plans**: 4 plans

Plans:
- [x] 05-01: XP accumulation in Redis
- [x] 05-02: Streak tracking with consecutive day detection
- [x] 05-03: Replay XP reduction
- [x] 05-04: Wallet sync to MariaDB

### Phase 6: Content Pipeline
**Goal**: Automated JSON generation with cache invalidation
**Plans**: 4 plans

Plans:
- [x] 06-01: Build queue with debounce
- [x] 06-02: Hierarchy JSON generation
- [x] 06-03: Bitmap and unit content JSON
- [x] 06-04: Cache invalidation via pub/sub

### Phase 7: Sync Mechanisms
**Goal**: Background sync of Redis hot data to MariaDB
**Plans**: 4 plans

Plans:
- [x] 07-01: Progress sync (Redis to MariaDB hex)
- [x] 07-02: Wallet sync
- [x] 07-03: Interaction buffer flush
- [x] 07-04: Scheduler wiring

</details>

### 🚧 v1.1 Feature Expansion (In Progress)

**Milestone Goal:** Extend the platform with game sessions for lesson flow tracking, competitive leaderboards, device management for security, and scheduled maintenance tasks.

#### Phase 8: Device Management
**Goal**: Secure device registration with 3-device limit enforcement
**Depends on**: Phase 2 (JWT authentication)
**Requirements**: DEVICE-01, DEVICE-02
**Success Criteria** (what must be TRUE):
  1. User's device is registered with metadata on login
  2. User with 3 devices is blocked from logging in on 4th device
  3. Device registration is atomic (no race conditions with concurrent logins)
**Plans**: 2 plans

Plans:
- [ ] 08-01-PLAN.md — Device service foundation (models, Lua script, DeviceService)
- [ ] 08-02-PLAN.md — Login integration and admin removal hook

#### Phase 9: Game Sessions
**Goal**: Lesson flow tracking with session lifecycle and validation
**Depends on**: Phase 4 (progress tracking), Phase 8 (device metadata optional)
**Requirements**: SESSION-01, SESSION-02, SESSION-03, SESSION-04, SESSION-05, SESSION-06
**Success Criteria** (what must be TRUE):
  1. User can start a lesson and create a session with 1-hour TTL
  2. User can complete stages within an active session
  3. User cannot complete stages without an active session
  4. User can end a lesson and trigger completion flow (XP, progress, streak)
  5. User can resume a lesson after app crash (session recovery)
  6. User cannot be in multiple lessons simultaneously (concurrent session detection)
**Plans**: TBD

Plans:
- [ ] 09-01: TBD
- [ ] 09-02: TBD
- [ ] 09-03: TBD

#### Phase 10: Leaderboards
**Goal**: Competitive rankings with daily/all-time/streak leaderboards
**Depends on**: Phase 5 (XP and streak mechanics), Phase 9 (session context optional)
**Requirements**: LEADER-01, LEADER-02, LEADER-03, LEADER-04, LEADER-05
**Success Criteria** (what must be TRUE):
  1. User can view all-time XP leaderboard with top N players
  2. User can view daily XP leaderboard (resets at midnight Asia/Amman)
  3. User can view streak leaderboard ranked by current streak length
  4. User can retrieve their rank position in any leaderboard
  5. Yesterday's daily leaderboard is archived for 30 days
**Plans**: TBD

Plans:
- [ ] 10-01: TBD
- [ ] 10-02: TBD
- [ ] 10-03: TBD

#### Phase 11: Scheduled Tasks
**Goal**: Automated maintenance for streaks, sessions, and leaderboards
**Depends on**: Phase 5 (streaks), Phase 9 (sessions), Phase 10 (leaderboards)
**Requirements**: SCHED-01, SCHED-02, SCHED-03
**Success Criteria** (what must be TRUE):
  1. Users who miss activity have streaks reset at midnight Asia/Amman
  2. Expired session keys are removed hourly
  3. Daily leaderboard archives yesterday's data and creates new key at midnight
  4. All scheduled tasks are idempotent (safe on retry/duplicate execution)
**Plans**: TBD

Plans:
- [ ] 11-01: TBD
- [ ] 11-02: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 8 → 9 → 10 → 11

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Project Foundation | v1.0 | 3/3 | Complete | 2026-02-01 |
| 2. Authentication | v1.0 | 4/4 | Complete | 2026-02-01 |
| 3. Access Control | v1.0 | 4/4 | Complete | 2026-02-01 |
| 4. Progress Tracking | v1.0 | 3/3 | Complete | 2026-02-02 |
| 5. Gamification | v1.0 | 4/4 | Complete | 2026-02-02 |
| 6. Content Pipeline | v1.0 | 4/4 | Complete | 2026-02-02 |
| 7. Sync Mechanisms | v1.0 | 4/4 | Complete | 2026-02-02 |
| 8. Device Management | v1.1 | 0/2 | Planned | - |
| 9. Game Sessions | v1.1 | 0/TBD | Not started | - |
| 10. Leaderboards | v1.1 | 0/TBD | Not started | - |
| 11. Scheduled Tasks | v1.1 | 0/TBD | Not started | - |
