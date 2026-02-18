<!--
SYNC IMPACT REPORT
==================
Version change: N/A → 2.0.0 (initial ratification of top-level project constitution)
Version bump rationale: MAJOR — inaugural unified constitution; starts at 2.0.0 to
  distinguish from domain-level constitutions (voucher-constitution.md v1.0.0,
  testing-constitution.md v1.0.0) and signal higher authority.

Modified principles: N/A (initial)

Added sections:
  - Core Principles (8):
      I.   Self-Healing Cache Architecture (NON-NEGOTIABLE)
      II.  Sub-20ms Game API Performance
      III. Content Hierarchy Integrity
      IV.  Double-Gate Access Control
      V.   Cryptographic Voucher Security (NON-NEGOTIABLE)
      VI.  Financial Precision
      VII. Auditable State Machines
      VIII.Test-First Coverage
  - Architecture & Constraints (dual architecture diagram, content hierarchy,
    game systems table, voucher domain glossary, service layers, scheduled jobs,
    hard constraints, FSRS memory state constraints)
  - Known Gaps & Remediation (10 identified gaps with severity ratings)
  - Governance

Removed sections: N/A (initial)

Templates requiring updates:
  - .specify/templates/plan-template.md — Constitution Check gates filled
    dynamically at plan time; no template change needed ✅
  - .specify/templates/spec-template.md — FR-XXX format and user story
    structure compatible with all 8 principles ✅
  - .specify/templates/tasks-template.md — Phase-based structure compatible;
    Test-First principle (VIII) aligns with optional test phases ✅
  - .specify/templates/commands/ — No command files exist, N/A ✅

Follow-up TODOs: None — all placeholders resolved.
-->

# Memora Admin Constitution

## Core Principles

### I. Self-Healing Cache Architecture (NON-NEGOTIABLE)

Redis is the **hot-data layer** (progress bitmaps, wallets, sessions,
access grants, hierarchies). MariaDB is the **cold-data source of truth**.
Every piece of data in Redis MUST be reconstructable from MariaDB.

- **Cache-miss hydration**: Every service that reads from Redis MUST
  implement an `ensure_hydrated()` pattern — on cache miss, fetch from
  MariaDB via `FrappeClient`, populate Redis, then return.
- **No Redis-only state**: Redis loss (FLUSHDB, restart, eviction)
  MUST be recoverable without manual intervention. Any data that
  exists only in Redis is one restart away from permanent loss.
- **Dirty-set sync**: Writes go to Redis first, then a dirty-set key
  (`memora:dirty:progress`, `memora:dirty:wallets`) is marked.
  Background tasks (`sync.py`) flush dirty sets to MariaDB every minute.
- **Cross-cache invalidation**: When data from one DocType feeds into
  another cache (e.g., Plan Subject `meta_data` → hierarchy cache
  `free_units`/`free_topics`), the event hook MUST invalidate ALL
  affected caches, not just the "obvious" one.
- **Two-pronged invalidation**: Cache invalidation uses both direct
  `r.delete()` AND Redis pubsub publish so the FastAPI sidecar's
  in-process services also invalidate.

**Self-healing key map**:

| Redis Key Pattern | Source of Truth | Hydration |
|---|---|---|
| `memora:access:{player}` | `Memora Player Subscription` | `AccessService.ensure_hydrated()` |
| `memora:progress:{user}:{subj}:v{ver}` | `Memora Structure Progress` | `ProgressService.ensure_hydrated()` |
| `memora:hierarchy:{subject}` | Frappe hierarchy API | Fetched on cache miss (1h TTL) |
| `memora:wallet:{player}` | `Memora Player Profile` | `WalletService.ensure_hydrated()` |
| `memora:stats:{user}:{subj}:v{ver}` | Computed from bitmap | Cold-start recompute |
| `memora:plan:{plan}:free_subjects` | `Memora Plan Subject` | `plan_sync.py` every 6h + event hooks |
| `memora:subjects_with_free_content` | Hierarchy fetch | Auto-repaired on hierarchy fetch |

**Rationale**: Redis is ephemeral by design. The self-healing pattern
ensures operational resilience — the platform recovers automatically
from any Redis data loss without operator intervention.

### II. Sub-20ms Game API Performance

The FastAPI sidecar exists for one reason: sub-20ms response times for
the mobile game client. Every design decision in `fastapi_app/` MUST
preserve this guarantee.

- **No Frappe ORM in hot paths**: FastAPI endpoints MUST use Redis for
  reads and writes. `FrappeClient` calls are permitted ONLY inside
  `ensure_hydrated()` (cache-miss fallback) and background tasks.
- **Pipeline batching**: Multiple Redis commands MUST be batched into
  pipelines (e.g., `GETBIT` × N lessons in a single round-trip).
- **Lua atomicity**: Multi-step Redis operations (session completion,
  device registration, streak updates) MUST use Lua scripts to
  guarantee atomicity without round-trip overhead.
- **No blocking I/O**: All endpoint handlers MUST be `async`.
  Synchronous database calls are FORBIDDEN in request handlers.

**Performance targets**:

| Operation | Target |
|---|---|
| Access check | < 2ms |
| Progress fetch | < 20ms |
| Stage complete | < 10ms |
| Lesson complete (full pipeline) | < 30ms |

**Rationale**: The mobile app's UX depends on instant feedback.
Latency spikes above 100ms create perceptible lag in the learning
flow, directly harming student engagement and retention.

### III. Content Hierarchy Integrity

The content structure follows a strict hierarchy:
**Subject → Track → Unit → Topic → Lesson → Stage**.
This chain is the backbone of progress tracking, access control,
and content delivery.

- **Bitmap versioning**: Each subject has a `version` field. Structural
  changes (adding/removing lessons) MUST increment the version and
  trigger a bitmap migration. Old bitmaps remain readable.
- **Bit index uniqueness**: Every lesson has a unique `bit_index`
  within its subject. Bit indexes MUST NEVER be reused. Deleted
  lessons are tracked in `excluded_bits` for accurate percentage
  calculation.
- **Linear unlock chains**: When `is_linear=True` on a Track, Unit,
  or Topic, completion of the previous sibling is required to unlock
  the next. Unlock state is computed from `completed_bits`, not stored.
- **Free content model**: Units and Topics can be marked `is_free`,
  allowing access without explicit grants. Free content metadata is
  derived from Plan Subject `meta_data` and cached in hierarchy JSON.
- **Build pipeline**: Content changes trigger debounced builds via
  `Memora Build Queue`. The build worker generates JSON files and
  publishes to CDN. Cache invalidation fires via Redis pubsub.

**Rationale**: Progress bitmaps are the performance foundation —
O(1) per-lesson lookups via `GETBIT`. Any corruption in the
hierarchy-to-bitmap mapping creates phantom completions or lost
progress, both unacceptable for an educational platform.

### IV. Double-Gate Access Control

All content access flows through two sequential gates. Both MUST pass
before content is served.

- **Gate 1 — Season validation**: Check season status + `end_ts` via
  Redis hash. Expired or unpublished seasons block all access.
- **Gate 2 — Player access set**: Check `memora:access:{player}` via
  Redis `SISMEMBER`. Grants are keyed as `SUB-{subject}` or
  `TRK-{track}`.
- **Free content bypass**: Lessons inside `is_free` Units or Topics
  bypass Gate 2. Gate 1 still applies.
- **Plan membership**: Subjects with `is_premium=0` in the player's
  plan are accessible without explicit grants (`check_access_with_plan`).
- **Grant propagation**: `on_subscription_change` hook fires `SADD`/
  `SREM` immediately (sub-second propagation). Grants are additive
  and permanent until explicitly revoked.

**Rationale**: Two gates separate temporal access (season) from content
entitlement (grants). This allows seasons to expire without revoking
permanent purchases, and allows content to be restricted without
touching the season lifecycle.

### V. Cryptographic Voucher Security (NON-NEGOTIABLE)

Every voucher PIN MUST be generated using `secrets.choice()` from a
30-character unambiguous alphabet. The `random` module is FORBIDDEN for
any security-sensitive operation.

- **PIN storage**: HMAC-SHA256 hash only. Plaintext MUST NEVER be
  persisted in the database.
- **PIN verification**: MUST use `hmac.compare_digest()` (timing-safe
  comparison) to prevent timing attacks.
- **Export encryption**: Fernet (AES-128-CBC + HMAC-SHA256) with
  HKDF-SHA256-derived key from `site_config.json` secret. Export
  access requires System Manager role and produces an audit log entry.
- **HMAC secret**: Stored in `site_config.json` as
  `voucher_hmac_secret`. MUST NOT exist in the database or version
  control.
- **Card locking**: Redemption flow MUST use `SELECT ... FOR UPDATE`
  to prevent race conditions.
- **Serial numbers**: Atomic `tabSeries` reservation with
  `FOR UPDATE` guarantees no gaps under concurrency.

**Rationale**: Voucher PINs are bearer credentials with monetary value.
Any weakness in generation, storage, or verification creates direct
financial exposure.

### VI. Financial Precision

All monetary calculations MUST use `decimal.Decimal` with explicit
quantization. Python `float` is FORBIDDEN in any financial path.

- **Commission calculation**: `Decimal` multiplication with
  `ROUND_HALF_UP` quantization to 3 decimal places (JOD fils).
- **Commission resolution**: Three-tier priority chain —
  Allocation-level → Customer-level → Season-level. First non-null
  wins.
- **Invoice creation**: Via Frappe ORM `frappe.get_doc({...}).insert()`
  to ensure GL entries and JoFotara e-invoicing hooks fire correctly.
  Direct SQL INSERT into accounting tables is FORBIDDEN.
- **Credit notes**: Return allocations MUST generate a Credit Note
  linked to the original Sales Invoice.

**Rationale**: JOD has 3 decimal places (fils). Floating-point errors
in commission or invoice calculations compound across batch sizes of
up to 1,000 cards, creating irreconcilable ledger discrepancies.

### VII. Auditable State Machines

Every entity with a lifecycle MUST follow its defined state machine
exactly. No state transition may occur outside the documented paths.

**Voucher Batch**: `Draft` → `Generated` → `Active` → `Closed`

**Voucher Card**: `Available` → `Allocated` → `Redeemed` | `Void` | `Expired`
(with `Allocated` → `Available` on return allocation)

**Voucher Allocation**: `Draft` → `Pending Approval` → `Approved` →
`Completed` | `Rejected`
(auto-approve: `Draft` → `Approved` → `Completed` when library skips
approval)

**Build Queue**: `Pending` → `Processing` → `Completed` | `Failed`
(with retry requeue on failure)

**Subscription Transaction**: `Pending Approval` → `Approved` | `Rejected`
(voucher redemptions auto-approve instantly)

- The `Memora Voucher Redemption Log` is IMMUTABLE. Every redemption
  attempt (success or failure) MUST be recorded with its outcome code.
- Terminal states (`Redeemed`, `Void`, `Expired`) are irreversible.
- Batch counters MUST stay consistent with actual card states. Counter
  drift is a data integrity violation.

**Rationale**: State machines enforce business rules at the domain level.
Skipping or reversing states creates orphaned records, incorrect
financial reports, and audit trail gaps.

### VIII. Test-First Coverage

TDD is mandatory for all production code. Tests written first,
verified to fail, then implementation proceeds.

- **Pure logic tests**: Commission math, XP calculation, level
  thresholds — use `unittest.TestCase` (no DB needed).
- **Integration tests**: Full lifecycle paths
  (`Batch → Generate → Allocate → Redeem → Invoice`) — use
  `FrappeTestCase`.
- **Concurrency tests**: Required where atomic operations exist
  (serial reservation, card locking, device registration).
- **Fixtures**: MUST create minimal, isolated data. Each test MUST
  clean up after itself (Frappe test runner handles rollback).
- **No mocking Redis in FastAPI tests**: Test against a real Redis
  instance to catch serialization and pipeline issues.

**Test framework**: Frappe's `frappe.tests.utils.FrappeTestCase` for
Frappe-side code. FastAPI tests use pytest with `httpx.AsyncClient`.
Test environment: `x.conanacademy.com`.

**Rationale**: The platform handles monetary transactions and student
progress data. Untested state transitions and financial calculations
create direct business risk. Lost student progress is unrecoverable.

## Architecture & Constraints

### Dual Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Frappe v15 (Admin)                        │
│  DocTypes (31) · ORM · Background Jobs · Hooks · Admin Panel │
├──────────────────────────────────────────────────────────────┤
│                          MariaDB                             │
├──────────────────────────────────────────────────────────────┤
│                      Redis (Shared)                          │
├──────────────────────────────────────────────────────────────┤
│                  FastAPI Sidecar (Game API)                   │
│  Progress · Sessions · Wallets · Leaderboards · Reviews      │
├──────────────────────────────────────────────────────────────┤
│                     Mobile App (Client)                       │
└──────────────────────────────────────────────────────────────┘
```

- **Frappe v15**: Admin panel, content management, ORM, 31 DocTypes,
  voucher system, build pipeline, event hooks.
- **FastAPI sidecar**: High-performance game API on port 8002.
  Services: progress, sessions, wallets, leaderboards, reviews,
  access control, hierarchy, devices, profile, catalog.
- **Redis**: Shared between Frappe and FastAPI. Hot cache for all
  game state. Prefixed with `memora:`.
- **MariaDB**: Source of truth for all persistent data. Accessed by
  Frappe ORM (admin) and `FrappeClient` (FastAPI cache-miss fallback).

### Content Hierarchy

```
Subject
├── Track (is_linear, is_sold_separately)
│   ├── Unit (is_linear, is_free)
│   │   ├── Topic (is_linear, is_free)
│   │   │   ├── Lesson (bit_index, xp, max_hearts, is_reviewable)
│   │   │   │   └── Stage (stage_type, is_skippable)
│   │   │   └── ...
│   │   └── ...
│   └── ...
└── ...
```

### Game Systems

| System | Redis Key Pattern | Sync Frequency |
|---|---|---|
| Progress | `memora:progress:{user}:{subj}:v{ver}` (bitmap) | Dirty set → every 1 min |
| Wallet | `memora:wallet:{player}` (hash: xp, coins, streak) | Dirty set → every 1 min |
| Sessions | `memora:session:{user}` (hash, 1h TTL) | Ephemeral (no MariaDB sync) |
| Leaderboards | `memora:lb:{period}:{scope}:{id}` (sorted set) | Daily/weekly archive |
| Devices | `memora:devices:{user}` (hash) | Frappe hook sync |
| Stats | `memora:stats:{user}:{subj}:v{ver}` (hash) | Computed from bitmap |
| FSRS | `memora:buffer:interactions` (list) | Flushed every 1 min |

### Voucher Domain Glossary

| Term | Definition | DocType |
|---|---|---|
| Batch | Generation order: quantity, PIN length, face value, grants | `Memora Voucher Batch` |
| Card | Individual voucher with serial number and HMAC-hashed PIN | `Memora Voucher Card` |
| Library | B2B customer (bookstore, school, distributor) | `Customer` (Frappe core) |
| Allocation | Formal transfer of cards from batch to library | `Memora Voucher Allocation` |
| Redemption Log | Immutable audit trail of every redemption attempt | `Memora Voucher Redemption Log` |
| Sale Model | Payment method: Prepaid (at allocation) or Consignment (at redemption) | Field on Allocation + Card |
| Commission | Library's cut via three-tier priority chain | Calculated at invoice time |

### Service Layers

**FastAPI Services** (`fastapi_app/services/`):

| Service | Responsibility |
|---|---|
| `progress.py` | Bitmap SETBIT/GETBIT, ensure_hydrated, dirty marking |
| `access.py` | Double-gate checks, plan membership, grant hydration |
| `hierarchy.py` | Subject structure caching, free content resolution |
| `game_session.py` | Lua-based session lifecycle, atomic completion |
| `wallet.py` | XP/coin/streak management, ensure_hydrated |
| `leaderboard.py` | Sorted set updates, period archival |
| `review.py` | FSRS due items, review submission via Frappe API |
| `device.py` | Lua-based device registration with limit enforcement |
| `stats.py` | Per-subject completion stats cache |
| `profile.py` | Display name/avatar caching for leaderboards |
| `catalog.py` | Product grant catalog for in-app store |

**Frappe Services** (`memora_admin/services/`):

| Service | Responsibility |
|---|---|
| `voucher/generator.py` | PIN generation, HMAC, serial reservation |
| `voucher/crypto.py` | HKDF key derivation, Fernet encrypt/decrypt |
| `voucher/commission.py` | Commission chain resolution, Decimal math |
| `voucher/invoice.py` | Sales Invoice / Credit Note creation |
| `voucher/batch_utils.py` | Shared recount and auto-close helper |
| `build/generator.py` | Subject JSON generation for CDN |
| `build/plan_generator.py` | Plan-centric JSON with overrides |
| `build/publisher.py` | CDN upload with retry |

### Scheduled Jobs

| Schedule | Task | Purpose |
|---|---|---|
| Every 1 min | `sync_dirty_progress` | Flush progress bitmaps to MariaDB |
| Every 1 min | `sync_dirty_wallets` | Flush wallet changes to MariaDB |
| Every 1 min | `flush_interaction_buffer` | Move interactions from Redis to MariaDB |
| Every 1 min | `process_fsrs_reviews` | Compute FSRS memory states from interactions |
| Every 1 min | `process_pending_builds` | Run queued content builds |
| Daily 00:05 | `reset_broken_streaks` | Reset streaks after midnight (Asia/Amman) |
| Daily 00:10 | `archive_daily_leaderboard` | Archive and reset daily leaderboard |
| Friday 00:15 | `archive_weekly_leaderboard` | Archive weekly leaderboard (Islamic week) |
| Hourly :15 | `cleanup_expired_sessions` | Safety net for orphaned session keys |
| Hourly :30 | `warm_profile_cache` | Pre-warm profiles for active leaderboard players |
| Every 6h | `sync_all_plan_subjects_to_redis` | Safety net for plan free-subject sets |
| Daily 01:05 | `expire_season_cards` | Expire voucher cards for ended seasons |
| Daily 02:30 | `cleanup_expired_exports` | Delete encrypted voucher exports > 30 days |

### Hard Constraints

- **Tech Stack**: Frappe v15 (Python 3.11+), FastAPI, MariaDB, Redis
- **Code Style**: Ruff (tabs, double quotes, 110 char line length)
- **Max Batch Size**: 1,000 voucher cards per batch
- **PIN Lengths**: 12, 14, or 16 characters (Select field)
- **Currency**: JOD (Jordanian Dinar, 3 decimal places)
- **Invoice Item Code**: `MEMORA-VOUCHER-CARD`
- **Permissions**: System Manager role for all admin operations
- **Redis Key Prefix**: `memora:` for all keys
- **FastAPI Port**: 8002
- **Session TTL**: 1 hour (3600s)
- **Test Environment**: x.conanacademy.com
- **FSRS**: Item-level spaced repetition, Memory State in RANGE-partitioned
  table (raw SQL ONLY — Frappe ORM is FORBIDDEN for this table)

### FSRS Memory State Constraints

The `tabMemora Memory State` table is RANGE-partitioned and designed
for 10+ billion rows. Special rules apply:

- **Raw SQL only**: `frappe.db.sql()` for ALL queries. Frappe ORM
  (`get_doc`, `get_all`, `get_list`, `db.get_value`) is FORBIDDEN.
- **Partition pruning**: `season_seq` MUST appear in every `WHERE`
  clause. Queries without `season_seq` cause full table scans.
- **Binary UUID**: `item_id` stored as `BINARY(16)` via `UUID_TO_BIN()`.
  Use `BIN_TO_UUID()` for reads.
- **FSRS ratings**: `fail_count == 0` → Good (3), `== 1` → Hard (2),
  `>= 2` → Again (1).

## Known Gaps & Remediation

| ID | Gap | Severity | Area |
|---|---|---|---|
| GAP-01 | Rate limiting referenced in Redemption Log but not implemented | High | Security |
| GAP-02 | Consignment invoicing not implemented (only Prepaid exists) | High | Financial |
| GAP-03 | Batch auto-close logic missing (no auto-transition to Closed) | Medium | Lifecycle |
| GAP-04 | `Memora Voucher Allocation Card` child table DocType not found | Medium | Schema |
| GAP-05 | `sales_invoice` field in invoice.py but not in Card DocType JSON | Medium | Schema |
| GAP-06 | Customer commission custom fields assumed but not verified | Medium | Schema |
| GAP-07 | No test infrastructure exists (zero unit or integration tests) | Critical | QA |
| GAP-08 | `bulk_insert` comment says max 1000 but code passes 10,000 | Low | Code |
| GAP-09 | Season expiration does not update batch counters | Medium | Counters |
| GAP-10 | Card autoname vs generator serial_no format potential conflict | Medium | Schema |

Each gap MUST be addressed with a specification, implementation, and
test before the voucher system is considered production-ready.
GAP-07 (test infrastructure) is the highest priority as it blocks
validation of all other gap remediations.

## Governance

- This constitution is the authoritative reference for **all** Memora
  Admin development — Frappe, FastAPI, voucher, content, and game
  systems alike. It supersedes ad-hoc decisions and informal agreements.
- **Amendments** require: (1) documented rationale, (2) impact
  assessment on existing code, (3) updated version number following
  SemVer (MAJOR for principle removals/redefinitions, MINOR for new
  principles/sections, PATCH for clarifications).
- **Compliance review**: Every PR touching production code MUST be
  verified against the relevant principles before merge. The
  Constitution Check gate in `plan-template.md` enforces this at
  design time.
- **Gap tracking**: New gaps discovered during development MUST be
  added to the Known Gaps table with severity rating and area
  classification.
- **Runtime guidance**: See `CLAUDE.md` for development commands,
  Redis key reference, and operational procedures.

**Version**: 2.0.0 | **Ratified**: 2026-02-18 | **Last Amended**: 2026-02-18
