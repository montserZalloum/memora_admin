# Implementation Plan: FSRS Card State Persistence

**Branch**: `018-fsrs-card-state` | **Date**: 2026-02-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/018-fsrs-card-state/spec.md`

## Summary

Fix the FSRS spaced repetition system by persisting three missing card state fields (`state`, `step`, `last_review`) to the `Memora Memory State` partitioned table. Currently, only `stability`, `difficulty`, and `next_review` are stored. Without the other three fields, every card is treated as brand-new on each review, causing the algorithm to output short intervals that get clamped to "tomorrow." After the fix, review intervals will grow naturally with mastery.

**Approach**: Add 3 nullable columns via the existing `setup.py` migration pattern (instant ALTER TABLE on MariaDB InnoDB). Update card reconstruction and persistence in both the background processor (`fsrs_processor.py`) and the submit reviews API (`reviews.py`). Existing records with NULL values in the new fields are treated as Learning cards needing re-initialization. No data migration. Self-correcting over 1-2 review cycles.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: Frappe Framework (ORM-blocked, raw SQL only), `fsrs` 6.3.0 (FSRS library), `redis` (synchronous, for background processor)
**Storage**: MariaDB 10.6 via `frappe.db.sql()` (RANGE-partitioned `tabMemora Memory State`), Redis at `redis://127.0.0.1:13000` (card state cache)
**Testing**: `frappe.tests.utils.FrappeTestCase` (Frappe tests), `pytest` (FastAPI tests)
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Dual-architecture (Frappe + FastAPI sidecar)
**Performance Goals**: No degradation in review query performance. Due item queries and overview queries remain within existing targets (<20ms).
**Constraints**: All SQL must include `season_seq` for partition pruning. All `item_id` operations must use `UUID_TO_BIN()`/`BIN_TO_UUID()`. No Frappe ORM on Memory State table.
**Scale/Scope**: 100k+ concurrent students, table designed for 10B+ rows. 5 files modified, ~100 lines changed.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Source-of-Truth Awareness | PASS | Changes touch both MariaDB (SQL) and Redis (cache). Write paths update DB + cache. No hydration behavior changes needed (Memory State is not hydrated from Redis). |
| II. Atomic Operation Integrity | PASS | No Lua scripts or pipelines involved. Each review update is a single SQL UPDATE (already atomic). Background processor commits after full batch. |
| III. Edge-Case-First Design | PASS | Spec covers: NULL fields (pre-migration), inflated stability, relearning lapse, concurrent processing, stale Redis cache. All handled. |
| IV. Test Isolation | PASS | Tests will use factory-generated unique player IDs and clean up Redis keys in teardown. MariaDB via FrappeTestCase auto-rollback. |
| V. Business Flow Completeness | PASS | Both review paths covered: background processor (Interaction Log → FSRS) and submit_reviews API (direct review). |

### Post-Design Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Source-of-Truth Awareness | PASS | New columns in MariaDB (source of truth). Redis cache extended with new fields. Cache miss falls through to DB lookup. |
| II. Atomic Operation Integrity | PASS | No change to atomicity model. Single UPDATE per card per review. |
| III. Edge-Case-First Design | PASS | Research R3 documents all NULL-handling edge cases. State transitions verified via FSRS library simulation. |
| IV. Test Isolation | PASS | Test plan includes unique player/item IDs, Redis key cleanup, MariaDB rollback. |
| V. Business Flow Completeness | PASS | Full flow tested: Interaction → FSRS processor → DB persist → API submit → verify intervals grow. |

## Project Structure

### Documentation (this feature)

```text
specs/018-fsrs-card-state/
├── plan.md              # This file
├── research.md          # Phase 0: FSRS library behavior, column addition patterns, NULL handling
├── data-model.md        # Phase 1: Schema changes, field mappings, state transitions
├── quickstart.md        # Phase 1: Deployment and verification guide
├── contracts/
│   ├── memory-state-sql.md    # Updated SQL statements (DDL + DML)
│   └── card-reconstruction.md # Card ↔ DB mapping contracts
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (files to modify)

```text
memora_admin/
├── memora_admin/
│   ├── setup.py                          # Add _ensure_fsrs_state_columns()
│   ├── tasks/
│   │   └── fsrs_processor.py             # Update lookup/reconstruct/persist/cache
│   ├── api/
│   │   └── reviews.py                    # Update submit_reviews card handling
│   └── doctype/
│       └── memora_memory_state/
│           └── memora_memory_state.json   # Add 3 is_virtual fields for admin display
```

**Structure Decision**: Existing dual-architecture (Frappe + FastAPI). All changes are to existing files within the Frappe module. No new files except potential test file.

## Complexity Tracking

> No constitution violations. No complexity justification needed.

| Aspect | Assessment |
|--------|-----------|
| Files changed | 4 production files (setup.py, fsrs_processor.py, reviews.py, JSON) |
| Lines changed | ~100 lines (mostly SQL updates and card reconstruction logic) |
| New abstractions | None. Follows existing patterns exactly. |
| Migration risk | Low. Nullable columns on MariaDB InnoDB = instant metadata change. |
| Rollback risk | Low. Reverting code ignores NULL columns. No data to reverse. |
