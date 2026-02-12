# Roadmap: Memora Platform

## Milestones

- SHIPPED **v1.0 MVP** — Phases 1-7 (shipped 2026-02-02)
- SHIPPED **v1.1 Feature Expansion** — Phases 8-11 (shipped 2026-02-03)
- SHIPPED **v1.2 Plan System Enhancement** — Phase 12 (shipped 2026-02-03)
- SHIPPED **v1.2.1 Gap Closure** — Phase 13 (shipped 2026-02-03)
- SHIPPED **v1.3 Leaderboard Profiles & Admin Device Management** — Phases 14-20 (shipped 2026-02-07)
- SHIPPED **v1.4 Product Store** — Phases 21-23 (shipped 2026-02-08)
- SHIPPED **v1.5 Real-Time Notifications** — Phase 24 (shipped 2026-02-08)
- SHIPPED **v1.6 FSRS Review System** — Phase 25 (shipped 2026-02-09)
- SHIPPED **v1.7 Profile Page API** — Phase 26 (shipped 2026-02-10)
- SHIPPED **v1.8 Memory State Redesign** — Phase 27 (shipped 2026-02-11)
- SHIPPED **v1.9 Tech Debt & Reliability Fixes** — Phase 28 (shipped 2026-02-12)
- IN PROGRESS **v2.0 Mobile-First Player Authentication** — Phases 29-32

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

<details>
<summary>v1.3 Leaderboard Profiles & Admin Device Management (Phases 14-20) — SHIPPED 2026-02-07</summary>

- [x] Phase 14: Profile Display Names (3/3 plans) — completed 2026-02-05
- [x] Phase 15: JWT Simplification (2/2 plans) — completed 2026-02-05
- [x] Phase 16: Admin Device Management (2/2 plans) — completed 2026-02-07
- [x] Phase 17: Progress API Optimization (2/2 plans) — completed 2026-02-05
- [x] Phase 18: Lesson Completion Status API (1/1 plan) — completed 2026-02-06
- [x] Phase 19: Stage Content Editor (2/2 plans) — completed 2026-02-07
- [x] Phase 20: Lesson Complete Pipeline Overhaul (4/4 plans) — completed 2026-02-07

See: `.planning/milestones/v1.3-ROADMAP.md` for full details

</details>

<details>
<summary>v1.4 Product Store (Phases 21-23) — SHIPPED 2026-02-08</summary>

- [x] Phase 21: Product Catalog API (2/2 plans) — completed 2026-02-08
- [x] Phase 22: Purchase Request Flow (2/2 plans) — completed 2026-02-08
- [x] Phase 23: Approval and Access Grant (1/1 plan) — completed 2026-02-08

See: `.planning/milestones/v1.4-ROADMAP.md` for full details (archived with v1.9)

</details>

<details>
<summary>v1.5 Real-Time Notifications (Phase 24) — SHIPPED 2026-02-08</summary>

- [x] Phase 24: Real-Time Subscription Notifications (2/2 plans) — completed 2026-02-08

See: `.planning/milestones/v1.5-ROADMAP.md` for full details (archived with v1.9)

</details>

<details>
<summary>v1.6 FSRS Review System (Phase 25) — SHIPPED 2026-02-09</summary>

- [x] Phase 25: FSRS Review System (3/3 plans) — completed 2026-02-09

See: `.planning/milestones/v1.6-ROADMAP.md` for full details (archived with v1.9)

</details>

<details>
<summary>v1.7 Profile Page API (Phase 26) — SHIPPED 2026-02-10</summary>

- [x] Phase 26: Profile Page API (2/2 plans) — completed 2026-02-10

See: `.planning/milestones/v1.7-ROADMAP.md` for full details (archived with v1.9)

</details>

<details>
<summary>v1.8 Memory State Redesign (Phase 27) — SHIPPED 2026-02-11</summary>

- [x] Phase 27: Memory State Redesign (5/5 plans) — completed 2026-02-11

See: `.planning/milestones/v1.8-ROADMAP.md` for full details (archived with v1.9)

</details>

<details>
<summary>v1.9 Tech Debt & Reliability Fixes (Phase 28) — SHIPPED 2026-02-12</summary>

- [x] Phase 28: Tech Debt & Reliability Fixes (4/4 plans) — completed 2026-02-11

See: `.planning/milestones/v1.9-ROADMAP.md` for full details

</details>

### v2.0 Mobile-First Player Authentication (In Progress)

**Milestone Goal:** Replace Frappe User-based email authentication for players with phone+password model on Player Profile DocType. Players authenticate via phone number + password stored directly in Memora Player Profile. Admins keep Frappe User email auth.

- [ ] **Phase 29: DocType Schema Foundation** - Player Profile schema changes for phone+password identity
- [ ] **Phase 30: Frappe Auth API Bridge** - Whitelisted Frappe APIs for password verification and player management
- [ ] **Phase 31: FastAPI Auth Endpoints + OTP System** - Player-facing login, registration, password reset, and OTP
- [ ] **Phase 32: Event Handler & API Migration** - Update all code referencing old identity model

#### Phase 29: DocType Schema Foundation

**Goal:** Player Profile DocType supports phone+password identity with proper hashing, normalization, and backward compatibility
**Depends on:** Phase 28 (v1.9 complete)
**Requirements:** SCHEMA-01, SCHEMA-02, SCHEMA-03, SCHEMA-04, SCHEMA-05, SCHEMA-06, SEC-03
**Success Criteria** (what must be TRUE):
  1. New Player Profile created via Frappe Desk is autonamed `PLAYER-00001` (not email-based)
  2. Phone number stored as digits-only (non-digit characters stripped, 9-15 digit length enforced) and UNIQUE constraint prevents duplicates
  3. Password stored as PBKDF2-SHA256 hash in `__Auth` table (not Fernet-encrypted in Password fieldtype), verified by `check_password()` returning docname
  4. Existing code referencing `doc.user` continues to work (field exists, nullable, not required)
  5. Passwords under 8 characters rejected by validate() with clear error message
**Plans:** TBD

#### Phase 30: Frappe Auth API Bridge

**Goal:** FastAPI can verify player passwords and manage player accounts through Frappe without creating Frappe sessions
**Depends on:** Phase 29
**Requirements:** MIGR-05, RESET-06
**Success Criteria** (what must be TRUE):
  1. `curl` to `verify_player_password(mobile, password)` whitelisted API returns player profile data on success and 401-equivalent on failure, without creating a Frappe session
  2. `curl` to `register_player(mobile, password, display_name)` creates a new Player Profile with hashed password and returns the docname
  3. `curl` to `set_player_password(player_name, new_password)` updates the password hash and can be called from admin context (Frappe Desk)
  4. Admin can reset a player's password from the Player Profile form in Frappe Desk, and the player's existing sessions are invalidated
**Plans:** TBD

#### Phase 31: FastAPI Auth Endpoints + OTP System

**Goal:** Players can register, log in, and reset passwords via the mobile app using phone number + password, with OTP verification for registration and password reset
**Depends on:** Phase 30
**Requirements:** AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, REG-01, REG-02, REG-03, REG-04, REG-05, REG-06, RESET-01, RESET-02, RESET-03, RESET-04, RESET-05, SEC-01, SEC-02, SEC-04, SEC-05, MIGR-01, MIGR-02, MIGR-07
**Success Criteria** (what must be TRUE):
  1. Player can register by sending OTP to phone, verifying with "1111", and receiving JWT tokens -- the created Player Profile has wallet and Redis state initialized
  2. Player can log in with phone+password via `POST /auth/player/login` and receives tokens plus profile data (display_name, avatar, gender, XP) in a single response
  3. Admin can log in with email+password via `POST /auth/admin/login` using existing Frappe User flow, and both player and admin can refresh tokens via `POST /auth/refresh`
  4. Player can reset forgotten password via 3-step OTP flow (request OTP, verify OTP to get temp token, set new password with temp token) and all existing sessions are invalidated afterward
  5. OTP sending is rate-limited (3/phone/10min, 10/IP/10min), verification attempts limited (3 incorrect = OTP invalidated), and resend has 60-second cooldown
**Plans:** TBD

#### Phase 32: Event Handler & API Migration

**Goal:** All event handlers and Frappe APIs work with the new Player Profile identity model (docname-based instead of user-based)
**Depends on:** Phase 31
**Requirements:** MIGR-03, MIGR-04, MIGR-06
**Success Criteria** (what must be TRUE):
  1. Subscription change for a player (created with PLAYER-##### naming) correctly syncs access grant to Redis and invalidates the player's session
  2. Purchase flow, profile update, and device removal all work for PLAYER-##### named profiles without `{"user": player_id}` lookups
  3. plan_change_sync.py and profile_sync.py write to the FastAPI Redis instance (`get_fastapi_redis()`) instead of `frappe.cache()`, verified by checking Redis keys after triggering sync events
**Plans:** TBD

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
| 15. JWT Simplification | v1.3 | 2/2 | Complete | 2026-02-05 |
| 16. Admin Device Management | v1.3 | 2/2 | Complete | 2026-02-07 |
| 17. Progress API Optimization | v1.3 | 2/2 | Complete | 2026-02-05 |
| 18. Lesson Completion Status API | v1.3 | 1/1 | Complete | 2026-02-06 |
| 19. Stage Content Editor | v1.3 | 2/2 | Complete | 2026-02-07 |
| 20. Lesson Complete Pipeline Overhaul | v1.3 | 4/4 | Complete | 2026-02-07 |
| 21. Product Catalog API | v1.4 | 2/2 | Complete | 2026-02-08 |
| 22. Purchase Request Flow | v1.4 | 2/2 | Complete | 2026-02-08 |
| 23. Approval and Access Grant | v1.4 | 1/1 | Complete | 2026-02-08 |
| 24. Real-Time Subscription Notifications | v1.5 | 2/2 | Complete | 2026-02-08 |
| 25. FSRS Review System | v1.6 | 3/3 | Complete | 2026-02-09 |
| 26. Profile Page API | v1.7 | 2/2 | Complete | 2026-02-10 |
| 27. Memory State Redesign | v1.8 | 5/5 | Complete | 2026-02-11 |
| 28. Tech Debt & Reliability Fixes | v1.9 | 4/4 | Complete | 2026-02-11 |
| 29. DocType Schema Foundation | v2.0 | 0/TBD | Not started | - |
| 30. Frappe Auth API Bridge | v2.0 | 0/TBD | Not started | - |
| 31. FastAPI Auth Endpoints + OTP System | v2.0 | 0/TBD | Not started | - |
| 32. Event Handler & API Migration | v2.0 | 0/TBD | Not started | - |

**Total:** 32 phases (85 plans complete, v2.0 plans TBD) across 12 milestones
