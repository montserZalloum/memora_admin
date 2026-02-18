# Quickstart: Stats Cache Staleness Detection

**Feature**: 019-stats-content-hash
**Date**: 2026-02-18

## What This Feature Does

Adds a structural fingerprint (`content_hash`) to the subject hierarchy and per-user stats cache. When a content editor adds, removes, or reorganizes lessons, the fingerprint changes. On the student's next progress request, the system detects the mismatch and recomputes stats from the bitmap — eliminating the up-to-1-hour stale window with zero write amplification.

## Files to Modify (5 total)

| # | File | Change Type | Complexity |
|---|------|-------------|------------|
| 1 | `memora_admin/api/hierarchy.py` | Add function + 1 line call | Low |
| 2 | `fastapi_app/models/progress.py` | Add 1 field | Low |
| 3 | `fastapi_app/services/stats.py` | Add 1 line to output dict | Low |
| 4 | `fastapi_app/api/v1/endpoints/progress.py` | Extend condition in 4 places | Medium |
| 5 | `fastapi_app/api/v1/endpoints/sessions.py` | Cold-start path gets hash via stats function | Low |

## Implementation Order

### Step 1: Hash Computation (Frappe side)

Add `_compute_content_hash()` to `memora_admin/api/hierarchy.py`:

```python
import hashlib

def _compute_content_hash(hierarchy: dict) -> str:
    h = hashlib.md5()
    h.update(str(hierarchy["bit_range"]).encode())
    excluded = hierarchy.get("excluded_bits", [])
    h.update(str(len(excluded)).encode())
    for eb in sorted(excluded):
        h.update(str(eb).encode())
    for track in hierarchy["tracks"]:
        h.update(track["track_id"].encode())
        for unit in track["units"]:
            h.update(unit["unit_id"].encode())
            for topic in unit["topics"]:
                h.update(topic["topic_id"].encode())
                h.update(str(len(topic["lessons"])).encode())
                for lesson in topic["lessons"]:
                    h.update(lesson["lesson_id"].encode())
                    h.update(str(lesson["bit_index"]).encode())
    return h.hexdigest()[:8]
```

Call it at the end of `get_subject_hierarchy()`:
```python
hierarchy["content_hash"] = _compute_content_hash(hierarchy)
return hierarchy
```

### Step 2: Model Field (FastAPI side)

Add to `SubjectHierarchy` in `fastapi_app/models/progress.py`:
```python
content_hash: str = ""
```

### Step 3: Stats Output (FastAPI side)

In `compute_stats_from_hierarchy()` in `fastapi_app/services/stats.py`, add to the output dict:
```python
stats["_content_hash"] = hierarchy.content_hash
```

### Step 4: Staleness Check (FastAPI side)

In `fastapi_app/api/v1/endpoints/progress.py`, update 4 endpoints. Change:
```python
if stats is None or "total" not in stats:
```
To:
```python
if stats is None or "total" not in stats or stats.get("_content_hash") != hierarchy.content_hash:
```

### Step 5: Session Cold-Start (FastAPI side)

In `fastapi_app/api/v1/endpoints/sessions.py`, the cold-start path calls `compute_stats_from_hierarchy()` which now includes `_content_hash` automatically. No additional changes needed in this file unless the cold-start check condition also needs updating.

## Deployment

1. Deploy Frappe code → `bench restart` (required for Frappe API changes)
2. Deploy FastAPI code → `pkill -f "uvicorn fastapi_app.main:app"` (supervisor auto-restarts)
3. Optionally invalidate hierarchy caches: `redis-cli -p 13000 DEL memora:hierarchy:SUBJ-00704` (or wait for 1h TTL)
4. No stats migration — pre-existing stats self-heal on next read

## Verification

```bash
# 1. Check hierarchy has content_hash
curl -s http://127.0.0.1:8002/api/v1/health/live

# 2. Check Redis hierarchy cache for content_hash field
redis-cli -p 13000 GET memora:hierarchy:SUBJ-00704 | python3 -c "import sys,json; d=json.load(sys.stdin); print('content_hash:', d.get('content_hash', 'MISSING'))"

# 3. Check stats cache for _content_hash field
redis-cli -p 13000 HGETALL memora:stats:PLAYER-00272:SUBJ-00704:v1
```

## Test Strategy

| Test Type | File | What It Verifies |
|-----------|------|-----------------|
| Unit | `test_content_hash.py` | Hash determinism, sensitivity to structural changes, stability on irrelevant changes |
| Integration | `test_stats_staleness.py` | End-to-end staleness detection, pre-migration self-healing, HINCRBY preservation |
