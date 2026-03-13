# Quickstart: Analytics Lakehouse

**Feature**: 046-analytics-lakehouse | **Date**: 2026-03-12

## Prerequisites

- Python 3.11+
- DuckDB (`pip install duckdb`)
- PyArrow (`pip install pyarrow`)
- Click (`pip install click`)
- Existing archive executor deployed at `/opt/memora-archive/`
- SSH access from production server to analytics server

## 1. Install the Analytics CLI

```bash
# On the Analytics Server
cd /opt/analytics/
git clone <repo> memora-analytics
cd memora-analytics/analytics_cli
pip install -e .

# Verify installation
memora-analytics --help
```

## 2. Configure Environment

```bash
# /etc/memora-analytics/env (on Analytics Server)
export DUCKDB_PATH=/data/analytics/analytics.duckdb
export LAKE_PATH=/data/analytics/lake
export DIMENSIONS_PATH=/data/analytics/dimensions
export MANIFESTS_PATH=/data/analytics/manifests
export LOG_PATH=/var/log/memora-analytics/
```

```bash
# Production server env additions (for archive_executor)
export ANALYTICS_SSH_HOST=analytics.example.com
export ANALYTICS_SSH_USER=memora
export ANALYTICS_SSH_KEY_PATH=/etc/memora/analytics_key
export ANALYTICS_CMD_PATH=/opt/analytics/memora-analytics/venv/bin/memora-analytics
export REMOTE_ARCHIVE_PATH=/data/analytics/lake
export REMOTE_LIVE_PATH=/data/analytics/lake
export REMOTE_SNAPSHOT_PATH=/data/analytics/lake/structure_progress
export SYNC_REMOTE_PATH=/data/analytics/lake
export REMOTE_DUCKDB_PATH=/data/analytics/analytics.duckdb
```

## 3. Initialize DuckDB

```bash
# First run creates tables and views
memora-analytics ingest-archive --batch-dir /tmp/empty
# Or manually:
memora-analytics init
```

## 4. Run the Full Pipeline

```bash
# Production server (already configured in cron)
# 01:20 — Scheduler creates Pending jobs
# 02:00 — Executor runs: export → transfer → ingest → handoff → purge
# 03:00 — Live sync runs
# Weekly — Snapshot runs

# Manual execution:
python3 -m archive_executor.run          # Archive pipeline
python3 -m archive_executor.live_sync    # Live sync
python3 -m archive_executor.snapshot     # Structure progress snapshot
python3 -m archive_executor.sync         # Incremental memory state sync
```

## 5. Query Analytics Data

```bash
# On Analytics Server
duckdb /data/analytics/analytics.duckdb

# Practice log with partition pruning
SELECT COUNT(*) FROM practice_log_archive
WHERE year = 2025 AND month = 12;

# Combined view (archive + live)
SELECT player_id, COUNT(*) AS attempts
FROM practice_log_combined
GROUP BY player_id
ORDER BY attempts DESC
LIMIT 10;

# SCD2 temporal join — player's plan at time of practice
SELECT p.player_id, p.last_seen_at, h.plan_id, h.grade
FROM practice_log_combined p
JOIN dim_player_history h
  ON p.player_id = h.player_id
  AND p.last_seen_at >= h.valid_from
  AND (p.last_seen_at < h.valid_to OR h.is_current = TRUE);

# Memory state by season
SELECT season_seq, COUNT(*) AS states
FROM memory_state_archive
GROUP BY season_seq;

# Structure progress trends
SELECT snapshot_date, AVG(completion_percentage) AS avg_completion
FROM structure_progress_snapshots
GROUP BY snapshot_date
ORDER BY snapshot_date;
```

## 6. Run Health Checks

```bash
# On Analytics Server
memora-analytics verify
# Returns JSON with check results for: duplicates, checksums, dimension coverage, partition sizes
```

## 7. Development & Testing

```bash
# Analytics CLI tests (DuckDB in-memory, no SSH needed)
cd analytics_cli/
python3 -m pytest tests/ -v

# Production-side integration tests (existing)
cd /home/corex/aurevia-bench/apps/memora_admin/
DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \
  python3 -m pytest archive_executor/tests/ -v
```

## Key Architectural Notes

1. **Archive data is immutable** — DuckDB views read Parquet directly, no COPY INTO
2. **Live data uses atomic swap** — `ingest-live` replaces the entire live table
3. **No overlap guaranteed** — live sync excludes archived date ranges at export time
4. **Schema evolution** — `union_by_name=true` handles missing columns across versions
5. **Analytics CLI is stateless** — all state is in DuckDB file and Parquet files on disk
6. **Production executor owns job lifecycle** — analytics CLI only returns success/failure JSON
