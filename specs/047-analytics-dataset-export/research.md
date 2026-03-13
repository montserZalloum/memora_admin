# Research: Educational Analytics Dataset Export

**Branch**: `047-analytics-dataset-export` | **Date**: 2026-03-13

## R-001: Incremental Practice Log Export — Merge Strategy

**Question**: The practice log has composite PK `(player_id, item_id)` — each row is updated in-place as a student reviews items. How do we efficiently produce a correct full-snapshot Parquet file using only the changed rows (delta)?

**Decision**: Read-merge-write using PyArrow in-memory upsert.

**Rationale**:
- Delta query (`WHERE last_seen_at > watermark`) returns only rows changed since last export — satisfies SC-006 (measurably faster when <10% changed).
- Existing full Parquet is loaded as a PyArrow Table. Delta rows are keyed by `(player_id, item_id)`. A pandas-style merge (or pure PyArrow via `pyarrow.concat_tables` with deduplication on PK) produces the updated full snapshot.
- The final output file is always a **complete snapshot** — analytics server does not need to handle delta files.
- On first run (no existing Parquet), falls back to full mode: `SELECT * FROM tabMemora Practice Log`.

**Merge algorithm**:
1. Load existing `practice_log.parquet` as PyArrow Table `T_existing`.
2. Run delta query → `T_delta` (rows with `last_seen_at > watermark`).
3. Concat `T_existing` + `T_delta`, sort by `(player_id, item_id)`, keep LAST occurrence (delta row wins for updated keys).
4. Write merged table back to `practice_log.parquet`.
5. New watermark = `max(last_seen_at)` across all rows in merged table.

**Alternatives considered**:
- *DuckDB upsert*: Would require shipping DuckDB as a dependency and adds complexity; PyArrow sufficient.
- *Delta-only output file*: Shifts merge complexity to analytics server; violates the "total row count reflects current state" requirement (US1 scenario 2).
- *Always full scan*: Correct but fails SC-006 performance criterion.

---

## R-002: READ COMMITTED Isolation for Non-Blocking Exports

**Question**: FR-004/FR-023 require no table locks and read-committed (or snapshot) isolation. MariaDB defaults to REPEATABLE READ. What's the correct PyMySQL approach?

**Decision**: Set `SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED` on each connection before running export queries.

**Rationale**:
- MariaDB's `REPEATABLE READ` holds a snapshot from transaction start, which can cause the memory REPEATABLE READ gotcha documented in the project (stale snapshots if a connection is held). READ COMMITTED takes a fresh snapshot per statement, which is correct for a bulk export that reads consistent data without blocking writers.
- `SELECT` statements without `LOCK IN SHARE MODE` or `FOR UPDATE` do not acquire row locks in MariaDB InnoDB under any isolation level — so concurrent writes are never blocked.
- The `db.py` connection factory should issue `SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED` immediately after opening each connection.

**Alternatives considered**:
- *LOCK IN SHARE MODE*: Explicitly forbidden by FR-004.
- *REPEATABLE READ default*: Risks stale snapshot if export connection is reused across multiple queries. Read-committed is safer and explicit.

---

## R-003: PyArrow Schema Mapping for Frappe Fields

**Question**: How do Frappe DocType field types map to PyArrow types for the analytics exports?

**Decision**: Reuse `_sql_type_to_arrow()` pattern from `archive_executor/exporter.py`, with explicit schema definitions in YAML for each export.

**Rationale**: The archive_executor pattern (YAML column list → PyArrow schema construction) is well-tested (278 tests pass). Applying the same pattern to analytics_exporter avoids reinvention. The key type mappings:

| Frappe / SQL Type | PyArrow Type | Notes |
|---|---|---|
| Data / VARCHAR | `pa.string()` | Item IDs, names, FK references |
| Int / INT | `pa.int64()` | sort_order, season_seq, counts |
| Check / TINYINT(1) | `pa.int64()` | 0/1 booleans; analytics server treats as int |
| Date / DATE | `pa.date32()` | start_date, end_date |
| Datetime / DATETIME | `pa.timestamp("us")` | first_seen_at, last_seen_at |
| INT UNSIGNED | `pa.int64()` | attempt_count, correct_count |
| ENUM | `pa.string()` | last_result: 'Correct' / 'Incorrect' |

---

## R-004: Watermark Persistence

**Question**: Where and how to persist the incremental watermark for practice log export?

**Decision**: JSON file at `{analytics_output_path}/.watermark.json`.

**Rationale**:
- Co-located with output files — no additional DB table, no Frappe dependency.
- Simple atomic write (write to `.watermark.json.tmp`, then `os.replace()`) prevents corruption on interrupted export.
- Structure:
  ```json
  {
    "practice_log": {
      "last_watermark": "2026-03-12T02:30:00",
      "last_export_at": "2026-03-13T01:00:00",
      "last_row_count": 1234567
    }
  }
  ```
- The `last_watermark` is the `max(last_seen_at)` from the previous merged output, not the export start time. This ensures rows updated during the export window are captured in the next run.

**Alternatives considered**:
- *MariaDB table*: Adds write dependency to the production DB; violates the read-only nature of the exporter.
- *Separate `.env`-configured path*: Adds complexity; co-location is cleaner and the output directory is already configurable via `ANALYTICS_OUTPUT_PATH`.

---

## R-005: YAML Schema Pattern for Analytics Exports

**Question**: Should analytics_exporter use the same YAML schema registry pattern as archive_executor, or a simpler approach?

**Decision**: Simplified YAML schemas — one file per output dataset, no registry loader needed.

**Rationale**:
- Archive schemas support complex multi-dimensional exports with staging, jobs, and derived dimensions. Analytics exports are simple: one SQL → one Parquet file.
- Each YAML file defines: `dataset`, `output_file`, `mode` (snapshot/incremental_watermark), `sql`, `columns` (name + type), `primary_key` (for DQ validation), `dq_rules`.
- The `run.py` loads schemas directly from `analytics_exporter/schemas/`, no registry indirection needed.

**Alternatives considered**:
- *Reuse archive_schemas registry*: Conflates two different pipelines; would require adapting the job-scoped exporter to support analytics exports — over-engineering.
- *Hardcoded SQL in Python*: No schema visibility, hard to maintain, breaks the config-driven pattern.

---

## R-006: Zero-Row Export Handling

**Question**: FR-024 says "row count > 0 for tables expected to have data." What happens when a table has zero rows?

**Decision**: Always write a valid empty Parquet file with correct schema. Zero-row export is success, not error. The `dq_rules` in each schema define `min_rows` — only flag as DQ failure if `min_rows > 0` and actual count is 0.

**Rationale**: Matches archive_executor behavior. The spec edge case explicitly states: "A valid empty Parquet file with correct schema is still produced to signal a successful export of an empty dataset." Academic context and hierarchy tables in production are never empty (grades, majors, seasons must exist for the platform to operate), so `min_rows: 1` is appropriate for those.

---

## R-007: Item Mapping Source Query

**Question**: FR-006 says "source item-curriculum links from Memora Review Item and related Lesson/Stage tables." Do we need JOINs or are the hierarchy IDs directly on the Review Item?

**Decision**: Direct SELECT from `tabMemora Review Item` — all five hierarchy IDs (`subject`, `track`, `unit`, `topic`, `lesson`) are stored directly as fields on the DocType. No JOINs needed for the IDs.

**Rationale**: Confirmed by DocType inspection — `memora_review_item.json` has `subject` (Link to Memora Subject), `track`, `unit`, `topic`, `lesson` all as direct fields. The mapping query is:
```sql
SELECT
  `item_id`,
  `lesson`  AS lesson_id,
  `topic`   AS topic_id,
  `unit`    AS unit_id,
  `track`   AS track_id,
  `subject` AS subject_id
FROM `tabMemora Review Item`
WHERE `lesson` IS NOT NULL AND `lesson` != ''
  AND `topic`  IS NOT NULL AND `topic`  != ''
  AND `unit`   IS NOT NULL AND `unit`   != ''
  AND `track`  IS NOT NULL AND `track`  != ''
  AND `subject`IS NOT NULL AND `subject`!= ''
ORDER BY `item_id`
```
The WHERE clause implements FR-007 (exclude items without fully resolved curriculum path).

---

## R-008: grade_majors Source Query

**Question**: `tabMemora Grade Major` is a Frappe child table. What columns does it have and how to query it?

**Decision**: Query using `parent` as grade reference, filter `parenttype = 'Memora Grade'`.

**Rationale**: Frappe child tables store the parent DocType name in `parenttype` and parent name in `parent`. The Grade Major child table has: `name` (Frappe PK), `parent`, `parenttype`, `parentfield`, `idx`, `major`. The export query:
```sql
SELECT `parent` AS grade, `major`
FROM `tabMemora Grade Major`
WHERE `parenttype` = 'Memora Grade'
ORDER BY `parent`, `major`
```
No additional JOIN needed since grade IDs are already in `grades.parquet`.
