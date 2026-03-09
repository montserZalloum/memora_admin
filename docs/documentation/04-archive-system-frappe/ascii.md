Memora Data Movement & Analytics Platform
│
├── 1) Core Goal
│   ├── Move selected analytics workload away from production
│   ├── Keep current-season reporting available
│   ├── Archive ended seasons safely
│   └── Serve analytics from a separate analytics server
│
├── 2) Main Domains
│   ├── Live Sync
│   ├── Archive
│   ├── Purge
│   └── Analytics Serving
│
├── 3) Source of Truth Rules
│   ├── Current season
│   │   └── Production DB
│   └── Archived seasons
│       └── Archived datasets on analytics server
│
├── 4) High-Level Architecture
│   ├── Production Server
│   │   ├── MariaDB
│   │   ├── Frappe Control Plane
│   │   ├── Live Sync Executor
│   │   └── Archive Executor
│   │
│   ├── Analytics Server
│   │   ├── DuckDB
│   │   ├── Live snapshot datasets
│   │   └── Archived Parquet batches
│   │
│   └── Transport Layer
│       ├── Push model only
│       ├── SSH
│       ├── rsync / scp
│       └── One-way from production to analytics
│
├── 5) Control Plane (inside Frappe)
│   ├── Archive Job DocType
│   │   ├── source_doctype
│   │   ├── archive_scope
│   │   ├── status
│   │   ├── priority
│   │   ├── schema_version
│   │   ├── row_count
│   │   ├── file_path
│   │   ├── file_checksum
│   │   ├── file_size_bytes
│   │   ├── started_at
│   │   ├── completed_at
│   │   ├── claimed_at
│   │   ├── retry_count
│   │   ├── error_log
│   │   ├── post_archive_action
│   │   ├── source_deleted
│   │   ├── sync_paused
│   │   └── meta
│   │
│   ├── Admin Actions
│   │   ├── Retry failed archive job
│   │   ├── Trigger live sync manually
│   │   └── View job/sync status
│   │
│   └── Future Dashboard
│       ├── Live Sync Status
│       ├── Archive Status
│       ├── Transfer Status
│       ├── Ingest Status
│       └── Purge Status
│
├── 6) Shared Schema Registry
│   ├── dimensions/
│   │   ├── player.v2.yaml
│   │   └── review_item.v1.yaml
│   │
│   ├── archive_types/
│   │   └── practice_log.v1.yaml
│   │
│   └── sync_types/
│       └── practice_log_live.v1.yaml
│
├── 7) Live Sync
│   ├── Goal
│   │   └── Daily full snapshot of selected tables only
│   │
│   ├── Scope
│   │   ├── Not the whole production database
│   │   ├── Only explicitly selected tables
│   │   ├── Only tables needed for analytics
│   │   └── Defined through sync_types YAML files
│   │
│   ├── Design Choice
│   │   ├── Full snapshot
│   │   ├── Not incremental
│   │   ├── No triggers
│   │   ├── No _sync_changed_at
│   │   ├── No change log
│   │   └── New snapshot replaces old snapshot
│   │
│   ├── Schedule
│   │   ├── Automatic daily cron
│   │   │   └── 0 3 * * * /usr/bin/python3 /opt/memora-sync/run.py
│   │   └── Manual Sync Now button
│   │       └── Cooldown protection
│   │
│   ├── Inputs
│   │   ├── Selected fact tables only
│   │   │   └── Example: tabMemora Practice Log
│   │   └── Related dimensions only for those tables
│   │       ├── player
│   │       └── review_item
│   │
│   ├── Sync Flow
│   │   ├── Acquire file lock
│   │   ├── Read sync YAML definitions
│   │   ├── For each configured sync type
│   │   │   ├── Check sync_paused
│   │   │   ├── Read full fact data
│   │   │   ├── Extract distinct referenced IDs
│   │   │   ├── Build dimension snapshots
│   │   │   ├── Export Parquet files
│   │   │   ├── Verify files
│   │   │   ├── Build manifest.json
│   │   │   ├── Transfer to analytics server
│   │   │   ├── Confirm transfer_succeeded
│   │   │   ├── Load into DuckDB
│   │   │   ├── Confirm ingest_succeeded
│   │   │   └── Confirm analytics_visible
│   │   └── Release file lock
│   │
│   ├── Output Structure
│   │   └── /var/sync/memora/live/
│   │       └── <sync_type>/
│   │           ├── manifest.json
│   │           ├── fact_*.parquet
│   │           └── dim_*.parquet
│   │
│   ├── Publish Strategy
│   │   ├── Build in staging path
│   │   ├── Verify completely
│   │   ├── Transfer completely
│   │   ├── Ingest safely
│   │   └── Atomic swap to visible live dataset
│   │
│   └── Retention Rule
│       ├── Keep only latest live snapshot
│       └── Do not keep historical live versions
│
├── 8) Archive System
│   ├── Goal
│   │   └── Export ended-season data for historical analytics
│   │
│   ├── Trigger
│   │   ├── Season ends
│   │   └── Control plane creates Pending archive job
│   │
│   ├── Executor
│   │   ├── Separate Python executor
│   │   ├── Separate virtualenv
│   │   ├── No Frappe bootstrap
│   │   ├── Direct DB connection
│   │   └── Reads schema registry from configurable path
│   │
│   ├── Archive Flow
│   │   ├── Acquire file lock
│   │   ├── Find Pending jobs
│   │   ├── Atomic DB claim
│   │   ├── Read meta.query_filter
│   │   ├── Read fact rows for archive scope
│   │   ├── Extract distinct referenced IDs
│   │   ├── Build dimension snapshots
│   │   ├── Export Parquet files
│   │   ├── Verify files
│   │   ├── Build manifest.json
│   │   ├── Transfer to analytics server
│   │   ├── Confirm transfer_succeeded
│   │   ├── Load into DuckDB
│   │   ├── Confirm ingest_succeeded
│   │   ├── Confirm analytics_visible
│   │   └── Mark archive ready for completion/purge
│   │
│   ├── Archive Output
│   │   └── /var/archive/memora/
│   │       └── batch_<scope>_<type>/
│   │           ├── manifest.json
│   │           ├── fact_*.parquet
│   │           └── dim_*.parquet
│   │
│   ├── State Machine
│   │   ├── Pending
│   │   ├── Processing
│   │   ├── Exported
│   │   ├── Transferred
│   │   ├── Ingested
│   │   ├── Completed
│   │   ├── Purged
│   │   └── Failed
│   │
│   └── Manifest Contents
│       ├── batch_id
│       ├── source_doctype
│       ├── archive_scope
│       ├── schema_version
│       ├── created_at
│       └── files[]
│
├── 9) Coordination Between Live Sync and Archive
│   ├── Problem
│   │   └── Archive and live sync may overlap on the same logical scope
│   │
│   ├── Coordination Mechanism
│   │   └── sync_paused
│   │
│   ├── Scope Definition
│   │   └── Derived from archive job meta.query_filter
│   │
│   └── Protected Order
│       ├── Archive starts
│       ├── sync_paused = true for that scope
│       ├── Live sync skips paused scope
│       ├── Archive exports data
│       ├── Archive transfers data
│       ├── Archive ingests into DuckDB
│       ├── Archive becomes analytics-visible
│       ├── Analytics side removes overlapping live data for that scope
│       ├── Production purge runs
│       └── sync_paused cleared / scope no longer part of live data
│
├── 10) Purge System
│   ├── Goal
│   │   └── Delete archived data from production only after safe completion
│   │
│   ├── Preconditions
│   │   ├── transfer_succeeded
│   │   ├── ingest_succeeded
│   │   └── analytics_visible
│   │
│   ├── Purge Flow
│   │   ├── Find eligible archive jobs
│   │   ├── Check post_archive_action = Delete
│   │   ├── Delete in batches
│   │   ├── Sleep between batches
│   │   ├── Track purge_progress
│   │   ├── Resume if interrupted
│   │   ├── Mark source_deleted = 1
│   │   └── Mark job = Purged
│   │
│   └── Purge Config
│       ├── PURGE_BATCH_SIZE
│       └── PURGE_BATCH_SLEEP_SECONDS
│
├── 11) Analytics Server Model
│   ├── Live Data
│   │   ├── Daily full snapshots
│   │   ├── Only for selected tables
│   │   ├── Replaced on each sync
│   │   └── Represents current season
│   │
│   ├── Archived Data
│   │   ├── Immutable Parquet batches
│   │   ├── One batch per archive scope
│   │   └── Represents past seasons
│   │
│   └── Query Layer
│       ├── Query current season from live datasets
│       ├── Query old seasons from archived datasets
│       └── Union / compare across seasons
│
├── 12) Security & Protection
│   ├── Archive Files at Rest
│   │   └── Encrypted at rest
│   │
│   ├── SSH Security
│   │   ├── Key pair authentication
│   │   ├── Private key on production only
│   │   └── Analytics server has no reverse access
│   │
│   ├── Filesystem Protection
│   │   └── Restricted permissions on output directories
│   │
│   └── Credentials
│       ├── Stored in env/config
│       └── Validated before execution
│
├── 13) Runtime & Configuration
│   ├── Executor Environment
│   │   ├── Separate virtualenv
│   │   ├── Separate Linux user
│   │   ├── Optional systemd limits
│   │   └── No dependence on bench runtime
│   │
│   ├── Key Config
│   │   ├── SCHEMA_REGISTRY_PATH
│   │   ├── ARCHIVE_OUTPUT_PATH
│   │   ├── LIVE_SYNC_OUTPUT_PATH
│   │   ├── SSH_HOST
│   │   ├── SSH_USER
│   │   ├── SSH_KEY_PATH
│   │   ├── REMOTE_PATH
│   │   ├── PURGE_BATCH_SIZE
│   │   └── PURGE_BATCH_SLEEP_SECONDS
│   │
│   └── Paths
│       ├── Archive path configurable
│       └── Live sync path configurable
│
├── 14) Reliability & Safety
│   ├── File Lock
│   │   └── Prevent concurrent executor runs
│   │
│   ├── Atomic DB Claim
│   │   └── Prevent duplicate archive processing
│   │
│   ├── Idempotency
│   │   ├── Completed jobs are ignored
│   │   ├── Failed jobs can retry
│   │   └── Partial staging is cleaned before retry
│   │
│   ├── Retry Policy
│   │   ├── Automatic retries
│   │   ├── Max retries
│   │   └── Manual retry from admin UI
│   │
│   ├── Stuck Job Detection
│   │   └── Processing timeout marks job as Failed
│   │
│   └── Observability
│       ├── Structured logs
│       ├── started_at
│       ├── completed_at
│       ├── duration_seconds
│       ├── row_count
│       ├── retry_count
│       └── last_error
│
└── 15) Future Evolution
    ├── Monitoring dashboard
    ├── More sync types
    ├── More archive types
    ├── Incremental live sync later if needed
    ├── Move to ClickHouse if scale grows
    └── Optional external storage (S3 / MinIO)