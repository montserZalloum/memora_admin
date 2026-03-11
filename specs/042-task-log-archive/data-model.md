# Data Model: Task Log Archive

## Entities

### 1. `tabMemora Task Run Log` (existing — source table)

Standard Frappe DocType. Archive-eligible rows are read-only by convention after `completed_at` is set.

| Column | Type | Notes |
|--------|------|-------|
| `name` | VARCHAR(140) | PK, autoname `TASK-.#####.` |
| `task_name` | VARCHAR(140) | Required |
| `run_date` | Date | Required |
| `started_at` | Datetime | Required |
| `completed_at` | Datetime | Nullable — rows with NULL excluded from archive |
| `duration_sec` | Float | |
| `status` | Select | `Success`, `Failed`, `Partial` (NOT `Skipped` — see research R-01) |
| `triggered_by` | Select | `Scheduler`, `Manual`, `Catch-up` |
| `processed_count` | Int | Default 0 |
| `failed_count` | Int | Default 0 |
| `error_message` | Text | |
| `failed_details` | Code (JSON) | |

**Archive eligibility**:
```sql
WHERE status IN ('Success', 'Failed', 'Partial')
  AND completed_at IS NOT NULL
  AND completed_at < DATE_SUB(NOW(), INTERVAL :retention_days DAY)
```

**New index (created in setup.py / before_migrate)**:
```sql
ALTER TABLE `tabMemora Task Run Log`
  ADD INDEX IF NOT EXISTS `idx_task_log_archive` (`status`, `completed_at`, `name`)
```

---

### 2. `Memora Task Log Archive Batch` (new Frappe DocType)

Tracks the source-side lifecycle of each archive batch. One record per archive job.

| Field | Type | Notes |
|-------|------|-------|
| `name` | VARCHAR(140) | PK, autoname `TLBATCH-.#####.` |
| `source_doctype` | Data(140) | Always `Memora Task Run Log` |
| `date_from` | Date | Inclusive start of batch window |
| `date_to` | Date | Exclusive end of batch window |
| `cutoff_date` | Date | Retention cutoff used at batch creation |
| `row_count` | Int | Rows exported (0 if archive job exports 0 rows) |
| `file_path` | Data(500) | Local Parquet directory path |
| `file_checksum` | Data(64) | SHA-256 of fact Parquet file |
| `status` | Select | `Pending`, `Exported`, `Synced`, `Purged`, `Failed` |
| `exported_at` | Datetime | When status → Exported |
| `synced_at` | Datetime | When status → Synced (archive job Completed) |
| `purged_at` | Datetime | When status → Purged |
| `last_error` | Text | Most recent error message |
| `retry_count` | Int | Default 0 |
| `archive_job_id` | Data(140) | FK (soft link) to `tabMemora Archive Job` |

**Module**: `Memora Admin`
**No UNIQUE constraint** — uniqueness enforced at the archive job level (`idx_archive_job_unique`).

**Permissions**:
- System Manager: read, export, report
- Task Admin: read, write, create, delete, export, report

---

### 3. `tabMemora Archive Job` (existing — analytics delivery)

Not modified. One archive job is created per date window by the archive task. Drives the executor pipeline (Pending → Processing → Exported → Transferred → Ingested → Completed).

The batch's `Synced` transition is triggered when the linked archive job reaches `Completed`.

---

## State Machine: `Memora Task Log Archive Batch`

```
[New eligible window detected]
         │
         ▼
      Pending ──── Archive job creation fails ──► (job not created, no batch created)
         │
         │  archive_task_log.py creates archive job + batch
         ▼
      Exported ◄── archive_executor processes the archive job (Exported)
         │
         │  archive_executor completes: Transferred → Ingested → Completed
         │  archive_task_log.py detects Completed and transitions batch
         ▼
      Synced ──── purge_task_log.py is now permitted to delete source rows
         │
         │  purge_task_log.py deletes rows in sub-batches of 10,000
         ▼
      Purged

   At any stage:
      * ───► Failed  (error captured in last_error, retry_count incremented)
             │
             └─► next archive task run retries up to max_retry_count
                 (default: 3 per FR-018, matching archive_executor pattern)
```

**Transition rules**:
- `Pending → Exported`: set by archive task when the archive job transitions out of `Pending` (i.e., the executor claims and begins processing it). Actually this is set when the archive job itself reaches `Exported` status.
- `Exported → Synced`: set by archive task monitoring loop when linked archive job `status = 'Completed'`
- `Synced → Purged`: set by purge task when all source rows are deleted
- Any → `Failed`: set by archive task or purge task on error; clears when retried

---

## Archive YAML Schema: `task_run_log.v1`

See `contracts/task_run_log.v1.yaml` for the full schema.

Key differences from `interaction_log.v1`:
- `scope_column: completed_at` (not `timestamp`)
- `fact_sql.filtered` adds `AND status IN ('Success', 'Failed', 'Partial')` guard
- `dimensions: []` (no player/lesson snapshots)
- No `scope_range` DQ rule on a FK column; uses `scope_range` on `completed_at`

---

## Covering Index

```sql
-- Eligibility query support (SC-008: <1s for 500k rows)
ALTER TABLE `tabMemora Task Run Log`
  ADD INDEX IF NOT EXISTS `idx_task_log_archive`
    (`status`, `completed_at`, `name`)
```

This is a covering index for the archive eligibility query pattern:
```sql
SELECT name FROM `tabMemora Task Run Log`
WHERE status IN ('Success', 'Failed', 'Partial')
  AND completed_at IS NOT NULL
  AND completed_at < :cutoff
ORDER BY completed_at
LIMIT 50000
```

The `name` column in the index allows index-only scans for the select-then-delete purge pattern.
