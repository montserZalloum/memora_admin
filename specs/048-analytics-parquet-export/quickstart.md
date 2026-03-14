# Quickstart: Analytics Parquet Dataset Export (v2)

## What This Does

`analytics_exporter` is a standalone Python CLI that exports 18 datasets (~22 Parquet files) from MariaDB to `analytics_exports/` for the analytics server. Each file is accompanied by a `{dataset}.manifest.json` with SHA-256 checksum and row count.

| Category | Datasets | Files | Update Mode |
|---|---|---|---|
| Dimensions | dim_player, dim_content_hierarchy, dim_review_item, dim_season, dim_academic_plan | 5 | Full snapshot |
| Core Facts | fact_interaction, fact_memory_state, fact_practice, fact_subscription, fact_voucher, fact_challenge (2 files) | 8 | Mixed (date-range, snapshot, incremental) |
| Supplementary | fact_structure_progress, fact_player_wallet, dim_lesson_stage, fact_content_report, fact_live_challenge (2 files), fact_archive_job, fact_task_run (2 files) | 9 | Full snapshot |

## Prerequisites

```bash
pip install pyarrow pymysql pyyaml
```

## First Run (Full Export)

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 \
  DB_USER=<user> DB_PASSWORD=<pass> DB_NAME=<db> \
  ANALYTICS_OUTPUT_PATH=analytics_exports \
  python3 -m analytics_exporter
```

Expected log output:

```
[dim_player]                          rows=364    duration=0s   status=ok
[dim_content_hierarchy]               rows=47     duration=0s   status=ok
[dim_review_item]                     rows=202    duration=0s   status=ok
[dim_season]                          rows=8      duration=0s   status=ok
[dim_academic_plan]                   rows=917    duration=0s   status=ok
[fact_interaction]                    rows=10906  duration=3s   status=ok
[fact_memory_state]                   rows=103    duration=0s   status=ok
[fact_practice]                       rows=20     duration=0s   status=ok  mode=full
[fact_subscription]                   rows=73     duration=0s   status=ok
[fact_voucher]                        rows=4872   duration=1s   status=ok
[fact_challenge_attempt]              rows=30     duration=0s   status=ok
[fact_challenge_detail]               rows=95     duration=0s   status=ok
[fact_structure_progress]             rows=124    duration=0s   status=ok
[fact_player_wallet]                  rows=330    duration=0s   status=ok
[dim_lesson_stage]                    rows=222    duration=0s   status=ok
[fact_content_report]                 rows=6      duration=0s   status=ok
[fact_live_challenge_event]           rows=2      duration=0s   status=ok
[fact_live_challenge_participation]   rows=1      duration=0s   status=ok
[fact_archive_job]                    rows=60     duration=0s   status=ok
[fact_task_run_log]                   rows=1735   duration=0s   status=ok
[fact_build_queue]                    rows=215    duration=0s   status=ok
```

## Output Directory Layout

```
analytics_exports/
├── dim_player.parquet
├── dim_player.manifest.json
├── dim_content_hierarchy.parquet
├── dim_content_hierarchy.manifest.json
├── dim_review_item.parquet
├── dim_review_item.manifest.json
├── dim_season.parquet
├── dim_season.manifest.json
├── dim_academic_plan.parquet
├── dim_academic_plan.manifest.json
├── fact_interaction.parquet
├── fact_interaction.manifest.json
├── fact_memory_state.parquet
├── fact_memory_state.manifest.json
├── fact_practice.parquet
├── fact_practice.manifest.json
├── fact_subscription.parquet
├── fact_subscription.manifest.json
├── fact_voucher.parquet
├── fact_voucher.manifest.json
├── fact_challenge.manifest.json              # Combined manifest for both files
├── fact_challenge_attempt.parquet
├── fact_challenge_detail.parquet
├── fact_structure_progress.parquet
├── fact_structure_progress.manifest.json
├── fact_player_wallet.parquet
├── fact_player_wallet.manifest.json
├── dim_lesson_stage.parquet
├── dim_lesson_stage.manifest.json
├── fact_content_report.parquet
├── fact_content_report.manifest.json
├── fact_live_challenge.manifest.json         # Combined manifest for both files
├── fact_live_challenge_event.parquet
├── fact_live_challenge_participation.parquet
├── fact_archive_job.parquet
├── fact_archive_job.manifest.json
├── fact_task_run.manifest.json               # Combined manifest for both files
├── fact_task_run_log.parquet
├── fact_build_queue.parquet
└── .watermark.json                           # Incremental state for fact_practice
```

## Key Differences from v1 (047)

| Aspect | v1 (047) | v2 (048) |
|---|---|---|
| Datasets | 12 | 18 (~22 files) |
| Manifests | None | SHA-256 + row count per dataset |
| Content hierarchy | 5 separate files | 1 denormalized file |
| Academic context | 5 files (plans + grades + majors + grade_majors) | 1 denormalized file |
| Review items | 6 columns (mapping only) | 8 columns (+ question text, stage info) |
| New domains | None | Player profiles, interactions, memory state, subscriptions, vouchers, challenges, progress, wallets, stages, content reports, live challenges, archive jobs, task runs |

## Running Tests

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 \
  DB_USER=<user> DB_PASSWORD=<pass> DB_NAME=<db> \
  python3 -m pytest analytics_exporter/tests/ -v
```

## Key Design Notes

- **No Frappe dependency**: Pure PyMySQL + PyArrow. Can run outside Frappe environment.
- **READ COMMITTED isolation**: No table locks, no blocking concurrent writes.
- **Manifest integrity**: Every Parquet file has a sidecar manifest with SHA-256 checksum. The analytics server verifies checksums before ingesting.
- **Multi-file atomicity**: Datasets that produce 2 files (challenge, live_challenge, task_run) either both succeed or both fail.
- **Memory State handling**: `BIN_TO_UUID()` and `CAST(... AS DOUBLE)` in SQL ensure correct types without Python-side conversion.
- **Incremental practice log**: Same delta-merge strategy as v1 — watermark on `last_seen_at`.
- **Interaction log**: Date-range filtered (defaults to last 30 days).
