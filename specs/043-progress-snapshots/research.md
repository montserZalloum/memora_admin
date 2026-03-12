# Research: Weekly Structure Progress Snapshots

**Feature**: 043-progress-snapshots | **Date**: 2026-03-11

## R-001: Module Placement — archive_executor vs Frappe Task

**Decision**: Build as `archive_executor/snapshot.py` (standalone module), not a Frappe scheduled task.

**Rationale**:
- The archive_executor already contains all required infrastructure: pymysql connection management, streaming cursors for memory-efficient reads, pyarrow Parquet writing, structured JSON logging, manifest generation, and atomic staging-to-final directory swaps.
- Frappe tasks (`memora_admin/tasks/`) depend on the Frappe runtime (`frappe.db.sql`, `frappe.get_doc`), which adds unnecessary overhead for a pure read-extract-write pipeline.
- The archive executor runs in its own virtualenv at `/opt/memora-archive/venv/`, isolated from Frappe process restarts and worker queue contention.
- Consistent operational model: all Parquet-writing pipelines live in `archive_executor/`.

**Alternatives considered**:
- **Frappe task wrapper**: Would require importing pyarrow into Frappe's process, mixing I/O-heavy batch work with the web worker pool. Rejected.
- **Hybrid (Frappe triggers, executor runs)**: Unnecessary indirection for a self-contained weekly job. Rejected.

## R-002: Scheduling Mechanism — System Cron vs Frappe Scheduler

**Decision**: System cron entry: `0 3 * * 0` in Asia/Amman timezone (Sunday 03:00 AM).

**Rationale**:
- The archive executor already uses system cron for its daily and seasonal jobs. Adding another cron entry is operationally consistent.
- Frappe's `scheduler_events` in hooks.py does not support timezone specification — it runs in the server's system timezone. System cron with `TZ=Asia/Amman` is explicit and auditable.
- Weekly cadence (`0 3 * * 0`) is a single cron expression. Frappe's cron dict supports the same syntax, but the executor's virtualenv and Config would need to be wired through a Frappe task wrapper.

**Alternatives considered**:
- **Frappe scheduler (`hooks.py`)**: Works for Frappe tasks, but this runs outside Frappe. Would need a thin Frappe task that shells out to the executor — unnecessary layer. Rejected.

## R-003: Job Tracking — Database Job Table vs Stateless

**Decision**: Stateless pipeline with filesystem-level idempotency. No `tabMemora Snapshot Job` table in v1.

**Rationale**:
- The spec explicitly excludes archive/purge logic (FR-011), so there is no multi-stage lifecycle requiring state machine tracking.
- Idempotency is achieved via atomic directory swap: write to `.staging/`, then `os.rename()` to final `snapshot_date/` directory. If the directory already exists, rename to `.old`, swap in the new one, then delete `.old`.
- Job-level observability (FR-006: rejected row counts, success/failure) is handled by structured JSON logging to the log file — no DB persistence needed.
- If job tracking is desired in a future version, it can be added incrementally without changing the core pipeline.

**Alternatives considered**:
- **Reuse `tabMemora Archive Job`**: This table has a UNIQUE constraint on `(source_doctype, archive_scope, schema_version)` designed for archive lifecycle tracking. Adding weekly snapshots would require special-casing the "no purge" path and polluting the archive job list. Rejected.
- **New `tabMemora Snapshot Job` DocType**: Over-engineering for v1. The spec has 13 functional requirements, none of which require DB-level job tracking. Rejected for now; revisit if retention/purge is added.

## R-004: Plan Enrichment Join Strategy

**Decision**: INNER JOIN `tabMemora Player Profile` with `WHERE pp.plan IS NOT NULL` for valid rows; separate COUNT query for rejected rows.

**Rationale**:
- FR-002 requires resolving `plan_id` via `sp.player → pp.name → pp.plan`.
- FR-005 requires rejecting rows where plan cannot be resolved (no profile OR null plan).
- An INNER JOIN with the plan NOT NULL condition naturally excludes both cases (no profile match → no join; null plan → filtered out).
- A separate lightweight COUNT query captures the rejection count for logging (FR-006) without doubling the main query's complexity.
- LEFT JOIN + post-filter would work but forces the pipeline to transfer and discard rejected rows — wasteful for large tables.

**Alternatives considered**:
- **LEFT JOIN + application-level filter**: Transfers rejected rows over the wire, then discards. Wastes bandwidth. Rejected.
- **Single query with CASE for rejection tagging**: Adds complexity to the main query without benefit — we only need the count, not the individual rejected rows. Rejected.

## R-005: Parquet Output Layout

**Decision**: Directory-per-snapshot layout: `{snapshot_output_path}/structure_progress/{snapshot_date}/fact_structure_progress.parquet` + `manifest.json`.

**Rationale**:
- Consistent with archive executor's `{output_path}/{job_name}/fact_{type}.parquet` pattern.
- `snapshot_date` directory name (e.g., `2026-03-08`) enables simple globbing for DuckDB queries: `read_parquet('structure_progress/*/fact_structure_progress.parquet')`.
- Including `snapshot_date` as both a directory name and a column in the Parquet file provides redundancy for query flexibility (filter in DuckDB WHERE clause or via filesystem).
- Manifest tracks checksum, row count, and schema version for each snapshot.

**Alternatives considered**:
- **Hive-style partitioning** (`snapshot_date=2026-03-08/`): Adds `=` in directory names which complicates shell operations. DuckDB can still read the flat layout with a glob pattern. Rejected for simplicity.
- **Single appended Parquet file**: Breaks idempotency — cannot atomically replace a week's data without rewriting the entire file. Rejected.
- **DuckDB direct write**: Adds a dependency on DuckDB Python bindings. pyarrow is already available and proven. Rejected.

## R-006: Idempotent Overwrite Strategy

**Decision**: Atomic staging-to-final swap matching the archive executor's pattern.

**Rationale**:
- FR-007 requires identical output on rerun. The atomic swap ensures either the old snapshot or the new snapshot is visible — never a partial write.
- Pattern: write to `.staging/{snapshot_date}/`, verify file integrity, then `os.rename()` to final path. If final exists, rename to `.old` first, swap, then delete `.old`.
- Crash safety: if the process dies during `.staging` write, the old snapshot (if any) remains intact. On next run, `.staging` is cleaned up before starting.

**Alternatives considered**:
- **DELETE + rewrite in place**: Not atomic — a crash between delete and write leaves no data. Rejected.
- **Timestamp-suffixed directories** (e.g., `2026-03-08_run2`): Creates duplicates, violating FR-007 intent. Rejected.

## R-007: Empty Table Handling

**Decision**: Write a valid Parquet file with the correct schema but zero rows, plus a manifest with `row_count: 0`.

**Rationale**:
- FR-013 requires graceful handling of empty source tables.
- The archive executor already handles this in `exporter.py` — creates a valid empty Parquet with schema from the schema snapshot when row count is 0.
- Analytics queries over the snapshot directory will naturally skip zero-row files without errors.

## R-008: Streaming Cursor for Memory Efficiency

**Decision**: Use `SSDictCursor` (unbuffered server-side cursor) via `archive_executor.db.streaming_cursor()`.

**Rationale**:
- Structure Progress table could grow to 100K+ rows. Fetching all rows into memory risks OOM on the archive server.
- The archive executor's streaming cursor pattern reads rows in chunks (default 50K) and writes Parquet batches incrementally.
- This is the same pattern used for practice_log and memory_state exports.

## R-009: No Dimension Tables in v1

**Decision**: Flat fact-only output. No separate dimension Parquet files for player, plan, or subject.

**Rationale**:
- The snapshot output has only 5 columns (FR-004): `snapshot_date`, `player_id`, `plan_id`, `subject_id`, `completion_percentage`. These are all identifiers and a single metric.
- The archive pipeline's dimension tables exist to support denormalized analytics on archived data that can no longer be joined to live tables. Weekly snapshots remain alongside live data indefinitely (no purge), so dimensions can be joined at query time from the live DB or from existing archived dimensions.
- Adding dimension exports would triple the pipeline complexity for marginal benefit.

**Alternatives considered**:
- **Include player dimension**: Useful if snapshot consumers need player metadata (grade, major). Deferred to a future version if needed. Rejected for v1.
- **Include subject dimension**: Subject titles rarely change. Can be joined from archive dimension exports. Rejected for v1.

## R-010: Schema Definition Format

**Decision**: YAML schema file at `archive_schemas/snapshot_types/structure_progress.v1.yaml` defining columns, types, the extraction SQL, and DQ rules.

**Rationale**:
- Consistent with the archive pipeline's YAML-driven approach (`archive_types/`, `sync_types/`, `dimensions/`).
- Enables the generic DQ validator to run rules against the snapshot output.
- Schema versioning (`v1`) supports future evolution without breaking existing snapshots.
- The YAML defines the SQL query template, column types for Arrow schema generation, and DQ rules (not_null, unique_key).
