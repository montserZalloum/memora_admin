# Data Movement & Analytics Platform — Full Architecture

```
╔════════════════════════════════════════════════════════════════════════════════════╗
║                      DATA MOVEMENT & ANALYTICS PLATFORM                          ║
║                                                                                  ║
║   4 data streams  ·  23 source tables  ·  rsync over SSH  ·  DuckDB warehouse   ║
╚════════════════════════════════════════════════════════════════════════════════════╝


PRODUCTION SERVER (MariaDB / Frappe)                    ANALYTICS SERVER (DuckDB)
────────────────────────────────────                    ──────────────────────────

 Source DocType Tables                                   /data/analytics/
 ┌────────────────────────────────┐     rsync / SSH      ├── archives/ARCH-XXXXX/
 │ tabMemora Practice Log        │─┐   ─────────────►   ├── live/LSYNC-XXXXX/
 │ tabMemora Memory State        │ │   checksum verify   ├── dimensions/
 │ tabMemora Interaction Log     │ │                     ├── datasets/
 │ tabMemora Task Run Log        │ │                     └── memora.duckdb
 │ tabMemora Player Profile      │ │
 │ tabMemora Player Plan History │ │   Local staging      /opt/analytics/
 │ tabMemora Season              │ ├─► /data/memora/      └── memora-analytics (CLI)
 │ tabMemora Academic Plan       │ │   ├── archives/
 │ tabMemora Review Item         │ │   ├── live/
 │ tabMemora Lesson              │ │   └── analytics_exports/
 │ tabMemora Subject             │ │
 │ tabMemora Topic               │ │
 │ tabMemora Structure Progress  │ │
 │ tabMemora Player Wallet       │ │
 │ tabMemora Subscription        │ │
 │ tabMemora Voucher             │ │
 │ tabMemora Challenge Attempt   │ │
 │ tabMemora Challenge Detail    │ │
 │ tabMemora Live Challenge Event│ │
 │ tabMemora Live Challenge      │ │
 │   Participation              │ │
 │ tabMemora Build Queue         │ │
 │ tabMemora Content Report      │ │
 │ tabMemora Archive Job         │ │
 └────────────────────────────────┘ │
                                    │
     ┌──────────────────────────────┘
     │
     ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                          FOUR DATA STREAMS                                 │
 │                                                                            │
 │  [1] Archive Executor ···· cron 02:00 ···· cold-path archival             │
 │  [2] Live Sync Executor ·· cron 03:05 ···· hot-path daily snapshot        │
 │  [3] Dimension Refresh ··· cron 04:15 ···· event-driven + safety-net      │
 │  [4] Analytics Exporter ·· cron 06:45 ···· full analytics dataset export  │
 │                                                                            │
 └─────────────────────────────────────────────────────────────────────────────┘
```

---

## [1] Archive Executor — Cold-Path Archival

```
cron: 0 2 * * *  (02:00 daily)
binary: /opt/memora-archive/venv/bin/python -m archive_executor.run

Pipeline stages:
  Pending → Processing → Exported → Transferred → Ingested → Completed → Purged

Retry: up to 3 attempts per job, auto-reset to Pending on failure

archive_executor.run
│
├── tabMemora Archive Job (scheduler + status tracking)
│
├── FACT TYPES (4 archive schemas)
│   │
│   ├── practice_log (v1)
│   │   ├── source: tabMemora Practice Log
│   │   ├── scope:  last_seen_at (date-range)
│   │   ├── PK:     (player_id, item_id)
│   │   └── cols:   player_id, item_id, first_seen_at, last_seen_at,
│   │               last_result, attempt_count, correct_count,
│   │               season_id, plan_id
│   │
│   ├── memory_state (v1)
│   │   ├── source: tabMemora Memory State
│   │   ├── scope:  season_seq (season-triggered, not date-range)
│   │   ├── PK:     (name BIGINT, season_seq INT)
│   │   └── cols:   name, season_seq, subject, player, item_id,
│   │               stage_id, stability, difficulty, next_review,
│   │               lesson, state, step, last_review, modified
│   │
│   ├── interaction_log (v1)
│   │   ├── source: tabMemora Interaction Log
│   │   ├── scope:  timestamp (date-range)
│   │   ├── PK:     (name)
│   │   └── cols:   name, player, lesson, stage_id, item_id,
│   │               event_type, time_spent, errors_count,
│   │               timestamp, season_id, plan_id
│   │
│   └── task_run_log (v1)
│       ├── source: tabMemora Task Run Log
│       ├── scope:  completed_at (date-range, completed only)
│       ├── PK:     (name)
│       └── cols:   name, task_name, run_date, started_at,
│                   completed_at, duration_sec, status,
│                   triggered_by, processed_count,
│                   failed_count, error_message
│
├── DIMENSIONS (6 bundled per batch)
│   │
│   ├── player (v3)
│   │   ├── source: tabMemora Player Profile
│   │   │           JOIN Season, Academic Plan
│   │   └── cols:   player_id, grade, major,
│   │               season_id, season_title, plan_id, plan_name
│   │
│   ├── player_history (v1)
│   │   ├── source: tabMemora Player Plan History
│   │   │           (SCD Type 2 via LEAD() window)
│   │   └── cols:   player_id, plan_id, plan_name, grade, major,
│   │               season_id, valid_from, valid_to,
│   │               is_current, trigger_reason
│   │
│   ├── season (v1)  [derived]
│   │   ├── source: tabMemora Season
│   │   └── cols:   season_id, season_title, start_date, end_date
│   │
│   ├── plan (v1)  [derived]
│   │   ├── source: tabMemora Academic Plan
│   │   │           JOIN Season
│   │   └── cols:   plan_id, plan_name, grade, major,
│   │               season, season_title, is_published
│   │
│   ├── review_item (v2)
│   │   ├── source: tabMemora Review Item
│   │   │           JOIN Subject, Topic, Lesson
│   │   └── cols:   item_id, subject, subject_title, topic,
│   │               topic_title, lesson, lesson_title,
│   │               question_text, item_type, difficulty
│   │
│   └── lesson (v1)
│       ├── source: tabMemora Lesson
│       │           JOIN Topic
│       └── cols:   lesson_id, lesson_title, topic, topic_title,
│                   subject, track, unit, base_xp,
│                   is_published, is_reviewable
│
├── DERIVED DIMENSION RESOLUTION
│   ├── Pass 1: export direct dims (player, review_item, lesson)
│   │           by reading fact parquet → extracting referenced IDs
│   └── Pass 2: export derived dims (season, plan)
│               by reading player dim parquet → extracting season_id, plan_id
│
└── OUTPUT
    ├── local:  /data/memora/archives/ARCH-XXXXX/
    │           ├── manifest.json (SHA-256, row_count, size_bytes)
    │           ├── fact_practice_log.parquet
    │           ├── dim_player.parquet
    │           ├── dim_player_history.parquet
    │           ├── dim_season.parquet
    │           ├── dim_plan.parquet
    │           ├── dim_review_item.parquet
    │           └── dim_lesson.parquet
    │
    └── remote: /data/analytics/archives/ARCH-XXXXX/
                (rsync -avz --checksum --partial --compress -e ssh)
```

---

## [2] Live Sync Executor — Hot-Path Daily Snapshot

```
cron: 5 3 * * *  (03:05 daily)
binary: /opt/memora-archive/venv/bin/python -m archive_executor.live_sync

Pipeline stages:
  Pending → Processing → Exported → Transferred → Ingested → Completed
  (NO purge stage — source data is never deleted)

Deduplication: excludes date ranges already covered by completed archive jobs

archive_executor.live_sync
│
├── FACT TYPE (1 sync schema)
│   │
│   └── practice_log_live (v1)
│       ├── source: tabMemora Practice Log
│       ├── mode:   full_snapshot minus archived ranges
│       ├── scope:  last_seen_at (exclusion-based)
│       └── cols:   same as practice_log archive
│
├── DIMENSIONS (6 bundled per batch — same as archive)
│   ├── player (v3)
│   ├── player_history (v1)
│   ├── season (v1)  [derived]
│   ├── plan (v1)  [derived]
│   ├── review_item (v2)
│   └── lesson (v1)
│
└── OUTPUT
    ├── local:  /data/memora/live/LSYNC-XXXXX/
    │           ├── manifest.json
    │           ├── fact_practice_log_live.parquet
    │           └── dim_*.parquet (6 dimension files)
    │
    └── remote: /data/analytics/live/LSYNC-XXXXX/
                (rsync -avz --checksum --partial --compress -e ssh)
```

---

## [3] Dimension Refresh — Event-Driven + Safety Net

```
cron: 15 4 * * *  (04:15 daily — safety-net reconciliation)
also: Frappe doc_events (on_update, on_trash) — real-time triggers

runtime: Frappe worker process
module:  memora_admin.services.dimension_refresh

dimension_refresh
│
├── DIMENSION REGISTRY (6 dimensions)
│   │
│   ├── player (v3)
│   │   └── source: tabMemora Player Profile JOIN Season, Academic Plan
│   │
│   ├── player_history (v1)
│   │   └── source: tabMemora Player Plan History (SCD2)
│   │
│   ├── season (v1)
│   │   └── source: tabMemora Season
│   │
│   ├── plan (v1)
│   │   └── source: tabMemora Academic Plan JOIN Season
│   │
│   ├── review_item (v2)
│   │   └── source: tabMemora Review Item JOIN Subject, Topic, Lesson
│   │
│   └── lesson (v1)
│       └── source: tabMemora Lesson JOIN Topic
│
├── PROCESS
│   ├── 1. Load YAML schema from archive_schemas/dimensions/
│   ├── 2. Strip WHERE clause (full refresh, no ID filtering)
│   ├── 3. Execute frappe.db.sql() query
│   ├── 4. Write Parquet via pyarrow
│   └── 5. rsync to analytics server
│
└── OUTPUT
    ├── local:  temp directory (cleaned up after transfer)
    │           ├── dim_player.parquet
    │           ├── dim_player_history.parquet
    │           ├── dim_season.parquet
    │           ├── dim_plan.parquet
    │           ├── dim_review_item.parquet
    │           └── dim_lesson.parquet
    │
    └── remote: /data/analytics/dimensions/
                (rsync -az -e ssh)
```

---

## [4] Analytics Exporter — Full Dataset Export

```
cron: 45 6 * * *  (06:45 daily)
binary: python3 -m analytics_exporter

modes:
  auto        — switches full/incremental based on watermark
  full        — force full snapshot
  incremental — requires existing watermark, updates it

analytics_exporter.run
│
├── DIMENSIONS (6 datasets)
│   │
│   ├── dim_player
│   │   └── source: tabMemora Player Profile
│   │
│   ├── dim_content_hierarchy
│   │   └── source: tabMemora Subject / Topic / Lesson (denormalized tree)
│   │
│   ├── dim_review_item
│   │   └── source: tabMemora Review Item JOIN Subject, Topic, Lesson
│   │
│   ├── dim_season
│   │   └── source: tabMemora Season
│   │
│   ├── dim_academic_plan
│   │   └── source: tabMemora Academic Plan
│   │
│   └── dim_lesson_stage
│       └── source: tabMemora Lesson / Stage (supplementary dim)
│
├── FACT DATASETS — CORE (5 datasets)
│   │
│   ├── fact_interaction
│   │   ├── source: tabMemora Interaction Log
│   │   └── mode:   date-range filtered (default last 30 days)
│   │
│   ├── fact_memory_state
│   │   ├── source: tabMemora Memory State
│   │   └── mode:   full snapshot
│   │
│   ├── fact_practice
│   │   ├── source: tabMemora Practice Log
│   │   └── mode:   incremental (watermark)
│   │
│   ├── fact_subscription
│   │   ├── source: tabMemora Subscription
│   │   └── mode:   full snapshot
│   │
│   └── fact_voucher
│       ├── source: tabMemora Voucher
│       └── mode:   full snapshot
│
├── FACT DATASETS — CHALLENGE (2 datasets, group alias: fact_challenge)
│   │
│   ├── fact_challenge_attempt
│   │   └── source: tabMemora Challenge Attempt
│   │
│   └── fact_challenge_detail
│       └── source: tabMemora Challenge Detail
│
├── FACT DATASETS — LIVE CHALLENGE (2 datasets, group alias: fact_live_challenge)
│   │
│   ├── fact_live_challenge_event
│   │   └── source: tabMemora Live Challenge Event
│   │
│   └── fact_live_challenge_participation
│       └── source: tabMemora Live Challenge (participation records)
│
├── FACT DATASETS — SUPPLEMENTARY (4 datasets)
│   │
│   ├── fact_structure_progress
│   │   └── source: tabMemora Structure Progress
│   │
│   ├── fact_player_wallet
│   │   └── source: tabMemora Player Wallet
│   │
│   ├── fact_content_report
│   │   └── source: tabMemora Content Report
│   │
│   └── fact_archive_job
│       └── source: tabMemora Archive Job
│
├── FACT DATASETS — TASK RUN (2 datasets, group alias: fact_task_run)
│   │
│   ├── fact_task_run_log
│   │   └── source: tabMemora Task Run Log
│   │
│   └── fact_build_queue
│       └── source: tabMemora Build Queue
│
├── MULTI-FILE GROUP ALIASES
│   ├── fact_challenge      → [fact_challenge_attempt, fact_challenge_detail]
│   ├── fact_live_challenge  → [fact_live_challenge_event, fact_live_challenge_participation]
│   └── fact_task_run        → [fact_task_run_log, fact_build_queue]
│
└── OUTPUT
    ├── local:  /data/memora/analytics_exports/
    │           ├── manifest per dataset (SHA-256, row_count, size_bytes)
    │           ├── dim_player.parquet
    │           ├── dim_content_hierarchy.parquet
    │           ├── dim_review_item.parquet
    │           ├── dim_season.parquet
    │           ├── dim_academic_plan.parquet
    │           ├── dim_lesson_stage.parquet
    │           ├── fact_interaction.parquet
    │           ├── fact_memory_state.parquet
    │           ├── fact_practice.parquet
    │           ├── fact_subscription.parquet
    │           ├── fact_voucher.parquet
    │           ├── fact_challenge_attempt.parquet
    │           ├── fact_challenge_detail.parquet
    │           ├── fact_live_challenge_event.parquet
    │           ├── fact_live_challenge_participation.parquet
    │           ├── fact_structure_progress.parquet
    │           ├── fact_player_wallet.parquet
    │           ├── fact_content_report.parquet
    │           ├── fact_archive_job.parquet
    │           ├── fact_task_run_log.parquet
    │           └── fact_build_queue.parquet
    │
    └── remote: /data/analytics/datasets/
                (rsync -avz --checksum --partial --compress -e ssh)
```

---

## Transfer Mechanism

```
All four streams use rsync over SSH with checksum verification.

┌─────────────────────────────────────────────────────────────────────────────┐
│  TRANSFER PROTOCOL                                                        │
│                                                                           │
│  command:  rsync -avz --checksum --partial --compress                     │
│            -e "ssh -i {key} -p {port} -o StrictHostKeyChecking=accept-new │
│                   -o BatchMode=yes"                                       │
│            {local_dir}/  {user}@{host}:{remote_path}/                     │
│                                                                           │
│  config:   ANALYTICS_SSH_HOST, ANALYTICS_SSH_USER, ANALYTICS_SSH_KEY_PATH │
│            ANALYTICS_SSH_PORT (default 22)                                │
│            ANALYTICS_SSH_TIMEOUT (default 300s)                           │
│                                                                           │
│  manifest: JSON per batch/dataset                                         │
│            ├── filename                                                   │
│            ├── row_count                                                  │
│            ├── checksum (SHA-256)                                         │
│            └── size_bytes                                                 │
│                                                                           │
│  verify:   post-transfer checksum validation via SSH remote command       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Analytics Server — Remote Filesystem & Services

```
ANALYTICS SERVER
│
├── /data/analytics/                          ← ANALYTICS_REMOTE_PATH
│   │
│   ├── archives/                             ← archive executor output
│   │   ├── ARCH-00001/
│   │   │   ├── manifest.json
│   │   │   ├── fact_practice_log.parquet
│   │   │   └── dim_*.parquet
│   │   ├── ARCH-00002/
│   │   └── ...
│   │
│   ├── live/                                 ← live sync executor output
│   │   ├── LSYNC-00001/
│   │   │   ├── manifest.json
│   │   │   ├── fact_practice_log_live.parquet
│   │   │   └── dim_*.parquet
│   │   ├── LSYNC-00002/
│   │   └── ...
│   │
│   ├── dimensions/                           ← dimension refresh output
│   │   ├── dim_player.parquet
│   │   ├── dim_player_history.parquet
│   │   ├── dim_season.parquet
│   │   ├── dim_plan.parquet
│   │   ├── dim_review_item.parquet
│   │   └── dim_lesson.parquet
│   │
│   ├── datasets/                             ← analytics exporter output
│   │   ├── dim_player.parquet
│   │   ├── dim_content_hierarchy.parquet
│   │   ├── dim_review_item.parquet
│   │   ├── dim_season.parquet
│   │   ├── dim_academic_plan.parquet
│   │   ├── dim_lesson_stage.parquet
│   │   ├── fact_interaction.parquet
│   │   ├── fact_memory_state.parquet
│   │   ├── fact_practice.parquet
│   │   ├── fact_subscription.parquet
│   │   ├── fact_voucher.parquet
│   │   ├── fact_challenge_attempt.parquet
│   │   ├── fact_challenge_detail.parquet
│   │   ├── fact_live_challenge_event.parquet
│   │   ├── fact_live_challenge_participation.parquet
│   │   ├── fact_structure_progress.parquet
│   │   ├── fact_player_wallet.parquet
│   │   ├── fact_content_report.parquet
│   │   ├── fact_archive_job.parquet
│   │   ├── fact_task_run_log.parquet
│   │   └── fact_build_queue.parquet
│   │
│   └── memora.duckdb                         ← star-schema data warehouse
│       ├── ingested archive facts + dims
│       └── ingested live sync snapshots
│
└── /opt/analytics/
    └── memora-analytics                      ← Analytics CLI
        ├── ingest   (load parquet into DuckDB)
        ├── handoff  (remove overlapping live data after archive ingest)
        └── refresh  (rebuild analytics views)
```

---

## Cron Schedule Summary

```
TIME        STREAM                   ENTRY POINT
──────────  ───────────────────────  ──────────────────────────────────────────────
01:20       Archive trigger          memora_admin.tasks.archive_trigger
            (check ended seasons)      .check_seasons_for_archive

02:00       Archive executor         python -m archive_executor.run
            (cold-path archival)

02:00       Task log archive         memora_admin.tasks.archive_task_log
            (Frappe-side trigger)       .archive_task_log

03:00       Live sync trigger        memora_admin.tasks.live_sync_trigger
            (create LSYNC jobs)        .trigger_daily_live_sync

03:05       Live sync executor       python -m archive_executor.live_sync
            (hot-path snapshot)

04:15       Dimension refresh        memora_admin.tasks.dimension_sync
            (safety-net full)          .reconcile_dimensions

*/6h        Archive health monitor   memora_admin.tasks.archive_monitor
                                       .check_archive_health

06:45       Analytics exporter       python -m analytics_exporter
            (full dataset export)
```

---

## Schema Registry — File Layout

```
archive_schemas/
├── archive_types/                   ← fact schemas for archive executor
│   ├── practice_log.v1.yaml
│   ├── memory_state.v1.yaml
│   ├── interaction_log.v1.yaml
│   └── task_run_log.v1.yaml
│
├── dimensions/                      ← shared dimension schemas
│   ├── player.v1.yaml
│   ├── player.v2.yaml
│   ├── player.v3.yaml               ← current
│   ├── player_history.v1.yaml
│   ├── season.v1.yaml
│   ├── plan.v1.yaml
│   ├── review_item.v1.yaml
│   ├── review_item.v2.yaml          ← current
│   └── lesson.v1.yaml
│
├── sync_types/                      ← live sync schemas
│   └── practice_log_live.v1.yaml
│
└── snapshot_types/                  ← point-in-time snapshots
    └── structure_progress.v1.yaml

analytics_exporter/schemas/          ← analytics exporter schemas (21 YAMLs)
├── dim_player.yaml
├── dim_content_hierarchy.yaml
├── dim_review_item.yaml
├── dim_season.yaml
├── dim_academic_plan.yaml
├── dim_lesson_stage.yaml
├── fact_interaction.yaml
├── fact_memory_state.yaml
├── fact_practice.yaml
├── fact_subscription.yaml
├── fact_voucher.yaml
├── fact_challenge_attempt.yaml
├── fact_challenge_detail.yaml
├── fact_live_challenge_event.yaml
├── fact_live_challenge_participation.yaml
├── fact_structure_progress.yaml
├── fact_player_wallet.yaml
├── fact_content_report.yaml
├── fact_archive_job.yaml
├── fact_task_run_log.yaml
└── fact_build_queue.yaml
```

---

## Dataset Count Summary

```
STREAM                    FACTS   DIMS   TOTAL FILES   OUTPUT FORMAT
────────────────────────  ──────  ─────  ────────────  ──────────────
[1] Archive Executor       4       6      10 per batch  Parquet + JSON
[2] Live Sync Executor     1       6       7 per batch  Parquet + JSON
[3] Dimension Refresh      —       6       6            Parquet
[4] Analytics Exporter    15       6      21            Parquet + JSON
```

---

## Source Data Lifecycle — Cleanup & Purge

Three categories of removal: (A) scheduled age-based cleanup, (B) event-triggered
deletion, (C) archive-pipeline purge where data is exported to the analytics server
first, then deleted from MariaDB.

---

### [A] Scheduled Age-Based Cleanup

```
Hard-deleted on a Frappe cron schedule. No export occurs — data is permanently gone.

DOCTYPE / TABLE                         SCHEDULE      RETENTION         BATCH    REASON
──────────────────────────────────────  ────────────  ────────────────  ───────  ──────────────────────────────────────────
tabMemora Voucher Redemption Log        daily 05:30   100 days          1,000    Short-lived redemption audit trail.
                                                                                 No long-term analytics value after 100 days.

tabMemora Sync Log                      daily 05:00   7 days            1,000    Operational rsync/transfer log.
                                                                                 Stale after one week.

tabMemora Build Queue                   daily 04:00   Completed: 7d     1,000    Job records only needed for short post-run
  (status = Completed / Failed only)                  Failed:    14d             review; failed jobs kept longer for debugging.

tabMemora Task Log Archive Batch        daily 04:30   14 days           500      Tracking rows become redundant once the
  (status = Purged only)                                                         source Task Run Log is confirmed purged.

tabMemora Archive Job                   daily 06:30   Purged: 30d       500      Job metadata no longer needed after data
  (status = Purged / Failed only)                     Failed:  90d               is confirmed archived and purged.
                                                                                 Guard: only deleted when no child batches
                                                                                 are still active.

tabMemora Live Sync Job                 daily 06:00   10 days           500      Job tracking rows only needed while
  (status = Completed only)                                                      pipeline is active; 10 days covers any
                                                                                 late debugging window.

tabMemora Announcement (expired only)   daily 01:00   effective_end     —        Past announcements have no value after
                                                      _date < today              their display window closes.

tabMemora Voucher Batch                 daily 02:30   30 days           —        Encrypted card export file attachments
  (encrypted_file_url attachment only)                                           purged to reclaim storage.
                                                                                 The Batch doc itself is NOT deleted.
```

---

### [B] Event-Triggered Deletion

```
Rows are deleted immediately when a business event occurs, not on a schedule.

──────────────────────────────────────────────────────────────────────────────────────
EVENT: Player Plan Change  (FR-024 "clean slate")
──────────────────────────────────────────────────────────────────────────────────────

When:   Player.plan field changes  OR  execute_plan_change() API called.

Why:    A plan change moves a player to entirely different content.
        Carrying over old learning state would corrupt progress metrics and
        produce misleading analytics.  Wallet is also reset to 0.

Tables deleted (all in a single atomic transaction):

  tabMemora Player Subscription   DELETE WHERE player = {player_id}            (all rows)
  tabMemora Structure Progress    DELETE WHERE player = {player_id}            (all rows)
  tabMemora Memory State          DELETE WHERE player = {player_id}
                                         AND season_seq = {current_season_seq} (current season only)
  tabMemora Practice Log          DELETE WHERE player_id = {player_id}         (all rows)
  tabPlayer Practice Summary      DELETE WHERE player_id = {player_id}         (all rows, derived table)
  tabMemora Challenge Progress    DELETE WHERE player = {player_id}            (all rows)

Redis also cleared for this player:
  memora:ch:progress:{player_id}:*   ← cleared before MariaDB delete so the
                                       dirty-flush task cannot re-create rows

  Note: a snapshot of the player's state is created before deletion for audit purposes.

──────────────────────────────────────────────────────────────────────────────────────
EVENT: Season End / Unpublish
──────────────────────────────────────────────────────────────────────────────────────

When:   season.end_date < today  OR  is_published set to 0.
        Detected by: daily cron 01:05 (season_expiration task) + on_update doc_event.

Sub-event A — Voucher Card expiration  (status transition, NOT deletion)

  tabMemora Voucher Card
    Available → Expired   (void_reason = "Season Ended")
    Allocated → Expired   (void_reason = "Season Ended")
    Redeemed, Void        — terminal; never modified.

  If all cards in a batch reach a terminal state → Batch is auto-closed.

  Why:  Cards tied to an inactive season can no longer be redeemed.
        Keeping them "Available" would cause failed redemptions and support noise.

Sub-event B — Redis leaderboard & challenge hub cache wipe + MariaDB cleanup

  Redis keys deleted:
    memora:ch:progress:*             ← challenge hub in-flight progress (all players)
    memora:lb:ch:{season_id}:*       ← challenge leaderboard keys (season-scoped)
    memora:lbmeta:ch:{season_id}:*   ← challenge leaderboard tier metadata

  MariaDB rows deleted:
    tabMemora Challenge Progress     DELETE WHERE season = {season_id}

  Pre-flush: dirty challenge progress is written back to MariaDB BEFORE the cache
             is wiped so no completed work is lost.  MariaDB delete happens after
             Redis is cleared so the dirty-flush task cannot re-create deleted rows.

  Why:  Cache holds hot in-flight data for an active season.
        Once a season ends no new activity is expected; stale cache wastes memory
        and could serve incorrect data to any late reader.
        Challenge Progress is not exported to the analytics server so keeping
        rows in MariaDB after season end serves no purpose.

──────────────────────────────────────────────────────────────────────────────────────
EVENT: Redis session orphan cleanup  (hourly, not season-tied)
──────────────────────────────────────────────────────────────────────────────────────

  memora:gamesession:{id}   — deleted when TTL = -1 (no expiry set, i.e. orphaned)
  Schedule: hourly @ :15

  Why:  Crashed or interrupted game sessions can leave keys with no TTL.
        Orphaned sessions block players from starting new sessions.
```

---

### [C] Archive Pipeline Purge

```
Data is only deleted from MariaDB AFTER the full archive pipeline has completed:
  Exported → Transferred → Ingested → Completed → Purged

All purges are:
  · Resumable   — purge_progress JSON tracks batch position; safe to interrupt/restart
  · Audited     — archive_delete_audit_log records rows_deleted, batches, duration, executor
  · Guarded     — os.path.isdir(file_path) verified before any destructive operation
  · Rate-limited — 2-second sleep between batches to reduce replication lag

──────────────────────────────────────────────────────────────────────────────────────────────
SOURCE TABLE           PURGE MODE       TRIGGER                        DELETE STRATEGY
─────────────────────  ───────────────  ─────────────────────────────  ────────────────────────
tabMemora             season_scope      Season end_date passes.         ALTER TABLE DROP PARTITION
  Memory State        (DROP PARTITION)  archive_trigger creates job     p_season_{seq}
                                        with filter_type = "season"
                                        post_archive_action = "Delete"  4 safety gates required before
                                                                         DROP executes:
                                                                          1. archive files exist on disk
                                                                          2. season data integrity check
                                                                          3. player access verification
                                                                          4. downstream replica sync confirmed

                                        Why: Memory State is range-partitioned by season_seq.
                                             Dropping the partition is O(1) and avoids row-by-row
                                             DELETE on millions of rows.  Data is already in DuckDB.

──────────────────────────────────────────────────────────────────────────────────────────────
tabMemora             player_scope      Season ends.                    Batched DELETE
  Practice Log                          archive_trigger creates job      WHERE player_id IN (...)
  tabPlayer                             with post_archive_action          5,000 players / batch
  Practice Summary                      = "Delete"                       10,000 rows / sub-batch
  (derived table)                                                         2s sleep between sub-batches

                                        Why: Practice Log is partitioned by player; deleting by
                                             player_id matches the access pattern and keeps
                                             batch sizes predictable.  Summary table is derived
                                             and must stay in sync.

──────────────────────────────────────────────────────────────────────────────────────────────
tabMemora             date_window       Logs older than 14 days.        Batched DELETE
  Task Run Log                          archive_task_log.py creates       WHERE completed_at IN window
                                        archive job; purge_task_log.py    AND status IN (Success, Failed,
                                        handles the actual DELETE.        Partial)
                                                                          10,000 rows / batch
                                                                          lock_wait_timeout = 5s

                                        Why: Task logs are operational; 14 days covers any
                                             meaningful post-mortem window.  Archived copy in
                                             DuckDB is the long-term record.

──────────────────────────────────────────────────────────────────────────────────────────────
tabMemora             date_window       Season date range.              Batched DELETE
  Interaction Log                       archive_trigger creates job      WHERE timestamp IN date_from…date_to
                                        with post_archive_action          10,000 rows / batch
                                        = "Delete"

                                        Why: Interaction logs grow unboundedly with player
                                             activity.  After archival to DuckDB they no longer
                                             need to live in the transactional database.
──────────────────────────────────────────────────────────────────────────────────────────────
```

---

### Cleanup Schedule Summary

```
TIME        TASK                              ENTRY POINT
──────────  ────────────────────────────────  ──────────────────────────────────────────────
01:00       Announcement cleanup              memora_admin.tasks.announcement_cleanup
            (delete expired announcements)      .cleanup_expired_announcements

01:05       Season card expiration            memora_admin.tasks.season_expiration
            (Available/Allocated → Expired)     .expire_season_cards

01:10       Challenge hub reset              memora_admin.events.access_sync
            (expired season → unpublish)       .check_expired_seasons_challenge_reset

02:30       Voucher export file cleanup       memora_admin.tasks.voucher_cleanup
            (delete encrypted file attachments) .cleanup_voucher_export_files

03:00       Leaderboard cleanup               memora_admin.tasks.leaderboard_cleanup
            (daily: 30d, weekly: 90d)           .cleanup_leaderboard_data

03:30       Task log purge                    memora_admin.tasks.purge_task_log
            (delete archived task run rows)     .purge_task_log

04:00       Build queue cleanup               memora_admin.tasks.build_cleanup
            (Completed: 7d, Failed: 14d)        .cleanup_build_queue

04:30       Task log batch cleanup            memora_admin.tasks.task_log_archive_batch_cleanup
            (delete Purged batch records)       .cleanup_task_log_archive_batches

05:00       Sync log cleanup                  memora_admin.tasks.sync_log_cleanup
            (7-day retention)                   .cleanup_sync_logs

05:30       Voucher redemption log cleanup    memora_admin.tasks.voucher_log_cleanup
            (100-day retention)                 .cleanup_voucher_redemption_logs

06:00       Live sync job cleanup            memora_admin.tasks.live_sync_job_cleanup
            (Completed: 10d retention)         .cleanup_live_sync_jobs

06:30       Archive job cleanup               memora_admin.tasks.archive_job_cleanup
            (Purged: 30d, Failed: 90d)          .cleanup_archive_jobs

*/1h:15     Session orphan cleanup            memora_admin.tasks.session_cleanup
            (Redis TTL=-1 orphaned sessions)    .cleanup_orphaned_sessions
```
