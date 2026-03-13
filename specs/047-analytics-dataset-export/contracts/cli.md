# CLI Contract: analytics_exporter

## Invocation

```bash
python3 -m analytics_exporter
```

Runs all exports in order: practice_log → item_mapping → subjects → tracks → units → topics → lessons → seasons → grades → majors → academic_plans → grade_majors.

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
| `ANALYTICS_OUTPUT_PATH` | `analytics_exports` | Directory for output Parquet files; created if absent |
| `ANALYTICS_SCHEMA_PATH` | `analytics_exporter/schemas` | Path to YAML schema definitions |
| `ANALYTICS_CHUNK_SIZE` | `50000` | Rows per streaming fetch batch |
| `ANALYTICS_LOG_PATH` | `logs/analytics_exporter.log` | Log file path |
| `ANALYTICS_MODE` | `auto` | `auto` (incremental if watermark exists, else full), `full` (always full scan), `incremental` (fail if no watermark) |
| `ANALYTICS_DATASETS` | *(all)* | Comma-separated list of datasets to run (e.g., `practice_log,seasons`) |

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All exports succeeded, all DQ rules passed |
| `1` | One or more exports failed (see log for details) |
| `2` | Configuration error (missing required env var, invalid path) |

## Output

On each run:
- Writes/updates Parquet files to `ANALYTICS_OUTPUT_PATH/`
- Updates `.watermark.json` if practice_log ran successfully
- Logs one line per dataset: `[dataset] rows=N duration=Xs status=ok|failed`
- On failure: logs `[dataset] error=<message> rows_at_failure=N`

## Example: Full Run

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 \
  DB_USER=_9be6802bfff1e8ca DB_PASSWORD=zjAACevKaH5VGVP2 \
  DB_NAME=_9be6802bfff1e8ca \
  ANALYTICS_OUTPUT_PATH=/data/memora/analytics_exports \
  python3 -m analytics_exporter
```

## Example: Incremental Practice Log Only

```bash
DB_HOST=127.0.0.1 ... \
  ANALYTICS_DATASETS=practice_log \
  ANALYTICS_MODE=incremental \
  python3 -m analytics_exporter
```

## Example: Full Scan Override

```bash
DB_HOST=127.0.0.1 ... \
  ANALYTICS_MODE=full \
  python3 -m analytics_exporter
```

## Scheduled Invocation (Frappe)

Add to `memora_admin/memora_admin/config/scheduler.json` or call via `frappe.utils.background_jobs`:

```python
# In a Frappe scheduled job (daily):
import subprocess
result = subprocess.run(
    ["python3", "-m", "analytics_exporter"],
    env={**os.environ, "ANALYTICS_OUTPUT_PATH": "/data/memora/analytics_exports"},
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    frappe.log_error(result.stderr, "Analytics Exporter Failed")
```
