# Research: Task Log Archive

## R-01: Terminal Status Discrepancy

**Question**: Spec (FR-002) lists terminal statuses as `Success`, `Failed`, `Skipped`. The `tabMemora Task Run Log` DocType JSON shows `options: "Success\nFailed\nPartial"`. Which is correct?

**Finding**: The DocType definition is authoritative. The column is a Frappe `Select` field locked to the three values present in the JSON. There is no `Skipped` status and no `Partial` status mentioned by both — the spec's use of `Skipped` appears to be a holdover from an earlier design iteration.

**Decision**: Implementation uses `('Success', 'Failed', 'Partial')` as the terminal status set. This should be defined as a constant in the archive task and referenced in both the eligibility query and the YAML's `fact_sql`. If a `Skipped` status is added to the DocType in future, it must be explicitly classified at that time.

**Rationale**: SQL queries against a `Select` field with values not in the options list would return zero rows silently, which is a safety hazard. Using the actual DocType values prevents silent exclusions.

**Alternatives considered**: Use spec's `Skipped` — rejected because it does not exist in the DocType.

---

## R-02: Analytics Pipeline Integration Pattern

**Question**: How does the `Exported → Synced` transition work? Should the archive task create `tabMemora Archive Job` records and let the executor handle transfer/ingestion, or should it handle the analytics handoff independently?

**Finding**: The existing archive executor is the analytics pipeline. It runs as a standalone cron process and handles Transfer → Ingest → Complete for any `tabMemora Archive Job` record. All existing archive types (practice_log, interaction_log, memory_state) follow this pattern.

**Decision**: The Frappe archive task acts as a scheduler:
1. Creates `tabMemora Archive Job` records (using scheduler.py's `create_pending_jobs()` pattern adapted for `completed_at` scope and status filtering)
2. Creates a linked `Memora Task Log Archive Batch` record for each new archive job
3. On each subsequent run, scans `Exported` batches whose linked archive job has reached `Completed` and transitions them to `Synced`

The `Synced` transition is purely driven by the archive job's `Completed` status. No new monitoring task is needed — the daily archive task does both scheduling and status synchronization.

**Rationale**: Maximises infrastructure reuse (executor, transfer, ingestion). No changes to archive_executor beyond adding the new YAML schema.

**Alternatives considered**: Full self-contained Frappe task (no archive_executor) — rejected because it would duplicate the transfer/ingestion logic and create a second code path to maintain.

---

## R-03: Purge Pattern — Select-then-Delete vs Direct DELETE

**Question**: FR-013 mandates select-then-delete. The existing `purge.py` uses `DELETE WHERE filter_column BETWEEN ... LIMIT n`. Why the difference, and how does it affect implementation?

**Finding**: The task run log purge must use `SELECT name ... LIMIT 10000` → `DELETE WHERE name IN (...)` because:
- It enables status re-verification at purge time (only rows still terminal-status get deleted, protecting any rows that were updated after export)
- It avoids locking entire date-range scans on a table that may still have active writes
- The existing purge in `purge.py` is acceptable for practice/interaction logs because their rows are immutable after creation; task run logs may theoretically be updated (e.g., `completed_at` set after the export window)

**Decision**: The purge task (`purge_task_log.py`) uses:
```sql
SELECT name FROM `tabMemora Task Run Log`
WHERE status IN ('Success', 'Failed', 'Partial')
  AND completed_at >= %s AND completed_at < %s
  AND completed_at < DATE_SUB(NOW(), INTERVAL %s DAY)
ORDER BY completed_at
LIMIT 10000
```
Then: `DELETE FROM `tabMemora Task Run Log` WHERE name IN (%s, %s, ...)`

The retention window guard (`completed_at < NOW() - INTERVAL retention_days DAY`) is applied in the SELECT even for `Synced` batches to prevent any edge-case deletion of rows that regressed into the retention window (e.g., system clock correction).

**Rationale**: Correctness over performance. For 50,000-row batches in 10,000-row sub-batches, this adds 5 SELECT round trips but provides a meaningful safety net.

---

## R-04: innodb_lock_wait_timeout Session Setting

**Question**: FR-015 requires `innodb_lock_wait_timeout = 5` per purge sub-batch connection. Is this safe and achievable with the current DB connection pattern?

**Finding**: MariaDB supports `SET SESSION innodb_lock_wait_timeout = 5` without elevated privileges. The `get_connection()` helper in `db.py` returns a new PyMySQL connection for each call. Setting the session variable immediately after acquiring the connection is safe and standard practice.

**Decision**: The purge task opens a new connection for each sub-batch, sets `innodb_lock_wait_timeout = 5` immediately, executes the DELETE, commits, closes. The sleep between batches happens outside the connection context.

---

## R-05: Batch Concurrency Guard

**Question**: FR (edge case) says two archive instances must not create duplicate batches. How is this enforced?

**Finding**: The existing scheduler's `_job_exists()` check (per `source_doctype + archive_scope + schema_version`) already enforces this for `tabMemora Archive Job`. Since each archive batch maps 1:1 to an archive job, and archive jobs have a UNIQUE constraint on `(source_doctype, archive_scope, schema_version)`, a second concurrent archive task run would detect the existing job and skip it.

**Decision**: No additional concurrency guard needed at the batch level. The archive task also acquires a Frappe-level file lock (or uses Frappe's scheduler single-execution guarantee) to prevent concurrent task executions.

---

## R-06: Covering Index Strategy

**Question**: FR-019 requires `(status, completed_at, name)` index. How is this created in a Frappe app?

**Finding**: Frappe's standard DocType JSON does not support arbitrary composite indexes. Custom indexes must be created in `setup.py`'s `before_migrate` or `after_migrate` hook using direct SQL (`ALTER TABLE ... ADD INDEX`). This pattern is already used in the project for `tabMemora Memory State`'s partitioning and the `archive_delete_audit_log` table.

**Decision**: Add the index in `memora_admin/memora_admin/setup.py` under `before_migrate` using:
```sql
ALTER TABLE `tabMemora Task Run Log`
  ADD INDEX IF NOT EXISTS `idx_task_log_archive`
    (`status`, `completed_at`, `name`)
```
`IF NOT EXISTS` is supported in MariaDB 10.1+ and ensures idempotency across migrations.

---

## R-07: Batch DocType — Naming and Autoname

**Question**: The spec says `Memora Task Log Archive Batch` does not need a unique constraint like `idx_archive_job_unique`. What autoname strategy is appropriate?

**Finding**: Using `autoname: "TLBATCH-.#####."` gives human-readable identifiers. Since multiple batches can exist for different date ranges, there is no uniqueness conflict at the batch level. The linked archive job name (stored as a Data field) provides the analytical uniqueness bridge.

**Decision**: `autoname: "TLBATCH-.#####."`. No UNIQUE constraint on the batch DocType itself (multiple batches per date range are allowed if they cover non-overlapping windows; overlaps are prevented at the archive job level).

---

## R-08: Dimensions in task_run_log Archive

**Question**: Does the task_run_log need player/lesson dimension snapshots?

**Finding**: `tabMemora Task Run Log` has no foreign key to player, lesson, or any other entity requiring a dimension snapshot. Fields like `task_name`, `triggered_by`, and status are all primitive values (strings, datetimes, integers). The archive is self-contained.

**Decision**: `dimensions: []` in the YAML schema. No dimension export logic needed. The `related_tables` in job_meta will be empty. The DQ validation uses only intrinsic checks (not_null, enum_values, min_value, scope_range, unique_key).

---

## R-09: Runtime Cap Implementation

**Question**: FR-016 requires a 300-second runtime cap. How should this be implemented in the Frappe tasks?

**Finding**: The archive task and purge task can both capture a `start_time = time.monotonic()` and check `time.monotonic() - start_time >= 300` after each batch/operation. Since both tasks are scheduled daily, any remaining work will be processed on the next run.

**Decision**: Both tasks have a `RUNTIME_CAP_SECONDS = 300` constant and check elapsed time:
- Archive task: after each archive job creation
- Purge task: after each 10,000-row sub-batch is committed

When the cap is reached, the task exits cleanly and logs the remaining work count.
