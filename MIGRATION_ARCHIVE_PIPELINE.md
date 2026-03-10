# Migration Guide — Memora Archive Pipeline

## When to use this guide

Use this guide when:
- Moving the Frappe server to new hardware
- Rebuilding the server from scratch
- Handing off to a new team

---

## What needs to move

| Component | Location | Notes |
|-----------|----------|-------|
| App code | `apps/memora_admin/` | Checked in to git |
| Schema registry | `apps/memora_admin/archive_schemas/` | Checked in to git |
| Standalone venv | `/opt/memora-archive/venv/` | Recreate from requirements.txt |
| Env file | `/etc/memora-archive.env` | Manually migrate — contains secrets |
| SSH private key | `ANALYTICS_SSH_KEY_PATH` | Manually migrate or re-generate |
| Local batch dirs | `/data/memora/archives/`, `/data/memora/live/` | Optionally move if mid-flight jobs exist |
| Log files | `/var/log/memora-archive/` | Optional (historical only) |
| Lock files | `/var/run/memora-archive.lock`, `/var/run/memora-live-sync.lock` | Do NOT copy — recreated at runtime |

---

## Step 1 — Export env config from old server

```bash
# On old server
cat /etc/memora-archive.env > /tmp/memora-archive.env.bak
# Transfer securely (e.g., scp)
scp /tmp/memora-archive.env.bak newserver:/tmp/
```

---

## Step 2 — On new server: install app and venv

```bash
# Pull app (already in your bench)
cd /home/corex/aurevia-bench
bench get-app memora_admin --branch develop

# Create venv
python3 -m venv /opt/memora-archive/venv
/opt/memora-archive/venv/bin/pip install -r \
    apps/memora_admin/archive_executor/requirements.txt
```

---

## Step 3 — Restore env file

```bash
cp /tmp/memora-archive.env.bak /etc/memora-archive.env
chmod 600 /etc/memora-archive.env

# Update paths if server layout changed
nano /etc/memora-archive.env
```

Key path that commonly changes:
- `SCHEMA_REGISTRY_PATH` — must point to the new app checkout location
- `ANALYTICS_SSH_KEY_PATH` — SSH key must exist at this path

---

## Step 4 — Re-establish SSH connectivity

If generating a new SSH key (recommended for security):

```bash
# On new Frappe server
ssh-keygen -t ed25519 -f /home/memora/.ssh/id_rsa_analytics -C "memora-archive-$(date +%Y%m%d)"

# Copy new public key to analytics server
ssh-copy-id -i /home/memora/.ssh/id_rsa_analytics.pub memora@analytics.example.com

# Test
ssh -i /home/memora/.ssh/id_rsa_analytics -o BatchMode=yes memora@analytics.example.com "echo OK"
```

Update `ANALYTICS_SSH_KEY_PATH` in `/etc/memora-archive.env` with the new key path.

---

## Step 5 — Create required directories

```bash
mkdir -p /data/memora/archives /data/memora/live
chown corex:corex /data/memora/archives /data/memora/live
chmod 700 /data/memora/archives /data/memora/live

mkdir -p /var/log/memora-archive
chown corex:corex /var/log/memora-archive
```

---

## Step 6 — Restore any mid-flight batch directories (optional)

If jobs were in Exported/Transferred/Ingested state on the old server, the local Parquet batches may still be needed for verification. Copy them:

```bash
# On old server
rsync -avz /data/memora/archives/ newserver:/data/memora/archives/
rsync -avz /data/memora/live/ newserver:/data/memora/live/
```

If you don't do this, jobs in those states will fail (missing local file). They will auto-retry up to 3 times before permanently failing. You can also manually reset them to Pending via SQL.

---

## Step 7 — Set up cron on new server

```bash
crontab -e
```

Add:
```cron
0 2 * * * /home/corex/aurevia-bench/apps/memora_admin/archive_executor/run_archive.sh >> /var/log/memora-archive/cron.log 2>&1
5 3 * * * /home/corex/aurevia-bench/apps/memora_admin/archive_executor/run_live_sync.sh >> /var/log/memora-archive/cron.log 2>&1
```

Frappe scheduler jobs are configured in `hooks.py` and run automatically when `bench start` / Supervisor is running.

---

## Step 8 — Verify Frappe scheduler is running

```bash
bench status
# Should show: redis_queue, redis_socketio, web, worker_default, worker_long, worker_short
```

Check that archive-related tasks appear in scheduled job logs:
```bash
bench --site x.conanacademy.com execute \
    "memora_admin.tasks.archive_trigger.check_seasons_for_archive"
```

---

## Post-migration validation checklist

- [ ] `/opt/memora-archive/venv/bin/python -m archive_executor.run` exits cleanly (no ERROR log lines)
- [ ] SSH to analytics server works: `ssh -i $ANALYTICS_SSH_KEY_PATH $ANALYTICS_SSH_USER@$ANALYTICS_SSH_HOST echo OK`
- [ ] `/app/memora-archive-job` in Desk shows correct statuses
- [ ] A test run of the archive executor processes at least one Pending job (or reports 0 jobs found)
- [ ] Log file appears at `/var/log/memora-archive/archive.log`
- [ ] Cron runs appear in `/var/log/memora-archive/cron.log`
- [ ] No stale `sync_paused=1` jobs remaining from old server

### Reset stale paused jobs after migration

```bash
. /etc/memora-archive.env
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT name, status, sync_paused_at FROM \`tabMemora Archive Job\` WHERE sync_paused=1 AND status NOT IN ('Processing', 'Exported', 'Transferred', 'Ingested');"
```

If any rows appear, clear the pause:
```bash
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "UPDATE \`tabMemora Archive Job\` SET sync_paused=0, sync_paused_at=NULL WHERE sync_paused=1 AND status NOT IN ('Processing', 'Exported', 'Transferred', 'Ingested');"
```
