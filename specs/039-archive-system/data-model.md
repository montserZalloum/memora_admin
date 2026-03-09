# Data Model: Memora Archive System

**Date**: 2026-03-09 | **Feature**: 039-archive-system

## Entity: Memora Archive Job (Frappe DocType)

### Identity

- **Autoname**: `ARCH-.#####.` (e.g., ARCH-00001)
- **Module**: Memora Admin
- **Unique constraint**: Composite index on (`source_doctype`, `archive_scope`, `schema_version`) — enforced via migration script (Frappe JSON only supports single-field unique)

### Fields

| Field | Fieldtype | Required | Read-Only | Default | Options/Notes |
|-------|-----------|----------|-----------|---------|---------------|
| **Identity & Scope** | | | | | |
| `source_doctype` | Data | Yes | Yes | — | e.g., "Memora Practice Log" |
| `archive_scope` | Data | Yes | Yes | — | Season ID, e.g., "SEAS-00027" |
| `schema_version` | Data | Yes | Yes | — | e.g., "v1" |
| `archive_type` | Data | No | Yes | — | Archive type key from YAML registry |
| **Status & Lifecycle** | | | | | |
| `status` | Select | Yes | Yes | Pending | Pending / Processing / Completed / Purged / Failed |
| `execution_stage` | Data | No | Yes | — | claiming / exporting_fact / exporting_dimensions / verifying / publishing / done |
| `priority` | Select | No | Yes | Normal | Low / Normal / High |
| **Execution Tracking** | | | | | |
| `started_at` | Datetime | No | Yes | — | When executor began processing |
| `completed_at` | Datetime | No | Yes | — | When processing finished |
| `claimed_at` | Datetime | No | Yes | — | When executor claimed the job |
| `snapshot_taken_at` | Datetime | No | Yes | — | When dimension snapshots were captured |
| `duration_seconds` | Float | No | Yes | — | Total execution time |
| **Output Metadata** | | | | | |
| `row_count` | Int | No | Yes | 0 | Rows exported in fact file |
| `file_path` | Data | No | Yes | — | Final archive batch directory path |
| `file_checksum` | Data | No | Yes | — | SHA-256 of the fact Parquet file |
| `file_size_bytes` | Int | No | Yes | 0 | Fact file size in bytes |
| **Retry & Error** | | | | | |
| `retry_count` | Int | No | Yes | 0 | Number of retry attempts |
| `error_log` | Long Text | No | Yes | — | Error details on failure |
| **Behavior** | | | | | |
| `post_archive_action` | Select | No | Yes | Keep | Keep / Delete |
| `source_deleted` | Check | No | Yes | 0 | Whether source data has been purged |
| `purge_progress` | JSON | No | Yes | — | Tracks purge resumption state |
| **Transfer Lifecycle** | | | | | |
| `transfer_status` | Select | No | Yes | Pending | Pending / Transferred / Transfer Failed |
| `transferred_at` | Datetime | No | Yes | — | When transfer was verified |
| `local_deleted_at` | Datetime | No | Yes | — | When local copy was deleted |
| **Metadata** | | | | | |
| `meta` | JSON | No | Yes | — | Query instructions (see schema below) |
| **Actions** | | | | | |
| `retry_btn` | Button | — | — | — | `depends_on: eval:doc.status=='Failed'` |

### Meta JSON Schema

```json
{
  "query_filter": {
    "date_from": "2025-09-01",
    "date_to": "2026-01-01",
    "filter_column": "last_seen_at"
  },
  "related_tables": [
    {
      "entity": "player",
      "schema_version": "v1",
      "source_table": "tabMemora Player Profile",
      "join_column": "player_id",
      "fact_column": "player_id"
    },
    {
      "entity": "review_item",
      "schema_version": "v1",
      "source_table": "tabMemora Review Item",
      "join_column": "item_id",
      "fact_column": "item_id"
    }
  ],
  "export_columns": [
    "player_id", "item_id", "first_seen_at", "last_seen_at",
    "last_result", "attempt_count", "correct_count"
  ],
  "schema_snapshot": {
    "columns": [
      {"name": "player_id", "type": "VARCHAR(140)"},
      {"name": "item_id", "type": "VARCHAR(36)"},
      {"name": "first_seen_at", "type": "DATETIME"},
      {"name": "last_seen_at", "type": "DATETIME"},
      {"name": "last_result", "type": "ENUM('Correct','Incorrect')"},
      {"name": "attempt_count", "type": "INT UNSIGNED"},
      {"name": "correct_count", "type": "INT UNSIGNED"}
    ],
    "primary_key": ["player_id", "item_id"]
  },
  "notes": "Season SEAS-00027 end-of-term archive"
}
```

### State Machine

```
Pending ──→ Processing ──→ Completed ──→ Purged
                │
                ↓
             Failed (retry_count < 3 → back to Pending)
             Failed (retry_count >= 3 → permanent, notify admin)
```

Valid transitions:

| From | To | Trigger |
|------|----|---------|
| Pending | Processing | Executor atomic claim (`UPDATE ... SET status='Processing' WHERE status='Pending'`) |
| Processing | Completed | Executor finishes successfully |
| Processing | Failed→Pending | Executor fails, retry_count < 3 (goes to Pending with retry_count++) |
| Processing | Failed | Executor fails, retry_count >= 3 |
| Completed | Purged | Purge process deletes source data |
| Failed | Pending | Manual retry button (resets retry_count to 0) |

### Validation Rules

1. Status transitions enforced via `VALID_TRANSITIONS` dict in Python class
2. Manual retry only allowed when `status == "Failed"`
3. No field is user-editable — all set programmatically
4. Composite unique constraint on (`source_doctype`, `archive_scope`, `schema_version`)

### Permissions

- System Manager: Read only (all fields are read-only, no create/write from UI)
- Programmatic creation via scheduled task (with `ignore_permissions=True`)

---

## Entity: Dimension Schema Definition (YAML file)

### Location

`{SCHEMA_REGISTRY_PATH}/dimensions/{entity}.v{version}.yaml`

### Schema

```yaml
entity: player            # Entity name (used in manifest)
version: v1               # Immutable version
source_table: "tabMemora Player Profile"  # MariaDB table
id_column: name           # Column used for ID matching
fields:                   # Columns to export
  - name
  - display_name
  - grade
  - academic_plan
  - mobile
```

### Rules

- Version is immutable — never modify an existing version file
- New versions create new files (e.g., `player.v2.yaml`)
- `id_column` is used to filter dimension rows by referenced IDs from fact data

---

## Entity: Archive Type Definition (YAML file)

### Location

`{SCHEMA_REGISTRY_PATH}/archive_types/{type}.v{version}.yaml`

### Schema

```yaml
archive_type: practice_log   # Type identifier
version: v1                  # Immutable version
source_table: "tabMemora Practice Log"   # Fact table
fact_columns:                 # Columns to export from fact table
  - player_id
  - item_id
  - first_seen_at
  - last_seen_at
  - last_result
  - attempt_count
  - correct_count
scope_column: last_seen_at    # Column used for date-range scoping
dimensions:                   # Required dimension snapshots
  - entity: player
    schema_version: v1
    join_column: player_id    # Fact table column → dimension ID
  - entity: review_item
    schema_version: v1
    join_column: item_id      # Fact table column → dimension ID
schema_snapshot:              # Table schema at this version
  columns:
    - {name: player_id, type: "VARCHAR(140)"}
    - {name: item_id, type: "VARCHAR(36)"}
    - {name: first_seen_at, type: DATETIME}
    - {name: last_seen_at, type: DATETIME}
    - {name: last_result, type: "ENUM('Correct','Incorrect')"}
    - {name: attempt_count, type: "INT UNSIGNED"}
    - {name: correct_count, type: "INT UNSIGNED"}
  primary_key: [player_id, item_id]
```

---

## Entity: Archive Batch Directory (filesystem)

### Structure

```
{ARCHIVE_OUTPUT_PATH}/
  └── ARCH-00001/                    # Deterministic from job name
      ├── manifest.json
      ├── fact_practice_log.parquet
      ├── dim_player.parquet
      └── dim_review_item.parquet
```

### Permissions

- Directory: `0700` (owner-only)
- Files: `0600` (owner-only)

---

## Entity: Manifest (JSON file)

### Schema

```json
{
  "batch_id": "ARCH-00001",
  "source_doctype": "Memora Practice Log",
  "archive_scope": "SEAS-00027",
  "schema_version": "v1",
  "created_at": "2026-03-09T02:15:00",
  "snapshot_taken_at": "2026-03-09T02:15:01",
  "files": [
    {
      "role": "fact",
      "filename": "fact_practice_log.parquet",
      "row_count": 850000,
      "checksum": "sha256:abc123...",
      "size_bytes": 52428800
    },
    {
      "role": "dimension",
      "entity": "player",
      "snapshot_schema_version": "v1",
      "scope": "batch_referenced",
      "referenced_by": "player_id",
      "filename": "dim_player.parquet",
      "row_count": 1200,
      "checksum": "sha256:def456...",
      "size_bytes": 204800
    },
    {
      "role": "dimension",
      "entity": "review_item",
      "snapshot_schema_version": "v1",
      "scope": "batch_referenced",
      "referenced_by": "item_id",
      "filename": "dim_review_item.parquet",
      "row_count": 5000,
      "checksum": "sha256:ghi789...",
      "size_bytes": 512000
    }
  ]
}
```

---

## Relationships

```
Memora Season (1) ──triggers──→ (N) Memora Archive Job
                                      │
                                      │ meta.related_tables
                                      ↓
                              Dimension Schema (YAML)
                                      │
                              Archive Type (YAML)
                                      │
                                      ↓
                              Archive Batch Directory
                                  ├── manifest.json
                                  ├── fact_*.parquet
                                  └── dim_*.parquet
```
