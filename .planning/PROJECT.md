# Memora Platform

## What This Is

Memora is a gamified educational platform backend for Arabic-speaking students. It provides a high-performance FastAPI game API (sub-10ms hot path) for content delivery, bitmap-based progress tracking, XP/streak gamification with hearts bonus, game session lifecycle management with Lua-optimized completion pipeline, competitive leaderboards with player profiles, FSRS item-level spaced repetition with RANGE-partitioned storage, device security, stage content editing, product store with admin approval flow, real-time WebSocket notifications, rich profile page API, and subscription-based Double-Gate access control. The platform runs a FastAPI sidecar alongside Frappe for admin/content management, with Redis for hot data and background sync to MariaDB. The codebase has been hardened with shared constants, DRY dependency injection, safe Lua patterns, and reusable admin auth.

## Core Value

**Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.**

## Requirements

### Validated

**v1.0 MVP:**
- FastAPI project structure with lifespan events and dependency injection — v1.0
- Redis async connection pooling with shared Frappe instance — v1.0
- Nginx reverse proxy routing (/api/v1/* -> FastAPI, /api/method/* -> Frappe) — v1.0
- Login endpoint with JWT access + refresh tokens — v1.0
- Refresh endpoint for token exchange — v1.0
- Stateless JWT verification middleware — v1.0
- Gate 1 season validation (status + end_ts) — v1.0
- Gate 2 player access set check — v1.0
- Free preview bypass for is_free content — v1.0
- Payment webhook with Redis SADD + MariaDB subscription — v1.0
- Admin grant UI in Frappe Desk — v1.0
- Redis bitmap progress tracking (SETBIT/GETBIT) — v1.0
- Unlock state calculation with is_linear enforcement — v1.0
- Lesson completion endpoint — v1.0
- XP accumulation in Redis hash — v1.0
- Streak tracking with consecutive day detection — v1.0
- Replay XP reduction (50%) — v1.0
- Build queue with debounce (doc_events hooks) — v1.0
- Hierarchy JSON generation — v1.0
- Bitmap JSON generation — v1.0
- Unit content JSON generation — v1.0
- Lesson JSON generation with stages — v1.0
- Cache invalidation via Redis pub/sub — v1.0
- Mock CDN layer (swappable for R2) — v1.0
- Progress sync (Redis -> MariaDB hex) — v1.0
- Wallet sync (Redis -> MariaDB) — v1.0
- Interaction buffer flush — v1.0
- Build worker scheduled task (2-minute) — v1.0
- Sync tasks scheduled (1-minute) — v1.0

**v1.1 Feature Expansion:**
- Device registration with metadata on login — v1.1
- 3-device limit enforcement with atomic Lua script — v1.1
- Start session creates Redis hash with 1-hour TTL — v1.1
- Stage completion updates session with interaction data — v1.1
- End session triggers completion flow (XP, progress, streak) — v1.1
- Session validation rejects completions without active session — v1.1
- Session recovery allows resuming mid-lesson after crash — v1.1
- Concurrent session detection prevents multiple lessons — v1.1
- All-time XP leaderboard with composite scoring — v1.1
- Daily XP leaderboard (resets midnight Asia/Amman) — v1.1
- Weekly XP leaderboard (resets Friday midnight) — v1.1
- User rank retrieval with neighbor context — v1.1
- Daily streak reset at midnight for missed activity — v1.1
- Hourly session cleanup removes expired keys — v1.1
- Daily leaderboard archival with 90-day retention — v1.1

**v1.2 Plan System Enhancement:**
- Grade-Major child table linking with Plan form filtering — v1.2
- Plan-centric JSON generation (manifest, subject hierarchies, unit content) — v1.2
- FastAPI endpoint for Plan manifest serving with Redis caching — v1.2
- Build queue integration with hooks for Plan, Plan Subject, Plan Overrider — v1.2
- Plan Overrides applied during generation — v1.2

**v1.2.1 Gap Closure:**
- Plan cache invalidation wired to PlanService.invalidate() — v1.2.1
- Complete end-to-end flow: Build → CDN → Cache invalidation — v1.2.1

**v1.3 Leaderboard Profiles & Admin Device Management:**
- ✓ Leaderboard responses include display_name and avatar from player profiles — v1.3
- ✓ ProfileService with Redis-cached batch lookups (1hr TTL, <25ms for 100 entries) — v1.3
- ✓ Profile cache invalidated on Memora Player Profile update via pub/sub — v1.3
- ✓ JWT simplified: plan_id added, timezone/role removed, mobile login supported — v1.3
- ✓ Login response enriched with profile data (display_name, avatar, gender, xp) — v1.3
- ✓ Plan change invalidates session (re-login required) — v1.3
- ✓ Admin device management: view/remove player devices from Frappe Desk — v1.3
- ✓ Device data live-synced from Redis on form load — v1.3
- ✓ Progress stats cached in Redis hash with O(1) atomic updates — v1.3
- ✓ SSE streaming endpoint for progressive progress delivery (<10ms first chunk) — v1.3
- ✓ Per-lesson completion status via pipeline GETBIT (<5ms) — v1.3
- ✓ Stage content editor with type-specific dialogs (MATCHING, REVEAL, SENTENCE_BUILDER) — v1.3
- ✓ Lesson completion Lua hot path (~4 Redis round-trips, <10ms) — v1.3
- ✓ Hearts bonus XP (remaining_hearts * xp_per_heart) — v1.3
- ✓ FSRS spaced repetition background task (1-minute processing cycle) — v1.3
- ✓ Legacy POST /progress/complete endpoint removed — v1.3

**v1.4 Product Store:**
- ✓ FastAPI endpoint to list available Product Grants for player's plan (cached in Redis) — v1.4
- ✓ Product data assembled from Product Grant → Plan Subject → Item Price — v1.4
- ✓ Exclude already-purchased products from catalog — v1.4
- ✓ Subscription Transaction creation via Frappe API — v1.4
- ✓ Admin approval flow creating subscriptions and syncing access to Redis — v1.4
- ✓ Redis cache for product catalog per plan, invalidated on Product Grant changes — v1.4

**v1.5 Real-Time Notifications:**
- ✓ WebSocket notification system with ConnectionManager (100K+ concurrent) — v1.5
- ✓ Redis pub/sub per-user channels for cross-instance delivery — v1.5
- ✓ JWT-authenticated WebSocket connections — v1.5
- ✓ Deprecated SSE endpoint and sse-starlette dependency removed — v1.5

**v1.6 FSRS Review System:**
- ✓ FSRS processor bug fixes (skippable filter, is_reviewable, date clamping) — v1.6
- ✓ Review API endpoints (overview, due stages, submit with inline FSRS) — v1.6
- ✓ MariaDB composite index for 200K+ user review queries — v1.6
- ✓ 3 XP per review session with Redis-cached overview — v1.6

**v1.7 Profile Page API:**
- ✓ Hero section (avatar, username, level, XP progress) — v1.7
- ✓ Subject-filtered stats grid (streak, items learned, XP) — v1.7
- ✓ Memory mastery breakdown (mature/learning/new) — v1.7
- ✓ Weekly activity chart and avatar selection — v1.7
- ✓ Logout endpoint with session/device invalidation — v1.7

**v1.8 Memory State Redesign:**
- ✓ BIGINT AUTO_INCREMENT PK replacing composite string — v1.8
- ✓ Item-level FSRS tracking (1 memory state per sub-element) — v1.8
- ✓ BINARY(16) item_id with UUID generation for interactive stages — v1.8
- ✓ RANGE partitioning by season_seq for instant archival — v1.8
- ✓ Review and mastery APIs updated to item level — v1.8
- ✓ Skippable stage types excluded from item_id generation — v1.8

**v1.9 Tech Debt & Reliability Fixes:**
- ✓ Interaction buffer LTRIM data loss race condition fixed — v1.9
- ✓ Redis key constants unified in single shared location — v1.9
- ✓ deps.py DRY consolidation (RedisClient sub-dependency pattern) — v1.9
- ✓ Dead code removed (136 lines: log_slow_redis, deprecated models, SSE models) — v1.9
- ✓ RequireAdmin dependency replacing magic string checks — v1.9
- ✓ calculate_xp_award moved to service layer — v1.9
- ✓ Lua tonumber safety pattern — v1.9
- ✓ player_id Path validation with regex — v1.9
- ✓ redirect_slashes replacing dual route decorators — v1.9

### Active

(None — planning next milestone)

### Out of Scope

- React Student App — frontend is a separate project
- Actual Cloudflare R2 setup — mock CDN layer for now, swap for production
- Offline support — future roadmap (Q2 2026)
- Push notifications (Firebase) — future roadmap
- Analytics pipeline/dashboards — future roadmap (Q3 2026)
- Anti-cheat system — future roadmap (Q4 2026)
- Monitoring (Grafana/Prometheus) — future roadmap
- League-based leaderboards — complex cohort logic
- Real-time leaderboard updates — WebSocket complexity
- Streak leaderboard — deferred to future milestone
- User-facing device management — admin-only for now
- Payment gateway auto-approval — manual approval only for now

## Context

**Current State (v1.9 shipped):**
- FastAPI sidecar: ~12,000 lines Python
- Frappe module: ~5,400 lines Python
- ~17,450 total Python LOC
- 32 Frappe DocTypes
- 28 phases completed, 85 plans executed
- 11 milestones shipped (v1.0 through v1.9)

**Technical Environment:**
- Frappe v15 for admin panel and content management
- FastAPI sidecar for high-performance game API
- Redis for hot data (progress, wallets, sessions, devices, leaderboards, profiles, stats, catalogs, notifications)
- MariaDB for cold data (via Frappe ORM) with RANGE partitioning on Memory State
- Mock CDN layer (local filesystem, R2-swappable)
- WebSocket notifications (replaced SSE in v1.5)
- fsrs package for item-level spaced repetition scheduling

**Performance Achieved:**
- Access check: O(1) Redis SISMEMBER
- Progress fetch: <10ms with cached stats hash
- Lesson complete hot path: <10ms with Lua script (~4 Redis round-trips)
- Device check: Atomic Lua script with race prevention
- Session operations: O(1) Redis hash operations
- Leaderboard fetch: O(log N) ZRANGE + batch profile enrichment (<25ms)
- Lesson status: <5ms via pipeline GETBIT
- WebSocket notification: <20ms propagation
- Review queries: <5ms with composite index at 200K+ users
- Profile endpoints: <50ms on cache hit

## Constraints

- **Tech stack**: Frappe v15 + FastAPI + Redis + MariaDB — as specified in PRD
- **Performance**: Sub-20ms response times for game API — critical for user experience
- **Scalability**: Design for 100K concurrent users — bitmap storage, batch writes
- **Compatibility**: Must work with existing 32 DocTypes — no breaking changes to schemas
- **CDN**: Mock layer that can be swapped for Cloudflare R2 — clean abstraction required

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| FastAPI sidecar vs Frappe API | Frappe REST too slow for game mechanics; FastAPI gives <20ms | Good |
| Bitmap progress tracking | O(1) completion check, minimal Redis memory per player-subject | Good |
| Double-Gate access control | Separates season (global) from player (individual) for instant updates | Good |
| Mock CDN layer | Enables development without R2 setup; clean swap for production | Good |
| Debounced builds | Collect changes for 2 min before building — reduces redundant work | Good |
| PyJWT (not python-jose) | Cleaner API, lighter dependency | Good |
| Lua script for rate limiting | Atomic increment/expiry in single round-trip | Good |
| Token family ID for session | Single-session enforcement without extra Redis lookup | Good |
| Redis hash for wallet | Allows atomic HINCRBY for XP | Good |
| Lua script for streak | Atomic date comparison and increment | Good |
| Dirty set tracking | SADD on mutation, SREM after sync — prevents lost updates | Good |
| 1-minute sync cycle | Minimizes data loss window without overloading | Good |
| Fingerprint without versions | Same device recognized after browser/app updates | Good |
| Lua script for device registration | Atomic count-check + registration prevents race conditions | Good |
| HTTP 429 for device limit | Matches rate limiting semantics for client-side handling | Good |
| Session force-close on new start | Single active session per user without explicit end | Good |
| 1-hour session TTL | Auto-cleanup of abandoned sessions | Good |
| Composite leaderboard score | Tie-breaking favors earlier achiever | Good |
| Dense ranking | No gaps in rank sequence (1,1,3 not 1,1,2) | Good |
| SCAN for scheduled tasks | Safe iteration without blocking Redis | Good |
| Idempotent task execution | has_run_today() prevents duplicate effects | Good |
| Grade-Major child table (A2) | Flexible linking, Plan form filtering by grade | Good |
| Plan-centric folder structure | Subjects nested in plans, lessons shared at root | Good |
| Plan manifest caching (1hr TTL) | Follows HierarchyService pattern, reduces Frappe calls | Good |
| Plan Overrides loaded once per plan | O(1) lookup via dict, efficient generation | Good |
| PlanService registration pattern | Consistent with HierarchyService for pubsub dispatch | Good |
| elif dispatch for plan messages | Only one handler fires per message type | Good |
| Pipeline MGET for profile cache | Individual keys with MGET for Redis <7.4 compatibility | Good |
| plan_id in JWT token | Avoids Frappe roundtrip on refresh; session JSON stores {fid, plan} | Good |
| frm.add_child for device sync | Avoids reload_doc infinite loop in Frappe form lifecycle | Good |
| WebSocket replacing SSE | Scalable to 100K+ concurrent; bi-directional capability | Good |
| Pipeline GETBIT for lesson status | O(1) per-lesson without loading full bitmap | Good |
| Lua session_complete script | Batches session end into ~4 Redis round-trips for <10ms | Good |
| FSRS in background task | Keeps hot path <10ms; processes interactions asynchronously | Good |
| Hearts bonus before streak multiplier | Rewards skill (hearts remaining) amplified by dedication (streak) | Good |
| BIGINT AUTO_INCREMENT PK | 8 bytes vs ~80 bytes composite string — massive storage savings | Good |
| BINARY(16) item_id | 16 bytes vs 36 bytes CHAR(36) — 50GB saved at 2.5B rows/season | Good |
| RANGE partition by season_seq | Instant archival via DROP PARTITION, query pruning | Good |
| Cross-module constant sharing | Frappe tasks import from fastapi_app.core.constants — single truth | Good |
| RedisClient sub-dependency | Eliminates 16 copy-pasted Redis constructions in deps.py | Good |
| RequireAdmin dependency | Eliminates 4 inline magic string checks across endpoints | Good |
| redirect_slashes=True | FastAPI built-in handles trailing slash — no dual decorators needed | Good |
| calculate_xp_award in service layer | Module-level function importable by any endpoint | Good |
| Safe Lua tonumber pattern | Two-step (raw and tonumber(raw)) or 0 handles all edge cases | Good |

---
*Last updated: 2026-02-12 after v1.9 milestone*
