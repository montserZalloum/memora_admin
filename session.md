# Session Handoff — Archive Pipeline E2E Debug

## What We're Doing

Running the **archive pipeline end-to-end** against the real analytics server at `187.77.93.112`:
1. Export fact + dimension Parquet files from MariaDB
2. Transfer via rsync+SSH to analytics server
3. Analytics server ingests into DuckDB via `memora-analytics ingest-archive`
4. Mark job Completed in Frappe

Current test job: **ARCH-00009** for season **SEAS-00623** with 8 real practice log rows for PLAYER-00320.

---

## Current State

The job is stuck at **Pending** (retry_count=1), failing at ingestion on the analytics server.

Last error (from `tabMemora Archive Job.error_log` on ARCH-00009):
```
IngestionError: Archive ingestion failed: load error: Binder Error: Column "player_id" does not exist on left side of join!
```

---

## Root Cause (Identified, NOT YET FIXED)

The analytics server's `build_curated_and_marts()` in:
`/home/memora-analytics/backend/ingestion/transforms/practice_log.py`

checks for a live view:
```python
has_live = _relation_exists(conn, "raw_live_practice_log_current")
```

This returns **True** because an old live view exists from an earlier stub test ingestion (March 10, ~10:41 AM) with columns `date, note, value`. This stale view points to `raw_live_practice_log__v20260310_104144`.

When `has_live=True`, the code unions in `_LIVE_SELECT_WITH_ANTIJOIN`:
```sql
FROM raw_live_practice_log_current l
LEFT JOIN raw_archive_practice_log_current a USING (player_id, item_id)
```
But `l` has `date, note, value` — NOT `player_id`. Hence the error.

Verify on analytics server:
```bash
ssh -i /etc/memora-archive/id_rsa_analytics analytics@187.77.93.112 \
  "/opt/analytics/venv/bin/python3 -c \"
import sys; sys.path.insert(0, '/home/memora-analytics')
import duckdb
conn = duckdb.connect('/data/analytics/memora.duckdb')
print(conn.execute('DESCRIBE raw_live_practice_log_current').fetchall())
conn.close()
\""
# Should show: [('date', ...), ('note', ...), ('value', ...)]
```

---

## Fix Required

### Step A — Clean stale DuckDB tables on analytics server

SSH to analytics server and run:
```bash
ssh -i /etc/memora-archive/id_rsa_analytics analytics@187.77.93.112
cd /home/memora-analytics
/opt/analytics/venv/bin/python3 << 'PYEOF'
import sys
sys.path.insert(0, '/home/memora-analytics')
import duckdb
conn = duckdb.connect('/data/analytics/memora.duckdb')
stale = [
    'raw_live_practice_log__v20260310_104144',
    'raw_live_practice_log_current',
    'curated_practice_log__v20260310_104144',
    'curated_practice_log__v20260310_104208',
    'curated_practice_log_current',
    'mart_practice_log__v20260310_104144',
    'mart_practice_log__v20260310_104208',
    'mart_practice_log_current',
]
for t in stale:
    conn.execute(f'DROP VIEW IF EXISTS {t}')
    conn.execute(f'DROP TABLE IF EXISTS {t}')
    print(f'Dropped {t}')
conn.close()
PYEOF
```

### Step B — Add schema-compatibility guard in `practice_log.py`

In `/home/memora-analytics/backend/ingestion/transforms/practice_log.py`,
find `build_curated_and_marts` (around line 660) and change:

**Current:**
```python
has_live = _relation_exists(conn, "raw_live_practice_log_current")
```

**Replace with:**
```python
def _view_has_column(conn, view_name, column):
    try:
        cols = {r[0] for r in conn.execute(f'DESCRIBE {view_name}').fetchall()}
        return column in cols
    except Exception:
        return False

has_live = _relation_exists(conn, "raw_live_practice_log_current") and _view_has_column(conn, "raw_live_practice_log_current", "player_id")
```

This prevents using the stale live view if it doesn't have `player_id`.

NOTE: `_view_has_column` helper can be defined right inside the function or at module level near `_relation_exists`.

### Step C — Reset and run

```bash
# On the Frappe server (bench root):
cd /home/corex/aurevia-bench
env/bin/python -c "
import frappe
frappe.init(site='x.conanacademy.com', sites_path='/home/corex/aurevia-bench/sites')
frappe.connect()
frappe.db.sql(\"UPDATE \`tabMemora Archive Job\` SET status='Pending', retry_count=0, exported_at=NULL, transferred_at=NULL, ingested_at=NULL, completed_at=NULL, error_log=NULL WHERE name='ARCH-00009'\")
frappe.db.commit()
frappe.destroy()
print('Reset done')
"

# Run executor:
set -a && source /etc/memora-archive.env && set +a && python3 -m archive_executor.run
```

Expected result: ARCH-00009 status → Completed.

---

## What Was Fixed In This Session (Summary)

### Our side (memora_admin)

1. **`meta` field renamed to `job_meta`** in both DocType JSONs (Frappe's `Document.meta` property conflicts with custom field named `meta`):
   - `memora_admin/memora_admin/doctype/memora_archive_job/memora_archive_job.json`
   - `memora_admin/memora_admin/doctype/memora_live_sync_job/memora_live_sync_job.json`
   - All Python references updated across 5 files
   - `bench --site x.conanacademy.com migrate` was run to apply column rename

2. **`archive_trigger.py`**: Removed `is_published` check and 90-day lookback

3. **`archive_executor/exporter.py`**:
   - `_coerce_value`: handles `str→int/float/datetime` for SSDictCursor
   - Fixed `_inject_metadata_into_rows` return value (was discarded)
   - Added `fact_sql` template support for JOIN-enriched queries

4. **`archive_executor/ingestion.py`**: Fixed `_parse_remote_json` (was using `rfind` finding last `{` inside nested JSON)

5. **`archive_executor/run.py`**: Added 0-row short-circuit (skip transfer/ingest when fact_row_count=0)

6. **`archive_executor/manifest.py`**: Rewritten to match analytics server Pydantic model schema exactly

7. **Dimension schemas fixed:**
   - `archive_schemas/dimensions/player.v2.yaml`: Fixed `academic_plan → plan`
   - `archive_schemas/dimensions/season.v1.yaml`: Added custom query outputting `season_id` (alias for `name`)
   - `archive_schemas/dimensions/plan.v1.yaml`: Added custom query outputting `plan_id` (alias for `name`)
   - `archive_schemas/dimensions/review_item.v1.yaml`: Rewrote — `subject`, `stage_type AS item_type`, `NULL AS difficulty`

8. **Archive/sync type schemas:**
   - `archive_schemas/archive_types/practice_log.v1.yaml`: Added `season_id`/`plan_id` to fact_columns + `fact_sql` for JOIN enrichment
   - `archive_schemas/sync_types/practice_log_live.v1.yaml`: Same

9. **`archive_trigger.py` and `live_sync_trigger.py`**: Pass `fact_sql` through to job meta

### Analytics server side

10. **`/home/memora-analytics/backend/ingestion/transforms/practice_log.py`**:
    - Fixed `_ensure_raw_current_views` to use `CREATE OR REPLACE VIEW` (was `CREATE VIEW` with `if not exists` guard — stale views were never updated on second+ runs)

---

## Environment

- **Env file**: `/etc/memora-archive.env`
- **SSH key**: `/etc/memora-archive/id_rsa_analytics`
- **Analytics server**: `187.77.93.112` (user: `analytics`)
- **Analytics CLI**: `/opt/analytics/memora-analytics`
- **DuckDB path on analytics**: `/data/analytics/memora.duckdb`
- **Archive drop zone on analytics**: `/data/analytics/archives/`
- **Run executor**: `set -a && source /etc/memora-archive.env && set +a && python3 -m archive_executor.run`
- **Frappe site**: `x.conanacademy.com` at `/home/corex/aurevia-bench/sites`
