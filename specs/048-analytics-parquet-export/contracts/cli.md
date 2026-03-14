# CLI Contract: analytics_exporter (v2)

## Invocation

```bash
python3 -m analytics_exporter
```

Runs all 18 datasets grouped by category: dimensions -> core facts -> supplementary facts.

## Environment Variables

### Required

| Variable | Description | Example |
|---|---|---|
| `DB_HOST` | MariaDB host | `127.0.0.1` |
| `DB_PORT` | MariaDB port | `3306` |
| `DB_USER` | MariaDB user | `_9be6802bfff1e8ca` |
| `DB_PASSWORD` | MariaDB password | `zjAACevKaH5VGVP2` |
| `DB_NAME` | Database name | `_9be6802bfff1e8ca` |

### Optional

| Variable | Default | Description |
|---|---|---|
| `ANALYTICS_OUTPUT_PATH` | `analytics_exports` | Directory for output Parquet + manifest files |
| `ANALYTICS_SCHEMA_PATH` | `analytics_exporter/schemas` | Path to YAML schema definitions |
| `ANALYTICS_CHUNK_SIZE` | `50000` | Rows per streaming fetch batch |
| `ANALYTICS_LOG_PATH` | `logs/analytics_exporter.log` | Log file path |
| `ANALYTICS_MODE` | `auto` | `auto`, `full`, or `incremental` |
| `ANALYTICS_DATASETS` | *(all)* | Comma-separated list of datasets to run |
| `ANALYTICS_INTERACTION_FROM` | *(today - 30 days)* | ISO date for interaction log start filter |
| `ANALYTICS_INTERACTION_TO` | *(today)* | ISO date for interaction log end filter |

## Dataset Names

Valid values for `ANALYTICS_DATASETS`:

**Dimensions**: `dim_player`, `dim_content_hierarchy`, `dim_review_item`, `dim_season`, `dim_academic_plan`

**Core Facts**: `fact_interaction`, `fact_memory_state`, `fact_practice`, `fact_subscription`, `fact_voucher`, `fact_challenge_attempt`, `fact_challenge_detail`

**Supplementary**: `fact_structure_progress`, `fact_player_wallet`, `dim_lesson_stage`, `fact_content_report`, `fact_live_challenge_event`, `fact_live_challenge_participation`, `fact_archive_job`, `fact_task_run_log`, `fact_build_queue`

**Multi-file groups** (selecting any member exports the full group):
- `fact_challenge` -> exports both `fact_challenge_attempt` + `fact_challenge_detail`
- `fact_live_challenge` -> exports both event + participation files
- `fact_task_run` -> exports both task run log + build queue files

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All exports succeeded, all DQ rules passed |
| `1` | One or more exports failed (see log for details) |
| `2` | Configuration error (missing required env var, unknown dataset name) |

## Output

On each run:
- Writes/updates Parquet files to `ANALYTICS_OUTPUT_PATH/`
- Writes per-dataset manifest files: `{dataset}.manifest.json`
- Updates `.watermark.json` if fact_practice ran successfully
- Logs one line per dataset: `[dataset] rows=N duration=Xs status=ok|failed`

## Example: Full Run

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 \
  DB_USER=_9be6802bfff1e8ca DB_PASSWORD=zjAACevKaH5VGVP2 \
  DB_NAME=_9be6802bfff1e8ca \
  ANALYTICS_OUTPUT_PATH=/data/memora/analytics_exports \
  python3 -m analytics_exporter
```

## Example: Dimensions Only

```bash
DB_HOST=127.0.0.1 ... \
  ANALYTICS_DATASETS=dim_player,dim_content_hierarchy,dim_review_item,dim_season,dim_academic_plan \
  python3 -m analytics_exporter
```

## Example: Interaction Log with Custom Date Range

```bash
DB_HOST=127.0.0.1 ... \
  ANALYTICS_DATASETS=fact_interaction \
  ANALYTICS_INTERACTION_FROM=2026-02-01 \
  ANALYTICS_INTERACTION_TO=2026-03-01 \
  python3 -m analytics_exporter
```

## Example: Force Full Practice Log Rescan

```bash
DB_HOST=127.0.0.1 ... \
  ANALYTICS_DATASETS=fact_practice \
  ANALYTICS_MODE=full \
  python3 -m analytics_exporter
```

## Manifest Output

Each dataset produces a sidecar `{dataset}.manifest.json`:

```json
{
  "manifest_version": "1.0",
  "dataset_key": "dim_player",
  "kind": "analytics",
  "schema_version": "1.0",
  "created_at": "2026-03-13T12:00:00Z",
  "source": "memora_admin",
  "files": [
    {
      "filename": "dim_player.parquet",
      "row_count": 364,
      "checksum": "sha256:abc123def456...",
      "size_bytes": 12345
    }
  ]
}
```

Multi-file datasets include multiple entries in the `files` array.
