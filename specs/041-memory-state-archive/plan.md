# Implementation Plan: Memory State Archive Lifecycle

**Branch**: `041-memory-state-archive` | **Date**: 2026-03-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/041-memory-state-archive/spec.md`

## Summary

Implement the full lifecycle for Memory State data: incremental sync of active seasons to analytics, season archive export with validation when seasons end, analytics current mirror cleanup, and production cleanup via DROP PARTITION with safety gates. This extends the existing archive executor pipeline with two new operational modes — continuous incremental sync (for active seasons) and season-scoped archiving (for ended seasons) — while reusing the proven export/validate/transfer/ingest/purge infrastructure.

The key architectural distinction from Practice Log and Interaction Log archiving is that Memory State is **season-scoped, not date-scoped**. Rows belong to a season (via `season_seq` partition key) and are updated in-place throughout the season's lifetime. Archiving is triggered by the season's `end_date` passing, and production cleanup uses `DROP PARTITION` (O(1)) rather than batched DELETE.

## Technical Context

**Language/Version**: Python 3.11+ (standalone executor, no Frappe runtime)
**Primary Dependencies**: PyArrow (Parquet), PyMySQL, PyYAML, rsync/SSH (transfer)
**Storage**: MariaDB (production, RANGE-partitioned by season_seq), DuckDB (analytics), Parquet (archive + transfer)
**Testing**: pytest with `@pytest.mark.integration` marker for DB-dependent tests
**Target Platform**: Linux server (cron-scheduled)
**Project Type**: Single project (CLI executor)
**Performance Goals**: Incremental sync within 15 minutes of source modification (SC-001); DROP PARTITION under 1 second (SC-005)
**Constraints**: Zero data loss (SC-006), zero duplicates on analytics (SC-002), safety gates block cleanup 100% when linkage exists (SC-007)
**Scale/Scope**: 10+ billion rows across seasons, 3+ concurrent active seasons, 10+ archived seasons (SC-008)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution is unpopulated (template only) — no gates to check. Proceeding.

**Post-design re-check**: No constitution violations. The design extends existing patterns (archive executor pipeline, YAML schema registry, dimension export) without introducing new architectural layers or external dependencies.

## Project Structure

### Documentation (this feature)

```text
specs/041-memory-state-archive/
├── plan.md              # This file
├── research.md          # Phase 0: Research decisions (9 topics)
├── data-model.md        # Phase 1: Entity models and analytics tables
├── quickstart.md        # Phase 1: Implementation guide
├── contracts/
│   ├── memory-state-archive-schema.yaml   # Archive type YAML contract
│   ├── season-scheduler-interface.md      # Season-based job creation
│   ├── incremental-sync-contract.md       # Sync pipeline contract
│   ├── safety-gates-contract.md           # Pre-cleanup safety checks
│   └── analytics-cli-extensions.md        # Analytics CLI changes
└── tasks.md             # Phase 2 output (/speckit.tasks - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
archive_executor/
├── run.py                  # MODIFY — handle season-scoped jobs (filter_type=season)
├── exporter.py             # MODIFY — season-scoped export (single param vs range)
├── scheduler.py            # MODIFY — add create_season_archive_jobs(), --mode season
├── ingestion.py            # MODIFY — add season-based handoff call
├── purge.py                # MODIFY — add _purge_partition() with DROP PARTITION
├── config.py               # MODIFY — add sync_state_path, sync_output_path
├── sync.py                 # NEW — incremental sync engine for active seasons
├── safety_gates.py         # NEW — pre-cleanup safety checks (4 gates)
└── tests/
    ├── conftest.py                         # MODIFY — add Memory State fixtures
    ├── test_memory_state_sync.py           # NEW — incremental sync tests
    ├── test_memory_state_archive.py        # NEW — season archive + purge tests
    └── test_safety_gates.py               # NEW — safety gate unit tests

archive_schemas/
├── archive_types/
│   ├── practice_log.v1.yaml       # EXISTING (no change)
│   ├── interaction_log.v1.yaml    # EXISTING (no change)
│   └── memory_state.v1.yaml       # NEW
└── dimensions/
    ├── player.v3.yaml             # EXISTING (reused)
    └── season.v1.yaml             # EXISTING (reused)
```

**Structure Decision**: Extends the existing `archive_executor/` and `archive_schemas/` structure. Two new modules (`sync.py`, `safety_gates.py`), one new YAML schema, and three new test files. No new top-level directories.

## Key Design Decisions

### D-01: Season-Scoped vs Date-Scoped Archiving (Research R-01)

Memory State archiving uses `season_seq` as the scope (not a date column). Each archive job covers one complete season. The `archive_scope` format is `season_N` (e.g., `season_3`). This aligns with the RANGE partition structure and enables O(1) cleanup via `DROP PARTITION`.

### D-02: Two Operational Modes (Research R-02)

1. **Incremental Sync** (`sync.py`): Runs every 15 minutes for active seasons. Uses the `modified` timestamp with per-season checkpoints and safety overlap. Exports changed rows to Parquet → transfers → upserts into analytics current mirror.

2. **Season Archive** (existing pipeline): Triggered when a season's `end_date` passes. Full export → validate → transfer → ingest archive → mirror cleanup → safety gates → DROP PARTITION.

### D-03: BINARY(16) item_id Conversion (Research R-03)

The `item_id` column is `BINARY(16)` on production. Converted to UUID string via `BIN_TO_UUID()` in the fact SQL. Stored as `VARCHAR(36)` in Parquet.

### D-04: DROP PARTITION for Production Cleanup (Research R-04)

Instead of the batched DELETE pattern, uses `ALTER TABLE ... DROP PARTITION p_season_N`. O(1) metadata operation. Falls back to error (not silent row deletion) if partition doesn't exist.

### D-05: Four Safety Gates Before Cleanup (Research R-05)

1. Archive validation — Completed archive job must exist
2. Active player linkage — No player profiles linked to season
3. Active plan linkage — No published plans linked to season
4. Partition exists — Target partition must be present

All four must pass. Any failure blocks cleanup with a descriptive message.

### D-06: Local JSON Checkpoint for Sync State (Research R-07)

Per-season sync checkpoints stored as local JSON files on the executor host. No new production tables (per spec: "Out of Scope — New production archive-control tables").

### D-07: Backward Compatibility

All changes are additive. The executor's existing date-based pipeline continues unchanged for Practice Log and Interaction Log. Season-scoped behavior is triggered by `filter_type=season` in `job_meta`, which only exists in Memory State archive jobs.

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| BINARY(16) item_id edge cases | Corrupted UUIDs in Parquet | BIN_TO_UUID() is a native MariaDB function; validate in DQ-04 (not_null on item_id) |
| Concurrent sync and archive for same season | Data race during transition | Sync detects archive job existence and pauses; archive does full export |
| DROP PARTITION on wrong partition | Catastrophic data loss | Safety gates verify partition name matches `p_season_\d+` pattern; never drop `p_future` |
| Clock skew between executor and DB | Missed rows in incremental sync | Safety overlap (default 5 min) with upsert dedup on analytics side |
| Season end_date changed after archive started | Stale archive | Archive job captures season state at creation time; re-archival requires manual Failed+retry |
| Large season (billions of rows) | Memory pressure during export | Existing streaming cursor + chunked Parquet writes handle this |

## Complexity Tracking

No constitution violations to justify.
