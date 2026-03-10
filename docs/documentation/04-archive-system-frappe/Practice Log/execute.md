Frappe-Side Tasks (run via bench)                                                                
                                         
  cd /home/corex/aurevia-bench

  # 1. Archive trigger — creates Pending archive jobs for ended seasons
  bench --site x.conanacademy.com execute
  memora_admin.tasks.archive_trigger.check_seasons_for_archive

  # 2. Live sync trigger — creates Pending live sync jobs
  bench --site x.conanacademy.com execute
  memora_admin.tasks.live_sync_trigger.trigger_daily_live_sync

  # 3. Archive health monitor — checks 4 alert conditions
  bench --site x.conanacademy.com execute memora_admin.tasks.archive_monitor.check_archive_health

  Standalone Executor (runs outside Frappe, needs env vars)

  cd /home/corex/aurevia-bench/apps/memora_admin

  # Set required env vars (get DB creds from site_config.json)
  export DB_HOST=127.0.0.1
  export DB_USER=<db_name_from_site_config>
  export DB_PASSWORD=<db_password_from_site_config>
  export DB_NAME=<db_name_from_site_config>
  export SCHEMA_REGISTRY_PATH=/home/corex/aurevia-bench/apps/memora_admin/archive_schemas
  export ARCHIVE_OUTPUT_PATH=/data/memora/archives/
  export LIVE_OUTPUT_PATH=/data/memora/live/
  export LOG_PATH=/var/log/memora-archive/
  export LOCK_FILE=/var/run/memora-archive.lock
  export LIVE_LOCK_FILE=/var/run/memora-live-sync.lock

  # 4. Archive executor — processes Pending archive jobs through full pipeline
  python3 -m archive_executor.run

  # 5. Live sync executor — exports full snapshots for analytics
  python3 -m archive_executor.live_sync

  Cron Schedule (automatic)

  ┌─────────────┬───────────────────────┬───────────────────────────────────────────────────┐
  │    Cron     │         Task          │                   What it does                    │
  ├─────────────┼───────────────────────┼───────────────────────────────────────────────────┤
  │ 20 1 * * *  │ archive_trigger       │ Creates archive jobs for ended seasons (>90 days) │
  ├─────────────┼───────────────────────┼───────────────────────────────────────────────────┤
  │ 0 3 * * *   │ live_sync_trigger     │ Creates live sync jobs                            │
  ├─────────────┼───────────────────────┼───────────────────────────────────────────────────┤
  │ 0 */6 * * * │ archive_monitor (NEW) │ Health check: freshness, lag, retries, stuck      │
  ├─────────────┼───────────────────────┼───────────────────────────────────────────────────┤
  │ 0 6 * * *   │ archive_notify        │ Email for permanently failed jobs                 │
  ├─────────────┼───────────────────────┼───────────────────────────────────────────────────┤
  │ 0 7 * * *   │ archive_stale_pause   │ Detects orphaned sync_paused states               │
  └─────────────┴───────────────────────┴───────────────────────────────────────────────────┘

  ---
  What to Expect

  Archive trigger (check_seasons_for_archive)

  - Currently: No output — returns immediately because there are no ended seasons (is_published=0,
  end_date < 90 days ago)
  - When seasons end: Creates one Pending archive job per (season, archive_type) combination

  Live sync trigger (trigger_daily_live_sync)

  - Currently: Creates 1 Pending live sync job for practice_log_live.v1
  - Skips if an active job already exists (dedup check)

  Archive executor (python3 -m archive_executor.run)

  - Currently: run_finished exported=0 — no pending archive jobs
  - When jobs exist: Exports fact + 4 dimensions (player v2, review_item v1, season v1, plan v1),
  runs 16 DQ validation rules, builds manifest, marks Exported. Transfer/ingestion skipped until SSH
  is configured.

  Live sync executor (python3 -m archive_executor.live_sync)

  - Currently: Exports 0-row Parquet files (Practice Log table is empty), produces 6 files: fact + 4
  dimensions + manifest
  - With data: Exports full snapshot excluding completed archive date ranges, injects 4 metadata
  columns (scope_type, sync_batch_id, schema_version, synced_at), exports derived season/plan
  dimensions from player data

  Archive health monitor (check_archive_health)

  - Currently: Sends 1 alert (live sync freshness — no completed live sync jobs found) to 8 admins
  - In production: Checks 4 conditions every 6 hours: live sync freshness (>24h), validation lag
  (>48h stuck), retry exhaustion (failed after 3 retries), stuck-state (>6h in Processing)