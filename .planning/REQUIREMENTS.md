# Requirements: Memora Platform

**Defined:** 2026-02-01
**Core Value:** Students can track their learning progress and earn rewards with instant feedback and sub-second response times

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Infrastructure

- [x] **INFRA-01**: FastAPI project structure with lifespan events and dependency injection
- [x] **INFRA-02**: Redis async connection pooling with shared Frappe instance
- [x] **INFRA-03**: Nginx reverse proxy routing (/api/v1/* -> FastAPI, /api/method/* -> Frappe)
- [x] **INFRA-04**: Portable configuration via .env file (Redis URL, JWT secret, paths)
- [x] **INFRA-05**: Server migration documentation for Redis/FastAPI portability

### Authentication

- [x] **AUTH-01**: Login endpoint verifies Frappe credentials and issues JWT access + refresh tokens
- [x] **AUTH-02**: Refresh endpoint exchanges refresh token for new access token
- [x] **AUTH-03**: FastAPI middleware verifies JWT tokens statelessly (no database lookup)

### Access Control

- [x] **ACCESS-01**: Gate 1 validates season status (active) and end timestamp (not expired)
- [x] **ACCESS-02**: Gate 2 checks player access set (direct grants + plan membership lookup)
- [x] **ACCESS-03**: Free preview logic bypasses Gate 2 for Units/Topics with is_free=true
- [x] **ACCESS-04**: Payment webhook grants access via Redis SADD and creates MariaDB subscription record
- [x] **ACCESS-05**: Admin can manually grant player access from Frappe Desk UI

### Progress Tracking

- [x] **PROG-01**: Redis bitmap stores lesson completion per player-subject (SETBIT/GETBIT)
- [x] **PROG-02**: Unlock state calculation respects is_linear flags at Track/Unit/Topic levels
- [x] **PROG-03**: API endpoint marks lesson complete and updates bitmap

### Wallet & Gamification

- [x] **WALLET-01**: XP accumulates in Redis hash (wallet:{player_id}) on lesson completion
- [x] **WALLET-02**: Streak tracks consecutive learning days with streak_date field
- [x] **WALLET-03**: Replaying completed lessons awards reduced XP (50%)

### Build Pipeline

- [ ] **BUILD-01**: Frappe doc_events hooks queue builds on content DocType changes
- [ ] **BUILD-02**: Generate hierarchy JSON (_h.json) with tracks, units, topics structure
- [ ] **BUILD-03**: Generate bitmap JSON (_b.json) with bit_range and excluded_bits per entity
- [ ] **BUILD-04**: Generate unit content JSON (*_c.json) with topics and lesson metadata
- [ ] **BUILD-05**: Generate lesson JSON with stages array and stage configurations
- [ ] **BUILD-06**: Redis pub/sub invalidates FastAPI bitmap cache on rebuild completion
- [ ] **BUILD-07**: Mock CDN layer with clean abstraction (swappable for Cloudflare R2)

### Sync Mechanisms

- [ ] **SYNC-01**: Progress sync writes Redis bitmap to MariaDB Structure Progress as hex string
- [ ] **SYNC-02**: Wallet sync writes Redis hash to MariaDB Player Wallet record
- [ ] **SYNC-03**: Interaction buffer flushes Redis list to MariaDB Interaction Log batch insert

### Scheduled Tasks

- [ ] **TASK-01**: Build worker processes pending builds every 2 minutes via Frappe scheduler

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Game Sessions

- **SESSION-01**: Start lesson creates session in Redis with TTL
- **SESSION-02**: Complete stage records interaction in active session
- **SESSION-03**: End lesson finalizes session and triggers completion flow

### Leaderboards

- **LEADER-01**: Daily XP leaderboard using Redis sorted set
- **LEADER-02**: All-time XP leaderboard
- **LEADER-03**: Current streak leaderboard

### Device Management

- **DEVICE-01**: Register authorized devices on login
- **DEVICE-02**: Enforce maximum 3 devices per player

### Additional Scheduled Tasks

- **TASK-02**: Sync jobs run every 1 minute
- **TASK-03**: Broken streak reset runs daily at midnight

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| React Student App | Frontend is separate project |
| Cloudflare R2 setup | Mock CDN for dev; production deployment later |
| Offline support | Future roadmap (Q2 2026) |
| Push notifications | Future roadmap |
| Analytics dashboards | Future roadmap (Q3 2026) |
| Anti-cheat system | Future roadmap (Q4 2026) |
| FSRS spaced repetition | Future roadmap |
| Grafana/Prometheus monitoring | Future roadmap |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 1 | Complete |
| INFRA-02 | Phase 1 | Complete |
| INFRA-03 | Phase 1 | Complete |
| INFRA-04 | Phase 1 | Complete |
| INFRA-05 | Phase 1 | Complete |
| AUTH-01 | Phase 2 | Complete |
| AUTH-02 | Phase 2 | Complete |
| AUTH-03 | Phase 2 | Complete |
| ACCESS-01 | Phase 3 | Complete |
| ACCESS-02 | Phase 3 | Complete |
| ACCESS-03 | Phase 3 | Complete |
| ACCESS-04 | Phase 3 | Complete |
| ACCESS-05 | Phase 3 | Complete |
| PROG-01 | Phase 4 | Complete |
| PROG-02 | Phase 4 | Complete |
| PROG-03 | Phase 4 | Complete |
| WALLET-01 | Phase 5 | Pending |
| WALLET-02 | Phase 5 | Pending |
| WALLET-03 | Phase 5 | Pending |
| BUILD-01 | Phase 6 | Pending |
| BUILD-02 | Phase 6 | Pending |
| BUILD-03 | Phase 6 | Pending |
| BUILD-04 | Phase 6 | Pending |
| BUILD-05 | Phase 6 | Pending |
| BUILD-06 | Phase 6 | Pending |
| BUILD-07 | Phase 6 | Pending |
| SYNC-01 | Phase 7 | Pending |
| SYNC-02 | Phase 7 | Pending |
| SYNC-03 | Phase 7 | Pending |
| TASK-01 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 30 total
- Mapped to phases: 30
- Unmapped: 0

---
*Requirements defined: 2026-02-01*
*Last updated: 2026-02-02 after Phase 4 completion*
