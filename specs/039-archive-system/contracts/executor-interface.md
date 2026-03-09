# Contract: Standalone Archive Executor

## Script Interface

**Location**: `/opt/memora-archive/run.py`
**Virtualenv**: `/opt/memora-archive/venv/`
**Cron**: `0 2 * * * /opt/memora-archive/venv/bin/python /opt/memora-archive/run.py`

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DB_HOST` | Yes | — | MariaDB host |
| `DB_PORT` | No | 3306 | MariaDB port |
| `DB_USER` | Yes | — | MariaDB user |
| `DB_PASSWORD` | Yes | — | MariaDB password |
| `DB_NAME` | Yes | — | MariaDB database name |
| `ARCHIVE_OUTPUT_PATH` | No | `/data/memora/archives/` | Final archive directory |
| `SCHEMA_REGISTRY_PATH` | Yes | — | Path to YAML schema files |
| `LOG_PATH` | No | `/var/log/memora-archive/` | Structured log output |
| `LOCK_FILE` | No | `/var/run/memora-archive.lock` | File lock path |
| `CHUNK_SIZE` | No | 50000 | Rows per chunk during export |
| `STUCK_TIMEOUT_HOURS` | No | 1 | Hours before a Processing job is considered stuck |

## Execution Flow

```
1. Acquire file lock
   ├── Lock held → exit(0) immediately
   └── Lock acquired → continue

2. Detect stuck jobs (Processing > STUCK_TIMEOUT_HOURS)
   └── Mark each as Failed

3. Query pending jobs: SELECT * FROM tabMemora Archive Job WHERE status='Pending' ORDER BY priority DESC, creation ASC

4. For each pending job:
   ├── Atomic claim (UPDATE WHERE status='Pending')
   │   └── affected_rows == 0 → skip (already claimed)
   ├── Load archive type YAML from SCHEMA_REGISTRY_PATH
   ├── Create staging directory: {ARCHIVE_OUTPUT_PATH}/.staging/{job_name}/
   ├── Update execution_stage = 'exporting_fact'
   ├── Export fact data to staging/fact_{source}.parquet
   │   ├── Execute SQL with server-side cursor
   │   ├── Stream rows in CHUNK_SIZE batches
   │   └── Write via pyarrow.parquet.ParquetWriter
   ├── Record snapshot_taken_at = NOW()
   ├── Update execution_stage = 'exporting_dimensions'
   ├── For each dimension in archive type:
   │   ├── Extract referenced IDs from fact data
   │   ├── Load dimension schema YAML
   │   ├── Query dimension table for referenced IDs only
   │   └── Write to staging/dim_{entity}.parquet
   ├── Update execution_stage = 'verifying'
   ├── Validate all files:
   │   ├── Row count matches query count
   │   ├── Schema structure matches expected columns
   │   ├── Compute SHA-256 checksums
   │   └── Record file sizes
   ├── Build manifest.json in staging directory
   ├── Update execution_stage = 'publishing'
   ├── Atomic move: rename staging dir → final dir
   │   └── Fallback: copy + verify + delete if cross-filesystem
   ├── Set directory permissions to 0700
   ├── Update job: status='Completed', file_path, checksum, etc.
   └── On failure:
       ├── Clean up staging directory entirely
       ├── If retry_count < 3: status='Pending', retry_count++
       └── If retry_count >= 3: status='Failed'

5. Release file lock
6. Exit
```

## Log Format (JSON Lines)

```json
{"ts": "2026-03-09T02:00:01", "level": "info", "event": "run_started", "pid": 12345}
{"ts": "2026-03-09T02:00:02", "level": "info", "event": "job_claimed", "job": "ARCH-00001", "source": "Memora Practice Log", "scope": "SEAS-00027"}
{"ts": "2026-03-09T02:15:30", "level": "info", "event": "job_completed", "job": "ARCH-00001", "rows": 850000, "duration_s": 930.5}
{"ts": "2026-03-09T02:15:31", "level": "info", "event": "run_finished", "jobs_processed": 1, "jobs_failed": 0}
```

## Purge Interface

**Integrated into the same executor or a separate script** (implementation choice).

```
For each job WHERE status='Completed' AND post_archive_action='Delete' AND source_deleted=0:
  1. Read meta.query_filter for date range
  2. Read purge_progress for resume point (if any)
  3. Loop:
     a. DELETE FROM source_table WHERE filter LIMIT 10000
     b. Update purge_progress with last deleted marker
     c. Sleep 2 seconds
     d. Repeat until 0 rows affected
  4. Set status='Purged', source_deleted=1
```
