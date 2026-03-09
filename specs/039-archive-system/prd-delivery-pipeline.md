# PRD: Archive Delivery Pipeline + Live Data Sync

**Branch**: `039-archive-system`
**Date**: 2026-03-09
**Status**: Implementation complete, pending QA review

---

## 1. What Changed (Executive Summary)

The archive system previously had a simplified lifecycle: `Pending → Processing → Completed → Purged`. It exported Parquet files locally but had no mechanism to transfer them to an analytics server, verify ingestion, or coordinate with live Redis→MariaDB sync tasks.

This implementation adds:

1. **Multi-stage delivery pipeline** — 7 statuses tracking each step from export through remote ingestion
2. **Remote transfer + ingestion** — SSH/rsync transfer with checksum verification and analytics-side CLI handoff
3. **sync_paused coordination** — prevents sync tasks from writing to tables actively being archived
4. **Live Data Sync system** — daily full-snapshot exports reusing archive infrastructure, with its own DocType and executor

---

## 2. Status Lifecycle (Before → After)

### Before
```
Pending → Processing → Completed → Purged
                   ↘ Failed
```
Separate `transfer_status` field: `Pending / Transferred / Transfer Failed`

### After
```
Pending → Processing → Exported → Transferred → Ingested → Completed → Purged
                   ↘ Failed  ↗       ↘ Failed  ↗     ↘ Failed ↗      ↘ Failed ↗
Failed → Pending (retry)
```
`transfer_status` field **removed**. Transfer is now a first-class main status.

### Graceful Degradation
- Without SSH configured: jobs stop at `Exported` (local Parquet ready, no transfer attempted)
- Without analytics server: jobs stop at `Transferred` (files on remote, no ingestion attempted)
- Each intermediate state retries up to 3 times before permanent `Failed`

---

## 3. Detailed Changes by Component

### 3.1 DocType: Memora Archive Job

**File**: `memora_admin/memora_admin/doctype/memora_archive_job/`

| Change | Details |
|--------|---------|
| Status options | `Pending \| Processing \| Exported \| Transferred \| Ingested \| Completed \| Purged \| Failed` |
| Fields removed | `transfer_status`, `section_transfer`, `column_break_transfer`, `local_deleted_at` |
| Fields added | `exported_at` (Datetime), `ingested_at` (Datetime), `remote_path` (Data), `sync_paused` (Check), `sync_paused_at` (Datetime), `clear_pause_btn` (Button) |
| Fields moved | `transferred_at` moved from Transfer section to Execution section |
| Python: transitions | 7-state `VALID_TRANSITIONS` dict |
| Python: new method | `clear_sync_pause(job_name)` — whitelist, System Manager only |
| Python: retry update | `retry_archive_job` now also clears `sync_paused=0` and `sync_paused_at=None` |
| JS: button handlers | `retry_btn` and `clear_pause_btn` wired to their whitelist methods with confirmation dialogs |

**QA verification**:
- Open a Failed archive job → "Retry" button visible, click resets to Pending
- Set sync_paused=1 on a job → "Clear Sync Pause" button visible, click clears it
- Verify status transitions enforce the VALID_TRANSITIONS map (e.g., cannot go from Exported directly to Completed)
- After `bench migrate`, all new fields exist and old fields (`transfer_status`, `local_deleted_at`) are removed

### 3.2 DocType: Memora Live Sync Job (NEW)

**File**: `memora_admin/memora_admin/doctype/memora_live_sync_job/`

| Field | Type | Notes |
|-------|------|-------|
| sync_type | Data (reqd) | e.g., "practice_log_live" |
| schema_version | Data (reqd) | e.g., "v1" |
| status | Select (reqd) | `Pending\|Processing\|Exported\|Transferred\|Ingested\|Completed\|Failed` (no Purged) |
| triggered_by | Select | `Cron\|Manual` |
| execution_stage | Data | read_only |
| started_at / exported_at / transferred_at / ingested_at / completed_at | Datetime | read_only |
| duration_seconds | Float | read_only |
| row_count | Int | read_only |
| file_path / file_checksum / file_size_bytes / remote_path | Data/Int | read_only |
| retry_count | Int | read_only |
| error_log | Long Text | read_only |
| meta | JSON | read_only |

Autoname: `LSYNC-.#####.`
Permissions: System Manager read-only.
Python: `VALID_TRANSITIONS` same as archive but `Completed` is terminal (no Purged state).
`before_insert()` prevents manual creation.

**QA verification**:
- After `bench migrate`, DocType exists and is visible in admin
- Cannot create a Live Sync Job manually from the UI (blocked by before_insert)
- Status transitions enforced correctly

### 3.3 Archive Executor Config

**File**: `archive_executor/config.py`

New fields (all optional with empty string/sane defaults):

| Field | Env Var | Default | Purpose |
|-------|---------|---------|---------|
| ssh_host | ANALYTICS_SSH_HOST | "" | Analytics server hostname |
| ssh_user | ANALYTICS_SSH_USER | "" | SSH username |
| ssh_key_path | ANALYTICS_SSH_KEY_PATH | "" | Path to SSH private key |
| ssh_port | ANALYTICS_SSH_PORT | 22 | SSH port |
| ssh_timeout | ANALYTICS_SSH_TIMEOUT | 300 | SSH command timeout (seconds) |
| remote_archive_path | REMOTE_ARCHIVE_PATH | "" | Remote directory for archive batches |
| remote_live_path | REMOTE_LIVE_PATH | "" | Remote directory for live snapshots |
| analytics_cmd_path | ANALYTICS_CMD_PATH | "/opt/analytics/memora-analytics" | Path to analytics CLI tool |
| duckdb_path | REMOTE_DUCKDB_PATH | "" | Path to DuckDB on analytics server |
| live_output_path | LIVE_OUTPUT_PATH | "/data/memora/live/" | Local staging for live snapshots |
| live_lock_file | LIVE_LOCK_FILE | "/var/run/memora-live-sync.lock" | Lock file for live sync executor |

New helper: `has_ssh_config() -> bool` — returns True only if host + user + key_path are all set.

**QA verification**:
- Executor starts successfully without any SSH env vars set (all default to empty)
- `has_ssh_config()` returns False when any of host/user/key is empty

### 3.4 Transfer Module (NEW)

**File**: `archive_executor/transfer.py`

| Function | Purpose |
|----------|---------|
| `_run_ssh_command(config, command, timeout)` | Execute command on remote via `subprocess.run` + `ssh` binary |
| `transfer_batch(config, local_dir, remote_base_path, job_name, log)` | rsync local batch to remote. Returns remote_path string |
| `verify_remote_checksums(config, remote_path, manifest, log)` | SSH + sha256sum on remote, compare with manifest checksums |

Raises `TransferError` on failure. Uses `BatchMode=yes` and `StrictHostKeyChecking=accept-new`.

**QA verification**:
- Without SSH configured: `transfer_batch()` raises `TransferError("SSH not configured")`
- With SSH configured (when analytics server available): rsync transfers files, checksums match

### 3.5 Ingestion + Handoff Module (NEW)

**File**: `archive_executor/ingestion.py`

| Function | Purpose |
|----------|---------|
| `ingest_archive_batch(config, remote_path, manifest, log)` | Calls `memora-analytics ingest-archive` via SSH |
| `ingest_live_snapshot(config, remote_path, manifest, log)` | Calls `memora-analytics ingest-live` via SSH (staging→swap) |
| `handoff_archive(config, archive_path, query_filter, log)` | Calls `memora-analytics handoff` — removes overlapping live data |
| `verify_ingestion(config, manifest, remote_path, log)` | Calls `memora-analytics verify` — confirms data queryable |

All functions use `_run_ssh_command` from `transfer.py`. The analytics-side CLI tool (`memora-analytics`) is **out of scope for this PR** — ingestion calls will gracefully fail and retry.

**QA verification**:
- Without analytics server: ingestion functions raise `IngestionError`, jobs stay at their current state and retry

### 3.6 Pipeline Refactor

**File**: `archive_executor/run.py`

The `main()` entry point now processes jobs across multiple states per run:

```python
def main():
    _fail_stuck_jobs(config, log)           # All active states
    _process_pending_jobs(config, log)      # Pending → Processing → Exported
    _process_exported_jobs(config, log)     # Exported → Transferred (skip if no SSH)
    _process_transferred_jobs(config, log)  # Transferred → Ingested (skip if no SSH)
    _process_ingested_jobs(config, log)     # Ingested → Completed (handoff + clear sync_paused)
    purge_completed_jobs(config, log)       # Completed → Purged
    cleanup_local_copies(config, log)       # Delete local dirs for Completed+ with remote_path
```

Key changes:
- `_claim_job()` now sets `sync_paused=1, sync_paused_at=NOW()` atomically with claim
- `_process_pending_jobs()` marks jobs as `Exported` (not `Completed`) after successful export
- `_fail_job()` accepts `current_status` parameter and retries within the same state (not always resetting to Pending)
- `_fail_stuck_jobs()` checks Processing (1h), Exported/Transferred/Ingested (24h each)
- `cleanup_local_copies()` replaces `cleanup_transferred_local_copies()` — cleans Completed/Purged jobs with remote_path set

**QA verification**:
- Create a Pending archive job → run executor → verify job reaches `Exported` status (not `Completed`)
- Verify `sync_paused=1` is set when job is claimed
- Without SSH: jobs stay at `Exported`, no errors
- Stuck jobs detected across all active states with appropriate timeouts
- On job completion (`Ingested → Completed`): `sync_paused` is cleared to 0

### 3.7 Exporter Update

**File**: `archive_executor/exporter.py`

Added `mode` parameter to `export_fact_data()`:
- `mode="filtered"` (default) — existing WHERE clause behavior using `query_filter`
- `mode="full_snapshot"` — no WHERE clause, exports ALL rows from the table

**QA verification**:
- Archive jobs (filtered mode): same behavior as before, exports date-range scoped data
- Live sync jobs (full_snapshot mode): exports entire table with no WHERE clause

### 3.8 Schema Registry Update

**File**: `archive_executor/schemas.py`

Added:
- `load_sync_type(registry_path, type_name, version)` — loads from `sync_types/` subdirectory
- `list_sync_types(registry_path)` — discovers all sync type YAMLs

**File**: `archive_schemas/sync_types/practice_log_live.v1.yaml` (NEW)

```yaml
sync_type: practice_log_live
version: v1
mode: full_snapshot
source_table: "tabMemora Practice Log"
fact_columns: [player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count]
dimensions:
  - entity: player (v1, join on player_id)
  - entity: review_item (v1, join on item_id)
```

**QA verification**:
- `load_sync_type("...", "practice_log_live", "v1")` loads correctly
- `list_sync_types("...")` discovers the YAML file

### 3.9 sync_paused Coordination

**File**: `memora_admin/tasks/sync.py`

Added:
- `_get_paused_filters()` — cached (60s TTL) query for archive jobs with `sync_paused=1`. Returns list of `{source_doctype, date_from, date_to, filter_column}` parsed from job meta.
- `_is_in_paused_range(timestamp_value, source_doctype, paused_filters)` — checks if a record falls within a paused date range.
- `flush_interaction_buffer()` now checks each interaction record's timestamp against paused ranges. Records in paused ranges are **skipped** (not inserted into `tabMemora Interaction Log`).

**Important**: Skipped records are still trimmed from the Redis buffer. This is safe because:
1. The archive export already captured those records from MariaDB
2. After archive completes, `sync_paused` is cleared and new interactions write normally
3. The archive covers the full date range, so no data gap

**QA verification**:
- Set `sync_paused=1` on an archive job with a date range
- Run `flush_interaction_buffer()`
- Verify interactions with timestamps in that range are skipped (logged as "paused")
- Verify interactions outside that range are inserted normally

### 3.10 Stale Pause Checker (NEW)

**File**: `memora_admin/tasks/archive_stale_pause.py`

`check_stale_archive_pauses()` — scheduled daily at 07:00.

Query: `sync_paused=1 AND sync_paused_at < NOW() - 24h AND status NOT IN (Processing, Exported, Transferred, Ingested)`

For each stale pause:
- Publish Desk realtime alert (orange indicator)
- Send email to System Manager users

**QA verification**:
- Create an archive job with `sync_paused=1, sync_paused_at=2 days ago, status=Failed`
- Run `check_stale_archive_pauses()`
- Verify email + Desk alert are sent

### 3.11 Live Sync Executor (NEW)

**File**: `archive_executor/live_sync.py`

Entry point: `python -m archive_executor.live_sync`

Pipeline stages (same as archive, without purge):
```python
_fail_stuck_live_jobs()
_process_pending_live_jobs()      # Full snapshot → Exported
_process_exported_live_jobs()     # Transfer → Transferred (skip if no SSH)
_process_transferred_live_jobs()  # Ingest (staging→swap) → Ingested
_process_ingested_live_jobs()     # Verify → Completed
```

Key differences from archive executor:
- No purge step (live sync doesn't delete source data)
- Uses `export_fact_data(mode="full_snapshot")` — no WHERE clause
- Uses `ingest_live_snapshot()` (swap pattern) instead of `ingest_archive_batch()` (INSERT)
- Respects `sync_paused` — skips if any archive job has `sync_paused=1` for the same source table
- Old output directories are overwritten (not accumulated like archive batches)
- Has its own lock file (`/var/run/memora-live-sync.lock`)

**QA verification**:
- Create a Pending live sync job → run executor → verify full table exported to Parquet
- Verify `sync_paused` check: if archive job is paused for same source table, live sync skips
- Without SSH: jobs stop at `Exported`

### 3.12 Live Sync Trigger (NEW)

**File**: `memora_admin/tasks/live_sync_trigger.py`

| Function | Schedule | Purpose |
|----------|----------|---------|
| `trigger_daily_live_sync()` | Cron 0 3 * * * | Load sync_types/ YAMLs, create Pending live sync jobs. Skips if active job exists. |
| `trigger_manual_sync()` | Whitelist API | Manual "Sync Now". 15-min cooldown against last completed job. Blocks if active job exists. |

**QA verification**:
- Run `trigger_daily_live_sync()` → verify `LSYNC-XXXXX` job created with status=Pending
- Run again immediately → verify no duplicate created (active job exists)
- After job completes, run `trigger_manual_sync()` within 15 min → verify cooldown error
- After cooldown expires → verify job created with `triggered_by=Manual`

### 3.13 Hooks Update

**File**: `memora_admin/hooks.py`

Added cron entries:
```python
"0 7 * * *": ["memora_admin.tasks.archive_stale_pause.check_stale_archive_pauses"]
"0 3 * * *": ["memora_admin.tasks.live_sync_trigger.trigger_daily_live_sync"]
```

### 3.14 Admin Settings (Informational)

**File**: `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json`

New "Analytics Server" section with display-only fields:
- `analytics_ssh_host`, `analytics_ssh_user`, `analytics_ssh_key_path`, `analytics_remote_path`

These are informational — the executor reads from env vars. Admin can see what's configured.

### 3.15 Validator Cleanup

**File**: `archive_executor/validator.py`

- **Removed**: `verify_transfer()` and `_update_transfer_status()` (referenced old `transfer_status` field)
- **Added**: `verify_local_transfer(destination_path, manifest)` — pure local checksum verification without DB updates
- Removed unused imports (`json`, `datetime`, `Config`, `atomic_update`)

---

## 4. New Files Created

| File | Purpose |
|------|---------|
| `archive_executor/transfer.py` | SSH/rsync transfer module |
| `archive_executor/ingestion.py` | Remote ingestion + handoff via analytics CLI |
| `archive_executor/live_sync.py` | Live sync executor (full snapshot pipeline) |
| `archive_executor/__main__.py` | Enables `python -m archive_executor` |
| `archive_schemas/sync_types/practice_log_live.v1.yaml` | Live sync schema definition |
| `memora_admin/tasks/archive_stale_pause.py` | Stale sync_paused checker |
| `memora_admin/tasks/live_sync_trigger.py` | Daily/manual live sync job creation |
| `memora_admin/memora_admin/doctype/memora_live_sync_job/__init__.py` | DocType init |
| `memora_admin/memora_admin/doctype/memora_live_sync_job/memora_live_sync_job.json` | DocType schema |
| `memora_admin/memora_admin/doctype/memora_live_sync_job/memora_live_sync_job.py` | DocType class |

## 5. Files Modified

| File | What Changed |
|------|-------------|
| `archive_executor/config.py` | +12 SSH/analytics/live fields, `has_ssh_config()` |
| `archive_executor/run.py` | Multi-phase pipeline, sync_paused on claim, per-state failure/retry |
| `archive_executor/purge.py` | `cleanup_local_copies()` replaces `cleanup_transferred_local_copies()` |
| `archive_executor/exporter.py` | `mode` parameter: "filtered" or "full_snapshot" |
| `archive_executor/schemas.py` | `load_sync_type()`, `list_sync_types()` |
| `archive_executor/validator.py` | Removed old transfer_status functions, added `verify_local_transfer()` |
| `memora_admin/hooks.py` | +2 cron entries (stale pause at 07:00, live sync at 03:00) |
| `memora_admin/tasks/sync.py` | `_get_paused_filters()`, `_is_in_paused_range()`, pause check in `flush_interaction_buffer()` |
| `memora_archive_job.json` | New statuses, new fields, removed old transfer fields |
| `memora_archive_job.py` | Updated transitions, `clear_sync_pause()`, retry clears pause |
| `memora_archive_job.js` | Button handlers for retry + clear pause |
| `memora_settings.json` | Analytics Server section (4 informational fields) |

---

## 6. Cron Schedule (Complete)

| Time | Job | Source |
|------|-----|--------|
| 01:20 | `check_seasons_for_archive` | Existing — creates Pending archive jobs |
| 02:00 | Archive executor cron | External — `python -m archive_executor` |
| 03:00 | `trigger_daily_live_sync` | NEW — creates Pending live sync jobs |
| 03:05 | Live sync executor cron | External — `python -m archive_executor.live_sync` |
| 06:00 | `notify_failed_archive_jobs` | Existing — emails for permanently failed jobs |
| 07:00 | `check_stale_archive_pauses` | NEW — warns if sync_paused stuck >24h |

---

## 7. QA Test Plan

### Test 1: Archive Pipeline (Local Only — No SSH)

1. `bench migrate` — verify new statuses, new fields, Live Sync Job DocType
2. Create a Pending archive job (via trigger or manually with `programmatic_creation=True`)
3. Run `python -m archive_executor` (with env vars set, no SSH vars)
4. **Expected**: Job goes `Pending → Processing → Exported` (stops here)
5. Verify: `sync_paused=1`, `exported_at` set, `file_path` points to valid Parquet directory
6. Verify: manifest.json exists with correct checksums

### Test 2: sync_paused Coordination

1. Have an archive job in `Exported` state with `sync_paused=1`
2. Add interactions to the Redis buffer with timestamps within the job's date range
3. Run `flush_interaction_buffer()`
4. **Expected**: Those interactions are skipped (logged as paused), others inserted normally

### Test 3: Stale Pause Detection

1. Create an archive job with `sync_paused=1, sync_paused_at = 2 days ago, status = Failed`
2. Run `check_stale_archive_pauses()`
3. **Expected**: Email + Desk alert sent to System Managers

### Test 4: Clear Sync Pause (UI)

1. Open an archive job with `sync_paused=1` in Frappe Desk
2. Click "Clear Sync Pause" button
3. **Expected**: `sync_paused` reset to 0, `sync_paused_at` cleared

### Test 5: Retry Failed Job

1. Open a Failed archive job
2. Click "Retry" button
3. **Expected**: Status resets to Pending, retry_count=0, error_log cleared, sync_paused cleared

### Test 6: Live Sync Job Creation

1. Run `trigger_daily_live_sync()`
2. **Expected**: `LSYNC-XXXXX` job created with `sync_type=practice_log_live, status=Pending, triggered_by=Cron`
3. Run again immediately
4. **Expected**: No duplicate (active job already exists)

### Test 7: Live Sync Execution

1. Have a Pending live sync job
2. Run `python -m archive_executor.live_sync` (no SSH vars)
3. **Expected**: Job goes `Pending → Processing → Exported` (full table snapshot)
4. Verify Parquet file contains ALL rows from `tabMemora Practice Log` (no WHERE filter)

### Test 8: Manual Sync Cooldown

1. Complete a live sync job (set status=Completed, completed_at=NOW())
2. Call `trigger_manual_sync()` immediately
3. **Expected**: Error "Cooldown active. Please wait X seconds"
4. Wait 15+ minutes, call again
5. **Expected**: New job created with `triggered_by=Manual`

### Test 9: Live Sync Respects Archive Pause

1. Create an archive job with `sync_paused=1` for `Memora Practice Log`
2. Create a Pending live sync job for `practice_log_live`
3. Run live sync executor
4. **Expected**: Live sync skipped (logged "source_paused_by_archive")

### Test 10: Status Transition Enforcement

Verify these transitions are **blocked** (should throw ValidationError):
- Pending → Completed (must go through Processing first)
- Exported → Completed (must go through Transferred first)
- Purged → anything (terminal state)
- Completed → Pending (not allowed)

### Test 11: Stuck Job Detection

1. Create a Processing archive job with `claimed_at = 3 hours ago`
2. Run executor
3. **Expected**: Job marked as Failed with "Stuck: exceeded timeout" error

### Test 12: Admin Settings

1. Open Memora Settings in Frappe Desk
2. **Expected**: "Analytics Server" section visible with 4 fields
3. Fields are editable (informational display)

---

## 8. Known Limitations

1. **Analytics server not ready**: Transfer/ingestion stages gracefully skip. Jobs accumulate at `Exported` status until SSH is configured.
2. **Analytics-side CLI tool not built yet**: `memora-analytics` commands will fail. The interface contract is defined but the tool is a separate deployment.
3. **sync_paused only affects `flush_interaction_buffer()`**: Other sync tasks (progress, wallets) don't currently write to archived tables, so the check is a no-op for them. The infrastructure is in place for future extension.
4. **Live sync exports entire table**: For very large tables, this could be slow. The streaming cursor + Parquet batching keeps memory bounded, but export time scales with table size.
5. **No partial retry within a state**: If transfer fails at file 3 of 5, the entire transfer retries. rsync's `--partial` flag handles this at the transport level.
