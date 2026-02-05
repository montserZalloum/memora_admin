# Roadmap: Memora Platform

## Milestones

- SHIPPED **v1.0 MVP** — Phases 1-7 (shipped 2026-02-02)
- SHIPPED **v1.1 Feature Expansion** — Phases 8-11 (shipped 2026-02-03)
- SHIPPED **v1.2 Plan System Enhancement** — Phase 12 (shipped 2026-02-03)
- SHIPPED **v1.2.1 Gap Closure** — Phase 13 (shipped 2026-02-03)
- IN PROGRESS **v1.3 Leaderboard Profiles & Admin Device Management** — Phases 14-16

## Phases

<details>
<summary>v1.0 MVP (Phases 1-7) — SHIPPED 2026-02-02</summary>

- [x] Phase 1: Project Foundation (3/3 plans) — completed 2026-02-01
- [x] Phase 2: Authentication (4/4 plans) — completed 2026-02-01
- [x] Phase 3: Access Control (4/4 plans) — completed 2026-02-01
- [x] Phase 4: Progress Tracking (3/3 plans) — completed 2026-02-02
- [x] Phase 5: Gamification (4/4 plans) — completed 2026-02-02
- [x] Phase 6: Content Pipeline (4/4 plans) — completed 2026-02-02
- [x] Phase 7: Sync Mechanisms (4/4 plans) — completed 2026-02-02

See: `.planning/milestones/v1.0-ROADMAP.md` for full details

</details>

<details>
<summary>v1.1 Feature Expansion (Phases 8-11) — SHIPPED 2026-02-03</summary>

- [x] Phase 8: Device Management (2/2 plans) — completed 2026-02-03
- [x] Phase 9: Game Sessions (4/4 plans) — completed 2026-02-03
- [x] Phase 10: Leaderboards (3/3 plans) — completed 2026-02-03
- [x] Phase 11: Scheduled Tasks (4/4 plans) — completed 2026-02-03

See: `.planning/milestones/v1.1-ROADMAP.md` for full details

</details>

<details>
<summary>v1.2 Plan System Enhancement (Phase 12) — SHIPPED 2026-02-03</summary>

- [x] Phase 12: Plan System Enhancement (4/4 plans) — completed 2026-02-03

See: `.planning/milestones/v1.2-ROADMAP.md` for full details

</details>

<details>
<summary>v1.2.1 Gap Closure (Phase 13) — SHIPPED 2026-02-03</summary>

- [x] Phase 13: Plan Cache Invalidation Fix (1/1 plans) — completed 2026-02-03

See: `.planning/phases/13-plan-cache-invalidation-fix/13-VERIFICATION.md` for details

</details>

### v1.3 Leaderboard Profiles & Admin Device Management (In Progress)

**Milestone Goal:** Enhance leaderboards with player display names and avatars, simplify JWT token structure, and provide admin tooling for device management.

#### Phase 14: Profile Display Names
**Goal**: Leaderboard responses show human-readable display names and avatars instead of player IDs
**Depends on**: Phase 13 (existing leaderboard infrastructure)
**Requirements**: PROF-01, PROF-02, PROF-03, PROF-04, PROF-05
**Success Criteria** (what must be TRUE):
  1. Leaderboard API returns display_name and avatar for each entry (not player_id placeholders)
  2. Profile data cached in Redis with 1-hour TTL (sub-2ms lookup)
  3. Batch profile fetch for 100 entries completes in under 25ms total
  4. Profile cache invalidates within seconds when admin updates Memora Player Profile
  5. Missing profiles gracefully fall back to "Player XXXX" format
**Plans**: 3 plans

Plans:
- [x] 14-01: ProfileService + Cache Infrastructure — completed 2026-02-05
- [x] 14-02: Frappe Integration (profile_sync hook, pub/sub handler) — completed 2026-02-05
- [x] 14-03: Leaderboard Enrichment (inject ProfileService, modify response) — completed 2026-02-05

#### Phase 15: JWT Simplification
**Goal**: Streamline access token payload, enable mobile login, and enrich login response with profile data
**Depends on**: Phase 14 (no code dependency, but sequential for testing)
**Requirements**: JWT-01, JWT-02, JWT-03
**Success Criteria** (what must be TRUE):
  1. Access token includes plan_id field (from Memora Player Profile)
  2. Access token no longer contains timezone field (hardcoded to Asia/Amman in code)
  3. Access token no longer contains role field (all API users are players)
  4. Login accepts email or mobile number (identifier field)
  5. Login response includes profile data (display_name, avatar, gender, xp)
  6. Plan change invalidates session (player must re-login)
**Plans**: 2 plans

Plans:
- [ ] 15-01-PLAN.md — Schema update + token payload changes + session storage
- [ ] 15-02-PLAN.md — Identifier login + enriched response + plan change hook

#### Phase 16: Admin Device Management
**Goal**: Admins can view and remove player devices from Frappe Desk
**Depends on**: Phase 15 (no code dependency, but sequential for milestone flow)
**Requirements**: ADMDEV-01, ADMDEV-02, ADMDEV-03
**Success Criteria** (what must be TRUE):
  1. Admin can see all registered devices in Memora Player Profile form
  2. Device list shows device_name, platform, and last_login for each device
  3. Admin can remove a specific device via UI action with confirmation dialog
  4. Device removal clears the device from Redis (source of truth)
  5. Frappe child table reflects current Redis device state (synced on form load)
**Plans**: 2 plans

Plans:
- [ ] 16-01: Device Sync to Frappe (Redis -> authorized_devices child table)
- [ ] 16-02: Device Removal UI (form script with removal dialog)

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Project Foundation | v1.0 | 3/3 | Complete | 2026-02-01 |
| 2. Authentication | v1.0 | 4/4 | Complete | 2026-02-01 |
| 3. Access Control | v1.0 | 4/4 | Complete | 2026-02-01 |
| 4. Progress Tracking | v1.0 | 3/3 | Complete | 2026-02-02 |
| 5. Gamification | v1.0 | 4/4 | Complete | 2026-02-02 |
| 6. Content Pipeline | v1.0 | 4/4 | Complete | 2026-02-02 |
| 7. Sync Mechanisms | v1.0 | 4/4 | Complete | 2026-02-02 |
| 8. Device Management | v1.1 | 2/2 | Complete | 2026-02-03 |
| 9. Game Sessions | v1.1 | 4/4 | Complete | 2026-02-03 |
| 10. Leaderboards | v1.1 | 3/3 | Complete | 2026-02-03 |
| 11. Scheduled Tasks | v1.1 | 4/4 | Complete | 2026-02-03 |
| 12. Plan System Enhancement | v1.2 | 4/4 | Complete | 2026-02-03 |
| 13. Plan Cache Invalidation Fix | v1.2.1 | 1/1 | Complete | 2026-02-03 |
| 14. Profile Display Names | v1.3 | 3/3 | Complete | 2026-02-05 |
| 15. JWT Simplification | v1.3 | 0/2 | Not started | - |
| 16. Admin Device Management | v1.3 | 0/2 | Not started | - |

**Total:** 16 phases, 51 plans completed, 4 plans pending (v1.3)
