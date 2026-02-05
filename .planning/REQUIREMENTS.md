# Requirements: Memora Platform

**Defined:** 2026-02-03
**Core Value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.

## v1.3 Requirements

Requirements for milestone v1.3: Leaderboard Profiles & Admin Device Management.

### Profile Display Names

- [x] **PROF-01**: Leaderboard responses include display_name from Memora Player Profile
- [x] **PROF-02**: Leaderboard responses include avatar from Memora Player Profile
- [x] **PROF-03**: ProfileService caches profiles in Redis hash (1hr TTL)
- [x] **PROF-04**: Batch profile lookup via Redis pipeline (<25ms for 100 entries)
- [x] **PROF-05**: Profile cache invalidated on Memora Player Profile update

### Admin Device Management

- [ ] **ADMDEV-01**: Admin can view player's registered devices in Frappe Desk
- [ ] **ADMDEV-02**: Device data synced from Redis to Frappe child table
- [ ] **ADMDEV-03**: Admin can remove a device (clears from Redis)

### JWT Simplification

- [x] **JWT-01**: Add plan_id to access token (from Memora Player Profile)
- [x] **JWT-02**: Remove timezone from access token (hardcode Asia/Amman)
- [x] **JWT-03**: Remove role from access token (all API users are players)

## Future Requirements

Deferred to future milestones. Tracked but not in current roadmap.

### Leaderboards

- **LEADER-03**: Streak leaderboard ranks players by current streak length

### Device Management

- **DEV-01**: User can view their registered devices (user-facing)
- **DEV-02**: User can remove their own devices (user-facing)
- **DEV-03**: Audit trail for admin device removals

### Session Security

- **SEC-01**: Immediate session invalidation on device removal (token blocklist)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| User-facing device management | Admin-only for v1.3, defer to future |
| Session invalidation on removal | Token expiry (15 min) is acceptable for v1.3 |
| Audit trail for device actions | Not needed for basic admin tooling |
| Streak leaderboard | Deferred from v1.3 to keep scope tight |
| Real-time profile updates | Cache TTL + invalidation is sufficient |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| PROF-01 | Phase 14 | Complete |
| PROF-02 | Phase 14 | Complete |
| PROF-03 | Phase 14 | Complete |
| PROF-04 | Phase 14 | Complete |
| PROF-05 | Phase 14 | Complete |
| JWT-01 | Phase 15 | Complete |
| JWT-02 | Phase 15 | Complete |
| JWT-03 | Phase 15 | Complete |
| ADMDEV-01 | Phase 16 | Pending |
| ADMDEV-02 | Phase 16 | Pending |
| ADMDEV-03 | Phase 16 | Pending |

**Coverage:**
- v1.3 requirements: 11 total
- Mapped to phases: 11
- Unmapped: 0

---
*Requirements defined: 2026-02-03*
*Last updated: 2026-02-05 after Phase 15 completion*
