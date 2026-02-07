# Project Milestones: Memora Platform

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
