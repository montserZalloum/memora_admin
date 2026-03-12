# Archivable & Transferable Parquet Data Hierarchy

## Pipeline Overview

```
Memora Admin (Source)
│
├── ARCHIVE Pipeline ──── rsync/SSH ────► Analytics Server (DuckDB)
├── LIVE SYNC Pipeline ── rsync/SSH ────► Analytics Server (DuckDB)
└── SNAPSHOT Pipeline ─── rsync/SSH ────► Analytics Server (DuckDB)
```

## Complete Parquet Output Tree

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
                    ┌──────────────────────────────────────────────────┐
                    │              Archive Job Stages                  │
                    │                                                  │
 Scheduler ──►  Pending ──► Processing ──► Exported ──► Transferred   │
                    │            │              │             │        │
                    │         export          rsync        remote      │
                    │        Parquet +       to analytics   checksum   │
                    │        manifest        server         verify     │
                    │                                        │        │
                    │                                     Ingested    │
                    │                                        │        │
                    │                                   DuckDB load   │
                    │                                   + handoff     │
                    │                                        │        │
                    │                                    Completed    │
                    │                                        │        │
                    │                                ┌───────┴──────┐ │
                    │                                │  if Delete   │ │
                    │                                │  post_action │ │
                    │                                └───────┬──────┘ │
                    │                                        │        │
                    │                                     Purged      │
                    │                                  (batched DEL   │
                    │                                   10k + 2s)     │
                    │                                                  │
                    │   On error at any stage ──► Failed (3 retries)  │
                    └──────────────────────────────────────────────────┘
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
