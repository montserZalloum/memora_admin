# Research: Interaction Log Archiving

**Feature Branch**: `040-interaction-log-archive`
**Date**: 2026-03-11

## R-01: Generalizing the Validator for Multi-DocType Support

**Decision**: Make DQ validation configurable per archive type by defining DQ rules in the YAML schema, with a generic validator engine that interprets them.

**Rationale**: The current `validate_fact_quality()` in `validator.py` is hardcoded for Practice Log fields (7 NOT NULL columns, `attempt_count >= 1`, `correct_count <= attempt_count`, `last_result` enum check, `first_seen_at <= last_seen_at`, composite key uniqueness). Interaction Log has completely different fields and semantics. Rather than adding if/else branches per doctype, a declarative DQ rule set in the YAML schema keeps the validator generic and extensible.

**Alternatives considered**:
- **Separate validator per doctype**: Rejected because it duplicates the validation engine logic and creates parallel maintenance burden.
- **Hardcoded switch in validator.py**: Rejected because it creates tight coupling and requires code changes for each new doctype.

**Design**: Add a `dq_rules` section to the archive type YAML. The generic validator reads these rules and applies them. Rule types: `not_null`, `min_value`, `max_value`, `enum_values`, `column_lte_column`, `scope_range`, `referential`, `unique_key`.

---

## R-02: Interaction Log Table Structure vs Practice Log

**Decision**: Treat Interaction Log as a standard Frappe DocType with `name` as the single unique key (auto-named `LOG-#####.`), and `timestamp` as the scope/filter column.

**Rationale**: Unlike Practice Log (custom table, composite PK `(player_id, item_id)`, no Frappe `name`), the Interaction Log is a standard Frappe DocType with auto-incrementing `name`. This means:
- Deduplication key on analytics: `name` (the record identifier)
- Scope column: `timestamp` (not `last_seen_at`)
- DELETE uses `name` column (WHERE name IN (...)) instead of composite key
- DQ-16 uniqueness check is on `name` alone (not a composite key)

**Alternatives considered**:
- Using `timestamp` + `player` as composite key: Rejected because two events can have the same timestamp for the same player (e.g., Started and Completed at same second).

---

## R-03: Dimension Strategy for Interaction Log

**Decision**: Reuse existing `player.v3` dimension. Create new `lesson.v1` dimension. Derive `season` and `plan` from player (same as Practice Log).

**Rationale**: Interaction Log links to `player` (Memora Player Profile) and `lesson` (Memora Lesson). The player dimension already captures season_id and plan_id. The lesson dimension is new and should capture: lesson_id, lesson_title, topic, subject, track, unit, base_xp, is_published, is_reviewable. This gives analytics rich context for slicing interaction data.

**Alternatives considered**:
- Embedding lesson fields directly in fact table via JOIN: Rejected because it denormalizes the fact table and inflates Parquet file size (lesson titles repeated millions of times).
- Skipping lesson dimension: Rejected because the spec requires grouping by lesson in aggregates.

---

## R-04: Analytics-Side Aggregation Strategy

**Decision**: Extend the analytics CLI with new commands for aggregation refresh and recent layer management. Aggregations run analytics-side (DuckDB) after ingestion, not on the production executor.

**Rationale**: The spec requires daily and monthly aggregates (interaction count, total time spent, total errors, completion rate) grouped by day/month + player + lesson + event_type. Running aggregation on the analytics server (DuckDB) is natural because:
1. All historical data lives there
2. DuckDB is optimized for analytical queries
3. Keeps the archive executor focused on data movement

**Design**:
- New CLI command: `memora-analytics refresh-aggregates --archive-type interaction_log`
- Analytics server maintains three tables: `interaction_log_raw` (historical), `interaction_log_recent` (90-day window), `interaction_log_daily_agg`, `interaction_log_monthly_agg`
- Refresh triggered after successful ingestion (new pipeline stage or post-ingestion hook)
- Recent layer: REPLACE/rebuild from raw where `timestamp >= NOW() - 90 days`

**Alternatives considered**:
- Running aggregation on the executor side: Rejected because it would require downloading all historical data back.
- Real-time aggregation: Rejected because batch post-ingestion is simpler and sufficient per SC-006 (within 1 hour).

---

## R-05: Purge Adaptation for Frappe DocType with `name` PK

**Decision**: Adapt the purge module to delete by `name` column using `DELETE FROM ... WHERE name IN (SELECT name FROM ... WHERE timestamp >= %s AND timestamp < %s LIMIT %s)` pattern.

**Rationale**: The current purge uses the scope column (last_seen_at) with a date range and LIMIT for batched deletion. For Interaction Log, the same pattern works but the scope column is `timestamp` and the table uses Frappe's standard `name` primary key. The DELETE needs to be by `name` to avoid issues with index usage on large tables.

**Design**: The purge module already reads `query_filter` from `job_meta` which contains `filter_column` and date range. The purge SQL template can be made generic:
```sql
DELETE FROM `{table}` WHERE `{filter_column}` >= %s AND `{filter_column}` < %s LIMIT %s
```
This already works for both composite PK and single PK tables.

**Alternatives considered**:
- DELETE by name list (SELECT then DELETE): Rejected as unnecessary — the date-range LIMIT pattern is simpler and already proven.

---

## R-06: Job Scheduling — Creating Daily Archive Jobs

**Decision**: Add a job creator script/command that scans for unarchived date ranges and creates Pending archive jobs. Runs daily via cron before the executor.

**Rationale**: The current pipeline assumes jobs are created externally (manually or by another process). For daily automated archiving, we need a mechanism to:
1. Determine the retention window (14 days from today)
2. Find unarchived date ranges (days with data older than retention, no existing Completed/Pending/Processing job)
3. Create one Pending job per day (or configurable batch size)

This keeps the executor stateless and focused on processing.

**Design**: New module `archive_executor/scheduler.py` with:
- `create_pending_jobs(config, archive_type, retention_days)` function
- Scans `tabMemora Interaction Log` for MIN(timestamp) and MAX(timestamp)
- Creates one job per day (date_from=day_start, date_to=day_start+1day) for all days older than retention_window
- Skips days that already have a non-Failed job
- Populates `job_meta` from the YAML archive type schema

**Alternatives considered**:
- Single large job per run: Rejected because the spec requires granular retry per batch (per-day scoping).
- Frappe scheduled task: Rejected because the executor runs outside Frappe in its own venv.

---

## R-07: Concurrent Job Prevention

**Decision**: Reuse the existing `idx_archive_job_unique` constraint on `(source_doctype, archive_scope, schema_version)` where `archive_scope` encodes the date range (e.g., `2026-02-25`).

**Rationale**: Each day gets a unique `archive_scope` value (the date string). The UNIQUE KEY prevents two jobs for the same doctype + date + schema version. This already works for Practice Log and naturally extends to Interaction Log.

**Alternatives considered**:
- Application-level locking: Rejected because the DB constraint is atomic and proven.

---

## R-08: Performance Target — 1M Records in 2 Hours

**Decision**: The existing pipeline architecture (streaming cursor, chunked Parquet writes, rsync transfer) is sufficient for 1M records within 2 hours.

**Rationale**: Practice Log benchmarks show ~10K rows exported per second with dimension enrichment. At that rate, 1M rows would take ~100 seconds for export. Transfer and ingestion add overhead proportional to file size, not row count. The 2-hour budget is generous. The main risk is the fact SQL query execution time on a large table — mitigated by having an index on `timestamp`.

**Action**: Ensure `tabMemora Interaction Log` has an index on `timestamp` column for efficient date-range filtering.
