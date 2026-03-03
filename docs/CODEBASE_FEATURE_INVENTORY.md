# Memora Codebase Feature Inventory

## Purpose

This document is a code-driven inventory of the major features implemented in the `memora_admin` repository. It summarizes what exists across the Frappe admin app, the FastAPI game sidecar, Redis-backed runtime systems, scheduled jobs, and voucher/business workflows.

It is intended as a practical feature map, not a field-by-field schema reference.

---

## System Overview

The repository implements a combined platform with two main runtime layers:

- `memora_admin` (Frappe app): admin/back-office workflows, DocTypes, reports, scheduler jobs, event hooks, and business operations.
- `fastapi_app` (FastAPI sidecar): player-facing mobile/game APIs, JWT auth, Redis-backed session/runtime logic, and real-time notifications.

Core architectural patterns present in the codebase:

- Frappe is the system of record for business data and admin workflows.
- Redis is used for low-latency game state, caches, leaderboards, sessions, and dirty-write buffers.
- FastAPI serves player/mobile APIs and reads/writes through Redis plus Frappe-backed services.
- Frappe hooks and background jobs keep Redis and MariaDB synchronized.

---

## Major Product Areas

The implemented feature set clusters into these product domains:

- Player authentication and account lifecycle
- Subscription, access, and plan entitlement management
- Content hierarchy and build pipeline
- Progress tracking and lesson completion
- Wallet, XP, streaks, achievements, and gamification
- Practice arena and FSRS spaced review
- Sessions, device registration, and real-time notifications
- Leaderboards and profile page aggregation
- Purchase requests and payment webhooks
- Voucher generation, allocation, redemption, voiding, and billing
- Admin reporting, audits, and task operations
- Redis health monitoring, data sync, and scheduled maintenance

---

## FastAPI Player-Facing Features

All player APIs are routed under `/api/v1`.

### 1. Health and Runtime Observability

Implemented endpoints:

- `/health/live`
- `/health/ready`
- `/health/redis`

Feature coverage:

- Liveness and readiness checks for the sidecar
- Redis connectivity verification
- Redis runtime metrics exposure for monitoring

### 2. Authentication and Account Lifecycle

Implemented under `/auth`:

- Player login
- Admin login
- JWT refresh
- Registration options lookup
- Player registration
- Registration OTP verification
- Registration OTP resend
- Password reset request
- Password reset OTP verification
- Password reset confirmation

Behavior implemented in code:

- JWT access and refresh token issuance
- Player login using mobile/password
- Admin login flow
- OTP-backed registration and password reset
- Device header enforcement for player login
- Per-IP and per-account rate limiting
- Session replacement on new login
- Forced WebSocket invalidation for superseded sessions

### 3. Device and Session Management

Implemented under `/sessions` plus auth internals:

- Current session lookup
- Session start
- Session end

Supporting features:

- Redis-backed active session tracking
- Session family/token validation
- Device registration with max-device limits
- Cleanup of expired/orphaned session keys via scheduler

### 4. Subscription, Access, and Plans

Implemented under:

- `/subscriptions`
- `/access`
- `/plans`

Feature coverage:

- Read player subscription state
- Grant content access to players
- Revoke content access
- Inspect grants for a player
- Read available plans / productized plan data
- Plan change flow
- Availability checks for plan changes

Related business behavior:

- Frappe-backed subscription creation
- Access recalculation when seasons, subscriptions, or plan subjects change
- Redis caching of plan free-subject metadata

### 5. Content Catalog and Hierarchy

Implemented under:

- `/catalog`
- `/progress` (hierarchy-rich reads)
- practice hierarchy browsing

Feature coverage:

- Plan-specific product/catalog discovery
- Subject, track, unit, topic, and lesson traversal
- Access-aware content visibility
- Cached hierarchy manifests
- Cache invalidation when content changes

### 6. Progress Tracking

Implemented under `/progress`:

- Subject summaries
- Track summaries
- Track detail
- Unit detail
- Topic lesson listing
- Subject progress detail

Feature coverage:

- Per-player progress reads across the learning hierarchy
- Lesson completion state
- Aggregated counts and summaries
- Redis bitmap-backed progress storage with DB sync
- Progress self-healing from MariaDB when cache entries expire

### 7. Wallet and Gamification

Implemented under `/wallet` and `/settings/gamification`:

- Wallet retrieval for current player
- Wallet lookup by player ID
- Gamification settings retrieval

Feature coverage:

- XP balance access
- Wallet caching in Redis
- Runtime gamification configuration
- Level/title configuration sync from admin settings

### 8. Leaderboards and Profile APIs

Implemented under:

- `/leaderboard`
- `/profile`

Profile endpoints include:

- hero/profile summary
- stats
- memory mastery
- weekly activity
- avatar update
- logout

Leaderboard endpoints include:

- leaderboard by type
- current player rank lookup

Feature coverage:

- Global leaderboard reads
- Rank resolution for the current player
- Profile aggregation for gameplay-facing UI
- Cached profile hydration
- Dense-rank/tier metadata support via maintenance tasks

### 9. Practice Arena

Implemented under `/practice`:

- Practice hierarchy browsing
- Start practice session
- Submit practice batch
- Continue practice session

Feature coverage:

- Filterable hierarchy for practice item selection
- Validation of track/unit/topic combinations
- Batch-based practice session state
- Session continuation rules
- Access checks for selected content
- Idempotent batch submission behavior

### 10. FSRS Review System

Implemented under `/reviews`:

- Review overview
- Due items by subject
- Review submission

Feature coverage:

- Due-item retrieval for spaced repetition
- Review answer submission
- Review item persistence
- Memory-state reconstruction and updates
- Background FSRS processing from buffered interactions

### 11. Announcements and Player Reporting

Implemented under:

- `/announcements`
- `/reports`

Feature coverage:

- Read active announcements
- Create content reports from players
- Admin notification triggers for reported content
- Announcement cache invalidation on admin changes

### 12. Purchases and Payment Webhooks

Implemented under:

- `/purchase`
- `/webhooks/payment`

Feature coverage:

- Purchase request submission
- Back-office purchase approval pipeline integration
- Payment webhook intake
- Subscription/access grant handoff after approval/payment events

### 13. Voucher Redemption APIs

Implemented under `/voucher`:

- Voucher preview
- Voucher redemption

Feature coverage:

- PIN preview before redemption
- Secure card lookup through HMAC-based matching
- Ownership filtering before redemption
- Atomic redemption flow
- Access/subscription creation after redemption
- Audit logging of success/failure attempts
- Voucher-specific rate limiting and validation

### 14. Real-Time Notifications

Implemented under `/notifications/ws`:

- JWT-authenticated WebSocket endpoint

Feature coverage:

- Per-player WebSocket connection management
- Redis pub/sub notification fanout
- Subscription/unsubscription by connected user
- Session invalidation push events
- Purchase/subscription notification delivery

---

## Frappe Admin Features

The Frappe app provides the admin control plane, back-office workflows, DocTypes, reports, and task triggers.

### 1. Content Authoring and Curriculum Structure

Core content DocTypes present:

- `Memora Subject`
- `Memora Track`
- `Memora Unit`
- `Memora Topic`
- `Memora Lesson`
- `Memora Lesson Stage`
- `Memora Lesson Stage Settings`
- `Memora Structure Progress`
- `Memora Review Item`

Feature coverage:

- Full subject hierarchy modeling
- Lesson-stage authoring
- Review-item extraction from lesson changes
- Build-trigger integration when content changes
- Admin-side lesson UI helpers (`game_lesson.js`)

### 2. Academic Plans and Entitlements

Plan-related DocTypes present:

- `Memora Academic Plan`
- `Memora Plan Subject`
- `Memora Plan Overrider`
- `Memora Product Grant`
- `Memora Grant Component`
- `Memora Subject Applicability`

Admin APIs present:

- `get_plan_manifest`
- `get_plan_catalog`
- `get_grant_keys`

Feature coverage:

- Plan composition by subject
- Plan-specific content overrides
- Product grant definitions
- Free-subject and free-content propagation
- Build trigger and cache invalidation on plan changes

### 3. Player and Subscription Operations

Player/business DocTypes present:

- `Memora Player Profile`
- `Memora Player Device`
- `Memora Player Subscription`
- `Memora Player Plan History`
- `Memora Player Wallet`
- `Memora Subscription Transaction`
- `Memora Season`

Admin API present:

- `create_subscription`

Feature coverage:

- Player profile management
- Device inventory tracking
- Subscription record management
- Plan history tracking
- Wallet persistence
- Season-based entitlement boundaries

### 4. Gamification and Progress Data Models

Gamification/configuration DocTypes present:

- `Memora Settings`
- `Memora Level Settings`
- `Memora Level Title`
- `Memora Achievement`
- `Memora Analytics Aggregate`
- `Memora Interaction Log`
- `Memora Memory State`

Feature coverage:

- Central gamification settings
- Level/title system
- Achievement definitions
- Player interaction logging
- Memory-state persistence for review scheduling
- Analytics aggregation primitives

### 5. Announcements and Moderation

Related DocTypes present:

- `Memora Announcement`
- `Memora Announcement Target Plan`
- `Memora Content Report`

Admin API present:

- `get_active_announcements`

Feature coverage:

- Admin-created announcements
- Plan-targeted announcement publishing
- Player-generated content issue reports
- Event-driven cache invalidation and admin notifications

### 6. Voucher Library and Distribution System

Voucher DocTypes present:

- `Memora Voucher Batch`
- `Memora Voucher Batch Grant`
- `Memora Voucher Card`
- `Memora Voucher Allocation`
- `Memora Voucher Allocation Card`
- `Memora Voucher Batch Export Log`
- `Memora Voucher Redemption Log`

Voucher admin APIs present:

- `generate_batch`
- `direct_activate`
- `export_for_print`
- `void_batch`
- `void_card`
- `preview_voucher`
- `redeem_voucher`

Allocation admin APIs present:

- `fill_cards`
- `submit_allocation`
- `approve_allocation`
- `reject_allocation`

Feature coverage:

- Batch creation and secure PIN generation
- Background card generation jobs
- Batch activation
- Encrypted export generation for printing
- Export retention and cleanup
- Card allocation to libraries/customers
- Approval/rejection workflows for allocations
- Batch/card void flows
- Redemption attempt audit trail
- Scholarship gift voucher support (backed by dedicated report/tests/specs)

### 7. Admin Task Operations

Task admin APIs present:

- `trigger_task`
- `get_task_status`
- `queue_manual_build`

Feature coverage:

- Manual scheduler task triggering
- Task execution status checks
- Manual content build queue injection

### 8. Reports

Reports implemented:

- `Batch Performance`
- `Consignment Reconciliation`
- `Sales by Library`
- `Scholarship Gift Grants`
- `Security Audit`

Feature coverage:

- Voucher batch performance tracking
- Consignment financial reconciliation
- Sales reporting by customer/library
- Scholarship grant reporting
- Security-focused operational audit output

### 9. Desk and Admin UX Extensions

Admin UX assets/features present:

- App-wide JS include: `admin_filter_helper.js`
- Custom DocType JS for player profile and lesson editing
- Custom desk page: `task_dashboard`
- Workspace fixtures for `Memora` and `Memora Library`
- Custom invoice/customer field extensions

Feature coverage:

- Admin filtering assistance
- Specialized document form behaviors
- Task dashboard for ops visibility
- Workspace onboarding inside Frappe Desk

---

## Event-Driven Synchronization Features

The app uses Frappe `doc_events` to keep runtime caches and derived state consistent.

Implemented event families:

- Access sync on season, subscription, unit, topic, and plan-subject changes
- Device sync on player profile updates
- Profile sync on player profile updates
- Plan-change sync when a profile plan changes
- Catalog cache invalidation on product grant changes
- Announcement cache invalidation on announcement changes
- Review-item extraction when lessons change
- Build trigger queueing when content or plans change
- Settings sync when `Memora Settings` changes
- Level sync when `Memora Level Settings` changes
- Purchase notification on subscription transaction creation
- Content report notification on content report creation

Key implemented outcomes:

- Redis cache invalidation without manual intervention
- Automatic rebuild triggers for content manifests
- Automatic recalculation of free-subject/free-content sets
- Downstream notification dispatch to the runtime layer

---

## Background Jobs and Scheduled Features

The Frappe scheduler is heavily used for maintenance and data processing.

### Every Minute / Near-Real-Time Processing

- Sync dirty progress from Redis to MariaDB
- Sync dirty wallets from Redis to MariaDB
- Flush buffered interactions
- Process FSRS reviews
- Process pending content builds
- Sync dirty review-item extraction every 2 minutes

### Daily / Hourly Runtime Maintenance

- Reset broken streaks daily
- Clean expired sessions hourly
- Archive daily leaderboards
- Archive weekly leaderboards
- Warm profile cache hourly
- Sync all plan subjects to Redis every 6 hours
- Monitor Redis health every 5 minutes
- Clean up old leaderboard keys daily

### Business and Operational Jobs

- Expire cards linked to ended/unpublished seasons
- Delete encrypted voucher exports older than 30 days
- Generate monthly consignment invoices

### Specialized Task Modules

Implemented task modules include:

- `sync.py`
- `fsrs_processor.py`
- `build_worker.py`
- `leaderboard_reset.py`
- `leaderboard_cleanup.py`
- `leaderboard_backfill.py`
- `profile_cache.py`
- `session_cleanup.py`
- `streak_reset.py`
- `plan_sync.py`
- `redis_monitor.py`
- `season_expiration.py`
- `voucher_cleanup.py`
- `consignment_billing.py`

---

## Redis-Backed Runtime Features

The codebase implements Redis as a dedicated gameplay/runtime store.

Feature coverage:

- Player wallet cache
- Progress bitmap cache
- Access cache
- Plan free-subject cache
- Dirty sets for deferred DB synchronization
- Buffered interaction queue for FSRS/review ingestion
- Session store
- Leaderboards and archive keys
- Profile caching
- Pub/sub channels for cache invalidation
- Pub/sub channels for per-player notifications

Operational characteristics documented in code/docs:

- Dedicated Memora Redis instance separate from Frappe cache Redis
- Health endpoint and periodic monitor task
- TTL-based eviction for recoverable data
- Protection of non-TTL critical keys (dirty sets, buffers, leaderboard cores)

---

## Voucher System Feature Breakdown

The voucher subsystem is one of the largest implemented domains and spans roughly the documented phases 33-38.

### Phase 33: Foundation

- Voucher schema and DocType model setup
- Batch, card, allocation, grant, export, and redemption log records

### Phase 34: Batch Generation and Voiding

- Batch generation API
- Secure PIN generation
- HMAC hashing of voucher codes
- Background card creation jobs
- Export generation for print vendors
- Batch and individual card voiding

### Phase 35: Allocation and Distribution

- Auto-fill allocation with available cards
- Allocation submission flow
- Approval/rejection workflow
- Customer/library-specific approval behavior
- Allocation-linked invoice creation paths

### Phase 36: Redemption API

- Voucher preview before commit
- Atomic redemption
- Subscription/access handoff after redemption
- Redemption logs and error-code handling

### Phase 37: Financial Integration

- Commission calculation modules
- Invoice generation modules
- Prepaid and consignment business paths
- Monthly consignment billing task

### Phase 38: Reporting and Lifecycle Closure

- Sales/batch/reconciliation/security reports
- Season-based card expiration
- Export cleanup
- Security audit visibility

---

## Testing and Verification Coverage

The repository contains broad automated coverage for the implemented features.

Observed test coverage domains:

- FastAPI endpoint tests
- FastAPI service tests
- Session and auth tests
- Sync task tests
- Characterization and integration tests
- FSRS/review tests
- Redis connection and monitoring tests
- Voucher generation tests
- Voucher allocation flow tests
- Voucher redemption flow tests
- Counter integrity and export filtering tests
- Commission and invoice tests
- Scholarship voucher tests
- Security audit regression tests

This indicates the codebase is not only feature-rich, but also organized around regression protection for the major workflows.

---

## Practical Feature Summary

In practical terms, the current codebase already supports:

- A mobile/game backend for login, registration, sessions, subscriptions, progress, review, practice, profile, wallet, leaderboard, voucher redemption, and notifications
- An admin backend for content modeling, plan/catalog management, player subscriptions, gamification settings, announcements, moderation, and operational task control
- A complete voucher business workflow from batch creation through financial reporting
- A Redis-centered runtime architecture with scheduled synchronization and self-healing cache behavior
- Real-time operational support through WebSockets, health endpoints, task dashboards, and admin reports

---

## Gaps This Document Does Not Attempt to Cover

This inventory does not enumerate:

- Every field on every DocType
- Every schema/model attribute in FastAPI response objects
- Every internal helper function
- Exact permission matrices by role

For those, use the DocType JSON files, endpoint modules, and existing subsystem-specific docs in `docs/` and `docs/library/`.
