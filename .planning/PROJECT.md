# Memora Platform

## What This Is

Memora is a gamified educational platform backend for Arabic-speaking students. It provides a high-performance API layer for content delivery, progress tracking, gamification (XP, streaks, leaderboards), and subscription-based access control. The platform uses a FastAPI sidecar for game mechanics (sub-20ms responses) alongside Frappe for admin and content management, with Redis for hot data and bitmap-based progress tracking.

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

### Active

**FastAPI Game API:**
- [ ] Progress endpoint returns subject progress with unlock states (<20ms)
- [ ] Start lesson creates session in Redis
- [ ] Complete stage records interaction and buffers for batch insert
- [ ] End lesson marks progress, awards XP, updates streak
- [ ] Wallet endpoint returns XP and streak
- [ ] Leaderboard endpoints return ranked players (daily, weekly, monthly, alltime)

**Redis Data Layer:**
- [ ] Progress bitmaps per player-subject (SETBIT/GETBIT)
- [ ] Wallet hashes (XP, streak, streak_date)
- [ ] Access sets per player (granted resources)
- [ ] Season meta hashes (status, end_ts)
- [ ] Plan-subject mapping sets
- [ ] Session storage with TTL
- [ ] Leaderboard sorted sets
- [ ] Dirty sets for sync queues
- [ ] Build queue sets
- [ ] Lesson info cache hash
- [ ] Interaction buffer list

**Access Control (Double-Gate):**
- [ ] Gate 1: Season validation (status + end_ts check)
- [ ] Gate 2: Player access set check (direct + plan membership)
- [ ] Free preview logic (is_free at Unit/Topic level)
- [ ] Access grant on purchase (Redis SADD + MariaDB record)
- [ ] Access revocation (manual and automatic via TTL)

**Build Pipeline:**
- [ ] Queue builds on content change (Frappe hooks)
- [ ] Generate hierarchy JSON (_h.json)
- [ ] Generate bitmap JSON (_b.json with bit_range, excluded_bits)
- [ ] Generate unit content JSON (*_c.json)
- [ ] Generate lesson JSON (stages, XP, hearts)
- [ ] Upload to CDN (mock layer, swappable for R2)
- [ ] Invalidate FastAPI cache via pub/sub
- [ ] Track builds in Build Queue DocType

**Sync Mechanisms:**
- [ ] Sync dirty progress (Redis bitmap → MariaDB hex)
- [ ] Sync dirty wallets (Redis hash → MariaDB)
- [ ] Flush interaction buffer (Redis list → MariaDB batch insert)
- [ ] Log syncs to Sync Log DocType

**Authentication API:**
- [ ] Login endpoint (verify credentials, issue JWT)
- [ ] Refresh endpoint (exchange refresh token for access token)
- [ ] Device registration and limit enforcement
- [ ] Session storage in Redis with TTL
- [ ] Stateless JWT verification in FastAPI

**Frappe Hooks:**
- [ ] Season on_update syncs to Redis
- [ ] Content DocType hooks queue builds
- [ ] Plan Overrider hooks trigger rebuild

**Scheduled Tasks:**
- [ ] Every 1 min: sync dirty progress, wallets, interactions
- [ ] Every 2 min: process pending builds
- [ ] Hourly: cleanup old sessions
- [ ] Daily: aggregate stats, reset broken streaks
- [ ] Weekly: cleanup old logs

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

**Existing Codebase:**
- 31 Frappe DocTypes already created with full schemas
- Standard Frappe app structure (Python classes, JSON schemas, JS form handlers)
- ERPNext installed in bench (Item codes available for Product Grant)
- Frappe's Redis instance available (needs Memora-specific configuration)

**Technical Environment:**
- Frappe v15 for admin panel and content management
- FastAPI sidecar for high-performance game API
- Redis for hot data (progress, wallets, sessions, access)
- MariaDB for cold data (users, logs, aggregated stats)
- Mock CDN layer (placeholder for Cloudflare R2 in production)

**PRD Reference:**
- Part 1: Infrastructure & Data Layer (DocTypes, Redis structures, JSON schemas)
- Part 2: Business Logic & APIs (Access control, FastAPI, Frappe APIs, Build pipeline)
- Part 3: Operations & Deployment (CDN, Security, Deployment)

**Performance Targets:**
- Access check: <2ms
- Progress fetch: <20ms
- Stage complete: <10ms
- Lesson complete: <30ms
- CDN content: <50ms
- Build per subject: <60s

## Constraints

- **Tech stack**: Frappe v15 + FastAPI + Redis + MariaDB — as specified in PRD
- **Performance**: Sub-20ms response times for game API — critical for user experience
- **Scalability**: Design for 100K concurrent users — bitmap storage, batch writes
- **Compatibility**: Must work with existing 31 DocTypes — no breaking changes to schemas
- **CDN**: Mock layer that can be swapped for Cloudflare R2 — clean abstraction required

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| FastAPI sidecar vs Frappe API | Frappe REST too slow for game mechanics; FastAPI gives <20ms | — Pending |
| Bitmap progress tracking | O(1) completion check, minimal Redis memory per player-subject | — Pending |
| Bit_range with excluded_bits | Handles lesson deletion without breaking existing progress | — Pending |
| Double-Gate access control | Separates season (global) from player (individual) for instant updates | — Pending |
| Mock CDN layer | Enables development without R2 setup; clean swap for production | — Pending |
| Debounced builds | Collect changes for 2 min before building — reduces redundant work | — Pending |

---
*Last updated: 2026-02-01 after initialization*
