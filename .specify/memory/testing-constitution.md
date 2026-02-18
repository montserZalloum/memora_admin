<!--
SYNC IMPACT REPORT
==================
Version change: Voucher 1.0.0 → Testing 1.0.0 (scope replacement)
Scope change: Voucher-only → All memora_admin (excluding Voucher & Library)
Previous constitution: Moved to .specify/memory/voucher-constitution.md

Modified principles:
  - I. Cryptographic Security → I. Source-of-Truth Awareness (NON-NEGOTIABLE)
  - II. Auditable Lifecycle → II. Atomic Operation Integrity (NON-NEGOTIABLE)
  - III. Financial Precision → III. Edge-Case-First Design
  - IV. Self-Healing Architecture → IV. Test Isolation (NON-NEGOTIABLE)
  - V. Test-First Coverage → V. Business Flow Completeness

Added sections:
  - Core Principles (5): Source-of-Truth Awareness, Atomic Operation
    Integrity, Edge-Case-First Design, Test Isolation, Business Flow
    Completeness
  - Excluded Scope (Voucher System, Library System)
  - Architecture Constraints (tech stack, Redis key patterns, DocTypes)
  - Known Risks & Required Test Coverage (10 risks with severity)
  - Quality Gates (3 gates: Pre-Merge, Coverage Threshold, Risk Coverage)
  - Governance

Removed sections:
  - All voucher-specific sections (moved to voucher-constitution.md)

Templates requiring updates:
  - .specify/templates/plan-template.md — Constitution Check gates
    derivable from principles ✅ (no update needed, gates filled at
    plan time)
  - .specify/templates/spec-template.md — FR-XXX format compatible ✅
  - .specify/templates/tasks-template.md — phase-based structure
    compatible ✅
  - .specify/templates/commands/ — no command files exist, N/A ✅

Follow-up TODOs: None
-->

# Memora Testing Constitution

## Core Principles

### I. Source-of-Truth Awareness (NON-NEGOTIABLE)

Every test MUST understand the dual-storage architecture: **Redis is
hot cache, MariaDB is source of truth**. Tests that only validate one
layer are incomplete.

- **Write paths**: MUST verify BOTH Redis state AND MariaDB state (or
  dirty-queue membership for async sync).
- **Read paths**: MUST verify hydration behavior — when Redis is empty,
  the system MUST self-heal from MariaDB.
- **Dirty sets**: Any operation that modifies Redis wallet/progress
  MUST add the player to the corresponding dirty set
  (`memora:dirty:wallets`, `memora:dirty:progress`).
- **Cache invalidation**: Tests for admin operations (product grant
  changes, settings updates) MUST verify that Redis caches are
  invalidated.

**Rationale**: 100k+ concurrent students depend on Redis-MariaDB
consistency. A test that validates only the API response without
checking underlying state is a false positive waiting to happen.

### II. Atomic Operation Integrity (NON-NEGOTIABLE)

Every Lua script and Redis pipeline MUST be tested as an atomic unit.
Tests MUST NOT decompose atomic operations into sequential steps.

- **Lua scripts**: `SESSION_COMPLETE_SCRIPT`, `START_SESSION_SCRIPT`,
  `STREAK_UPDATE_SCRIPT`, `REGISTER_DEVICE_SCRIPT` — each MUST be
  tested with its exact key/argument patterns.
- **Pipelines**: The lesson completion pipeline (HINCRBY xp -> SADD
  dirty -> HINCRBY stats -> EXPIRE) MUST be validated as a single
  transaction.
- **Return values**: Lua script return values (e.g., `is_replay` from
  SETBIT, `streak_updated` from streak script) MUST be asserted —
  they drive downstream business logic.

**Rationale**: Decomposing atomic operations into individual Redis
commands in tests masks race conditions that only appear under
concurrency.

### III. Edge-Case-First Design

Every test module MUST include edge case tests proportional to the
happy path tests. Minimum ratio: 1 edge case per 2 happy paths.

- **Registration**: duplicate phone, expired OTP, max attempts,
  pending reservation collision, missing X-Device-ID.
- **Session lifecycle**: end without start, double-end, session TTL
  expiry, force-close on new start.
- **XP calculation**: zero streak, max streak cap, replay vs fresh,
  zero hearts, negative edge.
- **Access control**: no grants, expired grants, plan-only access,
  track-level fallback, free content bypass.
- **Hydration**: empty Redis (cold start), partial Redis (some keys
  missing), MariaDB unreachable during hydration.

**Rationale**: At 100k+ students, every edge case WILL occur. A 0.01%
edge case at this scale means 10+ affected users per event.

### IV. Test Isolation (NON-NEGOTIABLE)

Every test MUST create its own data and clean up after itself. No test
may depend on another test's side effects.

- **Player creation**: Use factory functions that generate unique
  `PLAYER-TEST-{uuid}` identifiers.
- **Redis keys**: All test Redis keys MUST use a test-specific prefix
  or be cleaned in teardown.
- **MariaDB records**: Use `frappe.db.rollback()` or explicit cleanup
  in teardown.
- **Deterministic time**: Tests involving streak logic, OTP expiry, or
  session TTL MUST mock `datetime.now()` — never rely on wall clock.
- **No shared state**: Tests MUST NOT read from or write to production
  Redis keys without a test prefix.

**Rationale**: Non-isolated tests create flaky CI runs that erode
confidence in the test suite. A flaky test is worse than no test.

### V. Business Flow Completeness

Tests MUST cover complete user journeys, not just individual functions.
Every business flow MUST have at least one end-to-end integration test.

- **Registration flow**: Register -> OTP -> Verify -> Auto-login ->
  Wallet seeded -> Session created -> JWT valid.
- **Lesson flow**: Start session -> Validate access -> End session ->
  XP awarded -> Streak updated -> Leaderboard updated -> Stats cached
  -> Dirty set populated.
- **Sync flow**: Dirty wallet -> sync_dirty_wallets() -> MariaDB
  updated -> dirty set cleared.
- **Hydration flow**: FLUSHDB -> API call -> self-heals from MariaDB
  -> correct response.

**Rationale**: Unit tests prove components work; integration tests
prove the system works. Both are required.

## Excluded Scope

The following systems are **permanently excluded** from this testing
constitution. No test file, fixture, or helper may reference them:

| System | Reason |
|--------|--------|
| **Voucher System** | Separate constitution exists (`.specify/memory/voucher-constitution.md`). Includes: voucher endpoints, batches, cards, allocations, PINs, redemption, commission, invoice generation. |
| **Library System** | Out of scope per product decision. |

**Enforcement**: Any test file importing from `services/voucher/`,
`api/voucher.py`, `api/allocation.py`, or referencing DocTypes
`Memora Voucher Batch`, `Memora Voucher Card`,
`Memora Voucher Allocation`, or `Memora Voucher Redemption Log`
MUST be rejected in code review.

## Architecture Constraints

### Technology Stack

| Layer | Technology | Test Framework |
|-------|-----------|---------------|
| FastAPI endpoints | Python 3.11+, FastAPI, Pydantic v2 | `pytest` + `httpx.AsyncClient` |
| Frappe business logic | Frappe v15, Document hooks | `frappe.tests.utils.FrappeTestCase` |
| Redis operations | `redis.asyncio`, Lua scripts | `pytest` + real Redis (test DB) |
| Background jobs | Frappe scheduler, `sync.py` | `pytest` + direct function calls |
| MariaDB | Frappe ORM, raw SQL | `FrappeTestCase` (auto-rollback) |

### Redis Key Patterns Under Test

| Pattern | Type | Service |
|---------|------|---------|
| `memora:wallet:{player_id}` | Hash (xp, streak, streak_date) | WalletService |
| `memora:session:{player_id}` | String (JSON: fid, plan) | SessionService |
| `memora:gamesession:{player_id}` | Hash (session fields) | GameSessionService |
| `memora:progress:{player_id}:{subject}:v{ver}` | Bitmap | ProgressService |
| `memora:access:{player_id}` | Set (content keys) | AccessService |
| `memora:devices:{player_id}` | Hash (device fields) | DeviceService |
| `memora:stats:{player_id}:{subject}:v{ver}` | Hash (completion counts) | StatsService |
| `memora:lb:{type}:{scope}` | Sorted Set | LeaderboardService |
| `memora:dirty:wallets` | Set (player IDs) | Sync tasks |
| `memora:dirty:progress` | Set (player:subject:version) | Sync tasks |
| `memora:buffer:interactions` | List (JSON strings) | Sync tasks |
| `memora:hierarchy:{subject}` | String (JSON) | HierarchyService |
| `memora:catalog:{plan_id}` | String (JSON) | CatalogService |
| `memora:settings:gamification` | String (JSON) | SettingsService |
| `memora:plan:{plan_id}:free_subjects` | Set | AccessService |
| `memora:pending:{pending_id}` | String (JSON) | OTPService |
| `memora:phone_reserved:{mobile}` | String | OTPService |
| `memora:ratelimit:*` | String (counter) | RateLimiter |

### DocTypes Under Test

| DocType | Role | Key Fields |
|---------|------|------------|
| Memora Player Profile | Player identity | mobile, plan, grade, major, season, display_name, avatar, gender |
| Memora Player Wallet | MariaDB wallet mirror | player, total_xp, current_streak, dirty_flag |
| Memora Subject | Content root | subject_title, version, last_bit_index, is_linear |
| Memora Track | Subject child | subject, is_linear, is_sold_separately |
| Memora Unit | Track child | track, is_linear, is_free |
| Memora Topic | Unit child | unit, is_linear, is_free |
| Memora Lesson | Topic child | topic, bit_index, base_xp, max_hearts, is_reviewable |
| Memora Player Subscription | Access grant | player, access_key, is_active |
| Memora Structure Progress | Progress snapshot | player, subject, passed_lessons_bitset |
| Memora Interaction Log | Interaction record | player, lesson, stage_id, item_id, event_type |
| Memora Sync Log | Sync audit | job_id, sync_type, records_processed, status |
| Memora Settings | Gamification config | base_lesson_xp, replay_xp, max_hearts, xp_per_heart, max_streak_multiplier_percent |
| Memora Plan | Subscription plan | plan subjects (child table) |
| Memora Season | Time boundary | status, season_seq |
| Memora Memory State | FSRS state | player, subject, item_id, stage_id, stability, difficulty, next_review |
| Memora Grade | Academic grade | grade_title, majors (child table) |
| Memora Major | Academic major | major_title |

## Known Risks & Required Test Coverage

| ID | Risk | Severity | Required Test |
|----|------|----------|---------------|
| RISK-01 | Redis FLUSHDB resets all XP/progress to zero | CRITICAL | Hydration tests for Wallet, Access, Progress services — verify self-heal from MariaDB |
| RISK-02 | Lesson completion Lua script fails mid-execution | HIGH | Test SESSION_COMPLETE_SCRIPT with missing session, already-completed bit, empty interactions |
| RISK-03 | Dirty wallet sync fails -> XP permanently lost | CRITICAL | Test sync_dirty_wallets with: Redis down mid-sync, partial sync, duplicate dirty entries |
| RISK-04 | Streak reset on timezone boundary | HIGH | Test streak update Lua with: same-day replay, consecutive-day, missed-day, timezone edge (23:59 -> 00:00 Amman) |
| RISK-05 | Session family_id mismatch -> false 401 | HIGH | Test single-session enforcement: login device A -> login device B -> device A gets 401 |
| RISK-06 | XP HINCRBY on empty wallet starts from 0 | CRITICAL | Test ensure_hydrated before HINCRBY — verify XP is correct after Redis flush |
| RISK-07 | Stats cache cold start double-counts | HIGH | Test stats initialization from bitmap vs HINCRBY path — verify no double-counting |
| RISK-08 | Access check returns false after Redis flush | CRITICAL | Test AccessService.ensure_hydrated -> verify grants restored from Memora Player Subscription |
| RISK-09 | Interaction buffer partial flush leaves duplicates | MEDIUM | Test flush_interaction_buffer with: parse errors mid-batch, DB commit failure |
| RISK-10 | OTP rate limit bypass via IP rotation | MEDIUM | Test rate limiter with: IP limit, account limit, cooldown, boundary conditions |

## Quality Gates

### Gate 1: Pre-Merge

- All tests pass (`pytest` exit code 0)
- No test uses `time.sleep()` (use mocked time)
- No test imports from excluded scope (Voucher/Library)
- Every new endpoint has >=1 happy path + >=1 error path test

### Gate 2: Coverage Threshold

- Business logic services: >=80% line coverage
- API endpoints: 100% of routes have >=1 test
- Lua scripts: 100% of scripts have dedicated tests
- Background sync jobs: 100% of sync functions tested

### Gate 3: Risk Coverage

- All RISK-01 through RISK-10 items have explicit test cases
- Each risk test includes both the failure scenario AND the
  recovery/fix validation

## Governance

- This constitution is the authoritative reference for all
  memora_admin testing (excluding Voucher and Library systems).
  It supersedes ad-hoc testing practices and informal agreements.
- **Amendments** require: (1) documented rationale, (2) impact
  assessment on existing tests, (3) updated version number following
  SemVer (MAJOR for principle removals/redefinitions, MINOR for new
  principles/sections, PATCH for clarifications).
- **Compliance review**: Every PR touching tested code MUST be
  verified against the relevant principles before merge. The
  Constitution Check gate in `plan-template.md` enforces this at
  design time.
- **Voucher system**: Has its own constitution at
  `.specify/memory/voucher-constitution.md` — there is no overlap.
- **Gap tracking**: New risks discovered during development MUST be
  added to the Known Risks table with severity rating and required
  test description.
- **Runtime guidance**: See `CLAUDE.md` for development commands,
  Redis key reference, and operational procedures.

**Version**: 1.0.0 | **Ratified**: 2026-02-17 | **Last Amended**: 2026-02-17
