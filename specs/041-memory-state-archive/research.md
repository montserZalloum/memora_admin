# Research: Memory State Archive Lifecycle

**Feature Branch**: `041-memory-state-archive`
**Date**: 2026-03-11

## R-01: Season-Scoped vs Date-Scoped Archiving

**Decision**: Use `season_seq` (the RANGE partition key) as the archive scope, not a date column. Each archive job covers one complete season.

**Rationale**: Memory State is RANGE-partitioned by `season_seq`. Each season has a natural lifecycle: active → ended → archived → purged. Archiving an entire season as one unit aligns with the partition structure and enables O(1) cleanup via `DROP PARTITION`. Date-based archiving (used for Practice Log and Interaction Log) makes no sense here because Memory State rows are updated in-place — a row's `modified` timestamp changes but the row belongs to a fixed season.

**Alternatives considered**:
- **Date-range archiving (like Practice Log)**: Rejected because Memory State rows don't "age out" by date — they belong to a season and are updated until that season ends. A row modified yesterday may still be in an active season.
- **Hybrid date+season**: Rejected as unnecessary complexity. The season boundary is the only meaningful cut point.

---

## R-02: Incremental Sync Strategy for Active Seasons

**Decision**: Use the Frappe `modified` timestamp with per-season checkpoints and a configurable safety overlap (default 5 minutes) for incremental extraction. Export changed rows to Parquet, transfer via rsync, and upsert into the analytics current mirror via `ingest-live`.

**Rationale**: Memory State rows are updated in-place whenever the FSRS algorithm reschedules a learner's review. The `modified` column (Frappe standard field, auto-updated on every `doc.save()` / `db.set_value()`) reliably tracks changes. Per-season checkpoints ensure independent progress tracking for concurrent seasons. The safety overlap (extracting from `last_checkpoint - overlap`) prevents gap-based data loss from clock skew or transaction timing.

**Design**:
1. Sync metadata stored in a new `memory_state_sync_meta` table (analytics-side control, not production):
   ```
   season_seq INT, last_sync_checkpoint DATETIME, sync_status ENUM, parquet_location VARCHAR
   ```
   Actually — keep it simpler. Store sync checkpoints in an existing mechanism or as a lightweight JSON file per season on the executor side. No new production tables (per spec: "Out of Scope — New production archive-control tables").

2. Checkpoint stored in a local JSON file: `sync_state/memory_state/season_{N}.json`
   ```json
   {"season_seq": 3, "last_checkpoint": "2026-03-11T14:30:00", "rows_synced": 45200}
   ```

3. Extraction query:
   ```sql
   SELECT ... FROM `tabMemora Memory State` ms
   WHERE ms.season_seq = %s AND ms.modified >= %s
   ORDER BY ms.modified
   ```
   This query benefits from partition pruning (season_seq in WHERE) and the Frappe standard index on `modified`.

**Alternatives considered**:
- **CDC/binlog replication**: Rejected — adds infrastructure complexity and operational burden for a batch-friendly workload.
- **Full snapshot each cycle**: Rejected — violates FR-017 (no repeated full snapshots) and doesn't scale for large seasons.
- **Global cursor (single checkpoint for all seasons)**: Rejected — violates FR-002 (must track per-season).

---

## R-03: BINARY(16) item_id Handling in Parquet Export

**Decision**: Convert `item_id` from `BINARY(16)` to UUID string using MariaDB's `BIN_TO_UUID()` function in the fact SQL query. Store as `VARCHAR(36)` in Parquet.

**Rationale**: The `item_id` column on `tabMemora Memory State` is `BINARY(16)` (managed via `is_virtual` in the Frappe DocType JSON, created by `setup.py`). Binary data in Parquet is technically possible but creates friction in analytics queries (DuckDB, dashboard tools). Converting to standard UUID string format at export time is zero-cost (MariaDB function) and universally compatible.

**Design**: The fact SQL in the archive type YAML uses `BIN_TO_UUID(ms.item_id) AS item_id`. The `schema_snapshot` declares `item_id` as `VARCHAR(36)`.

**Alternatives considered**:
- **Store as BINARY in Parquet**: Rejected — breaks downstream UUID comparisons and human readability.
- **Convert at analytics ingestion time**: Rejected — better to normalize at source extraction.

---

## R-04: Production Cleanup via DROP PARTITION

**Decision**: Use `ALTER TABLE ... DROP PARTITION p_season_N` for production cleanup instead of the batched DELETE pattern used by Practice Log and Interaction Log.

**Rationale**: Memory State is explicitly RANGE-partitioned by `season_seq`. Each season gets its own partition (`p_season_N`) created during `memora_season.after_insert()`. `DROP PARTITION` is an O(1) metadata operation — it doesn't scan or lock rows. This is critical for a table designed for "10+ billion rows" (per DocType description). The batched DELETE with 10K rows + 2s sleep would take hours for a large season.

**Design**:
1. Before DROP PARTITION:
   - Verify partition `p_season_N` exists via `INFORMATION_SCHEMA.PARTITIONS`
   - Run all safety gates (archive validated, no active linkage)
2. Execute: `ALTER TABLE \`tabMemora Memory State\` DROP PARTITION p_season_{N}`
3. After DROP:
   - Update Archive Job: `source_deleted = 1, status = 'Purged'`
   - Log to `archive_delete_audit_log`

**Fallback**: If the partition doesn't exist or the table isn't partitioned, block cleanup with a clear error rather than falling back to row-by-row deletion (per spec edge case: "never silently performing row-by-row deletion on a large table").

**Alternatives considered**:
- **Batched DELETE (existing pattern)**: Rejected — too slow for potentially billions of rows per season.
- **TRUNCATE PARTITION**: Rejected — MariaDB doesn't support TRUNCATE for individual RANGE partitions in the same way; DROP PARTITION is the standard approach.

---

## R-05: Safety Gates Before Production Cleanup

**Decision**: Implement three mandatory safety gates that must ALL pass before production cleanup is permitted. Gates are checked in a dedicated module (`safety_gates.py`) and results logged.

**Rationale**: Production cleanup is irreversible (DROP PARTITION cannot be undone). The spec requires three independent safety checks (FR-012, FR-013, FR-014).

**Design**:

**Gate 1 — Archive Validation** (FR-012):
```sql
SELECT status FROM `tabMemora Archive Job`
WHERE source_doctype = 'Memora Memory State'
  AND archive_scope = %s AND schema_version = 'v1'
  AND status IN ('Completed', 'Purged')
```
Must return at least one row. Blocks cleanup if no validated archive exists.

**Gate 2 — Active Player Linkage** (FR-013):
```sql
SELECT COUNT(*) AS cnt FROM `tabMemora Player Profile`
WHERE season = %s
```
Where `%s` is the Season DocType name (e.g., `SEAS-00003`). Blocks if cnt > 0.

**Gate 3 — Active Plan Linkage** (FR-014):
```sql
SELECT COUNT(*) AS cnt FROM `tabMemora Academic Plan`
WHERE season = %s AND is_published = 1
```
Blocks if cnt > 0. Only published plans are considered active (unpublished plans may exist as drafts).

**Alternatives considered**:
- **Single aggregate check**: Rejected — each gate should report independently so operators know exactly what's blocking.
- **Soft gates (warn but allow)**: Rejected — spec says MUST block, not warn.

---

## R-06: Season Archive Scheduler

**Decision**: Extend the existing scheduler module with a `create_season_archive_jobs()` function that scans for seasons whose `end_date` has passed and creates one archive job per eligible season.

**Rationale**: The existing scheduler creates per-day jobs based on date ranges. Memory State needs per-season jobs based on the season lifecycle. The logic is: "find seasons where `end_date < TODAY` and no non-Failed archive job exists for that season."

**Design**:
```python
def create_season_archive_jobs(config, archive_type="memory_state"):
    # 1. Find ended seasons with no existing archive job
    # SELECT s.name, s.season_seq FROM `tabMemora Season` s
    # WHERE s.end_date < CURDATE()
    #   AND NOT EXISTS (
    #     SELECT 1 FROM `tabMemora Archive Job` aj
    #     WHERE aj.source_doctype = 'Memora Memory State'
    #       AND aj.archive_scope = CONCAT('season_', s.season_seq)
    #       AND aj.schema_version = 'v1'
    #       AND aj.status NOT IN ('Failed')
    #   )
    # 2. For each eligible season, create a Pending job
    # 3. job_meta includes season_seq, season_name, filter_type=season
```

The `archive_scope` format is `season_N` (e.g., `season_3`), which is unique per season and readable.

**Alternatives considered**:
- **Manual job creation only**: Rejected — spec requires automated lifecycle management.
- **Reusing the date-based scheduler as-is**: Rejected — fundamentally different scoping model.

---

## R-07: Incremental Sync Checkpoint Storage

**Decision**: Store per-season sync checkpoints as local JSON files on the executor host at `{config.sync_state_path}/memory_state/season_{N}.json`. No new production database tables.

**Rationale**: The spec explicitly marks "New production archive-control tables" as Out of Scope. The sync checkpoint is executor-side operational state, not production audit data. A simple JSON file per season is:
- Easy to inspect and debug
- Survives executor restarts
- Doesn't require database changes
- Each season is independent (no table contention)

**Format**:
```json
{
  "season_seq": 3,
  "season_name": "SEAS-00003",
  "last_checkpoint": "2026-03-11T14:30:00",
  "last_sync_rows": 1250,
  "total_rows_synced": 45200,
  "last_sync_at": "2026-03-11T14:45:00"
}
```

**Alternatives considered**:
- **Database table on production**: Rejected per spec scope boundary.
- **Database table on analytics**: Possible but adds coupling; the executor manages the sync, so the state belongs with the executor.
- **Single state file for all seasons**: Rejected — per-season files allow independent management and avoid write contention.

---

## R-08: Analytics Current Mirror Table Design

**Decision**: The analytics current mirror for Memory State is a DuckDB table (`memory_state_current`) that holds the latest state for all active seasons, updated via upsert on the `(name, season_seq)` composite key.

**Rationale**: The current mirror must:
1. Support upsert (FR-004) — rows are updated in-place as they change
2. Be season-partitioned logically — dashboard queries should be able to filter by season efficiently
3. Support cleanup — removing an entire season after archival must be fast

**Design**:
- Table: `memory_state_current`
- Primary key: `(name, season_seq)`
- Columns match the fact export columns
- Upsert via DuckDB's `INSERT OR REPLACE` or equivalent merge
- Cleanup: `DELETE FROM memory_state_current WHERE season_seq = N`

The archived Parquet files are stored separately at `archive/memory_state/season_{N}/` and are only queried for explicit historical analysis.

**Alternatives considered**:
- **Separate table per season**: Rejected — adds table management overhead and complicates cross-season dashboard queries.
- **Partitioned DuckDB table**: DuckDB doesn't have native RANGE partitioning like MariaDB; filtering by `season_seq` with an index is sufficient.

---

## R-09: Handling the `p_future` Partition

**Decision**: Never drop the `p_future` partition. Only drop named `p_season_N` partitions.

**Rationale**: The `p_future` partition is a catch-all for rows with `season_seq` values that don't yet have a dedicated partition. Dropping it would prevent inserts for any season without its own partition. The REORGANIZE PARTITION logic in `memora_season.py` splits `p_future` when new seasons are created, so it always exists.

**Design**: The safety gate validates that the target partition name matches the pattern `p_season_\d+` before allowing DROP. Any other partition name is rejected.
