# Research: Memora Archive System

**Date**: 2026-03-09 | **Feature**: 039-archive-system

## R-001: Practice Log Table Structure

**Decision**: Practice Log is a raw SQL table (`tabMemora Practice Log`), not a Frappe DocType.

**Rationale**: High-volume table (~500M rows) managed via `setup.py` DDL, following the same pattern as `tabMemora Memory State`. Frappe ORM is too slow for tables at this scale.

**Findings**:
- **Schema** (from `memora_admin/memora_admin/setup.py:632-645`):
  - `player_id` VARCHAR(140) NOT NULL — composite PK part 1
  - `item_id` VARCHAR(36) NOT NULL — composite PK part 2
  - `first_seen_at` DATETIME NOT NULL
  - `last_seen_at` DATETIME NOT NULL
  - `last_result` ENUM('Correct', 'Incorrect') NOT NULL
  - `attempt_count` INT UNSIGNED NOT NULL DEFAULT 1
  - `correct_count` INT UNSIGNED NOT NULL DEFAULT 0
  - PRIMARY KEY (`player_id`, `item_id`)
- **Not partitioned** (unlike Memory State)
- **No `name` column** — no standard Frappe document ID
- **No season column** — season scoping must use programmatic logic (date ranges or player subscription dates in the `meta` field)

**Alternatives considered**: None — table already exists, schema is fixed.

---

## R-002: Season Lifecycle & End Detection

**Decision**: Hook into the existing `check_expired_seasons_challenge_reset()` pattern to detect ended seasons and create archive jobs.

**Rationale**: A daily scheduled task already detects expired seasons (daily at 01:10). Adding archive job creation as a separate scheduled task (e.g., daily at 01:15) keeps concerns separated while leveraging the same lifecycle moment.

**Findings**:
- **Season DocType**: `Memora Season` with fields: `season_title`, `season_seq` (Int, unique, read-only), `start_date`, `end_date`, `is_published`
- **Autoname**: `SEAS-.#####.` (e.g., SEAS-00027)
- **Existing end detection**: `check_expired_seasons_challenge_reset()` in `events/access_sync.py:91-129` — runs daily at 01:10, finds `is_published=1 AND end_date < today()`, sets `is_published=0`
- **Season events in hooks.py:157-161**: `after_insert`, `on_update`, `on_trash` → `access_sync.on_season_updated/deleted`
- **Season expiration task**: `tasks/season_expiration.py` — expires voucher cards at 01:05

**Season → Archive trigger approach**:
1. Add a new scheduled task `check_seasons_for_archive` at daily 01:20 (after season unpublish at 01:10)
2. Query: `SELECT name FROM tabMemora Season WHERE is_published=0 AND end_date < CURDATE()`
3. For each ended season, create Archive Job records (one per registered archive type)
4. Use `is_archived` flag on Season OR rely on Archive Job unique constraint to prevent duplicates

---

## R-003: Archive Job DocType Design

**Decision**: Standard Frappe DocType with read-only fields, status state machine, retry server action, and composite unique constraint via migration.

**Rationale**: Follows existing patterns (Voucher Batch for state machine, Admin Filter for server action buttons). Composite unique constraint requires a migration script since Frappe JSON schema only supports single-field `unique`.

**Findings**:
- **State machine pattern**: `memora_voucher_batch.py` uses `VALID_TRANSITIONS` dict + `_validate_status_transition()` in `validate()`
- **Read-only enforcement**: Both JSON-level (`"read_only": 1`) and JS-level (`frm.set_df_property("field", "read_only", 1)`)
- **Server action button**: JSON `Button` fieldtype + JS handler calling `frappe.call()` → `@frappe.whitelist()` Python method
- **Unique constraint**: Single-field via JSON `"unique": 1`. Composite requires raw SQL: `CREATE UNIQUE INDEX idx_archive_job_unique ON tabMemora Archive Job (source_doctype, archive_scope, schema_version)`
- **JSON field**: Use `"fieldtype": "JSON"` with `"read_only": 1` (pattern from `memora_plan_subject.json:43-47`)
- **Long Text field**: Use `"fieldtype": "Long Text"` for error_log (pattern from `memora_task_run_log.json:98-101`)

---

## R-004: Notification Pattern for Failed Jobs

**Decision**: Use the existing `frappe.publish_realtime()` + `frappe.sendmail()` dual notification pattern.

**Rationale**: Already proven in `purchase_sync.py` and `report_sync.py`. Reaches admins via both Desk popup and email.

**Findings** (from `events/purchase_sync.py`):
1. Query System Manager users: `frappe.get_all("Has Role", filters={"role": "System Manager", "parenttype": "User"}, fields=["parent"])`
2. Filter enabled users with email
3. `frappe.publish_realtime("eval_js", message=..., user="Administrator")` for Desk popup
4. `frappe.sendmail(recipients=..., subject=..., message=..., now=True)` for email

---

## R-005: Standalone Executor Environment

**Decision**: Separate Python virtualenv at `/opt/memora-archive/` with its own dependencies. DB credentials from environment variables. Schema registry path from `SCHEMA_REGISTRY_PATH` env var.

**Rationale**: User explicitly chose Option B (separate virtualenv) during clarification. Isolates the executor completely from Frappe runtime.

**Configuration**:
- **Virtualenv**: `/opt/memora-archive/venv/`
- **Dependencies**: `pyarrow`, `pandas`, `pymysql`, `pyyaml`
- **Env vars**: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `ARCHIVE_OUTPUT_PATH`, `SCHEMA_REGISTRY_PATH`, `LOG_PATH`
- **Cron**: `0 2 * * * /opt/memora-archive/venv/bin/python /opt/memora-archive/run.py`
- **File lock**: `/var/run/memora-archive.lock`

---

## R-006: Parquet Export Strategy

**Decision**: Use `pyarrow` directly (not via `pandas`) for memory efficiency with large tables. Stream rows in chunks.

**Rationale**: At 500M rows (though per-season batches will be much smaller), loading the entire result set into a pandas DataFrame would consume excessive memory. `pyarrow.parquet.ParquetWriter` supports incremental writes.

**Approach**:
1. Execute SQL query with server-side cursor (streaming)
2. Read rows in chunks of 50,000
3. Convert each chunk to `pyarrow.RecordBatch`
4. Write incrementally via `ParquetWriter`
5. Close writer → produces valid Parquet file

**Alternatives considered**:
- `pandas.to_parquet()`: Simpler API but requires full DataFrame in memory. Rejected for memory efficiency.
- `duckdb`: Could query MariaDB directly and export to Parquet. Rejected as unnecessary dependency.

---

## R-007: Season Scoping for Practice Log

**Decision**: Use date-range filtering based on season start/end dates. The `meta.query_filter` will contain `date_from` and `date_to` derived from the season record.

**Rationale**: Practice Log has no `season` column but has `first_seen_at` and `last_seen_at` datetime columns. The season's `start_date`/`end_date` provides the date range. Using `last_seen_at` as the filter column captures the most recent activity within the season window.

**Query pattern**:
```sql
SELECT player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count
FROM `tabMemora Practice Log`
WHERE last_seen_at >= '2025-09-01' AND last_seen_at < '2026-01-01'
```

**Note**: This means a row's `first_seen_at` might be in a previous season, but its `last_seen_at` places it in the current season. This is acceptable — the archive captures the state at the end of the season.

---

## R-008: Purge Strategy for Practice Log

**Decision**: Delete in batches of 10,000 rows with 2-second pauses, using the same date-range filter as the archive query.

**Rationale**: Practice Log has a composite PK (`player_id`, `item_id`). Batch deletion must use the same `WHERE` clause as the archive query to ensure only archived rows are deleted. Track progress via `purge_last_seen_at` (the `last_seen_at` value of the last deleted batch).

**Approach**:
```sql
DELETE FROM `tabMemora Practice Log`
WHERE last_seen_at >= %s AND last_seen_at < %s
ORDER BY last_seen_at, player_id, item_id
LIMIT 10000
```

**Resume**: Track `purge_progress` as JSON with `last_deleted_at` timestamp. On resume, add `AND last_seen_at >= %s` to the WHERE clause.
