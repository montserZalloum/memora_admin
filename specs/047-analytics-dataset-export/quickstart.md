# Quickstart: Educational Analytics Dataset Export

## What This Does

`analytics_exporter` is a standalone Python CLI that exports 12 Parquet datasets from MariaDB to `analytics_exports/` for the analytics server:

| File | Source | Update Mode |
|---|---|---|
| `practice_log.parquet` | tabMemora Practice Log | Incremental (delta merge) |
| `item_mapping.parquet` | tabMemora Review Item | Full snapshot |
| `subjects/tracks/units/topics/lessons.parquet` | Hierarchy tables | Full snapshot |
| `seasons/grades/majors/academic_plans/grade_majors.parquet` | Academic context | Full snapshot |

## Prerequisites

```bash
pip install pyarrow pymysql pyyaml
```

Or from the module requirements:

```bash
pip install -r analytics_exporter/requirements.txt
```

## First Run (Full Export)

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 \
  DB_USER=<user> DB_PASSWORD=<pass> DB_NAME=<db> \
  ANALYTICS_OUTPUT_PATH=analytics_exports \
  python3 -m analytics_exporter
```

Expected output (log lines):

```
[practice_log]    rows=1234567 duration=42s  status=ok  mode=full
[item_mapping]    rows=89012   duration=3s   status=ok
[subjects]        rows=5       duration=0s   status=ok
[tracks]          rows=18      duration=0s   status=ok
[units]           rows=72      duration=0s   status=ok
[topics]          rows=210     duration=0s   status=ok
[lessons]         rows=840     duration=0s   status=ok
[seasons]         rows=7       duration=0s   status=ok
[grades]          rows=12      duration=0s   status=ok
[majors]          rows=6       duration=0s   status=ok
[academic_plans]  rows=84      duration=0s   status=ok
[grade_majors]    rows=72      duration=0s   status=ok
```

## Subsequent Runs (Incremental)

After the first run, `.watermark.json` is written to `analytics_exports/`. On the next run:

- `practice_log.parquet` is updated incrementally: only rows with `last_seen_at > watermark` are queried, merged with the existing file, and rewritten.
- All other files are always full snapshots (small tables; no watermark needed).

## Run Only Specific Datasets

```bash
ANALYTICS_DATASETS=seasons,grades,majors python3 -m analytics_exporter
```

## Force Full Rescan of Practice Log

```bash
ANALYTICS_MODE=full python3 -m analytics_exporter
```

This ignores `.watermark.json` and re-exports all practice log rows.

## Output Directory Layout

```
analytics_exports/
├── practice_log.parquet       # ~GB range for large deployments
├── item_mapping.parquet       # KB–MB range
├── subjects.parquet
├── tracks.parquet
├── units.parquet
├── topics.parquet
├── lessons.parquet
├── seasons.parquet
├── grades.parquet
├── majors.parquet
├── academic_plans.parquet
├── grade_majors.parquet
└── .watermark.json            # Incremental state (hidden file)
```

## Running Tests

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 \
  DB_USER=<user> DB_PASSWORD=<pass> DB_NAME=<db> \
  python3 -m pytest analytics_exporter/tests/ -v
```

Tests use isolated test data with prefixed IDs and clean up after themselves.

## Key Design Notes

- **No Frappe dependency**: Pure PyMySQL + PyArrow. Can run outside Frappe environment.
- **READ COMMITTED isolation**: All connections use `SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED` — no table locks, no blocking concurrent writes.
- **Incremental practice log**: On delta runs, the exporter reads changed rows only, merges them into the existing Parquet (upsert by `(player_id, item_id)`), and rewrites the file. The output is always a complete snapshot.
- **Empty tables**: Always produces a valid Parquet file with correct schema even if the source table is empty.
- **Watermark safety**: Watermark is only updated after a successful export. An interrupted run safely retries in full mode.
- **Interaction Log**: NOT handled here — the existing archive pipeline (`interaction_log.v1.yaml`) handles it.
