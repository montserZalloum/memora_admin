# Quickstart: Weekly Structure Progress Snapshots

**Feature**: 043-progress-snapshots | **Date**: 2026-03-11

## Prerequisites

- Python 3.11+ with `pyarrow` and `pymysql` installed (archive executor virtualenv)
- Read access to MariaDB (`tabMemora Structure Progress`, `tabMemora Player Profile`)
- Write access to the snapshot output directory

## Environment Variables

All configuration is via environment variables, consistent with the archive executor:

```bash
# Database (required)
export DB_HOST=127.0.0.1
export DB_PORT=3306
export DB_USER=_9be6802bfff1e8ca
export DB_PASSWORD=zjAACevKaH5VGVP2
export DB_NAME=_9be6802bfff1e8ca

# Snapshot output (required)
export SNAPSHOT_OUTPUT_PATH=/srv/analytics/snapshots

# Optional overrides
export SNAPSHOT_CHUNK_SIZE=50000          # rows per streaming batch (default: 50000)
export SNAPSHOT_LOG_PATH=/var/log/memora  # log directory (default: /var/log/memora)
```

## Running Manually

```bash
# Activate the archive executor virtualenv
source /opt/memora-archive/venv/bin/activate

# Run for today's date (auto-detects Sunday date)
python -m archive_executor.snapshot

# Run for a specific date (e.g., backfill or rerun)
python -m archive_executor.snapshot --snapshot-date 2026-03-08

# Dry run (extract + validate, but don't write final output)
python -m archive_executor.snapshot --dry-run
```

## Cron Setup

Add to the archive server's crontab (Asia/Amman timezone):

```cron
# Weekly structure progress snapshot — Sunday 03:00 AM Asia/Amman
0 3 * * 0 TZ=Asia/Amman /opt/memora-archive/venv/bin/python -m archive_executor.snapshot >> /var/log/memora/snapshot.log 2>&1
```

## Output Verification

After a run, check the output:

```bash
# List snapshots
ls -la /srv/analytics/snapshots/structure_progress/

# Read the latest manifest
cat /srv/analytics/snapshots/structure_progress/2026-03-08/manifest.json | python -m json.tool

# Query with DuckDB
duckdb -c "
  SELECT snapshot_date, COUNT(*) as rows, COUNT(DISTINCT player_id) as students
  FROM read_parquet('/srv/analytics/snapshots/structure_progress/*/fact_structure_progress.parquet')
  GROUP BY snapshot_date
  ORDER BY snapshot_date
"
```

## Running Tests

```bash
# From repository root, with DB credentials
DB_HOST=127.0.0.1 \
DB_PORT=3306 \
DB_USER=_9be6802bfff1e8ca \
DB_PASSWORD=zjAACevKaH5VGVP2 \
DB_NAME=_9be6802bfff1e8ca \
python -m pytest archive_executor/tests/test_snapshot.py -v
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `FileNotFoundError` on output path | `SNAPSHOT_OUTPUT_PATH` not set or directory doesn't exist | Create the directory and set the env var |
| 0 rows exported but source has data | All students missing player profiles or plans | Check `tabMemora Player Profile` for null `plan` values |
| Permission denied on write | Archive user lacks write access to output dir | `chown`/`chmod` the output directory |
| Stale staging directory | Previous run crashed mid-write | Safe to delete `.staging/` and rerun |
