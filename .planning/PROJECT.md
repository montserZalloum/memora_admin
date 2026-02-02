# Memora Platform

## What This Is

Memora is a gamified educational platform backend for Arabic-speaking students. It provides a high-performance FastAPI game API (sub-20ms responses) for content delivery, bitmap-based progress tracking, XP/streak gamification, and subscription-based Double-Gate access control. The platform runs a FastAPI sidecar alongside Frappe for admin/content management, with Redis for hot data and background sync to MariaDB.

## Core Value

**Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.**

## Requirements

### Validated

- ✓ Content hierarchy DocTypes (Subject → Track → Unit → Topic → Lesson → Stage) — existing
- ✓ Academic structure DocTypes (Grade, Major, Season, Academic Plan) — existing
- ✓ Player DocTypes (Profile, Wallet, Subscription, Device) — existing
- ✓ Learning DocTypes (Structure Progress, Memory State) — existing
- ✓ Analytics DocTypes (Interaction Log, Analytics Aggregate) — existing
- ✓ Business DocTypes (Product Grant, Plan Overrider, Subscription Transaction) — existing
- ✓ System DocTypes (Build Queue, Sync Log, Settings) — existing
- ✓ ERPNext integration available for Item codes — existing
- ✓ FastAPI project structure with lifespan events and dependency injection — v1.0
- ✓ Redis async connection pooling with shared Frappe instance — v1.0
- ✓ Nginx reverse proxy routing (/api/v1/* → FastAPI, /api/method/* → Frappe) — v1.0
- ✓ Portable configuration via .env file — v1.0
- ✓ Server migration documentation — v1.0
- ✓ Login endpoint with JWT access + refresh tokens — v1.0
- ✓ Refresh endpoint for token exchange — v1.0
- ✓ Stateless JWT verification middleware — v1.0
- ✓ Gate 1 season validation (status + end_ts) — v1.0
- ✓ Gate 2 player access set check — v1.0
- ✓ Free preview bypass for is_free content — v1.0
- ✓ Payment webhook with Redis SADD + MariaDB subscription — v1.0
- ✓ Admin grant UI in Frappe Desk — v1.0
- ✓ Redis bitmap progress tracking (SETBIT/GETBIT) — v1.0
- ✓ Unlock state calculation with is_linear enforcement — v1.0
- ✓ Lesson completion endpoint — v1.0
- ✓ XP accumulation in Redis hash — v1.0
- ✓ Streak tracking with consecutive day detection — v1.0
- ✓ Replay XP reduction (50%) — v1.0
- ✓ Build queue with debounce (doc_events hooks) — v1.0
- ✓ Hierarchy JSON generation — v1.0
- ✓ Bitmap JSON generation — v1.0
- ✓ Unit content JSON generation — v1.0
- ✓ Lesson JSON generation with stages — v1.0
- ✓ Cache invalidation via Redis pub/sub — v1.0
- ✓ Mock CDN layer (swappable for R2) — v1.0
- ✓ Progress sync (Redis → MariaDB hex) — v1.0
- ✓ Wallet sync (Redis → MariaDB) — v1.0
- ✓ Interaction buffer flush — v1.0
- ✓ Build worker scheduled task (2-minute) — v1.0
- ✓ Sync tasks scheduled (1-minute) — v1.0

### Active

**Current Milestone: v1.1 Feature Expansion**

**Goal:** Extend the platform with game sessions for lesson flow tracking, competitive leaderboards, device management for security, and scheduled maintenance tasks.

**Game Sessions:**
- [ ] Start lesson creates session in Redis with TTL
- [ ] Complete stage records interaction in active session
- [ ] End lesson finalizes session and triggers completion flow

**Leaderboards:**
- [ ] Daily XP leaderboard using Redis sorted set
- [ ] All-time XP leaderboard
- [ ] Current streak leaderboard

**Device Management:**
- [ ] Register authorized devices on login
- [ ] Enforce maximum 3 devices per player

**Scheduled Tasks:**
- [ ] Broken streak reset runs daily at midnight
- [ ] Session cleanup runs hourly

### Out of Scope

- React Student App — frontend is a separate project
- Actual Cloudflare R2 setup — mock CDN layer for now, swap for production
- Offline support — future roadmap (Q2 2026)
- Push notifications (Firebase) — future roadmap
- Analytics pipeline/dashboards — future roadmap (Q3 2026)
- Anti-cheat system — future roadmap (Q4 2026)
- FSRS spaced repetition integration — future roadmap
- Monitoring (Grafana/Prometheus) — future roadmap

## Context

**Current State (v1.0 shipped):**
- FastAPI sidecar: ~3,700 lines Python
- Frappe module: ~2,700 lines Python
- 31 Frappe DocTypes (unchanged from project start)
- 7 phases completed, 30 plans executed

**Technical Environment:**
- Frappe v15 for admin panel and content management
- FastAPI sidecar for high-performance game API
- Redis for hot data (progress, wallets, sessions, access)
- MariaDB for cold data (via Frappe ORM)
- Mock CDN layer (local filesystem, R2-swappable)

**Performance Achieved:**
- Access check: O(1) Redis SISMEMBER
- Progress fetch: <20ms with cached hierarchy
- Lesson complete: Atomic SETBIT + HINCRBY + Lua streak
- Build per subject: Background worker with debounce

## Constraints

- **Tech stack**: Frappe v15 + FastAPI + Redis + MariaDB — as specified in PRD
- **Performance**: Sub-20ms response times for game API — critical for user experience
- **Scalability**: Design for 100K concurrent users — bitmap storage, batch writes
- **Compatibility**: Must work with existing 31 DocTypes — no breaking changes to schemas
- **CDN**: Mock layer that can be swapped for Cloudflare R2 — clean abstraction required

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| FastAPI sidecar vs Frappe API | Frappe REST too slow for game mechanics; FastAPI gives <20ms | ✓ Good |
| Bitmap progress tracking | O(1) completion check, minimal Redis memory per player-subject | ✓ Good |
| Double-Gate access control | Separates season (global) from player (individual) for instant updates | ✓ Good |
| Mock CDN layer | Enables development without R2 setup; clean swap for production | ✓ Good |
| Debounced builds | Collect changes for 2 min before building — reduces redundant work | ✓ Good |
| PyJWT (not python-jose) | Cleaner API, lighter dependency | ✓ Good |
| HTTPBearer for token extraction | Standard FastAPI pattern | ✓ Good |
| Lua script for rate limiting | Atomic increment/expiry in single round-trip | ✓ Good |
| Token family ID for session | Single-session enforcement without extra Redis lookup | ✓ Good |
| Redis hash for wallet | Allows atomic HINCRBY for XP | ✓ Good |
| Lua script for streak | Atomic date comparison and increment | ✓ Good |
| Dirty set tracking | SADD on mutation, SREM after sync — prevents lost updates | ✓ Good |
| 1-minute sync cycle | Minimizes data loss window without overloading | ✓ Good |

---
*Last updated: 2026-02-02 after starting v1.1 milestone*
