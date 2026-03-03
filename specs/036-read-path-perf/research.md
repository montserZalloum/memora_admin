# Research: Progress & Practice Read-Path Performance

**Feature Branch**: `036-read-path-perf`
**Date**: 2026-03-03

## Research Task 1: Stats-First Read Path — Can Unlock State Be Derived From Stats?

### Question
Can progress endpoints derive unlock state from the stats hash alone, avoiding the `get_completed_bits()` bitmap decode call?

### Finding
**YES** — all unlock checks reduce to "is previous sibling complete?" which is `completed == total`, both available in the stats hash.

**Current unlock functions** (`progress.py:75-150`):
- `_is_track_complete(track, completed_bits)` → iterates all lessons in track
- `_is_unit_complete(unit, completed_bits)` → iterates all lessons in unit
- `_is_topic_complete(topic, completed_bits)` → iterates all lessons in topic

**Stats-derivable equivalents**:
- Track complete: `int(stats[f"{track_id}:completed"]) >= int(stats[f"{track_id}:total"])` and total > 0
- Unit complete: `int(stats[f"{unit_id}:completed"]) >= int(stats[f"{unit_id}:total"])` and total > 0
- Topic complete: `int(stats[f"{topic_id}:completed"]) >= int(stats[f"{topic_id}:total"])` and total > 0

**Validation**: Stats totals are computed from the same hierarchy tree. The `_content_hash` match guarantees structural consistency. If stats have stale hash, the fallback path uses bitmap as today.

### Decision
Add stats-derived unlock helpers alongside existing bitmap-based ones. Endpoints try stats first, fall back to bitmap on miss/stale.

### Endpoints affected
| Endpoint | Current Bitmap Usage | Stats-First Viable? |
|----------|---------------------|---------------------|
| `GET /progress/{subject}/tracks` | `get_completed_bits()` for unlock + stats recompute | **Yes** — unlock derivable from stats |
| `GET /progress/{subject}/tracks/{track_id}` | Same | **Yes** |
| `GET /progress/{subject}/tracks/{track_id}/units/{unit_id}` | Same | **Yes** |
| `GET /progress/{subject}` | Same (full breakdown) | **Yes** — but uses HGETALL anyway |
| `GET /progress/` (summary) | `get_completed_count()` via BITCOUNT | **Partially** — can read `completed`+`total` from stats |
| `GET /progress/{subject}/topics/{topic_id}/lessons` | GETBIT pipeline per lesson | **No** — per-lesson status requires bitmap |

---

## Research Task 2: Partial Stats Reads — HMGET Field Analysis

### Question
What fields does each partial progress endpoint actually need from the stats hash?

### Finding

**Stats hash field schema** (`stats.py:283-340`):
```
completed, total, _content_hash
{track_id}:completed, {track_id}:total      (per track)
{unit_id}:completed, {unit_id}:total        (per unit)
{topic_id}:completed, {topic_id}:total      (per topic)
```

A typical subject (10 tracks, 50 units, 200 topics) has ~521 fields.

**Per-endpoint field requirements**:

| Endpoint | Fields Needed | Count | vs HGETALL |
|----------|--------------|-------|------------|
| `GET /{subject}/tracks` | `_content_hash` + all `{track_id}:completed/total` | 1 + 2T ≈ 21 | 96% less |
| `GET /{subject}/tracks/{track_id}` | `_content_hash` + 1 track + prev track + all units in track | ~7 + 2U ≈ 17 | 97% less |
| `GET /{subject}/tracks/{tid}/units/{uid}` | `_content_hash` + 1 unit + prev unit + prev track + all topics in unit | ~9 + 2To ≈ 19 | 96% less |
| `GET /{subject}` (full) | All fields | ~521 | No savings |
| `GET /` (summary) | `_content_hash` + `completed` + `total` | 3 | 99% less |

### Decision
Add `StatsService.get_partial_stats(user_id, subject_id, version, fields)` using Redis `HMGET`. Each endpoint builds its field list from the hierarchy before calling.

### Alternatives Considered
- **Redis hashes with separate keys per level**: Too many keys, breaks atomic HINCRBY updates.
- **Nested JSON in a STRING**: Loses granular HINCRBY, requires full parse on every read.

---

## Research Task 3: Cache-Fill Coalescing Pattern

### Question
What coalescing pattern should be used for hierarchy and practice metadata cache-miss paths?

### Finding

**Existing pattern in `StatsService`** (`stats.py:30-51`):
- Process-local `dict[str, asyncio.Lock]` keyed by Redis key
- `setdefault()` for atomic lock creation under concurrency
- Soft-bounded to 10,000 entries with pruning of unlocked entries
- Per-key lock + system semaphore (two tiers)

**HierarchyService** (`hierarchy.py:50-111`):
- No coalescing currently
- Cache miss directly calls `self.frappe.call()` — N concurrent requests = N Frappe calls
- Three-level cache (local → Redis → Frappe) means only L3 misses are the problem

**PracticeService._load_hierarchy_meta** (`practice.py:320-361`):
- No coalescing currently
- Cache miss directly calls `self.frappe.call()`

### Decision
Use the same `dict[str, asyncio.Lock]` pattern from `StatsService`:

1. **Hierarchy coalescing**: Add `_hierarchy_fill_locks: dict[str, asyncio.Lock]` at module level in `hierarchy.py`. Wrap the Frappe call path (after local cache miss, after Redis miss) in per-key lock. After acquiring lock, re-check Redis before fetching from Frappe (double-check pattern).

2. **Practice metadata coalescing**: Add `_meta_fill_locks: dict[str, asyncio.Lock]` at module level in `practice.py`. Same double-check pattern.

3. **No system semaphore needed**: Unlike stats recompute (CPU-bound), Frappe calls are I/O-bound. The per-key lock alone prevents duplicate work. The Frappe client's httpx connection pool already limits total concurrent HTTP requests.

4. **Timeout**: Use `asyncio.wait_for(lock.acquire(), timeout=5.0)`. On timeout, proceed without lock (bounded duplicate work is better than failing the request). This matches the stats service's graceful degradation philosophy.

### Alternatives Considered
- **Redis SETNX-based distributed lock**: Overkill for single-server deployment with uvicorn workers. asyncio.Lock is per-process, which is sufficient since each worker has its own local cache and Redis connection.
- **No timeout**: Risk of deadlock if the fill fails silently. Timeout ensures graceful degradation.

---

## Research Task 4: Subject Access Hoisting Approach

### Question
How to hoist subject-level access out of the practice hierarchy track loop without changing behavior?

### Finding

**Current code** (`practice.py:202-208`):
```python
for track in hier.tracks:
    has_full_access = await self._check_track_access(player_id, subject_id, track_id, plan_id)
```

**`_check_track_access`** (`practice.py:363-392`):
```python
async def _check_track_access(self, player_id, subject_id, track_id, plan_id) -> bool:
    subject_key = f"SUB-{subject_id}"
    if await self.access.check_access_with_plan(player_id, subject_key, plan_id):
        return True  # <-- Same check repeated N times
    track_key = f"TRK-{track_id}"
    if await self.access.check_access(player_id, track_key):
        return True
    return False
```

The subject-level check (`check_access_with_plan`) is **invariant** across the loop — same player, same subject, same plan for every track.

### Decision
1. Compute subject-level access once before the loop:
   ```python
   subject_key = f"SUB-{subject_id}"
   has_subject_access = await self.access.check_access_with_plan(player_id, subject_key, plan_id)
   ```
2. In the loop, short-circuit on subject access, only check track-level grants if no subject access:
   ```python
   for track in hier.tracks:
       if has_subject_access:
           has_full_access = True
       else:
           track_key = f"TRK-{track.track_id}"
           has_full_access = await self.access.check_access(player_id, track_key)
   ```

This reduces Redis calls from `N * (1 subject check + 1 track check)` to `1 subject check + N * 1 track check` (and if subject access is granted, just `1 subject check + 0 track checks`).

---

## Research Task 5: Bounded Concurrency for Progress Summary

### Question
Best pattern for limiting concurrent `_fetch_subject_summary` tasks?

### Finding

**Current** (`progress.py:233-236`):
```python
results = await asyncio.gather(
    *(_fetch_subject_summary(sid) for sid in all_accessible),
    return_exceptions=True,
)
```

**Options**:

| Approach | Pros | Cons |
|----------|------|------|
| `asyncio.Semaphore` wrapper | Simple, standard library, preserves gather pattern | Slightly more boilerplate |
| `asyncio.TaskGroup` with semaphore | Modern (3.11+), structured concurrency | Minor refactor needed |
| Chunked sequential batches | Simple | Loses pipelining within chunks |

### Decision
Use `asyncio.Semaphore` with a configurable limit (default 6). Wrap each task in a semaphore-guarded coroutine:

```python
sem = asyncio.Semaphore(PROGRESS_SUMMARY_CONCURRENCY)
async def _bounded_fetch(sid):
    async with sem:
        return await _fetch_subject_summary(sid)
results = await asyncio.gather(*(_bounded_fetch(sid) for sid in all_accessible), return_exceptions=True)
```

Add `PROGRESS_SUMMARY_CONCURRENCY = 6` as a module-level constant. Configurable via `Settings` if needed later.

### Alternatives Considered
- **Redis pipelining**: Summary uses `BITCOUNT` per subject — could pipeline all BITCOUNTs into one round-trip. But this would require refactoring the helper to separate Redis operations from response building. Consider as NTH-002 follow-up.

---

## Research Task 6: Production Tuning Settings

### Question
What are the current default vs recommended production values?

### Finding

**Current defaults** (`config.py`):

| Setting | Default | Recommended Production | Rationale |
|---------|---------|----------------------|-----------|
| `redis_max_connections` | 20 | 50 | 4 workers × 50 = 200 total connections. 100k users with sub-20ms means ~5k req/s, many concurrent Redis ops |
| `frappe_max_connections` | 100 | 100 | Already reasonable for upstream API |
| `frappe_max_keepalive` | 20 | 50 | More keepalive reduces TCP handshake overhead |
| `frappe_timeout` | 30.0 | 10.0 | Faster failure detection; 30s is too long for game API paths |

**Other production considerations**:
- uvicorn workers: Currently determined by deployment (typically 4). Not a Settings field.
- `MAX_CONCURRENT_STATS_RECOMPUTES`: Hardcoded at 30. Adequate for 4 workers.

### Decision
Document recommended production values in `quickstart.md`. No code changes needed — all settings already configurable via environment variables.
