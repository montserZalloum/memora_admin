# Requirements: Memora Platform

**Defined:** 2026-02-01
**Core Value:** Students can track their learning progress and earn rewards with instant feedback and sub-second response times

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Infrastructure

- [ ] **INFRA-01**: FastAPI project structure with lifespan events and dependency injection
- [ ] **INFRA-02**: Redis async connection pooling with shared Frappe instance
- [ ] **INFRA-03**: Nginx reverse proxy routing (/api/v1/* → FastAPI, /api/method/* → Frappe)
- [ ] **INFRA-04**: Portable configuration via .env file (Redis URL, JWT secret, paths)
- [ ] **INFRA-05**: Server migration documentation for Redis/FastAPI portability

### Authentication

- [ ] **AUTH-01**: Login endpoint verifies Frappe credentials and issues JWT access + refresh tokens
- [ ] **AUTH-02**: Refresh endpoint exchanges refresh token for new access token
- [ ] **AUTH-03**: FastAPI middleware verifies JWT tokens statelessly (no database lookup)

### Access Control

- [ ] **ACCESS-01**: Gate 1 validates season status (active) and end timestamp (not expired)
- [ ] **ACCESS-02**: Gate 2 checks player access set (direct grants + plan membership lookup)
- [ ] **ACCESS-03**: Free preview logic bypasses Gate 2 for Units/Topics with is_free=true
- [ ] **ACCESS-04**: Payment webhook grants access via Redis SADD and creates MariaDB subscription record
- [ ] **ACCESS-05**: Admin can manually grant player access from Frappe Desk UI

### Progress Tracking

- [ ] **PROG-01**: Redis bitmap stores lesson completion per player-subject (SETBIT/GETBIT)
- [ ] **PROG-02**: Unlock state calculation respects is_linear flags at Track/Unit/Topic levels
- [ ] **PROG-03**: API endpoint marks lesson complete and updates bitmap

### Wallet & Gamification

- [ ] **WALLET-01**: XP accumulates in Redis hash (wallet:{player_id}) on lesson completion
- [ ] **WALLET-02**: Streak tracks consecutive learning days with streak_date field
- [ ] **WALLET-03**: Replaying completed lessons awards reduced XP (50%)

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
| INFRA-01 | TBD | Pending |
| INFRA-02 | TBD | Pending |
| INFRA-03 | TBD | Pending |
| INFRA-04 | TBD | Pending |
| INFRA-05 | TBD | Pending |
| AUTH-01 | TBD | Pending |
| AUTH-02 | TBD | Pending |
| AUTH-03 | TBD | Pending |
| ACCESS-01 | TBD | Pending |
| ACCESS-02 | TBD | Pending |
| ACCESS-03 | TBD | Pending |
| ACCESS-04 | TBD | Pending |
| ACCESS-05 | TBD | Pending |
| PROG-01 | TBD | Pending |
| PROG-02 | TBD | Pending |
| PROG-03 | TBD | Pending |
| WALLET-01 | TBD | Pending |
| WALLET-02 | TBD | Pending |
| WALLET-03 | TBD | Pending |
| BUILD-01 | TBD | Pending |
| BUILD-02 | TBD | Pending |
| BUILD-03 | TBD | Pending |
| BUILD-04 | TBD | Pending |
| BUILD-05 | TBD | Pending |
| BUILD-06 | TBD | Pending |
| BUILD-07 | TBD | Pending |
| SYNC-01 | TBD | Pending |
| SYNC-02 | TBD | Pending |
| SYNC-03 | TBD | Pending |
| TASK-01 | TBD | Pending |

**Coverage:**
- v1 requirements: 22 total
- Mapped to phases: 0
- Unmapped: 22 ⚠️

---
*Requirements defined: 2026-02-01*
*Last updated: 2026-02-01 after initial definition*
