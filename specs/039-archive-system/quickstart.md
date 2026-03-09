# Quickstart: Memora Archive System

## Prerequisites

1. Frappe v15 bench with `memora_admin` app installed
2. Python 3.11+ available for standalone virtualenv
3. MariaDB accessible from the archive executor

## 1. Install the DocType

After pulling the branch, run:

```bash
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com migrate
```

This creates the `tabMemora Archive Job` table and applies the composite unique index.

## 2. Set Up the Executor Environment

```bash
# Create executor directory
sudo mkdir -p /opt/memora-archive
sudo chown $(whoami):$(whoami) /opt/memora-archive

# Create virtualenv
python3 -m venv /opt/memora-archive/venv

# Install dependencies
/opt/memora-archive/venv/bin/pip install pyarrow pandas pymysql pyyaml

# Create archive output directory
sudo mkdir -p /data/memora/archives
sudo chown $(whoami):$(whoami) /data/memora/archives
chmod 700 /data/memora/archives

# Create log directory
sudo mkdir -p /var/log/memora-archive
sudo chown $(whoami):$(whoami) /var/log/memora-archive
```

## 3. Configure Environment Variables

Create `/opt/memora-archive/.env`:

```bash
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME=your_site_db
ARCHIVE_OUTPUT_PATH=/data/memora/archives
SCHEMA_REGISTRY_PATH=/home/corex/aurevia-bench/apps/memora_admin/archive_schemas
LOG_PATH=/var/log/memora-archive
```

## 4. Copy Executor Script

```bash
# The executor script lives in the app repo but runs from /opt/memora-archive/
cp /home/corex/aurevia-bench/apps/memora_admin/archive_executor/run.py /opt/memora-archive/run.py
```

## 5. Set Up Cron

```bash
crontab -e
# Add:
0 2 * * * /opt/memora-archive/venv/bin/python /opt/memora-archive/run.py
```

## 6. Create Schema Registry

The YAML files live in the app repository:

```
memora_admin/archive_schemas/
├── dimensions/
│   ├── player.v1.yaml
│   └── review_item.v1.yaml
└── archive_types/
    └── practice_log.v1.yaml
```

## 7. Test Manually

```bash
# 1. Create a test archive job via bench console
bench --site x.conanacademy.com console
>>> job = frappe.get_doc({
...     "doctype": "Memora Archive Job",
...     "source_doctype": "Memora Practice Log",
...     "archive_scope": "SEAS-00027",
...     "schema_version": "v1",
...     "status": "Pending",
...     "post_archive_action": "Keep",
...     "meta": json.dumps({
...         "query_filter": {"date_from": "2025-01-01", "date_to": "2025-06-30", "filter_column": "last_seen_at"},
...         "export_columns": ["player_id", "item_id", "first_seen_at", "last_seen_at", "last_result", "attempt_count", "correct_count"],
...         "related_tables": [
...             {"entity": "player", "schema_version": "v1", "source_table": "tabMemora Player Profile", "join_column": "name", "fact_column": "player_id"},
...             {"entity": "review_item", "schema_version": "v1", "source_table": "tabMemora Review Item", "join_column": "item_id", "fact_column": "item_id"}
...         ],
...         "schema_snapshot": {"columns": [], "primary_key": ["player_id", "item_id"]}
...     })
... })
>>> job.insert(ignore_permissions=True)
>>> frappe.db.commit()

# 2. Run the executor manually
/opt/memora-archive/venv/bin/python /opt/memora-archive/run.py

# 3. Check the result
bench --site x.conanacademy.com console
>>> job = frappe.get_doc("Memora Archive Job", "ARCH-00001")
>>> print(job.status, job.row_count, job.file_path)

# 4. Verify output files
ls -la /data/memora/archives/ARCH-00001/
cat /data/memora/archives/ARCH-00001/manifest.json
```

## Key Files

| Component | Location |
|-----------|----------|
| DocType JSON | `memora_admin/memora_admin/doctype/memora_archive_job/memora_archive_job.json` |
| DocType Python | `memora_admin/memora_admin/doctype/memora_archive_job/memora_archive_job.py` |
| DocType JS | `memora_admin/memora_admin/doctype/memora_archive_job/memora_archive_job.js` |
| Archive trigger task | `memora_admin/tasks/archive_trigger.py` |
| Notification task | `memora_admin/tasks/archive_notify.py` |
| Schema registry | `archive_schemas/dimensions/*.yaml`, `archive_schemas/archive_types/*.yaml` |
| Executor script | `archive_executor/run.py` |
| Migration script | `memora_admin/patches/039_archive_job_unique_index.py` |
