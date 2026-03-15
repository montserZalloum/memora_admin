# Quickstart: Practice Arena V2

**Feature Branch**: `049-practice-arena-v2`
**Date**: 2026-03-14

---

## Prerequisites

- Python 3.11+
- Redis 7+ (with Streams support)
- MariaDB 10.6+
- Frappe v15 bench environment
- Cloudflare R2 credentials (production) or local storage (development)

---

## 1. Database Setup

The `tabPlayer Practice Summary` table is created automatically by `setup.py` during `bench migrate`. To create it manually:

```bash
bench --site {site} execute memora_admin.setup.create_player_practice_summary_table
```

### Verify Table Exists

```sql
DESCRIBE `tabPlayer Practice Summary`;
```

Expected columns: `player_id`, `track_id`, `subject_id`, `question_history`, `total_seen`, `total_correct`, `last_session_at`, `updated_at`.

---

## 2. Redis Streams Setup

The write queue consumer group is created automatically on FastAPI startup. To create manually:

```bash
redis-cli XGROUP CREATE memora:practice:write_queue practice-writers 0 MKSTREAM
```

### Verify Stream Exists

```bash
redis-cli XINFO STREAM memora:practice:write_queue
redis-cli XINFO GROUPS memora:practice:write_queue
```

---

## 3. Generate Practice Content (Map Files + Chunks)

### Generate for a Single Subject

```bash
bench --site {site} execute memora_admin.services.build.practice_content.generate_practice_content --args '["SUBJ-001"]'
```

### Generate for All Subjects

```bash
bench --site {site} execute memora_admin.services.build.practice_content.generate_all_practice_content
```

### Verify CDN Files

Development (local storage):
```bash
ls -la {bench_path}/sites/{site}/public/files/cdn/practice/maps/
ls -la {bench_path}/sites/{site}/public/files/cdn/practice/chunks/{subject_id}/
```

---

## 4. Backfill Player Summaries

One-time migration from existing Practice Log data:

```bash
bench --site {site} execute memora_admin.api.practice_summary.backfill_player_summaries --args '{"batch_size": 1000}'
```

### Monitor Progress

```bash
# Check row count
mysql -e "SELECT COUNT(*) FROM \`tabPlayer Practice Summary\`" {db_name}

# Compare with Practice Log distinct players
mysql -e "SELECT COUNT(DISTINCT player_id) FROM \`tabMemora Practice Log\`" {db_name}
```

---

## 5. Start the Background Writer

The practice writer runs as a Frappe scheduled task (every 1 minute). Verify it's registered in `hooks.py`:

```python
scheduler_events = {
    "cron": {
        "* * * * *": [
            # ... existing tasks ...
            "memora_admin.tasks.practice_writer.process_write_queue",
        ],
    },
}
```

### Manual Processing (for testing)

```bash
bench --site {site} execute memora_admin.tasks.practice_writer.process_write_queue
```

---

## 6. Test the V2 Endpoints

### Start a Session

```bash
curl -X POST http://localhost:8002/api/v2/practice/start \
  -H "Authorization: Bearer {jwt_token}" \
  -H "Content-Type: application/json" \
  -d '{
    "subject_id": "SUBJ-001",
    "track_ids": ["TRACK-A"],
    "unit_ids": null,
    "topic_ids": null
  }'
```

Expected response:
```json
{
  "session_active": true,
  "batch_seq": 0,
  "question_ids": ["uuid-1", "uuid-2", "...20 items"],
  "chunk_refs": [3, 7, 12],
  "total_available": 8500,
  "all_seen_warning": false
}
```

### Submit Answers

```bash
curl -X POST http://localhost:8002/api/v2/practice/submit \
  -H "Authorization: Bearer {jwt_token}" \
  -H "Content-Type: application/json" \
  -d '{
    "batch_seq": 0,
    "results": [
      {"item_id": "uuid-1", "is_correct": true},
      {"item_id": "uuid-2", "is_correct": false}
    ]
  }'
```

### Continue to Next Batch

```bash
curl -X POST http://localhost:8002/api/v2/practice/continue \
  -H "Authorization: Bearer {jwt_token}" \
  -H "Content-Type: application/json" \
  -d '{"batch_seq": 0}'
```

---

## 7. Running Tests

### Unit Tests (selection algorithm, scope filtering)

```bash
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_practice_selection.py -v
```

### Integration Tests (endpoints with real Redis)

```bash
python -m pytest fastapi_app/tests/test_practice_v2.py -v
```

### Writer Integration Tests (with real DB)

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER={user} DB_PASSWORD={password} DB_NAME={db} \
  python -m pytest fastapi_app/tests/test_practice_writer.py -v
```

---

## 8. Monitoring

### Queue Depth

```bash
redis-cli XLEN memora:practice:write_queue
```

### Pending Messages (unprocessed)

```bash
redis-cli XPENDING memora:practice:write_queue practice-writers
```

### Dead Letters

```bash
redis-cli XLEN memora:practice:write_queue:dead
redis-cli XRANGE memora:practice:write_queue:dead - + COUNT 10
```

### Active Sessions

```bash
redis-cli KEYS "memora:practice:v2:session:*" | wc -l
```

### Player Summary Cache Hit Rate

Check FastAPI logs for `practice_summary_cache_hit` / `practice_summary_cache_miss` metrics.

---

## 9. Admin Utilities

### Force-Expire All Sessions

```bash
bench --site {site} execute memora_admin.api.practice_summary.force_expire_all_practice_sessions
```

### Reprocess Dead Letters

```bash
bench --site {site} execute memora_admin.tasks.practice_writer.reprocess_dead_letters
```

### Regenerate All Practice Content

```bash
bench --site {site} execute memora_admin.services.build.practice_content.generate_all_practice_content
```

---

## Key Redis Keys Reference

| Key Pattern | Type | TTL | Purpose |
|---|---|---|---|
| `memora:practice:summary:{player}:{track}` | String (JSON) | 2h | Player practice history cache |
| `memora:practice:v2:session:{player}` | Hash | 1h | Active session state |
| `memora:practice:rate:{player}:sessions` | String (int) | 1h | Rate limit counter |
| `memora:practice:write_queue` | Stream | N/A | Write queue for background persistence |
| `memora:practice:write_queue:dead` | Stream | N/A | Failed messages after 5 retries |
| `memora:practice:map_invalidation` (pubsub) | Channel | N/A | Map file cache invalidation signal |

---

## Architecture at a Glance

```
Client ──► CDN (map + chunks)      ← Content files
Client ──► FastAPI (start/submit/continue)
                │
                ├── Redis (summary cache, sessions, rate limits)
                └── Redis Stream (write queue)
                         │
                    Frappe Worker
                         │
                    MariaDB (Practice Log + Player Summary)
```

**Zero DB queries during gameplay** (warm cache). DB reads only on cold-start cache miss. DB writes only via background worker.
