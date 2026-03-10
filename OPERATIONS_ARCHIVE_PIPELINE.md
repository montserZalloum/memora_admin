# Operations Guide — Memora Archive Pipeline

## Common commands

### Check job status (SQL)

```bash
. /etc/memora-archive.env

# Archive jobs summary
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT status, COUNT(*) as n FROM \`tabMemora Archive Job\` GROUP BY status;"

# Live sync jobs summary
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT status, COUNT(*) as n FROM \`tabMemora Live Sync Job\` GROUP BY status;"

# Recently failed archive jobs
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT name, archive_scope, status, retry_count, execution_stage, error_log FROM \`tabMemora Archive Job\` WHERE status='Failed' ORDER BY modified DESC LIMIT 10;"

# Jobs with sync_paused
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT name, status, sync_paused_at FROM \`tabMemora Archive Job\` WHERE sync_paused=1;"
```

### Trigger executor manually

```bash
cd /home/corex/aurevia-bench/apps/memora_admin
. /etc/memora-archive.env

# Archive executor
/opt/memora-archive/venv/bin/python -m archive_executor.run

# Live sync executor
/opt/memora-archive/venv/bin/python -m archive_executor.live_sync
```

---

## Where are the logs?

| Log | Path | Notes |
|-----|------|-------|
| Archive executor | `/var/log/memora-archive/archive.log` | JSON lines, one per event |
| Cron output | `/var/log/memora-archive/cron.log` | Combined stdout/stderr from cron |
| Frappe error log | `logs/frappe.log` (bench root) | Trigger task errors |
| Frappe scheduler log | Frappe Desk → Error Log | Scheduler task exceptions |

### Reading JSON logs

```bash
# Last 50 events
tail -50 /var/log/memora-archive/archive.log | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        e = json.loads(line)
        print(f\"{e['timestamp']} [{e['level'].upper():5}] {e['event']}  {' '.join(f'{k}={v}' for k,v in e.items() if k not in ('timestamp','level','event'))}\")
    except: pass
"

# Errors only
grep '"level": "error"' /var/log/memora-archive/archive.log | tail -20

# Specific job
grep '"job": "ARCH-00001"' /var/log/memora-archive/archive.log
```

---

## How to safely re-run a job

**Never directly set status=Pending on a Processing job** — the executor may still be running it.

1. Check no executor is running: `ps aux | grep "archive_executor"`
2. Check the job's `execution_stage` in Desk or SQL to understand where it stopped.
3. If stuck in Exported/Transferred (local files exist): simply run the executor again — it will pick up where it left off.
4. If the job failed permanently (status=Failed): inspect the error_log, then either:
   - Fix the root cause and manually reset: `UPDATE ... SET status='Pending', retry_count=0, error_log=NULL WHERE name='ARCH-00001';`
   - Or leave it as Failed and create a new job manually.

---

## Identifying stuck jobs

The executor auto-detects stuck jobs on every run:
- Processing > 1h → Failed
- Exported / Transferred / Ingested > 24h → Failed

To check manually:
```bash
. /etc/memora-archive.env
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT name, status, execution_stage, claimed_at, TIMESTAMPDIFF(HOUR, claimed_at, NOW()) as hours_since_claim FROM \`tabMemora Archive Job\` WHERE status IN ('Processing','Exported','Transferred','Ingested') ORDER BY claimed_at ASC;"
```

---

## Common failure modes and recovery

### 1. No archive jobs being created

**Symptom**: No new Archive Jobs in Desk after seasons end.

**Check**:
```bash
# Verify Frappe scheduler is running
bench status

# Check if trigger ran
bench --site x.conanacademy.com execute "memora_admin.tasks.archive_trigger.check_seasons_for_archive"

# Check ended seasons
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT name, end_date FROM \`tabMemora Season\` WHERE is_published=0 AND end_date < CURDATE() AND end_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY);"
```

**Fix**: Ensure Frappe scheduler workers are running (`bench start` or Supervisor).

---

### 2. Jobs stuck in Pending (executor not running)

**Symptom**: Jobs created but never progress from Pending.

**Check**: Is the cron running?
```bash
crontab -l | grep memora
tail -20 /var/log/memora-archive/cron.log
```

**Fix**: Add/fix cron entries (see Runbook Step 5). Run executor manually to unblock.

---

### 3. Transfer fails (SSH/rsync error)

**Symptom**: Job fails at `execution_stage = 'transferring'` with rsync error in error_log.

Analytics server: `187.77.93.112` (user: `analytics`, key: `/etc/memora-archive/id_rsa_analytics`)

**Check**:
```bash
. /etc/memora-archive.env
ssh -i "$ANALYTICS_SSH_KEY_PATH" -o BatchMode=yes "$ANALYTICS_SSH_USER@$ANALYTICS_SSH_HOST" "echo OK"
```

**Fix**: Restore SSH connectivity. Job will auto-retry (up to 3x) on next executor run.

If the key is missing, see Runbook Step 6 to regenerate and re-exchange it.

---

### 4. Ingestion CLI fails (analytics server error)

**Symptom**: Job fails at `execution_stage = 'ingesting'` with IngestionError.

**Check**: SSH to analytics server (`187.77.93.112`) and test the CLI:
```bash
. /etc/memora-archive.env
ssh -i "$ANALYTICS_SSH_KEY_PATH" "$ANALYTICS_SSH_USER@$ANALYTICS_SSH_HOST" \
    "$ANALYTICS_CMD_PATH --help"
```

Expected: the `memora-analytics` CLI prints usage. If it returns `command not found`, the tool is not deployed on the analytics server at `/opt/analytics/memora-analytics`.

**Fix**: Contact the analytics server team. The `memora-analytics` CLI must be deployed on `187.77.93.112` and accept the 4 commands: `ingest-archive`, `ingest-live`, `handoff`, `verify`. Job will auto-retry up to 3x.

---

### 5. sync_paused blocking live sync

**Symptom**: Live sync jobs are created but stay Pending; executor reports "source_paused".

**Check**:
```bash
. /etc/memora-archive.env
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT name, status, sync_paused_at FROM \`tabMemora Archive Job\` WHERE sync_paused=1;"
```

**Fix**: If the archive job that set the pause is Failed or Completed but `sync_paused` was not cleared (should not happen normally but can after a crash):

```bash
# Clear the stale pause
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "UPDATE \`tabMemora Archive Job\` SET sync_paused=0, sync_paused_at=NULL WHERE name='ARCH-00001';"
```

Or use the "Clear Sync Pause" button in Frappe Desk on the job.

---

### 6. DQ validation failure

**Symptom**: Job fails at `execution_stage = 'validating_dq'` with `Data quality validation failed: DQ-XX`.

**Resolution by rule**:

| Rule | Meaning | Typical fix |
|------|---------|-------------|
| DQ-01..DQ-07 | Null in mandatory column | Source data has NULLs — investigate `tabMemora Practice Log` |
| DQ-08 | `attempt_count < 1` | Bad source data |
| DQ-10 | `correct_count > attempt_count` | Bad source data |
| DQ-11 | Invalid `last_result` value | Source data has unexpected enum value |
| DQ-13 | Rows outside season date range | Season `start_date`/`end_date` set incorrectly, or data inserted outside season window |
| DQ-14 | Player IDs not in player dimension | Player was deleted after log was written |
| DQ-15 | Item IDs not in review_item dimension | Review item was deleted after log was written |
| DQ-16 | Duplicate (player_id, item_id) pairs | Practice Log should have at most one row per player+item |

---

### 7. Purge progresses but doesn't complete

**Symptom**: Job status = Completed, `post_archive_action = 'Delete'`, but `source_deleted` remains 0.

Purge is batched (10k rows, 2s pause between batches). It resumes from `purge_progress` JSON field on next executor run. Check progress:

```bash
. /etc/memora-archive.env
mysql -h $DB_HOST -u $DB_USER -p$DB_PASSWORD $DB_NAME \
    -e "SELECT name, purge_progress FROM \`tabMemora Archive Job\` WHERE name='ARCH-00001';"
```

Run the executor again to continue purge.

---

## Alert channels

Automated alerts are sent by the Frappe scheduler:

| Alert | When | Channel |
|-------|------|---------|
| Permanent failure | Failed job with retry_count=3 | Frappe Desk notification + email to System Managers |
| Stale sync_paused | sync_paused=1 for >24h without active processing | Frappe Desk notification + email |
| Health warning | Live sync >24h stale, archive lag >48h, retry exhaustion, jobs stuck >6h | Frappe Desk notification + email |

Check email delivery in Frappe Desk → Email Log.
