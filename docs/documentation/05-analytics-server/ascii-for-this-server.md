# Archivable & Transferable Parquet Data Hierarchy

## Pipeline Overview

```
Memora Admin (Source)                                   Analytics Server
│                                                       │
├── ARCHIVE Pipeline ──── rsync/SSH ──────────────────► │── Hive Lake (Parquet)
├── LIVE SYNC Pipeline ── rsync/SSH ──────────────────► │── DuckDB Live Tables
├── SNAPSHOT Pipeline ─── rsync/SSH ──────────────────► │── Hive Lake (Parquet)
└── DIMENSION REFRESH ─── rsync/SSH ──────────────────► │── Dimension Parquets
                                                        │
                                                        └── DuckDB Semantic Layer
                                                            ├── Archive Views
                                                            ├── Dimension Views
                                                            ├── Combined Views
                                                            └── Aggregate Tables
```

---

## A. Source-Side: Exported Parquet Batches

```
/data/memora/
│
├── archives/                                         ── ARCHIVE BATCHES
│   └── {BATCH_ID}/                                   ── e.g. ARCH-00027
│       ├── manifest.json
│       │
│       ├──────────────────────────────────────────────── PRACTICE LOG (v1)
│       │   │                                            source: tabMemora Practice Log
│       │   │                                            scope_column: last_seen_at
│       │   │                                            trigger_mode: date-window
│       │   │
│       │   ├── fact_practice_log.parquet
│       │   │   ├── player_id          VARCHAR(140)
│       │   │   ├── item_id            VARCHAR(36)
│       │   │   ├── first_seen_at      DATETIME
│       │   │   ├── last_seen_at       DATETIME       ◄── scope column
│       │   │   ├── last_result        ENUM(Correct, Incorrect)
│       │   │   ├── attempt_count      INT UNSIGNED
│       │   │   ├── correct_count      INT UNSIGNED
│       │   │   ├── season_id          VARCHAR(140)    ── derived via Player Profile
│       │   │   ├── plan_id            VARCHAR(140)    ── derived via Player Profile
│       │   │   ├── archive_scope      VARCHAR(140)    ── export metadata
│       │   │   ├── archive_job_id     VARCHAR(140)    ── export metadata
│       │   │   ├── schema_version     VARCHAR(10)     ── export metadata
│       │   │   └── exported_at        DATETIME        ── export metadata
│       │   │
│       │   ├── dim_player.parquet                     ── player.v3
│       │   │   ├── player_id          VARCHAR(140)
│       │   │   ├── grade              VARCHAR
│       │   │   ├── major              VARCHAR
│       │   │   ├── season_id          VARCHAR(140)
│       │   │   ├── season_title       VARCHAR
│       │   │   ├── plan_id            VARCHAR(140)
│       │   │   └── plan_name          VARCHAR
│       │   │
│       │   ├── dim_review_item.parquet                ── review_item.v2
│       │   │   ├── item_id            VARCHAR(36)
│       │   │   ├── subject            VARCHAR
│       │   │   ├── subject_title      VARCHAR
│       │   │   ├── topic              VARCHAR
│       │   │   ├── topic_title        VARCHAR
│       │   │   ├── lesson             VARCHAR
│       │   │   ├── lesson_title       VARCHAR
│       │   │   ├── question_text      VARCHAR
│       │   │   ├── item_type          VARCHAR
│       │   │   └── difficulty         FLOAT
│       │   │
│       │   ├── dim_season.parquet                     ── season.v1 (derived)
│       │   │   ├── season_id          VARCHAR(140)
│       │   │   ├── season_title       VARCHAR
│       │   │   ├── start_date         DATE
│       │   │   └── end_date           DATE
│       │   │
│       │   └── dim_plan.parquet                       ── plan.v1 (derived)
│       │       ├── plan_id            VARCHAR(140)
│       │       ├── plan_name          VARCHAR
│       │       ├── grade              VARCHAR
│       │       ├── major              VARCHAR
│       │       ├── season             VARCHAR(140)
│       │       ├── season_title       VARCHAR
│       │       └── is_published       BOOLEAN
│       │
│       ├──────────────────────────────────────────────── MEMORY STATE (v1)
│       │   │                                            source: tabMemora Memory State
│       │   │                                            scope_column: season_seq
│       │   │                                            trigger_mode: season
│       │   │
│       │   ├── fact_memory_state.parquet
│       │   │   ├── name               BIGINT          ── PK (non-auto-increment)
│       │   │   ├── season_seq         INT             ◄── scope column (partition key)
│       │   │   ├── subject            VARCHAR(140)
│       │   │   ├── player             VARCHAR(140)
│       │   │   ├── item_id            VARCHAR(36)     ── UUID (BIN_TO_UUID in SQL)
│       │   │   ├── stage_id           VARCHAR(140)
│       │   │   ├── stability          FLOAT           ── DECIMAL(21,9) in DB
│       │   │   ├── difficulty         FLOAT           ── DECIMAL(21,9) in DB
│       │   │   ├── next_review        DATETIME
│       │   │   ├── lesson             VARCHAR(140)
│       │   │   ├── state              TINYINT
│       │   │   ├── step               TINYINT
│       │   │   ├── last_review        DATETIME
│       │   │   ├── modified           DATETIME
│       │   │   ├── archive_scope      VARCHAR(140)    ── export metadata
│       │   │   ├── archive_job_id     VARCHAR(140)    ── export metadata
│       │   │   ├── schema_version     VARCHAR(10)     ── export metadata
│       │   │   └── exported_at        DATETIME        ── export metadata
│       │   │
│       │   ├── dim_player.parquet                     ── player.v3
│       │   │   └── (same schema as above)
│       │   │
│       │   └── dim_season.parquet                     ── season.v1 (derived)
│       │       └── (same schema as above)
│       │
│       ├──────────────────────────────────────────────── INTERACTION LOG (v1)
│       │   │                                            source: tabMemora Interaction Log
│       │   │                                            scope_column: timestamp
│       │   │                                            trigger_mode: date-window
│       │   │
│       │   ├── fact_interaction_log.parquet
│       │   │   ├── name               VARCHAR(140)
│       │   │   ├── player             VARCHAR(140)
│       │   │   ├── lesson             VARCHAR(140)
│       │   │   ├── stage_id           VARCHAR(140)
│       │   │   ├── item_id            VARCHAR(140)
│       │   │   ├── event_type         VARCHAR(20)     ── ENUM(Started, Completed, Failed, Skipped)
│       │   │   ├── time_spent         INT
│       │   │   ├── errors_count       INT
│       │   │   ├── timestamp          DATETIME        ◄── scope column
│       │   │   ├── season_id          VARCHAR(140)    ── derived via Player Profile
│       │   │   ├── plan_id            VARCHAR(140)    ── derived via Player Profile
│       │   │   ├── archive_scope      VARCHAR(140)    ── export metadata
│       │   │   ├── archive_job_id     VARCHAR(140)    ── export metadata
│       │   │   ├── schema_version     VARCHAR(10)     ── export metadata
│       │   │   └── exported_at        DATETIME        ── export metadata
│       │   │
│       │   ├── dim_player.parquet                     ── player.v3
│       │   │   └── (same schema as above)
│       │   │
│       │   ├── dim_lesson.parquet                     ── lesson.v1
│       │   │   ├── lesson_id          VARCHAR(140)
│       │   │   ├── lesson_title       VARCHAR
│       │   │   ├── topic              VARCHAR
│       │   │   ├── topic_title        VARCHAR
│       │   │   ├── subject            VARCHAR
│       │   │   ├── track              VARCHAR
│       │   │   ├── unit               VARCHAR
│       │   │   ├── base_xp            INT
│       │   │   ├── is_published       BOOLEAN
│       │   │   └── is_reviewable      BOOLEAN
│       │   │
│       │   ├── dim_season.parquet                     ── season.v1 (derived)
│       │   │   └── (same schema as above)
│       │   │
│       │   └── dim_plan.parquet                       ── plan.v1 (derived)
│       │       └── (same schema as above)
│       │
│       └──────────────────────────────────────────────── TASK RUN LOG (v1)
│           │                                            source: tabMemora Task Run Log
│           │                                            scope_column: completed_at
│           │                                            trigger_mode: date-window
│           │
│           └── fact_task_run_log.parquet
│               ├── name               VARCHAR(140)
│               ├── task_name          VARCHAR(140)
│               ├── run_date           DATE
│               ├── started_at         DATETIME
│               ├── completed_at       DATETIME        ◄── scope column
│               ├── duration_sec       FLOAT
│               ├── status             VARCHAR(20)     ── ENUM(Success, Failed, Partial)
│               ├── triggered_by       VARCHAR(20)     ── ENUM(Scheduler, Manual, Catch-up)
│               ├── processed_count    INT
│               ├── failed_count       INT
│               ├── error_message      TEXT
│               ├── archive_scope      VARCHAR(140)    ── export metadata
│               ├── archive_job_id     VARCHAR(140)    ── export metadata
│               ├── schema_version     VARCHAR(10)     ── export metadata
│               └── exported_at        DATETIME        ── export metadata
│               (no dimensions)
│
├── live/                                              ── LIVE SYNC BATCHES
│   └── {SYNC_BATCH_ID}/
│       ├── manifest.json
│       │
│       └──────────────────────────────────────────────── PRACTICE LOG LIVE (v1)
│           │                                            source: tabMemora Practice Log
│           │                                            mode: full_snapshot
│           │                                            excludes: completed archive date ranges
│           │
│           ├── fact_practice_log.parquet
│           │   ├── player_id          VARCHAR(140)
│           │   ├── item_id            VARCHAR(36)
│           │   ├── first_seen_at      DATETIME
│           │   ├── last_seen_at       DATETIME
│           │   ├── last_result        ENUM(Correct, Incorrect)
│           │   ├── attempt_count      INT UNSIGNED
│           │   ├── correct_count      INT UNSIGNED
│           │   ├── season_id          VARCHAR(140)
│           │   ├── plan_id            VARCHAR(140)
│           │   ├── scope_type         VARCHAR(20)     ── sync metadata
│           │   ├── sync_batch_id      VARCHAR(140)    ── sync metadata
│           │   ├── schema_version     VARCHAR(10)     ── sync metadata
│           │   └── synced_at          DATETIME        ── sync metadata
│           │
│           ├── dim_player.parquet                     ── player.v3
│           │   └── (same schema as archives)
│           │
│           ├── dim_review_item.parquet                ── review_item.v2
│           │   └── (same schema as archives)
│           │
│           ├── dim_season.parquet                     ── season.v1 (derived)
│           │   └── (same schema as archives)
│           │
│           └── dim_plan.parquet                       ── plan.v1 (derived)
│               └── (same schema as archives)
│
└── snapshots/                                         ── SNAPSHOT BATCHES
    └── {SNAPSHOT_BATCH_ID}/
        ├── manifest.json
        │
        └──────────────────────────────────────────────── STRUCTURE PROGRESS (v1)
            │                                            source: tabMemora Structure Progress
            │                                            mode: point-in-time snapshot
            │
            └── fact_structure_progress.parquet
                ├── snapshot_date       DATE
                ├── player_id          VARCHAR(140)
                ├── plan_id            VARCHAR(140)
                ├── subject_id         VARCHAR(140)
                └── completion_percentage  FLOAT        ── 0..100
```

## Dimension Reuse Matrix

```
                          dim_      dim_         dim_      dim_      dim_
                         player   review_item   season    plan     lesson
                         (v3)       (v2)         (v1)     (v1)      (v1)
                        ───────  ───────────   ───────  ───────  ─────────
Practice Log (archive)    X          X            X        X
Memory State (archive)    X                       X
Interaction Log (archive) X                       X        X        X
Task Run Log (archive)
Practice Log (live sync)  X          X            X        X
Structure Progress (snap)
```

## Archive Job Lifecycle

```
                    ┌──────────────────────────────────────────────────────┐
                    │              Archive Job Stages                      │
                    │                                                      │
 Scheduler ──►  Pending ──► Processing ──► Exported ──► Transferred       │
                    │            │              │             │            │
                    │         export          rsync       ingest-archive   │
                    │        Parquet +       to analytics + verify         │
                    │        manifest        server            │           │
                    │                                       Ingested      │
                    │                                          │           │
                    │                                       handoff        │
                    │                                      + refresh-recent│
                    │                                      + refresh-agg   │
                    │                                          │           │
                    │                                      Completed      │
                    │                                          │           │
                    │                                  ┌───────┴──────┐   │
                    │                                  │  if Delete   │   │
                    │                                  │  post_action │   │
                    │                                  └───────┬──────┘   │
                    │                                          │           │
                    │                                       Purged        │
                    │                                    (batched DEL     │
                    │                                     10k + 2s)       │
                    │                                                      │
                    │   On error at any stage ──► Failed (3 retries)      │
                    └──────────────────────────────────────────────────────┘
```

## SQL-to-Parquet Type Mapping

```
SQL Type                    Arrow / Parquet Type
──────────────────────────  ────────────────────
INT, BIGINT, TINYINT        int64
FLOAT, DOUBLE, DECIMAL      float64
DATETIME, TIMESTAMP         timestamp("us")
DATE                        date32
VARCHAR, TEXT, ENUM          string
```

---

## B. Analytics Server: Lakehouse Directory Layout

```
/data/analytics/                                       ── ANALYTICS_REMOTE_PATH
│
├── memora.duckdb                                    ── DuckDB database file (REMOTE_DUCKDB_PATH)
│
├── lake/                                              ── Hive-partitioned fact data
│   │
│   ├── practice_log/                                  ── DATE-PARTITIONED (last_seen_at)
│   │   └── year={YYYY}/
│   │       └── month={MM}/
│   │           └── day={DD}/
│   │               └── part-{BATCH_ID}.parquet
│   │
│   ├── interaction_log/                               ── DATE-PARTITIONED (timestamp)
│   │   └── year={YYYY}/
│   │       └── month={MM}/
│   │           └── day={DD}/
│   │               └── part-{BATCH_ID}.parquet
│   │
│   ├── task_run_log/                                  ── DATE-PARTITIONED (completed_at)
│   │   └── year={YYYY}/
│   │       └── month={MM}/
│   │           └── day={DD}/
│   │               └── part-{BATCH_ID}.parquet
│   │
│   ├── memory_state/                                  ── VALUE-PARTITIONED (season_seq)
│   │   └── season_seq={N}/
│   │       └── part-{BATCH_ID}.parquet
│   │
│   └── structure_progress/                            ── VALUE-PARTITIONED (snapshot_date)
│       └── snapshot_date={YYYY-MM-DD}/
│           └── part-{BATCH_ID}.parquet
│
├── dimensions/                                        ── DIMENSION PARQUET FILES
│   ├── dim_player.parquet                             ── player.v3
│   │   ├── player_id          VARCHAR
│   │   ├── grade              VARCHAR
│   │   ├── major              VARCHAR
│   │   ├── season_id          VARCHAR
│   │   ├── season_title       VARCHAR
│   │   ├── plan_id            VARCHAR
│   │   └── plan_name          VARCHAR
│   │
│   ├── dim_player_history.parquet                     ── player_history.v1 (SCD Type 2)
│   │   ├── player_id          VARCHAR                 ── source: tabMemora Player Plan History
│   │   ├── plan_id            VARCHAR
│   │   ├── plan_name          VARCHAR
│   │   ├── grade              VARCHAR
│   │   ├── major              VARCHAR
│   │   ├── season_id          VARCHAR
│   │   ├── valid_from         TIMESTAMP               ── LEAD() window boundary
│   │   ├── valid_to           TIMESTAMP               ── NULL = current record
│   │   ├── is_current         BOOLEAN                 ── 1 if latest per player
│   │   └── trigger_reason     VARCHAR
│   │
│   ├── dim_season.parquet                             ── season.v1
│   │   ├── season_id          VARCHAR
│   │   ├── season_title       VARCHAR
│   │   ├── start_date         DATE
│   │   └── end_date           DATE
│   │
│   ├── dim_plan.parquet                               ── plan.v1
│   │   ├── plan_id            VARCHAR
│   │   ├── plan_name          VARCHAR
│   │   ├── grade              VARCHAR
│   │   ├── major              VARCHAR
│   │   ├── season             VARCHAR
│   │   ├── season_title       VARCHAR
│   │   └── is_published       BOOLEAN
│   │
│   ├── dim_review_item.parquet                        ── review_item.v2
│   │   ├── item_id            VARCHAR
│   │   ├── subject            VARCHAR
│   │   ├── subject_title      VARCHAR
│   │   ├── topic              VARCHAR
│   │   ├── topic_title        VARCHAR
│   │   ├── lesson             VARCHAR
│   │   ├── lesson_title       VARCHAR
│   │   ├── question_text      VARCHAR
│   │   ├── item_type          VARCHAR
│   │   └── difficulty         FLOAT
│   │
│   └── dim_lesson.parquet                             ── lesson.v1
│       ├── lesson_id          VARCHAR
│       ├── lesson_title       VARCHAR
│       ├── topic              VARCHAR
│       ├── topic_title        VARCHAR
│       ├── subject            VARCHAR
│       ├── track              VARCHAR
│       ├── unit               VARCHAR
│       ├── base_xp            INTEGER
│       ├── is_published       BOOLEAN
│       └── is_reviewable      BOOLEAN
│
└── manifests/                                         ── STORED BATCH MANIFESTS
    └── archive/
        └── {BATCH_ID}.json
```

---

## C. DuckDB Semantic Layer

```
memora.duckdb
│
├── VIEWS (read-only, over Parquet)
│   │
│   ├── practice_log_archive ──────► lake/practice_log/**/*.parquet
│   ├── interaction_log_archive ───► lake/interaction_log/**/*.parquet
│   ├── memory_state_archive ──────► lake/memory_state/**/*.parquet
│   ├── task_run_log_archive ──────► lake/task_run_log/**/*.parquet
│   ├── structure_progress_snapshots ► lake/structure_progress/**/*.parquet
│   │
│   ├── dim_player ────────────────► dimensions/dim_player.parquet
│   ├── dim_player_history ────────► dimensions/dim_player_history.parquet
│   ├── dim_season ────────────────► dimensions/dim_season.parquet
│   ├── dim_plan ──────────────────► dimensions/dim_plan.parquet
│   ├── dim_review_item ───────────► dimensions/dim_review_item.parquet
│   ├── dim_lesson ────────────────► dimensions/dim_lesson.parquet
│   │
│   ├── practice_log_combined ─────► UNION ALL (archive + live)
│   │   ├── player_id, item_id, first_seen_at, last_seen_at
│   │   ├── last_result, attempt_count, correct_count
│   │   ├── season_id, plan_id
│   │   └── source                 ── 'archive' | 'live'
│   │
│   └── memory_state_combined ─────► UNION ALL (archive + current)
│       ├── name, season_seq, subject, player, item_id
│       ├── stage_id, stability, difficulty, next_review
│       ├── lesson, state, step, last_review, modified
│       └── source                 ── 'archive' | 'current'
│
├── TABLES (mutable, DuckDB-native)
│   │
│   ├── practice_log_live                              ── atomic staging-swap on ingest
│   │   ├── player_id          VARCHAR
│   │   ├── item_id            VARCHAR
│   │   ├── first_seen_at      TIMESTAMP
│   │   ├── last_seen_at       TIMESTAMP
│   │   ├── last_result        VARCHAR
│   │   ├── attempt_count      INTEGER
│   │   ├── correct_count      INTEGER
│   │   ├── season_id          VARCHAR
│   │   ├── plan_id            VARCHAR
│   │   ├── scope_type         VARCHAR
│   │   ├── sync_batch_id      VARCHAR
│   │   ├── schema_version     VARCHAR
│   │   └── synced_at          TIMESTAMP
│   │
│   ├── memory_state_current                           ── current season snapshot
│   │   ├── name               BIGINT
│   │   ├── season_seq         INTEGER
│   │   ├── subject            VARCHAR
│   │   ├── player             VARCHAR
│   │   ├── item_id            VARCHAR
│   │   ├── stage_id           VARCHAR
│   │   ├── stability          DOUBLE
│   │   ├── difficulty         DOUBLE
│   │   ├── next_review        TIMESTAMP
│   │   ├── lesson             VARCHAR
│   │   ├── state              TINYINT
│   │   ├── step               TINYINT
│   │   ├── last_review        TIMESTAMP
│   │   └── modified           TIMESTAMP
│   │
│   ├── practice_daily_agg                             ── rebuilt by refresh-aggregates
│   │   ├── date               DATE
│   │   ├── player_id          VARCHAR
│   │   ├── season_id          VARCHAR
│   │   ├── plan_id            VARCHAR
│   │   ├── total_attempts     BIGINT
│   │   ├── total_correct      BIGINT
│   │   └── unique_items       BIGINT
│   │
│   └── practice_monthly_agg                           ── rebuilt by refresh-aggregates
│       ├── year_month         VARCHAR                 ── 'YYYY-MM'
│       ├── player_id          VARCHAR
│       ├── season_id          VARCHAR
│       ├── plan_id            VARCHAR
│       ├── total_attempts     BIGINT
│       ├── total_correct      BIGINT
│       ├── unique_items       BIGINT
│       └── active_days        BIGINT
│
└── HANDOFF (archive ↔ live dedup)
    │
    ├── date-range mode ───► DELETE FROM practice_log_live
    │                        WHERE {date_column} BETWEEN from AND to
    │
    └── season mode ───────► DELETE FROM memory_state_current
                             WHERE season_seq = ?
```

---

## D. Dimension Reuse Matrix

```
                            dim_      dim_          dim_     dim_       dim_      dim_player_
                           player   review_item    season    plan      lesson     history
                           (v3)       (v2)          (v1)     (v1)      (v1)        (v1)
                          ───────  ───────────    ───────  ───────   ─────────  ─────────────
Practice Log (archive)      X          X             X        X
Memory State (archive)      X                        X
Interaction Log (archive)   X                        X        X         X
Task Run Log (archive)
Practice Log (live sync)    X          X             X        X
Structure Progress (snap)
Dimension Refresh (daily)   X          X             X        X         X            X
```

## E. Dimension Refresh Pipeline

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                   Dimension Refresh Triggers                        │
 │                                                                     │
 │  doc_events (real-time, deduplicated via frappe.enqueue)            │
 │  ├── Memora Player Profile  ──► dim_player + dim_player_history    │
 │  ├── Memora Academic Plan   ──► dim_plan                           │
 │  ├── Memora Season          ──► dim_season                         │
 │  ├── Memora Review Item     ──► dim_review_item                    │
 │  └── Memora Lesson          ──► dim_lesson                         │
 │                                                                     │
 │  cron (daily safety net at 04:15)                                   │
 │  └── reconcile_dimensions   ──► all 6 dimensions full refresh      │
 │                                                                     │
 │  Flow: SQL query ──► PyArrow table ──► Parquet ──► rsync/SSH       │
 └──────────────────────────────────────────────────────────────────────┘
```

## F. Hive Partition Strategies

```
Partitioning Type      Entity                  Column           Directory Pattern
─────────────────────  ──────────────────────  ───────────────  ────────────────────────────
DATE-PARTITIONED       practice_log            last_seen_at     year=YYYY/month=MM/day=DD/
DATE-PARTITIONED       interaction_log         timestamp        year=YYYY/month=MM/day=DD/
DATE-PARTITIONED       task_run_log            completed_at     year=YYYY/month=MM/day=DD/
VALUE-PARTITIONED      memory_state            season_seq       season_seq=N/
VALUE-PARTITIONED      structure_progress      snapshot_date    snapshot_date=YYYY-MM-DD/
```

## G. Archive Job Lifecycle (with CLI commands)

```
                    ┌──────────────────────────────────────────────────────────┐
                    │              Archive Job Stages                          │
                    │                                                          │
 Scheduler ──►  Pending ──► Processing ──► Exported ──► Transferred           │
                    │            │              │             │                │
                    │         export          rsync       ingest-archive       │
                    │        Parquet +       to analytics + verify             │
                    │        manifest        server            │               │
                    │                                       Ingested          │
                    │                                          │               │
                    │                          handoff (date-range or season)  │
                    │                         + refresh-recent  (best-effort)  │
                    │                         + refresh-aggregates (best-effort)
                    │                                          │               │
                    │                                      Completed          │
                    │                                          │               │
                    │                                  ┌───────┴──────┐       │
                    │                                  │  if Delete   │       │
                    │                                  │  post_action │       │
                    │                                  └───────┬──────┘       │
                    │                                          │               │
                    │                                       Purged            │
                    │                                    (batched DEL         │
                    │                                     10k + 2s)           │
                    │                                                          │
                    │   On error at any stage ──► Failed (3 retries)          │
                    └──────────────────────────────────────────────────────────┘

Live Sync Jobs (LSYNC):

 Trigger ──►  Pending ──► Processing ──► Exported ──► Transferred
                   │            │              │             │
                   │         export          rsync       ingest-live
                   │        Parquet +       to analytics + verify
                   │        manifest        server            │
                   │                                       Ingested
                   │                                          │
                   │                                      Completed
                   │                                   (no handoff/refresh)
                   │
                   │   On error at any stage ──► Failed (3 retries)
```

## H. Analytics CLI Commands (production contract)

These 7 commands are the only ones called by the production executor over SSH.

```
memora-analytics
│
├── ingest-archive   --batch-dir PATH                              ── Hive-partition fact + copy dims + refresh views
│                                                                     Called by: run.py (_process_transferred_jobs)
│
├── ingest-live      --batch-dir PATH                              ── Atomic staging-swap into practice_log_live
│                                                                     Called by: live_sync.py (_process_transferred_live_jobs)
│
├── verify                                                         ── Health checks (checksums, dupes, dims, partitions)
│                                                                     Called by: run.py + live_sync.py (post-ingest)
│
├── handoff          --archive-batch-dir PATH                      ── Remove archived rows from live tables (dedup)
│                    --date-column COL --from DATE --to DATE           (date-range mode: practice_log, interaction_log, task_run_log)
│                    --season-seq N --archive-type TYPE                (season mode: memory_state)
│                                                                     Called by: run.py (_process_ingested_jobs)
│
├── refresh-recent   --archive-type TYPE --window-days N           ── Rebuild rolling recent-N-days layer (default 90)
│                                                                     Called by: run.py (_process_ingested_jobs, best-effort)
│
└── refresh-aggregates --archive-type TYPE                         ── Rebuild daily + monthly aggregate tables
                                                                      Called by: run.py (_process_ingested_jobs, best-effort)
```

## I. SQL-to-Parquet Type Mapping

```
SQL Type                    Arrow / Parquet Type     DuckDB Type
──────────────────────────  ────────────────────     ───────────
INT, BIGINT, TINYINT        int64                    INTEGER / BIGINT / TINYINT
FLOAT, DOUBLE, DECIMAL      float64                  DOUBLE
DATETIME, TIMESTAMP         timestamp("us")          TIMESTAMP
DATE                        date32                   DATE
VARCHAR, TEXT, ENUM          string                   VARCHAR
```
