# Contract: Memora Archive Job DocType

## DocType JSON Key Properties

```json
{
  "name": "Memora Archive Job",
  "module": "Memora Admin",
  "autoname": "ARCH-.#####.",
  "sort_field": "modified",
  "sort_order": "DESC",
  "track_changes": 1
}
```

## Server Actions

### Retry Archive Job

**Endpoint**: `memora_admin.memora_admin.doctype.memora_archive_job.memora_archive_job.retry_archive_job`

**Method**: `@frappe.whitelist()`

**Input**: `job_name` (str) — Archive Job document name

**Precondition**: `job.status == "Failed"`

**Effect**:
- Sets `status = "Pending"`
- Sets `retry_count = 0`
- Clears `error_log`
- Clears `execution_stage`
- Saves with `ignore_permissions=True`

**Error**: Throws `frappe.ValidationError` if status is not "Failed"

---

## Scheduled Task

### check_seasons_for_archive

**Schedule**: Daily at 01:20 (`20 1 * * *`)

**Module**: `memora_admin.tasks.archive_trigger.check_seasons_for_archive`

**Logic**:
1. Query all seasons with `is_published = 0` and `end_date < CURDATE()`
2. For each season, load archive type definitions from YAML registry
3. For each archive type, attempt to create an Archive Job record:
   - `source_doctype` from archive type YAML
   - `archive_scope` = season name (e.g., "SEAS-00027")
   - `schema_version` from archive type YAML
   - `meta` = populated JSON with query_filter (date range from season), export_columns, schema_snapshot, related_tables
   - `status` = "Pending"
   - `post_archive_action` = "Keep" (default)
4. Catch `DuplicateEntryError` (unique constraint violation) — skip silently

---

## State Transition Contract

### Atomic Claim (Executor → DB)

```sql
UPDATE `tabMemora Archive Job`
SET status = 'Processing',
    claimed_at = NOW(),
    started_at = NOW(),
    execution_stage = 'claiming'
WHERE name = %s AND status = 'Pending'
```

Returns affected_rows. If 0 → job was already claimed, skip.

### Completion (Executor → DB)

```sql
UPDATE `tabMemora Archive Job`
SET status = 'Completed',
    completed_at = NOW(),
    execution_stage = 'done',
    row_count = %s,
    file_path = %s,
    file_checksum = %s,
    file_size_bytes = %s,
    duration_seconds = %s,
    snapshot_taken_at = %s
WHERE name = %s AND status = 'Processing'
```

### Failure with Retry (Executor → DB)

```sql
UPDATE `tabMemora Archive Job`
SET status = 'Pending',
    retry_count = retry_count + 1,
    error_log = %s,
    execution_stage = NULL
WHERE name = %s AND status = 'Processing' AND retry_count < 3
```

### Permanent Failure (Executor → DB)

```sql
UPDATE `tabMemora Archive Job`
SET status = 'Failed',
    error_log = %s,
    completed_at = NOW()
WHERE name = %s AND status = 'Processing' AND retry_count >= 3
```

### Stuck Job Detection (Executor → DB)

```sql
UPDATE `tabMemora Archive Job`
SET status = 'Failed',
    error_log = CONCAT('Job stuck in Processing for >1 hour at stage: ', COALESCE(execution_stage, 'unknown'))
WHERE status = 'Processing'
AND claimed_at < NOW() - INTERVAL 1 HOUR
```

### Stage Update (Executor → DB)

```sql
UPDATE `tabMemora Archive Job`
SET execution_stage = %s
WHERE name = %s
```

Called at each processing step: `claiming`, `exporting_fact`, `exporting_dimensions`, `verifying`, `publishing`, `done`.

---

## Notification Contract

### On Permanent Failure

**Trigger**: When executor sets `status = 'Failed'` (retry_count >= 3)

**Mechanism**: The executor cannot use Frappe runtime. Instead, the scheduled task `check_seasons_for_archive` (or a companion task) scans for newly failed jobs and sends notifications via Frappe.

**Alternative**: Add a separate daily task `notify_failed_archive_jobs` that:
1. Queries `status='Failed' AND notified=0`
2. Sends email via `frappe.sendmail()` to System Manager role
3. Sets `notified=1` (add field if needed)

---

## Migration Script

### Composite Unique Index

```sql
CREATE UNIQUE INDEX `idx_archive_job_unique`
ON `tabMemora Archive Job` (`source_doctype`(100), `archive_scope`(100), `schema_version`(50));
```

**Note**: Length prefixes required because MariaDB InnoDB limits index key length to 767 bytes with `utf8mb4`.
