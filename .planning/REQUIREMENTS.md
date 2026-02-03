# Requirements: Memora v1.1

**Defined:** 2026-02-02
**Core Value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.

## v1.1 Requirements

Requirements for v1.1 feature expansion. Each maps to roadmap phases.

### Game Sessions

- [x] **SESSION-01**: Start session creates Redis hash with lesson metadata and 1-hour TTL
- [x] **SESSION-02**: Stage completion updates session with interaction data
- [x] **SESSION-03**: End session finalizes lesson and triggers completion flow (XP, progress, streak)
- [x] **SESSION-04**: Session validation rejects stage completions without active session
- [x] **SESSION-05**: Session recovery allows resuming mid-lesson after app crash
- [x] **SESSION-06**: Concurrent session detection prevents same user in multiple lessons simultaneously

### Leaderboards

- [ ] **LEADER-01**: All-time XP leaderboard ranks players by total XP earned
- [ ] **LEADER-02**: Daily XP leaderboard ranks players by XP earned today
- [ ] **LEADER-03**: Streak leaderboard ranks players by current streak length
- [ ] **LEADER-04**: User can retrieve their rank position in any leaderboard
- [ ] **LEADER-05**: Daily leaderboard resets at midnight (archived for 30 days)

### Device Management

- [x] **DEVICE-01**: Device is registered with metadata on login
- [x] **DEVICE-02**: Login is rejected when user exceeds 3-device limit

### Scheduled Tasks

- [ ] **SCHED-01**: Daily streak reset runs at midnight for users who missed activity
- [ ] **SCHED-02**: Hourly session cleanup removes expired session keys
- [ ] **SCHED-03**: Daily leaderboard reset archives yesterday and creates new daily key

## Future Requirements

Deferred to later milestones. Tracked but not in v1.1 roadmap.

### Device Management (v1.2)

- **DEVICE-03**: User can list their authorized devices
- **DEVICE-04**: User can deauthorize a device remotely
- **DEVICE-05**: Automatic cleanup removes devices inactive for 90+ days

### Leaderboards (v1.2)

- **LEADER-06**: Cached leaderboard results (5-minute TTL) for performance at scale
- **LEADER-07**: User context shows ±2 positions around user's rank
- **LEADER-08**: Weekly XP leaderboard with Sunday midnight reset

### Game Sessions (v1.2)

- **SESSION-07**: Idle timeout warning before session expires
- **SESSION-08**: Multi-device session sync shows active session across devices

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| League-based leaderboards | Complex cohort logic; requires larger user base for meaningful cohorts |
| Real-time leaderboard updates | WebSocket complexity; cached results sufficient for v1.1 scale |
| Redis keyspace notifications | Optimization; cron-based cleanup works for v1.1 scale |
| Device fingerprinting | Privacy concerns; device limit enforcement sufficient |
| 2FA for new devices | Adds complexity; basic device management first |
| Session idle timeout warnings | Requires WebSocket or polling; TTL expiration sufficient |
| Passwordless authentication | Out of v1.1 scope; consider for v2.0 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DEVICE-01 | Phase 8 | Complete |
| DEVICE-02 | Phase 8 | Complete |
| SESSION-01 | Phase 9 | Complete |
| SESSION-02 | Phase 9 | Complete |
| SESSION-03 | Phase 9 | Complete |
| SESSION-04 | Phase 9 | Complete |
| SESSION-05 | Phase 9 | Complete |
| SESSION-06 | Phase 9 | Complete |
| LEADER-01 | Phase 10 | Pending |
| LEADER-02 | Phase 10 | Pending |
| LEADER-03 | Phase 10 | Pending |
| LEADER-04 | Phase 10 | Pending |
| LEADER-05 | Phase 10 | Pending |
| SCHED-01 | Phase 11 | Pending |
| SCHED-02 | Phase 11 | Pending |
| SCHED-03 | Phase 11 | Pending |

**Coverage:**
- v1.1 requirements: 16 total
- Mapped to phases: 16/16 (100%)
- Unmapped: 0

**Phase mapping:**
- Phase 8 (Device Management): 2 requirements
- Phase 9 (Game Sessions): 6 requirements
- Phase 10 (Leaderboards): 5 requirements
- Phase 11 (Scheduled Tasks): 3 requirements

---
*Requirements defined: 2026-02-02*
*Last updated: 2026-02-03 after Phase 9 completion*
