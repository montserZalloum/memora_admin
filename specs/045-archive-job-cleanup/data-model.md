# Data Model: Archive Job Cleanup

## Existing Entities (read/delete only — no schema changes)

### Memora Archive Job (`tabMemora Archive Job`)

The target table for cleanup. Frappe-managed DocType with autoname `ARCH-.#####.`.

| Field | Type | Cleanup Relevance |
|-------|------|-------------------|
| `name` | varchar(140) PK | Row identifier — used in `DELETE WHERE name IN (...)` |
| `status` | varchar(140) | Filter: only `Purged` and `Failed` are eligible |
| `modified` | datetime | Age indicator — compared against retention cutoff |
| `source_doctype` | varchar(140) | Not used by cleanup |
| `archive_scope` | varchar(140) | Not used by cleanup |
| `schema_version` | int | Not used by cleanup |

**Unique constraint**: `idx_archive_job_unique` on `(source_doctype, archive_scope, schema_version)` — one active job per combination. This constraint is irrelevant to cleanup (we only delete terminal rows).

**Status values**: `Pending`, `Processing`, `Exported`, `Transferred`, `Ingested`, `Completed`, `Purged`, `Failed`

**Terminal states for cleanup**:
- `Purged` — archive completed successfully, source rows purged. Safe to delete after 30 days.
- `Failed` — archive failed permanently. Safe to delete after 90 days (extended for postmortem).

**Non-terminal states (NEVER deleted)**: `Pending`, `Processing`, `Exported`, `Transferred`, `Ingested`, `Completed`

### Memora Task Log Archive Batch (`tabMemora Task Log Archive Batch`)

Child tracking table referenced by `archive_job_id`. Used for dependency safety check only — NOT deleted by this task.

| Field | Type | Cleanup Relevance |
|-------|------|-------------------|
| `archive_job_id` | varchar(140) | Join key — matches `Memora Archive Job.name` |
| `status` | varchar(140) | Dependency check: non-terminal statuses block parent deletion |

**Status values**: `Pending`, `Exported`, `Synced`, `Purged`, `Failed`

**Terminal states**: `Purged`, `Failed`
**Non-terminal states (block parent deletion)**: `Pending`, `Exported`, `Synced`

## Relationships

```
Memora Archive Job (1) ←── archive_job_id ──→ (0..N) Memora Task Log Archive Batch
```

- The relationship is a loose string reference, not a Frappe Link field with referential integrity.
- An archive job may have zero batch rows (e.g., for non-task-log doctypes).
- Cleanup rule: if ANY related batch row has a non-terminal status, the parent archive job is preserved.

## Cleanup Logic (query-level)

### Pass 1: Purged jobs (30-day retention)

```sql
SELECT aj.name
FROM `tabMemora Archive Job` aj
WHERE aj.status = 'Purged'
  AND aj.modified < %(cutoff_purged)s
  AND aj.name NOT IN (
    SELECT DISTINCT tlab.archive_job_id
    FROM `tabMemora Task Log Archive Batch` tlab
    WHERE tlab.status NOT IN ('Purged', 'Failed')
  )
ORDER BY aj.modified ASC, aj.name ASC
LIMIT %(batch_size)s
```

### Pass 2: Failed jobs (90-day retention)

```sql
SELECT aj.name
FROM `tabMemora Archive Job` aj
WHERE aj.status = 'Failed'
  AND aj.modified < %(cutoff_failed)s
  AND aj.name NOT IN (
    SELECT DISTINCT tlab.archive_job_id
    FROM `tabMemora Task Log Archive Batch` tlab
    WHERE tlab.status NOT IN ('Purged', 'Failed')
  )
ORDER BY aj.modified ASC, aj.name ASC
LIMIT %(batch_size)s
```

### Deletion

```sql
DELETE FROM `tabMemora Archive Job` WHERE name IN (%(names)s)
```

Executed via `frappe.db.delete("Memora Archive Job", {"name": ["in", names]})` followed by `frappe.db.commit()` after each batch.

## No Schema Changes Required

This feature operates entirely on existing tables with existing indexes. No migrations, no new columns, no new tables.
