# `Memora Task Log Archive Batch` DocType Contract

## File Paths

```
memora_admin/memora_admin/doctype/memora_task_log_archive_batch/
├── memora_task_log_archive_batch.json   # DocType definition
└── memora_task_log_archive_batch.py     # Controller (minimal)
```

## DocType JSON (key fields)

```json
{
  "name": "Memora Task Log Archive Batch",
  "module": "Memora Admin",
  "autoname": "TLBATCH-.#####.",
  "engine": "InnoDB",
  "track_changes": 0,
  "fields": [
    {"fieldname": "source_doctype", "fieldtype": "Data", "label": "Source DocType", "reqd": 1},
    {"fieldname": "date_from",      "fieldtype": "Date", "label": "Date From",     "reqd": 1},
    {"fieldname": "date_to",        "fieldtype": "Date", "label": "Date To",       "reqd": 1},
    {"fieldname": "cutoff_date",    "fieldtype": "Date", "label": "Cutoff Date"},
    {"fieldname": "row_count",      "fieldtype": "Int",  "label": "Row Count",     "default": "0"},
    {"fieldname": "file_path",      "fieldtype": "Data", "label": "File Path",     "length": 500},
    {"fieldname": "file_checksum",  "fieldtype": "Data", "label": "File Checksum", "length": 64},
    {"fieldname": "status", "fieldtype": "Select", "label": "Status", "reqd": 1,
     "options": "Pending\nExported\nSynced\nPurged\nFailed"},
    {"fieldname": "exported_at",    "fieldtype": "Datetime", "label": "Exported At"},
    {"fieldname": "synced_at",      "fieldtype": "Datetime", "label": "Synced At"},
    {"fieldname": "purged_at",      "fieldtype": "Datetime", "label": "Purged At"},
    {"fieldname": "last_error",     "fieldtype": "Text",     "label": "Last Error"},
    {"fieldname": "retry_count",    "fieldtype": "Int",      "label": "Retry Count", "default": "0"},
    {"fieldname": "archive_job_id", "fieldtype": "Data",     "label": "Archive Job ID", "length": 140}
  ]
}
```

## Permissions

| Role | Read | Write | Create | Delete | Export | Report |
|------|------|-------|--------|--------|--------|--------|
| System Manager | ✓ | | | | ✓ | ✓ |
| Task Admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

## No UNIQUE Constraints

Unlike `tabMemora Archive Job` (which has `idx_archive_job_unique` on `source_doctype + archive_scope + schema_version`), `Memora Task Log Archive Batch` has no uniqueness constraint. Deduplication is enforced at the archive job level. The `archive_job_id` field provides the linkage to the unique archive job.
