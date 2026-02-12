# Project Milestones: Memora Platform

## v1.9 Tech Debt & Reliability Fixes (Shipped: 2026-02-12)

**Delivered:** Fixed data reliability issues, eliminated code duplication across FastAPI/Frappe runtimes, and cleaned up dead code — hardening the codebase for production scale.

**Phases completed:** 28 (4 plans total)

**Key accomplishments:**
- Fixed interaction buffer LTRIM race condition preventing silent data loss on partial flush
- Unified Redis key constants into single source of truth (`fastapi_app/core/constants.py`)
- Consolidated 16 copy-pasted Redis client constructions into `RedisClient` sub-dependency pattern
- Removed 136 lines of dead code (log_slow_redis, deprecated models, SSE models, stale exports)
- Created reusable `RequireAdmin` dependency replacing 4 inline magic string checks
- Hardened input validation (Path regex on player_id, safe Lua tonumber patterns)

**Stats:**
- 19 files modified
- -122 net lines Python (189 added, 311 removed) — 17,450 total Python LOC
- 1 phase, 4 plans, ~9 tasks
- 1 day (2026-02-11 → 2026-02-12)

**Git range:** `docs(28): research` → `test(28): complete UAT`

**What's next:** Planning next milestone

---

## v1.8 Memory State Redesign (Shipped: 2026-02-11)

**Delivered:** Replaced composite-string PK with BIGINT AUTO_INCREMENT, added item-level FSRS tracking, and implemented RANGE partitioning by season for scalability to 25B+ rows.

**Phases completed:** 27 (5 plans total)

**Key accomplishments:**
- Schema redesign: BIGINT AUTO_INCREMENT PK, BINARY(16) item_id, season_seq partitioning
- Item-level FSRS tracking (1 memory state per sub-element within a stage)
- Content pipeline updated with item UUID generation for all interactive stage types
- Review system and profile mastery updated to count items instead of stages
- Gap closure: skippable stages excluded from item_id generation

**Stats:**
- 1 phase, 5 plans
- Shipped 2026-02-11

**Git range:** `docs(27): research` → Phase 27 completion

---

## v1.7 Profile Page API (Shipped: 2026-02-10)

**Delivered:** Backend API endpoints for player profile page with avatar selection, subject-filtered stats, memory mastery breakdown, weekly activity chart, and logout.

**Phases completed:** 26 (2 plans total)

**Key accomplishments:**
- Hero section API (avatar, username, level, XP progress)
- Subject-filtered stats grid (streak, items learned, XP)
- Memory mastery breakdown (mature/learning/new) and weekly activity chart
- Avatar selection from predefined DocType options + logout endpoint

**Stats:**
- 1 phase, 2 plans
- Shipped 2026-02-10

**Git range:** Phase 26 commits

---

## v1.6 FSRS Review System (Shipped: 2026-02-09)

**Delivered:** Spaced repetition review system with daily review sessions per subject, batched in groups of 10 stages, with FSRS scheduling and bug fixes.

**Phases completed:** 25 (3 plans total)

**Key accomplishments:**
- Fixed FSRS bugs (skippable filter, is_reviewable enforcement, date clamping)
- Review API endpoints (overview, due stages, submit with inline FSRS)
- MariaDB composite index for 200K+ users review queries
- 3 XP per review session with Redis-cached overview

**Stats:**
- 1 phase, 3 plans
- Shipped 2026-02-09

**Git range:** Phase 25 commits

---

## v1.5 Real-Time Notifications (Shipped: 2026-02-08)

**Delivered:** WebSocket notification system with Redis pub/sub for instant subscription updates, replacing deprecated SSE.

**Phases completed:** 24 (2 plans total)

**Key accomplishments:**
- WebSocket notification system with ConnectionManager for 100K+ concurrent users
- Redis pub/sub channel per user for cross-instance notification delivery
- JWT-authenticated WebSocket connections with graceful disconnect cleanup
- Deprecated SSE endpoint and sse-starlette dependency removed

**Stats:**
- 1 phase, 2 plans
- Shipped 2026-02-08

**Git range:** Phase 24 commits

---

## v1.4 Product Store (Shipped: 2026-02-08)

**Delivered:** Players can discover available products for their plan and submit purchase requests, with admin approval flow granting content access.

**Phases completed:** 21-23 (5 plans total)

**Key accomplishments:**
- Product catalog API with Redis caching and event-driven invalidation
- Purchase request flow creating Subscription Transactions with admin approval
- Approval handler auto-creating subscriptions and syncing access to Redis
- Catalog excludes already-purchased and pending products

**Stats:**
- 3 phases, 5 plans
- Shipped 2026-02-08

**Git range:** Phase 21-23 commits

---

## v1.3 Leaderboard Profiles & Admin Device Management (Shipped: 2026-02-07)

**Delivered:** Enhanced leaderboards with player profiles, simplified JWT tokens with mobile login, admin device management, optimized progress APIs with caching/streaming, lesson completion status lookups, stage content editor, and overhauled lesson completion pipeline with FSRS spaced repetition.

**Phases completed:** 14-20 (16 plans total)

**Key accomplishments:**
- Leaderboard profile enrichment with Redis-cached batch lookups (<25ms for 100 entries)
- JWT simplification (plan_id added, timezone/role removed) + mobile number login + enriched login response
- Admin device management UI with live Redis sync and per-device removal with session invalidation
- Progress API optimization with Redis-cached stats + SSE streaming (first chunk <10ms)
- Per-lesson completion status via pipeline GETBIT (<5ms regardless of lesson count)
- Stage content editor with type-specific dialogs (MATCHING, REVEAL, SENTENCE_BUILDER)
- Lesson completion pipeline overhaul: Lua hot path (~4 Redis round-trips), FSRS spaced repetition, hearts bonus XP

**Stats:**
- 59 files created/modified
- +5,845 / -851 lines (13,800 total Python LOC)
- 7 phases, 16 plans
- 4 days (2026-02-03 → 2026-02-07)

**Git range:** `docs(14)` → `test(16)` (~82 commits)

**What's next:** Planning next milestone

---

## v1.2.1 Gap Closure (Shipped: 2026-02-03)

**Delivered:** Closed critical integration gap where Plan cache invalidation messages were not wired to PlanService.

**Phases completed:** 13 (1 plan total)

**Key accomplishments:**
- Wired Plan cache invalidation into FastAPI pubsub listener
- PlanService registered in FastAPI lifespan following HierarchyService pattern
- Plan cache now clears within seconds of rebuild (previously stale until 1hr TTL)
- Complete end-to-end flow: Build worker → pubsub → PlanService.invalidate() → Redis DELETE

**Stats:**
- +25 lines Python (2 files modified)
- 1 phase, 1 plan
- Same day as v1.2 (2026-02-03)

**Git range:** `feat(13-01)` (2 commits)

**What's next:** v1.3 with streak leaderboard, profile display names, and device self-management

---

## v1.2 Plan System Enhancement (Shipped: 2026-02-03)

**Delivered:** Academic plan system with Grade-Major linking and plan-centric JSON generation for mobile app consumption.

**Phases completed:** 12 (4 plans total)

**Key accomplishments:**
- Grade-Major child table linking with Plan form filtering
- Plan-centric JSON generation (manifest, subject hierarchies, unit content)
- FastAPI endpoint for Plan manifest serving with Redis caching (1hr TTL)
- Build queue integration with hooks for Plan, Plan Subject, Plan Overrider changes
- Plan Overrides applied during generation (hidden units/topics, adjusted is_free flags)

**Stats:**
- ~600 lines Python added
- 1 phase, 4 plans
- Same day (2026-02-03)

**Git range:** `feat(12-01)` → `feat(12-04)`

**What's next:** v1.2.1 gap closure (Plan cache invalidation wiring)

---

## v1.1 Feature Expansion (Shipped: 2026-02-03)

**Delivered:** Extended platform with game sessions for lesson flow tracking, competitive leaderboards, device management for security, and scheduled maintenance tasks.

**Phases completed:** 8-11 (13 plans total)

**Key accomplishments:**
- Device management with 3-device limit via atomic Lua script (fingerprint matching, race condition prevention)
- Game session lifecycle with 1-hour TTL, crash recovery endpoint, and stage validation enforcement
- Competitive leaderboards (daily/weekly/all-time) with composite scoring and dense rank calculation
- Scheduled tasks infrastructure with Prometheus metrics, admin dashboard, and idempotency checks
- Streak reset, session cleanup, and leaderboard archival automation

**Stats:**
- ~9,300 lines of Python (FastAPI + Frappe)
- 4 phases, 13 plans
- 1 day (2026-02-03)

**Git range:** `feat(08-01)` → `feat(11-04)`

**What's next:** v1.2 with streak leaderboard, profile display names, and device self-management

---

## v1.0 MVP (Shipped: 2026-02-02)

**Delivered:** Gamified educational platform backend with FastAPI game API, Redis progress tracking, JWT authentication, Double-Gate access control, and background sync to MariaDB.

**Phases completed:** 1-7 (30 plans total)

**Key accomplishments:**
- FastAPI sidecar with Redis connection pooling for sub-20ms game API responses
- JWT stateless authentication with dual rate limiting and single-session enforcement
- Double-Gate access control (season + player grants) with payment webhook and admin UI
- Bitmap-based progress tracking with O(1) operations and linear unlock enforcement
- Wallet gamification with XP accumulation, streak multipliers, and replay detection
- Content build pipeline with debounced JSON generation and pub/sub cache invalidation
- Background sync (1-minute cycle) for progress, wallets, and interactions to MariaDB

**Stats:**
- ~6,400 lines of Python (FastAPI + Frappe)
- 7 phases, 30 plans
- 2 days from start to ship (2026-02-01 → 2026-02-02)

**Git range:** `feat(01-01)` → `feat(07-04)`

**What's next:** v1.1 with game sessions, leaderboards, and device management

---
