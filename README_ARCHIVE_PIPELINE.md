# Memora Archive / Live-Sync Pipeline

## What it does

The archive pipeline exports practice-log data from the Memora MariaDB to a remote analytics server (DuckDB) in two streams:

| Stream | Purpose | Frequency |
|--------|---------|-----------|
| **Archive** | Exports data for a closed season, verifies quality, transfers Parquet + manifest to analytics server, ingests into DuckDB, removes overlapping live data (handoff), then optionally purges from MariaDB | Nightly at 02:00 |
| **Live Sync** | Full snapshot of current (open-season) data for real-time dashboards; excludes rows already archived | Nightly at 03:05 |

Both streams respect a **sync-pause** mechanism: an archive job sets `sync_paused=1` on the source while it is actively processing, so the live sync executor skips that source and avoids duplicating data.

---

## High-level architecture

```
Frappe (Memora Admin)
├── hooks.py scheduler → archive_trigger.py      creates Memora Archive Job records (01:20)
│                      → live_sync_trigger.py     creates Memora Live Sync Job records (03:00)
│                      → archive_monitor.py        health alerts every 6h
│                      → archive_notify.py         failure emails at 06:00
│                      → archive_stale_pause.py    stale-pause alerts at 07:00
│
Standalone venv (/opt/memora-archive/)
├── archive_executor/run.py          archive pipeline
└── archive_executor/live_sync.py    live sync pipeline

Both executors talk directly to MariaDB (PyMySQL) and the analytics server (SSH/rsync).
They have NO Frappe dependency — they read/update `tabMemora Archive Job` and
`tabMemora Live Sync Job` directly via SQL.

Analytics Server (separate host)
├── /data/analytics/archives/         received archive Parquet batches
├── /data/analytics/live/             received live sync Parquet batches
├── /data/analytics/memora.duckdb     target DuckDB
└── /opt/analytics/memora-analytics   CLI tool called by executor over SSH
```

---

## Archive pipeline — job lifecycle

```
Pending
  │  archive_trigger creates the job
  ▼
Processing
  │  executor claims job (atomic UPDATE), sets sync_paused=1
  │  exports fact Parquet + dimension Parquets
  │  validates 16 DQ rules
  │  builds manifest.json
  ▼
Exported
  │  rsync batch dir to analytics server
  │  verifies remote checksums via SSH sha256sum
  ▼
Transferred
  │  calls: memora-analytics ingest-archive --manifest ... --db ...
  │  calls: memora-analytics verify --manifest ... --db ...
  ▼
Ingested
  │  calls: memora-analytics handoff --archive-path ... --filter ... --db ...
  │  (analytics server removes overlapping live data)
  ▼
Completed  ← sync_paused cleared here
  │  (optional) purges source rows from tabMemora Practice Log in batches
  ▼
Purged
```

If any stage fails:
- retry_count < 3 → reset to Pending (auto-retry on next executor run)
- retry_count >= 3 → permanent **Failed** (sync_paused cleared, admin notified)

---

## Live sync pipeline — job lifecycle

```
Pending
  │  live_sync_trigger creates the job
  ▼
Processing
  │  checks if any archive job has sync_paused=1 for same source (skips if so)
  │  exports full snapshot (excludes archived date ranges)
  │  exports dimension Parquets
  │  builds manifest.json
  ▼
Exported
  │  rsync batch dir to analytics server
  │  verifies remote checksums
  ▼
Transferred
  │  calls: memora-analytics ingest-live --manifest ... --db ...
  │  (analytics: staging table → verify → atomic swap)
  ▼
Ingested → Completed
```

No purge step. No handoff step.

---

## Required environment variables

These must be set before running either executor.
See `.env.archive.example` for a template.

| Variable | Required | Description |
|----------|----------|-------------|
| `DB_HOST` | Yes | MariaDB host |
| `DB_PORT` | No (3306) | MariaDB port |
| `DB_USER` | Yes | MariaDB user |
| `DB_PASSWORD` | Yes | MariaDB password |
| `DB_NAME` | Yes | Database name |
| `SCHEMA_REGISTRY_PATH` | Yes | Path to `archive_schemas/` directory |
| `ARCHIVE_OUTPUT_PATH` | No (`/data/memora/archives/`) | Local batch output |
| `LIVE_OUTPUT_PATH` | No (`/data/memora/live/`) | Local live sync output |
| `LOG_PATH` | No (`/var/log/memora-archive/`) | Log file directory |
| `LOCK_FILE` | No (`/var/run/memora-archive.lock`) | Archive executor lock |
| `LIVE_LOCK_FILE` | No (`/var/run/memora-live-sync.lock`) | Live sync executor lock |
| `CHUNK_SIZE` | No (50000) | Rows per streaming batch |
| `STUCK_TIMEOUT_HOURS` | No (1) | Hours before Processing state is declared stuck |
| `ANALYTICS_SSH_HOST` | No | Analytics server hostname |
| `ANALYTICS_SSH_USER` | No | SSH username |
| `ANALYTICS_SSH_KEY_PATH` | No | Path to SSH private key |
| `ANALYTICS_SSH_PORT` | No (22) | SSH port |
| `ANALYTICS_SSH_TIMEOUT` | No (300) | SSH command timeout (seconds) |
| `REMOTE_ARCHIVE_PATH` | No | Remote directory for archive batches |
| `REMOTE_LIVE_PATH` | No | Remote directory for live batches |
| `ANALYTICS_CMD_PATH` | No (`/opt/analytics/memora-analytics`) | Analytics CLI path on remote |
| `REMOTE_DUCKDB_PATH` | No | DuckDB path on analytics server |

If SSH vars are not set, executors run in **export-only mode**: jobs proceed through Exported but are not transferred or ingested.

---

## Analytics server contract

The executor calls the analytics CLI over SSH. The CLI must:

1. Accept JSON-formatted output (stdout) and exit 0 on success, exit 1 on failure.
2. Implement these sub-commands:

```
memora-analytics ingest-archive --manifest <remote_path>/manifest.json --db <duckdb_path>
# Returns: {"success": true, "tables_loaded": N, "errors": []}

memora-analytics ingest-live --manifest <remote_path>/manifest.json --db <duckdb_path>
# Returns: {"success": true, "tables_swapped": N, "errors": []}

memora-analytics handoff --archive-path <remote_path> --filter '{"date_from":"...","date_to":"..."}' --db <duckdb_path>
# Returns: {"success": true, "rows_removed_from_live": N, "errors": []}

memora-analytics verify --manifest <remote_path>/manifest.json --db <duckdb_path>
# Returns: {"valid": true, "errors": []}
```

3. Receive batches via rsync at:
   - Archive: `REMOTE_ARCHIVE_PATH/<job_name>/`
   - Live: `REMOTE_LIVE_PATH/<job_name>/`

Each batch directory contains:
- `fact_<archive_type>.parquet`
- `dim_<entity>.parquet` (one per dimension)
- `manifest.json` (checksums, row counts, schema metadata)

---

## Frappe-side Desk URLs

- Archive jobs: `/app/memora-archive-job`
- Live sync jobs: `/app/memora-live-sync-job`

Both DocTypes are read-only in the Desk (executors update them via SQL).
