# Archive & Analytics Pipeline — Server Setup Guide

> Everything needed to set up the archive executor and live sync pipeline on a new production server.
> This covers the Frappe/production side only (the server that exports and sends data).
> For the analytics server (receiving side), see `05-analytics-server/ascii-for-this-server.md`.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Directory Structure](#3-directory-structure)
4. [Environment File](#4-environment-file)
5. [SSH Key Setup](#5-ssh-key-setup)
6. [File Permissions](#6-file-permissions)
7. [Crontab Entries](#7-crontab-entries)
8. [Frappe Scheduler Tasks](#8-frappe-scheduler-tasks)
9. [Database Requirements](#9-database-requirements)
10. [Verification Checklist](#10-verification-checklist)
11. [Troubleshooting](#11-troubleshooting)
12. [Architecture Reference](#12-architecture-reference)

---

## 1. Overview

The pipeline has two independent executors that run as cron jobs under the `corex` user:

| Executor | Module | Purpose | Schedule |
|----------|--------|---------|----------|
| **Archive Executor** | `archive_executor.run` | Processes archive jobs (export, transfer, ingest, purge) | Daily 02:00 |
| **Live Sync Executor** | `archive_executor.live_sync` | Processes live sync jobs (daily snapshot to analytics) | Daily 03:05 |

Both executors:
- Are standalone Python scripts (no Frappe dependency)
- Read configuration from environment variables
- Use file-based locking to prevent concurrent runs
- Connect to MariaDB via TCP (not socket)
- Transfer Parquet files to the analytics server via rsync over SSH

Frappe handles job *creation* via its scheduler (hooks.py). The executors handle job *processing*.

```
Frappe Scheduler (hooks.py)          External Cron (crontab)
  01:20  create archive jobs    -->    02:00  archive executor processes them
  03:00  create live sync jobs  -->    03:05  live sync executor processes them
  06:00  send failure alerts
```

---

## 2. Prerequisites

### System packages

```bash
apt install -y rsync openssh-client python3 python3-pip
```

### Python packages (system-wide)

```bash
pip3 install pyarrow pymysql pydantic pydantic-settings pyyaml click
```

Minimum tested versions:
- Python 3.10+
- pyarrow 23.0+
- pymysql 1.1+
- pydantic 2.12+
- pyyaml 6.0+
- rsync 3.2+

### Application code

The archive executor lives inside the Frappe app at:
```
/home/corex/aurevia-bench/apps/memora_admin/archive_executor/
```

The schema registry lives at:
```
/home/corex/aurevia-bench/apps/memora_admin/archive_schemas/
```

Both must be accessible from the working directory when running `python3 -m archive_executor.run`.

---

## 3. Directory Structure

Create the following directories. All must be owned by the user that runs cron (typically `corex`).

```bash
# Data directories
sudo mkdir -p /data/memora/archives/.staging
sudo mkdir -p /data/memora/live/.staging

# Log directory
sudo mkdir -p /var/log/memora-archive

# SSH key directory
sudo mkdir -p /etc/memora-archive

# Fix ownership (replace corex with your user)
sudo chown -R corex:corex /data/memora/archives
sudo chown -R corex:corex /data/memora/live
sudo chown -R corex:corex /var/log/memora-archive
```

Final layout:

```
/data/memora/
  archives/           # Archive Parquet batches (ARCH-XXXXX directories)
    .staging/         # Temporary staging during export
  live/               # Live sync Parquet batches (LSYNC-XXXXX directories)
    .staging/         # Temporary staging during export

/var/log/memora-archive/
  archive.log         # Structured JSON log (both executors write here)

/var/run/
  memora-archive.lock      # Lock file for archive executor
  memora-live-sync.lock    # Lock file for live sync executor

/etc/memora-archive/
  id_rsa_analytics         # SSH private key for analytics server
  id_rsa_analytics.pub     # SSH public key

/etc/memora-archive.env    # Environment variables for both executors
```

---

## 4. Environment File

Create `/etc/memora-archive.env` with the values for your environment.
A template is available at `.env.archive.example` in the app repository.

```bash
sudo tee /etc/memora-archive.env > /dev/null << 'EOF'
# Archive Executor Environment — Production

# -- Database (required, TCP only — socket auth does not work) --
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=<your_db_user>
DB_PASSWORD=<your_db_password>
DB_NAME=<your_db_name>

# -- Local paths --
ARCHIVE_OUTPUT_PATH=/data/memora/archives/
LIVE_OUTPUT_PATH=/data/memora/live/
SCHEMA_REGISTRY_PATH=/home/corex/aurevia-bench/apps/memora_admin/archive_schemas
LOG_PATH=/var/log/memora-archive/
LOCK_FILE=/var/run/memora-archive.lock
LIVE_LOCK_FILE=/var/run/memora-live-sync.lock

# -- Performance --
CHUNK_SIZE=50000
STUCK_TIMEOUT_HOURS=1

# -- SSH / Remote transfer to analytics server --
ANALYTICS_SSH_HOST=<analytics_server_ip>
ANALYTICS_SSH_USER=analytics
ANALYTICS_SSH_KEY_PATH=/etc/memora-archive/id_rsa_analytics
ANALYTICS_SSH_PORT=22
ANALYTICS_SSH_TIMEOUT=300

# -- Remote directories on analytics server --
REMOTE_ARCHIVE_PATH=/data/analytics/archives/
REMOTE_LIVE_PATH=/data/analytics/live/

# -- Analytics CLI on remote server --
ANALYTICS_CMD_PATH=/opt/analytics/memora-analytics
REMOTE_DUCKDB_PATH=/data/analytics/memora.duckdb

# -- Purge safety --
PURGE_GRACE_DAYS=7

# -- Snapshots --
SNAPSHOT_OUTPUT_PATH=/data/memora/snapshots/
REMOTE_SNAPSHOT_PATH=/data/analytics/snapshots/

# -- Incremental sync (memory state) --
SYNC_STATE_PATH=/data/memora/sync_state/
SYNC_OUTPUT_PATH=/data/memora/sync_output/
SYNC_OVERLAP_SECONDS=300
SYNC_REMOTE_PATH=
EOF

sudo chmod 644 /etc/memora-archive.env
```

> **Important**: The file must be readable by the cron user. Use `644` (readable by all) or `640` with group ownership matching the cron user. Do NOT use `600` if cron runs as a non-root user different from the file owner.

---

## 5. SSH Key Setup

Generate or copy the SSH key pair for authenticating to the analytics server:

```bash
# Option A: Generate a new key pair
sudo ssh-keygen -t ed25519 -f /etc/memora-archive/id_rsa_analytics -N "" -C "memora-archive"

# Option B: Copy existing keys from backup/previous server
sudo cp id_rsa_analytics /etc/memora-archive/id_rsa_analytics
sudo cp id_rsa_analytics.pub /etc/memora-archive/id_rsa_analytics.pub
```

Fix ownership so the cron user can read the private key:

```bash
sudo chown corex:corex /etc/memora-archive/id_rsa_analytics
sudo chmod 600 /etc/memora-archive/id_rsa_analytics
```

Add the public key to the analytics server:

```bash
# On the analytics server (as the 'analytics' user):
cat >> ~/.ssh/authorized_keys << 'EOF'
<contents of id_rsa_analytics.pub>
EOF
```

Test the connection from the production server:

```bash
ssh -i /etc/memora-archive/id_rsa_analytics -p 22 analytics@<analytics_server_ip> "echo OK"
```

---

## 6. File Permissions

Everything must be owned by the cron user (`corex`). This is the most common source of failures on a fresh setup.

```bash
USER=corex

# Data directories
sudo chown -R $USER:$USER /data/memora/archives
sudo chown -R $USER:$USER /data/memora/live

# Log directory
sudo chown -R $USER:$USER /var/log/memora-archive

# Lock files (create them so cron doesn't fail on first run)
sudo touch /var/run/memora-archive.lock /var/run/memora-live-sync.lock
sudo chown $USER:$USER /var/run/memora-archive.lock /var/run/memora-live-sync.lock

# SSH key
sudo chown $USER:$USER /etc/memora-archive/id_rsa_analytics
sudo chmod 600 /etc/memora-archive/id_rsa_analytics

# Env file must be readable
sudo chmod 644 /etc/memora-archive.env
```

> **Lock files in /var/run**: On some systems, `/var/run` is a tmpfs that gets cleared on reboot. The executors create the lock file if it doesn't exist, but they need write permission to `/var/run`. If this is a problem, change `LOCK_FILE` and `LIVE_LOCK_FILE` in the env file to point to a persistent directory like `/data/memora/.locks/`.

---

## 7. Crontab Entries

Add these to the crontab for the `corex` user (`crontab -e`):

```cron
# Daily at 02:00: Run archive executor (export, transfer, ingest, purge)
0 2 * * * cd /home/corex/aurevia-bench/apps/memora_admin && set -a && . /etc/memora-archive.env && set +a && /usr/bin/python3 -m archive_executor.run >> /var/log/memora-archive/archive_cron.log 2>&1

# Daily at 03:05: Run live sync executor (daily snapshot to analytics)
5 3 * * * cd /home/corex/aurevia-bench/apps/memora_admin && set -a && . /etc/memora-archive.env && set +a && /usr/bin/python3 -m archive_executor.live_sync >> /var/log/memora-archive/live_sync_cron.log 2>&1
```

Key points:
- `set -a` / `set +a` ensures env vars are exported to the Python process
- `cd` to the app directory first so `python3 -m archive_executor` can find the module
- Stdout/stderr go to separate cron log files for debugging startup failures
- The structured JSON log goes to `/var/log/memora-archive/archive.log` (configured via `LOG_PATH`)

### Timing relationship with Frappe scheduler

The Frappe scheduler (via `hooks.py`) creates the jobs. The cron executors process them. The timing must be:

```
01:20  Frappe creates archive jobs (check_seasons_for_archive)
02:00  Archive executor runs and processes them         <-- cron
03:00  Frappe creates live sync jobs (trigger_daily_live_sync)
03:05  Live sync executor runs and processes them       <-- cron
06:00  Frappe sends failure notifications (notify_failed_archive_jobs)
```

---

## 8. Frappe Scheduler Tasks

These are registered in `memora_admin/hooks.py` and run automatically via `bench scheduler`. No manual setup needed — they activate when the Frappe app is installed.

| Schedule | Task | Purpose |
|----------|------|---------|
| `20 1 * * *` | `archive_trigger.check_seasons_for_archive` | Create archive jobs for ended seasons |
| `20 1 * * *` | `archive_trigger.check_season_scoped_archives` | Create season-scoped archive jobs |
| `0 3 * * *` | `live_sync_trigger.trigger_daily_live_sync` | Create live sync jobs |
| `0 */6 * * *` | `archive_monitor.check_archive_health` | Health checks (emails on alerts) |
| `0 6 * * *` | `archive_notify.notify_failed_archive_jobs` | Email for permanently failed jobs |
| `0 7 * * *` | `archive_stale_pause.check_stale_archive_pauses` | Detect stale sync_paused flags |
| `0 2 * * *` | `archive_task_log.archive_task_log` | Archive task run log rows |
| `30 3 * * *` | `purge_task_log.purge_task_log` | Purge archived task log source rows |
| `30 6 * * *` | `archive_job_cleanup.cleanup_archive_jobs` | Delete old terminal archive job records |
| `15 4 * * *` | `dimension_refresh.reconcile_dimensions` | Full dimension reconciliation (safety net) |

### Health Monitor Alerts

The `check_archive_health` task runs every 6 hours and emails System Manager users when:

| Check | Condition | Severity |
|-------|-----------|----------|
| `live_sync_freshness` | Last completed LSYNC job > 24h ago | WARNING |
| `archive_validation_lag` | Jobs stuck in Exported/Transferred > 48h | WARNING |
| `retry_exhaustion` | Failed jobs with retry_count >= 3 | CRITICAL |
| `stuck_state` | Jobs stuck in Processing/Exported/Transferred/Ingested > 6h | WARNING |

---

## 9. Database Requirements

### Connection

- **Must use TCP** (`host=127.0.0.1`), not socket. Socket auth fails with the archive executor.
- Standard MariaDB port `3306`.

### Required privileges

The DB user needs these privileges on the database:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON `<db_name>`.* TO '<user>'@'127.0.0.1';
```

### Required tables

These Frappe DocType tables must exist (created by app install):
- `tabMemora Archive Job` — archive job state machine
- `tabMemora Live Sync Job` — live sync job state machine
- `tabMemora Practice Log` — primary fact source (custom table, no Frappe standard columns)
- `tabMemora Memory State` — FSRS memory state (range-partitioned by season_seq)
- `tabMemora Interaction Log` — user interaction events
- `tabMemora Task Run Log` — task scheduler execution logs
- `tabMemora Player Profile` — player dimension source
- `tabMemora Review Item` — review item dimension source
- `tabMemora Season` — season dimension source
- `tabMemora Academic Plan` — plan dimension source
- `tabMemora Lesson` — lesson dimension source

### Required custom table

```sql
CREATE TABLE IF NOT EXISTS `archive_delete_audit_log` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `job_id` VARCHAR(140) NOT NULL,
  `source_doctype` VARCHAR(140),
  `rows_deleted` INT DEFAULT 0,
  `deleted_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `delete_range_start` DATETIME,
  `delete_range_end` DATETIME,
  UNIQUE KEY `uq_job_id` (`job_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### Required indexes

These are created by `memora_admin/setup.py` during `bench migrate`, but verify they exist:

```sql
-- On tabMemora Practice Log
SHOW INDEX FROM `tabMemora Practice Log` WHERE Key_name = 'idx_last_seen_at';

-- On tabMemora Task Run Log
SHOW INDEX FROM `tabMemora Task Run Log` WHERE Key_name = 'idx_task_log_archive';
```

If missing, create them:

```sql
CREATE INDEX idx_last_seen_at ON `tabMemora Practice Log` (last_seen_at);
CREATE INDEX idx_task_log_archive ON `tabMemora Task Run Log` (status, completed_at, name);
```

---

## 10. Verification Checklist

Run these checks after setting up a new server. Every check should pass.

### A. Environment

```bash
# 1. Env file is readable
set -a && . /etc/memora-archive.env && set +a && echo "DB_HOST=$DB_HOST"
# Expected: DB_HOST=127.0.0.1

# 2. Database connection works
python3 -c "
import pymysql, os
conn = pymysql.connect(
    host=os.environ['DB_HOST'], port=int(os.environ['DB_PORT']),
    user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'],
    database=os.environ['DB_NAME'])
print('DB OK:', conn.get_server_info())
conn.close()
"

# 3. Schema registry accessible
ls $SCHEMA_REGISTRY_PATH/archive_types/ $SCHEMA_REGISTRY_PATH/sync_types/ $SCHEMA_REGISTRY_PATH/dimensions/
```

### B. Directories and permissions

```bash
# 4. All paths are writable by cron user
touch /data/memora/archives/.staging/_test && rm /data/memora/archives/.staging/_test && echo "archives OK"
touch /data/memora/live/.staging/_test && rm /data/memora/live/.staging/_test && echo "live OK"
touch /var/log/memora-archive/_test.log && rm /var/log/memora-archive/_test.log && echo "logs OK"
touch /var/run/memora-archive.lock && echo "archive lock OK"
touch /var/run/memora-live-sync.lock && echo "live lock OK"
```

### C. SSH connectivity

```bash
# 5. SSH key has correct permissions
ls -la /etc/memora-archive/id_rsa_analytics
# Expected: -rw------- corex corex

# 6. SSH connection works
ssh -i /etc/memora-archive/id_rsa_analytics -p 22 -o ConnectTimeout=10 \
    analytics@$ANALYTICS_SSH_HOST "echo SSH_OK"

# 7. rsync works
rsync --version | head -1
```

### D. Python modules

```bash
# 8. Executor imports work
cd /home/corex/aurevia-bench/apps/memora_admin
python3 -c "from archive_executor.run import main; print('archive executor OK')"
python3 -c "from archive_executor.live_sync import main; print('live sync executor OK')"

# 9. Required packages
python3 -c "
import pyarrow, pymysql, pydantic, yaml, click
print('pyarrow', pyarrow.__version__)
print('pymysql', pymysql.__version__)
print('pydantic', pydantic.__version__)
"
```

### E. Cron

```bash
# 10. Cron entries exist
crontab -l | grep archive_executor
# Expected: two entries (run + live_sync)
```

### F. Frappe scheduler

```bash
# 11. Frappe scheduler is running
cd /home/corex/aurevia-bench
bench doctor
```

### G. Dry run

```bash
# 12. Run both executors manually and check logs
cd /home/corex/aurevia-bench/apps/memora_admin
set -a && . /etc/memora-archive.env && set +a

python3 -m archive_executor.run
tail -5 /var/log/memora-archive/archive.log

python3 -m archive_executor.live_sync
tail -5 /var/log/memora-archive/archive.log
```

---

## 11. Troubleshooting

### "Permission denied" errors

This is the #1 issue on fresh setups. Everything was initially run as root, leaving files owned by root.

```bash
# Fix everything at once
sudo chown -R corex:corex /data/memora/archives /data/memora/live /var/log/memora-archive
sudo chown corex:corex /var/run/memora-archive.lock /var/run/memora-live-sync.lock
sudo chown corex:corex /etc/memora-archive/id_rsa_analytics
```

### "KeyError: DB_HOST" when running executor

The env file was sourced without `set -a` (which tells bash to export all variables):

```bash
# Wrong:
. /etc/memora-archive.env && python3 -m archive_executor.run

# Correct:
set -a && . /etc/memora-archive.env && set +a && python3 -m archive_executor.run
```

### Live sync exports 0 rows

This is normal when all practice log data falls within completed archive date ranges. The job completes as "empty" with `row_count=0`. No alert is generated — the health monitor only cares that a job *completed*, not that it exported rows.

### SSH transfer fails: "Load key: Permission denied"

The SSH private key is not readable by the cron user:

```bash
sudo chown corex:corex /etc/memora-archive/id_rsa_analytics
sudo chmod 600 /etc/memora-archive/id_rsa_analytics
```

### Job stuck at "Pending" forever

The executor cron is not running. Check:
1. `crontab -l` — is the entry there?
2. `/var/log/memora-archive/archive_cron.log` or `live_sync_cron.log` — any errors?
3. Was `cd /home/corex/aurevia-bench/apps/memora_admin` included in the cron command?

### Job name pattern: ARCH-XXXXX only numeric

The archive executor's job name regex is `^ARCH-\d+$`. Jobs with letter suffixes (e.g., `ARCH-MS-001`) are silently skipped. Always use numeric-only names for jobs that need to be processed by the executor.

### Lock file prevents execution

If a previous run crashed, the lock file may still exist. The executors use `fcntl.flock()`, which is automatically released when the process exits:

```bash
# Check if any executor is actually running
ps aux | grep archive_executor

# If nothing is running, the lock is stale — just run the executor again
# (flock is advisory and process-scoped, so a new process can acquire it)
```

---

## 12. Architecture Reference

### Job State Machines

**Archive Jobs** (`tabMemora Archive Job`):
```
Pending -> Processing -> Exported -> Transferred -> Ingested -> Completed -> Purged
   ^                                                                |
   +---- Failed (auto-retry up to 3 times, then permanent) --------+
```

**Live Sync Jobs** (`tabMemora Live Sync Job`):
```
Pending -> Processing -> Exported -> Transferred -> Ingested -> Completed
   ^                                                    |
   +---- Failed (auto-retry up to 3 times) ------------+
```

### Data Flow

```
Production Server                              Analytics Server
=================                              ================

MariaDB                                        DuckDB + Parquet Lake
  |                                               ^
  | SQL query                                     | ingest CLI
  v                                               |
archive_executor/                                 |
  run.py (archives)     -- rsync over SSH -->   /data/analytics/archives/
  live_sync.py (live)   -- rsync over SSH -->   /data/analytics/live/
  |
  v
/data/memora/
  archives/ARCH-XXXXX/    (Parquet: fact + dimensions + manifest.json)
  live/LSYNC-XXXXX/       (Parquet: fact + dimensions + manifest.json)
```

### Schema Registry

```
archive_schemas/
  archive_types/           # Fact table definitions for archiving
    practice_log.v1.yaml
    memory_state.v1.yaml
    interaction_log.v1.yaml
    task_run_log.v1.yaml
  sync_types/              # Fact table definitions for live sync
    practice_log_live.v1.yaml
  snapshot_types/          # Point-in-time snapshot definitions
    structure_progress.v1.yaml
  dimensions/              # Shared dimension schemas (versioned)
    player.v1.yaml, player.v2.yaml, player.v3.yaml
    player_history.v1.yaml
    review_item.v1.yaml, review_item.v2.yaml
    season.v1.yaml
    plan.v1.yaml
    lesson.v1.yaml
```

---

## Quick Setup Script

For convenience, a single script that creates everything on a fresh server.
Run as root, then switch to the app user for crontab.

```bash
#!/bin/bash
set -euo pipefail

APP_USER=corex
APP_DIR=/home/corex/aurevia-bench/apps/memora_admin

echo "=== Creating directories ==="
mkdir -p /data/memora/archives/.staging
mkdir -p /data/memora/live/.staging
mkdir -p /var/log/memora-archive
mkdir -p /etc/memora-archive

echo "=== Creating lock files ==="
touch /var/run/memora-archive.lock
touch /var/run/memora-live-sync.lock

echo "=== Setting ownership ==="
chown -R $APP_USER:$APP_USER /data/memora
chown -R $APP_USER:$APP_USER /var/log/memora-archive
chown $APP_USER:$APP_USER /var/run/memora-archive.lock
chown $APP_USER:$APP_USER /var/run/memora-live-sync.lock

echo "=== Installing Python dependencies ==="
pip3 install pyarrow pymysql pydantic pydantic-settings pyyaml click

echo ""
echo "=== DONE. Manual steps remaining: ==="
echo "  1. Create /etc/memora-archive.env       (see Section 4 of the setup guide)"
echo "  2. Copy SSH key to /etc/memora-archive/  (see Section 5)"
echo "     chown $APP_USER:$APP_USER /etc/memora-archive/id_rsa_analytics"
echo "     chmod 600 /etc/memora-archive/id_rsa_analytics"
echo "  3. Add public key to analytics server authorized_keys"
echo "  4. Add crontab entries as $APP_USER:     (see Section 7)"
echo "     crontab -e"
echo "  5. Run verification checklist            (see Section 10)"
```
