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

### v1.4 Product Store (Phases 21-23)

**Milestone Goal:** Players can discover available products for their plan and submit purchase requests, with admin approval flow granting content access.

- [x] **Phase 21: Product Catalog API** — Players can browse available products for their plan with fast cached responses
- [x] **Phase 22: Purchase Request Flow** — Players can submit purchase requests that create trackable transactions
- [x] **Phase 23: Approval and Access Grant** — Approved transactions automatically create subscriptions and grant content access

## Phase Details

### Phase 21: Product Catalog API
**Goal**: Players can discover available products for their plan with rich product details and sub-100ms cached responses
**Depends on**: Nothing (first phase of v1.4; builds on existing plan infrastructure from v1.2)
**Requirements**: CTLG-01, CTLG-02, CTLG-03, CTLG-05, CTLG-06
**Success Criteria** (what must be TRUE):
  1. Player hits the catalog endpoint and receives a list of Product Grants available for their plan, excluding any products they have already purchased
  2. Each product in the response includes bundle name, subject titles (alias_title), descriptions (notes), and price (price_list_rate)
  3. Catalog response returns in under 100ms on subsequent requests (Redis cache hit)
  4. When a Product Grant is created, updated, or deleted in Frappe, the cached catalog for that plan refreshes on the next request
**Plans:** 2 plans
Plans:
- [x] 21-01-PLAN.md — Catalog endpoint, service, models, and Frappe data API
- [x] 21-02-PLAN.md — Cache invalidation wiring (Frappe hooks + pubsub + lifespan)

### Phase 22: Purchase Request Flow
**Goal**: Players can submit a purchase request for a product, creating a trackable Subscription Transaction with appropriate approval routing
**Depends on**: Phase 21 (catalog must exist so players know what to buy; pending status feeds back into catalog)
**Requirements**: CTLG-04, PRCHS-01, PRCHS-02, PRCHS-04
**Success Criteria** (what must be TRUE):
  1. Player can submit a purchase request for a specific Product Grant and receive confirmation that the request was created
  2. The purchase request creates a Memora Subscription Transaction DocType record with status "Pending Approval"
  3. Manual payment transactions stay in "Pending Approval" until an admin approves them in Frappe Desk
  4. After submitting a purchase, the product is hidden from the catalog (prevents duplicate purchases) [CTLG-04]
  5. Admin users receive email notification when a new purchase request is created

Note: Payment gateway auto-approval deferred to Phase 23 or future work
**Plans:** 2 plans
Plans:
- [x] 22-01-PLAN.md — Frappe DocType update, whitelisted API, and admin notification hook
- [x] 22-02-PLAN.md — FastAPI endpoint, PurchaseService, models, and router wiring

### Phase 23: Approval and Access Grant
**Goal**: When a transaction is approved, the player automatically receives content access through subscription records and Redis access sync
**Depends on**: Phase 22 (transactions must exist to be approved)
**Requirements**: PRCHS-05
**Success Criteria** (what must be TRUE):
  1. When an admin approves a Subscription Transaction (or it is auto-approved), Memora Player Subscription records are created for each subject in the Product Grant
  2. On subscription creation, the player's access set in Redis is updated (subjects become accessible without re-login)
  3. The approved product no longer appears in the player's catalog (excluded as already purchased)
**Plans:** 1 plan
Plans:
- [x] 23-01-PLAN.md — Subscription Transaction on_update handler for approval/rejection with subscription creation and Redis pending cleanup

### v1.5 Real-Time Notifications (Phase 24)

**Milestone Goal:** Players receive real-time subscription updates via WebSockets when admin approves purchases, replacing deprecated SSE with a scalable notification system for 100K+ concurrent users.

- [x] **Phase 24: Real-Time Subscription Notifications** — WebSocket notification system with Redis pub/sub for instant subscription updates at scale

## Phase Details — v1.5

### Phase 24: Real-Time Subscription Notifications
**Goal**: Players receive instant notification when their subscription status changes (approval/rejection), enabling the client to update the UI without polling. Replace deprecated SSE with WebSockets. Scale to 100K+ concurrent users with <20ms propagation.
**Depends on**: Phase 23 (subscription approval flow must exist to trigger notifications)
**Success Criteria** (what must be TRUE):
  1. When an admin approves a Subscription Transaction, all connected clients of that player receive a WebSocket message within 20ms
  2. WebSocket connections are authenticated via JWT and scoped per-user
  3. The deprecated SSE endpoint (`/progress/stream/{subject}`) and `sse-starlette` dependency are removed
  4. Redis pub/sub channel (`memora:notify:{user_id}`) broadcasts subscription changes across all FastAPI instances (stateless, load-balanceable)
  5. Connection manager handles 100K+ concurrent WebSocket connections (~200MB memory) with graceful disconnect cleanup
  6. Frappe approval hook publishes notification to Redis pub/sub, which all FastAPI instances forward to connected clients
  7. Client receives structured message with subscription details (subject_ids, product_name, status) for immediate UI update
**Plans:** 2 plans
Plans:
- [x] 24-01-PLAN.md — ConnectionManager, notification models, and Frappe-side Redis pub/sub publish
- [x] 24-02-PLAN.md — WebSocket endpoint, pub/sub listener integration, SSE removal

### v1.6 FSRS Review System (Phase 25)

**Milestone Goal:** Players can review previously-learned content through FSRS spaced repetition, with daily review sessions per subject, batched in groups of 10 stages, keeping knowledge retention high while fixing existing FSRS bugs.

- [x] **Phase 25: FSRS Review System** — Fix FSRS bugs, add review API endpoints, and implement daily spaced repetition review flow

## Phase Details — v1.6

### Phase 25: FSRS Review System
**Goal**: Players can fetch due review stages per subject and submit review results, with FSRS computing the next review date. Fix existing FSRS bugs (skippable filter, is_reviewable enforcement) and add review API endpoints with proper MariaDB indexing for 200K+ concurrent users.
**Depends on**: Phase 20 (lesson completion pipeline must exist to create Memory States)
**Success Criteria** (what must be TRUE):
  1. FSRS processor only creates Memory States for stages in lessons where `is_reviewable=true` (currently ignored)
  2. FSRS processor correctly filters skippable stages by looking up `stage_type` from the lesson's child table (fix: currently compares stage_id against stage_title — never matches)
  3. `next_review` is clamped to date-only (midnight) with minimum of tomorrow — no same-day reviews
  4. Composite index on `Memora Memory State (player, subject, next_review)` enables <5ms queries at 120M+ rows
  5. `GET /api/v1/reviews` returns list of subjects with due review counts for the authenticated player
  6. `GET /api/v1/reviews/{subject}` returns up to 10 due stages (FIFO — oldest due first) with `stage_id`, `lesson_id`, and `stage_type` only (client handles content via local cache/CDN)
  7. `POST /api/v1/reviews/{subject}/submit` accepts batch of reviewed stages with fail_count, runs inline FSRS to update Memory State immediately, awards 3 XP per review session, and returns `remaining_due` + `has_more` boolean
  8. Each subject is treated independently — reviews for one subject don't affect another
  9. Review overview cached in Redis (`memora:reviews_overview:{player}`) with 5-min TTL, invalidated on review submit
  10. Stages that are no longer in the lesson (removed by rebuild) are gracefully skipped in review results
**Plans:** 3 plans
Plans:
- [x] 25-01-PLAN.md — Fix FSRS processor bugs (is_reviewable, skippable filter, date clamping) + composite index
- [x] 25-02-PLAN.md — Frappe whitelisted review API (overview, due stages, submit with inline FSRS)
- [x] 25-03-PLAN.md — FastAPI review endpoints, ReviewService, models, and router wiring

**Design Decisions (agreed during brainstorming):**
- **Content serving**: Option B — API returns stage_id + lesson_id only, client fetches content from its local cache or CDN
- **Due date tracking**: MariaDB query via Frappe whitelisted API (not Redis sorted sets — memory cost too high at 200K users × 200 stages)
- **XP**: 3 per review session (batch of up to 10 stages), not per stage
- **Streak**: Reviews do NOT contribute to daily streak (lesson completions only)
- **"Do it tomorrow"**: No rescheduling — stages stay due today, client just closes the review UI
- **FSRS on submit**: Inline (not background queue) — safe at 200K users (~14 req/s peak). Designed for easy migration to queue at 500K+
- **Partitioning**: Composite index only, no MariaDB partitioning now. Queries are partition-friendly by design for future RANGE partitioning by season if needed at 500M+ rows
- **`is_time_calculated`**: Deferred — not wired in this phase

### v1.7 Profile Page API (Phase 26)

**Milestone Goal:** Players can view a rich profile page with avatar selection, subject-filtered stats (XP, streak, items learned), memory mastery breakdown, weekly activity chart, and logout — all powered by backend API endpoints.

- [x] **Phase 26: Profile Page API** — Backend endpoints for profile hero section, subject-filtered stats, memory mastery, weekly activity, and avatar management

## Phase Details — v1.7

### Phase 26: Profile Page API
**Goal**: Provide all backend API endpoints needed for the client profile page: hero section (avatar, username, level, XP progress), subject-filtered stats (streak, items learned, XP), memory mastery breakdown (mature/learning/new), weekly activity (XP per day), avatar selection from predefined options, and logout.
**Depends on**: Phase 25 (FSRS review system provides memory state data for mastery breakdown)
**Success Criteria** (what must be TRUE):
  1. **Hero Section**: API returns player's avatar URL, username, level title, current XP, and XP needed for next level
  2. **Avatar Selection**: Player can choose from predefined avatar options; selected avatar is persisted and returned in profile
  3. **Subject Filter**: All stats endpoints accept an optional `subject` parameter — when omitted, returns combined stats across all subjects
  4. **XP Progress**: Returns level progress (current XP within level, XP to next level) filtered by subject or total
  5. **Stats Grid**: Returns streak (consecutive days), total items learned, and total XP — all filterable by subject
  6. **Memory Mastery**: Returns breakdown of mature/learning/new memory states for a subject (or all subjects combined)
  7. **Weekly Activity**: Returns XP earned per day for the current week (Mon-Sun), with subject filter support
  8. **Logout**: Endpoint to invalidate session/device token
  9. **Performance**: All cached endpoints respond in <50ms on cache hit
**Plans:** 2 plans
Plans:
- [x] 26-01-PLAN.md — Level system constants, Pydantic models, and Frappe whitelisted APIs (mastery, avatar)
- [x] 26-02-PLAN.md — ProfilePageService aggregation layer, all 7 profile endpoints, deps wiring, router registration

**Design Decisions:**
- **Level system**: Static constants (not DB-configurable), 15 levels with increasing XP gaps
- **Per-subject XP**: From leaderboard all-time ZSETs, use `int(score)` to strip composite timestamp
- **Memory mastery cache**: Redis 5-min TTL, invalidated on review submit
- **Items learned**: Stats hash `completed` field, sum across subjects for global
- **Streak**: Global only (not per-subject), reviews don't count
- **Logout**: Invalidates session AND removes device (frees device slot)
- **Avatar validation**: Read valid options from DocType meta (not hardcoded)

### v1.8 Memory State Redesign (Phase 27)

**Milestone Goal:** Replace composite-string PK with BIGINT AUTO_INCREMENT, add item-level FSRS tracking (1 memory state per sub-element within a stage), and implement RANGE partitioning by season for scalability to 25B+ rows.

- [x] **Phase 27: Memory State Redesign (Item-Level FSRS)** — Schema redesign, item UUID generation, FSRS processor rewrite, review system update

## Phase Details — v1.8

### Phase 27: Memory State Redesign (Item-Level FSRS)
**Goal**: Replace the composite-string PK with BIGINT AUTO_INCREMENT, add item-level FSRS tracking (1 memory state per sub-element within a stage), and implement RANGE partitioning by season for scalability to 25B+ rows.
**Depends on**: Phase 25 (FSRS review system must exist), Phase 26 (profile mastery)
**Success Criteria** (what must be TRUE):
  1. `Memora Memory State` uses BIGINT AUTO_INCREMENT as primary key (not composite string)
  2. Each item within a stage gets its own Memory State record with individual stability/difficulty/next_review
  3. Items are identified by UUID (`item_id`), generated during content creation/editing
  4. Table is RANGE-partitioned by `season_seq` (INT) — each season in its own partition
  5. UNIQUE constraint on `(player, item_id, season_seq)` prevents duplicate records
  6. Composite index `(player, subject, next_review, season_seq)` enables <5ms review queries at 2.5B rows/season
  7. Session end API accepts per-item results (item_id + fail_count per item)
  8. Interaction Log includes `item_id` for item-level event tracking
  9. FSRS processor creates/updates Memory States per item (not per stage)
  10. Review APIs return due items (with stage context) and accept item-level review results
  11. Memory mastery counts items (mature/learning/new) instead of stages
  12. Old season partitions can be dropped instantly via `ALTER TABLE DROP PARTITION`
  13. Memory states reset per season (fresh FSRS curves)
  14. Skippable stage types do NOT get item_id UUIDs (excluded from FSRS tracking)
  15. Build generators output correct effective is_skippable (two-tier: per-stage override then global Lesson Stage Settings fallback)
**Plans:** 5 plans
Plans:
- [x] 27-01-PLAN.md — Schema foundation: DocType changes (BIGINT PK, item_id, season_seq), after_migrate partitioning + indexes, Interaction Log item_id
- [x] 27-02-PLAN.md — Content pipeline & session API: item UUID generation in stage config, JSON generator update, per-item StageResult/session endpoint
- [x] 27-03-PLAN.md — FSRS processor rewrite: item-level processing, lookup by (player, item_id, season_seq), Redis cache key update
- [x] 27-04-PLAN.md — Review system & profile update: Frappe review APIs at item level, FastAPI review endpoints, mastery item-level counting
- [x] 27-05-PLAN.md — Gap closure: skip item_id for skippable stages in editor + fix build generator two-tier is_skippable resolution

**Design Decisions (agreed during brainstorming):**
- **PK**: BIGINT AUTO_INCREMENT (8 bytes vs ~80 bytes composite string)
- **Item granularity**: 1 item = 1 sub-element (matching pair, question, fill-blank = individual items)
- **Item ID**: UUID per item, generated during content creation in stage config editor, stored in config_json
  - **Storage**: Use `BINARY(16)` instead of `CHAR(36)` — saves 20 bytes per row (16 vs 36 bytes). At 2.5B rows/season = ~50GB saved on this column alone
  - Internal storage/queries: `UUID_TO_BIN()` for writes, `BIN_TO_UUID()` for reads
  - API responses and display: always convert to string UUID via `BIN_TO_UUID()` before returning to client
- **Season scope**: Reset per season — UNIQUE on (player, item_id, season_seq)
- **Partitioning requirement** (standalone rule for clarity):
  - MariaDB enforces that ALL unique indexes (including PK) on a partitioned table MUST include the partition column
  - PK = `(name, season_seq)`
  - UNIQUE = `(player, item_id, season_seq)`
  - Composite review index = `(player, subject, next_review, season_seq)`
  - Any future unique index MUST also include `season_seq` — this is non-negotiable with RANGE partitioning
- **Partitioning**: RANGE by `season_seq` INT column — automatic pruning, instant archival of old seasons
- **Index strategy**: (player, subject, next_review, season_seq) for review queries; all indexes include partition key
- **Buffer pool sizing**: Hot partition (~2.5B rows/season) requires 32-64GB InnoDB buffer pool
  - Estimated active index working set: ~50-100GB (composite index + UNIQUE index on hot partition)
  - Without adequate buffer pool, index pages spill to disk and review latency degrades from <5ms to 50-200ms
  - Team should plan infra accordingly before scaling past 1B rows/season
- **Frappe compatibility**: Partitioning managed via `after_migrate` hook (raw SQL), same pattern as existing composite index
- **No data migration needed**: System is new, no existing production data

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

**Total:** 27 phases (81 plans, 81 complete)
