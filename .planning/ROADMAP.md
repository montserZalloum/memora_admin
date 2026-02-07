# Roadmap: Memora Platform

## Milestones

- SHIPPED **v1.0 MVP** — Phases 1-7 (shipped 2026-02-02)
- SHIPPED **v1.1 Feature Expansion** — Phases 8-11 (shipped 2026-02-03)
- SHIPPED **v1.2 Plan System Enhancement** — Phase 12 (shipped 2026-02-03)
- SHIPPED **v1.2.1 Gap Closure** — Phase 13 (shipped 2026-02-03)
- IN PROGRESS **v1.3 Leaderboard Profiles & Admin Device Management** — Phases 14-20

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
- [x] 15-01: JWT token structure + session storage — completed 2026-02-05
- [x] 15-02: Identifier login + enriched response + plan change hook — completed 2026-02-05

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
- [ ] 16-01-PLAN.md — Backend device APIs (sync from Redis + remove device + fix device_sync.py bugs)
- [ ] 16-02-PLAN.md — Frontend device management UI (form-load sync, read-only table, per-row Remove button)

#### Phase 17: Progress API Optimization
**Goal**: Scalable progress tracking with caching and streaming for next-gen UX
**Depends on**: Phase 16 (no code dependency, but sequential for milestone flow)
**Requirements**: PROG-OPT-01, PROG-OPT-02
**Success Criteria** (what must be TRUE):
  1. Progress stats cached in Redis hash (subject/track/unit/topic completion counts)
  2. Lesson completion updates cached stats atomically (O(1) instead of O(N) recomputation)
  3. GET /progress/{subject} returns in <10ms regardless of subject size (50K+ lessons)
  4. SSE streaming endpoint delivers progress data progressively (subject header first, then tracks)
  5. Client receives first data chunk within 10ms of request
  6. Existing bitmap storage unchanged (backward compatible)
**Plans**: 2 plans

Plans:
- [x] 17-01: Progress Stats Caching Layer (Redis hash + completion hook updates) — completed 2026-02-05
- [x] 17-02: SSE Streaming Endpoint (progressive progress delivery) — completed 2026-02-05

#### Phase 18: Lesson Completion Status API
**Goal**: Fast per-lesson completion lookups for topic pages at scale (100K concurrent players, 100+ lessons per topic)
**Depends on**: Phase 17 (leverages existing bitmap infrastructure)
**Requirements**: LESSON-STATUS-01, LESSON-STATUS-02
**Success Criteria** (what must be TRUE):
  1. GET /progress/{subject}/topics/{topic_id}/lessons returns completion status for all lessons in topic
  2. Response includes lesson_id, bit_index, and completed (boolean) for each lesson
  3. Endpoint returns in <5ms regardless of lesson count (leverages existing bitmap)
  4. Works correctly with 100K concurrent players (stateless, Redis-only lookups)
  5. Integrates with existing bit_index system (no new storage, O(1) per-lesson lookup)
**Plans**: 1 plan

Plans:
- [x] 18-01: Lesson completion status endpoint (models + endpoint) — completed 2026-02-06

#### Phase 19: Stage Content Editor
**Goal**: Provide inline content editing dialogs for lesson stages based on stage type
**Depends on**: None (Frappe-only feature, independent of FastAPI)
**Requirements**: STAGE-EDIT-01
**Success Criteria** (what must be TRUE):
  1. "Edit Content" button appears in Memora Lesson Stage child table rows
  2. Clicking button opens type-specific dialog based on stage_type Link value
  3. Dialog pre-populates with existing config_json data (if any)
  4. Save action serializes dialog values to JSON and stores in config_json field
  5. Supported stage types: MATCHING, REVEAL, SENTENCE_BUILDER (extensible pattern)
  6. Unsupported stage types show informative message
**Plans**: 2 plans

Plans:
- [x] 19-01: Stage Content Editor Wiring — completed 2026-02-07
- [x] 19-02: Use Frappe name field as stage_id in lesson.json — completed 2026-02-07

#### Phase 20: Lesson Complete Pipeline Overhaul
**Goal**: Overhaul the lesson completion pipeline for 100k concurrent users: implement FSRS spaced repetition, fix XP calculation bugs, optimize Redis operations to ~4 round-trips via Lua scripts + pipelining, implement hearts bonus XP, and remove the legacy progress/complete endpoint.
**Depends on**: Phase 19 (stage_id fix needed for FSRS stage tracking)
**Success Criteria** (what must be TRUE):
  1. Hierarchy API returns correct `base_xp` and `max_hearts` per lesson (not hardcoded 10)
  2. When lesson `base_xp`/`max_hearts` is 0, falls back to Memora Settings defaults
  3. Hearts bonus XP calculated: `remaining_hearts * xp_per_heart`, added before streak multiplier
  4. `StageResult.time_spent` accepts milliseconds (not seconds)
  5. Session/end hot path completes in <10ms with ~4 Redis round-trips (Lua + pipeline)
  6. All stage data batched into single RPUSH (not N pushes per stage)
  7. Legacy `POST /progress/complete` endpoint removed
  8. FSRS background task processes non-skippable stages every minute
  9. FSRS state (stability, difficulty, next_review) persisted in Redis + Memora Memory State DocType
  10. Skippable stages (from Lesson Stage Settings `is_skippable`) excluded from FSRS processing
  11. `fsrs` package installed and FSRS scheduler uses weights from Memora Settings
**Plans**: 4 plans

Plans:
- [x] 20-01-PLAN.md — Hierarchy & settings enrichment (base_xp, max_hearts, xp_per_heart + fsrs dep) — completed 2026-02-07
- [x] 20-02-PLAN.md — StageResult time_spent to milliseconds + legacy endpoint removal — completed 2026-02-07
- [x] 20-03-PLAN.md — Lua session_complete script + pipeline hot path rewrite + hearts XP — completed 2026-02-07
- [x] 20-04-PLAN.md — FSRS background task (scheduled processor + hooks registration) — completed 2026-02-07

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
| 16. Admin Device Management | v1.3 | 0/2 | Not started | - |
| 17. Progress API Optimization | v1.3 | 2/2 | Complete | 2026-02-05 |
| 18. Lesson Completion Status API | v1.3 | 1/1 | Complete | 2026-02-06 |
| 19. Stage Content Editor | v1.3 | 2/2 | Complete | 2026-02-07 |
| 20. Lesson Complete Pipeline Overhaul | v1.3 | 4/4 | Complete | 2026-02-07 |

**Total:** 20 phases, 62 plans completed, 2 plans pending (v1.3)
