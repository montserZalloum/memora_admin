# Runbook — Memora Archive Pipeline

## Prerequisites

- Python 3.10+ available (`/usr/bin/python3`)
- `rsync` and `ssh` binaries installed on the Frappe server
- SSH key-based access to the analytics server configured
- MariaDB credentials with SELECT/UPDATE on the Frappe database
- `pyarrow`, `pymysql`, `pyyaml` installable via pip

---

## Step 1 — Set up the standalone venv

The archive executor runs in a separate venv, isolated from the Frappe Python environment.

```bash
# Create venv
python3 -m venv /opt/memora-archive/venv

# Install dependencies
/opt/memora-archive/venv/bin/pip install -r \
    /home/corex/aurevia-bench/apps/memora_admin/archive_executor/requirements.txt
```

Confirm installed packages:
```bash
/opt/memora-archive/venv/bin/pip list | grep -E "pyarrow|pymysql|pyyaml"
```

---

## Step 2 — Configure environment variables

```bash
# Copy the template
cp /home/corex/aurevia-bench/apps/memora_admin/.env.archive.example /etc/memora-archive.env

# Edit and fill in real values
nano /etc/memora-archive.env

# Restrict permissions (contains DB password + SSH key path)
chmod 600 /etc/memora-archive.env
chown root:root /etc/memora-archive.env
```

Minimum required values in `/etc/memora-archive.env`:
```
DB_HOST=127.0.0.1
DB_USER=frappe
DB_PASSWORD=<real_password>
DB_NAME=<real_db_name>
SCHEMA_REGISTRY_PATH=/home/corex/aurevia-bench/apps/memora_admin/archive_schemas
```

For full production (with transfer + ingest), also set:
```
ANALYTICS_SSH_HOST=187.77.93.112
ANALYTICS_SSH_USER=analytics
ANALYTICS_SSH_KEY_PATH=/etc/memora-archive/id_rsa_analytics
REMOTE_ARCHIVE_PATH=/data/analytics/archives/
REMOTE_LIVE_PATH=/data/analytics/live/
REMOTE_DUCKDB_PATH=/data/analytics/memora.duckdb
ANALYTICS_CMD_PATH=/opt/analytics/memora-analytics
```

---

## Step 3 — Create required directories

```bash
# Local batch storage
mkdir -p /data/memora/archives /data/memora/live
chown corex:corex /data/memora/archives /data/memora/live
chmod 700 /data/memora/archives /data/memora/live

# Log directory
mkdir -p /var/log/memora-archive
chown corex:corex /var/log/memora-archive

# Lock directory (already exists on Linux, but confirm)
ls -la /var/run/
```

---

## Step 4 — Test the executor manually

Test archive executor (export-only, no SSH required):
```bash
cd /home/corex/aurevia-bench/apps/memora_admin
. /etc/memora-archive.env
/opt/memora-archive/venv/bin/python -m archive_executor.run
```

Test live sync executor:
```bash
cd /home/corex/aurevia-bench/apps/memora_admin
. /etc/memora-archive.env
/opt/memora-archive/venv/bin/python -m archive_executor.live_sync
```

Check output in `/var/log/memora-archive/archive.log` (JSON lines).

---

## Step 5 — Set up cron

Use the provided wrapper scripts (they source the env file automatically):

```bash
# Make scripts executable (if not already)
chmod +x /home/corex/aurevia-bench/apps/memora_admin/archive_executor/run_archive.sh
chmod +x /home/corex/aurevia-bench/apps/memora_admin/archive_executor/run_live_sync.sh

# Edit crontab for the corex user (or root)
crontab -e
```

Add these lines:
```cron
# Archive executor — runs after Frappe triggers create jobs at 01:20
0 2 * * * /home/corex/aurevia-bench/apps/memora_admin/archive_executor/run_archive.sh >> /var/log/memora-archive/cron.log 2>&1

# Live sync executor — runs after trigger creates jobs at 03:00
5 3 * * * /home/corex/aurevia-bench/apps/memora_admin/archive_executor/run_live_sync.sh >> /var/log/memora-archive/cron.log 2>&1
```

Frappe's own scheduler (inside `bench`) handles the trigger tasks; cron handles the executor tasks. Both sides must be running for end-to-end delivery.

---

## Step 6 — Set up the SSH key and verify connectivity

The executor connects to the analytics server at `187.77.93.112` as the `analytics` user.
The private key must be at `/etc/memora-archive/id_rsa_analytics`.

**6a — Generate the key (run once on this server):**
```bash
sudo mkdir -p /etc/memora-archive
sudo ssh-keygen -t ed25519 -f /etc/memora-archive/id_rsa_analytics -N "" -C "memora-archive-executor"
sudo chmod 600 /etc/memora-archive/id_rsa_analytics
sudo chown root:root /etc/memora-archive/id_rsa_analytics
```

**6b — Send the public key to the analytics team** to install on `187.77.93.112`:
```bash
sudo cat /etc/memora-archive/id_rsa_analytics.pub
```

The analytics team must append that output to `~analytics/.ssh/authorized_keys` on the analytics server.

**6c — Test once the key is installed:**
```bash
. /etc/memora-archive.env
ssh -i "$ANALYTICS_SSH_KEY_PATH" \
    -o StrictHostKeyChecking=accept-new \
    -o BatchMode=yes \
    "$ANALYTICS_SSH_USER@$ANALYTICS_SSH_HOST" "echo OK"
```

Expected output: `OK`

Test rsync:
```bash
rsync -avz --dry-run -e "ssh -i $ANALYTICS_SSH_KEY_PATH -o BatchMode=yes" \
    /tmp/ "$ANALYTICS_SSH_USER@$ANALYTICS_SSH_HOST:$REMOTE_ARCHIVE_PATH"
```

---

## Step 7 — Verify Frappe scheduler entries

In the Frappe bench root:
```bash
bench --site x.conanacademy.com execute \
    "memora_admin.tasks.archive_trigger.check_seasons_for_archive"
```

Check Frappe background jobs (Supervisor/Redis Queue):
```bash
bench status
```

---

## Inspecting jobs in Frappe Desk

- Archive jobs: https://x.conanacademy.com/app/memora-archive-job
- Live sync jobs: https://x.conanacademy.com/app/memora-live-sync-job

Filter by status to see active, failed, or stuck jobs.

The `execution_stage` field shows the last sub-step completed within each status.

---

## Troubleshooting failures

### Job stuck in Processing

Stuck-job detector in the executor auto-fails Processing jobs older than `STUCK_TIMEOUT_HOURS` (default 1h). If a job has been stuck longer and the executor isn't running:

```bash
# Check if executor is running
ps aux | grep "archive_executor"

# Manually fail a stuck job (replace ARCH-00001)
. /etc/memora-archive.env
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "UPDATE \`tabMemora Archive Job\` SET status='Failed', sync_paused=0, sync_paused_at=NULL, error_log='Manually reset' WHERE name='ARCH-00001';"
```

### sync_paused blocking live sync

If an archive job failed and left `sync_paused=1`, the live sync will be blocked. Identify stale pauses:

```bash
. /etc/memora-archive.env
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT name, status, sync_paused_at FROM \`tabMemora Archive Job\` WHERE sync_paused=1;"
```

Clear stale pause via Frappe Desk: open the job → click "Clear Sync Pause" button.
Or via SQL:
```bash
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "UPDATE \`tabMemora Archive Job\` SET sync_paused=0, sync_paused_at=NULL WHERE name='ARCH-00001';"
```

### Transfer errors

Check the log:
```bash
tail -100 /var/log/memora-archive/archive.log | python3 -m json.tool | grep -A3 '"event": "transfer'
```

Test SSH manually (see Step 6).

### DQ validation failures

Open the failed job in Desk, read `error_log`. It will list which DQ rules failed (DQ-01 through DQ-16). Common causes:
- `DQ-14`: Player IDs in fact table not in player dimension (data consistency issue)
- `DQ-16`: Duplicate (player_id, item_id) pairs (Practice Log has unexpected duplicates)
- `DQ-13`: Rows outside the archive scope date range (season dates set incorrectly)
