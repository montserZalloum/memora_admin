# Performance Audit Report

**Date:** 2026-02-20
**Target:** 100k concurrent users
**Status:** In Progress

---

## Completed

| Issue | Status | Date |
|-------|--------|------|
| ~~PERF-01: N+1 Query Storm in Hierarchy / Generator / Plan Generator~~ | ✅ Fixed | 2026-02-20 |
| ~~Uncached `_get_skippable_stage_types()` in Generator~~ | ✅ Fixed by PERF-01 | 2026-02-20 |

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL — Infrastructure Prerequisites | 3 |
| CRITICAL — Will Cause Outages | 4 |
| HIGH — Performance Degradation | 5 |
| MEDIUM — Optimization | 9 |
| **Total** | **21** |

---

## CRITICAL — Infrastructure Prerequisites

*Do these first. Everything else depends on them.*

---

### PERF-02: FrappeClient Creates New httpx.AsyncClient Per Request

**Status:** `[ ] Not Started`
**Priority:** 1 of 21
**Effort:** 2 hours
**Risk:** Near zero
**File:** `fastapi_app/services/frappe.py`

**Problem:**
Every FrappeClient method creates and destroys a new HTTP client with its own connection pool:

```python
async with httpx.AsyncClient(timeout=self.timeout) as client:
    response = await client.get(...)
```

At 100k concurrent users, FastAPI is creating/destroying hundreds of thousands of TCP connections to the Frappe backend per minute. The OS will exhaust file descriptors before any other bug matters. Nothing works without this fix.

**Fix Strategy:**
Create a persistent `httpx.AsyncClient` as a class attribute or module-level singleton. Initialize once on startup, reuse across all requests. The client manages its own internal connection pool.

```python
class FrappeClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
```

**Impact:** Eliminates hundreds of thousands of TCP handshakes/min. Prerequisite for all other fixes.

---

### PERF-03: Missing Composite Indexes on Hot Tables

**Status:** `[ ] Not Started`
**Priority:** 2 of 21
**Effort:** 1 hour
**Risk:** Near zero — read-only, no schema changes
**Files:** `memora_admin/memora_admin/setup.py` (add to `after_migrate`)

**Problem:**
Several high-traffic tables rely on single-column indexes where composite indexes would eliminate index intersection overhead. Also, `tabMemora Lesson.subject` needs an index (required by the PERF-01 fix already deployed).

| Table | Missing Index | Query Pattern | Est. Rows | Used By |
|-------|--------------|---------------|-----------|---------|
| `tabMemora Lesson` | `(subject)` | Bulk queries, stages JOIN | Millions | hierarchy, generators |
| `tabMemora Interaction Log` | `(event_type, creation)` | FSRS processor cutoff query | Millions | `fsrs_processor.py` (every 1 min) |
| `tabMemora Structure Progress` | `(player, subject)` | Every progress lookup | Hundreds of millions | `sync.py`, FastAPI progress |
| `tabMemora Voucher Card` | `(batch, status)` | Export, expiration, billing | Millions | `voucher.py`, `season_expiration.py` |
| `tabMemora Player Subscription` | `(player, access_key)` | Every access check | Millions | `access_sync.py`, `subscription_transaction.py` |

**Fix (SQL — add to `setup.py` with idempotent checks):**
```sql
-- Phase 1: Critical path
CREATE INDEX idx_lesson_subject
  ON `tabMemora Lesson` (subject);

CREATE INDEX idx_event_creation
  ON `tabMemora Interaction Log` (event_type, creation);

CREATE INDEX idx_player_subject
  ON `tabMemora Structure Progress` (player, subject);

-- Phase 2: High traffic
CREATE INDEX idx_batch_status
  ON `tabMemora Voucher Card` (batch, status);

CREATE INDEX idx_player_access
  ON `tabMemora Player Subscription` (player, access_key);
```

**Impact:** 50-95% query time reduction per table. Foundation for all other query optimizations.

---

### PERF-04: `_get_player_season_seq()` Called Repeatedly with No Caching

**Status:** `[ ] Not Started`
**Priority:** 3 of 21
**Effort:** 2 hours
**Risk:** Near zero
**Files:**
- `memora_admin/api/reviews.py:30-43`
- `memora_admin/api/profile.py`

**Problem:**
This 3-table JOIN runs on **every single Frappe API call** to reviews and profile endpoints:

```python
def _get_player_season_seq(player_id: str) -> int:
    result = frappe.db.sql("""
        SELECT s.season_seq
        FROM `tabMemora Player Profile` pp
        INNER JOIN `tabMemora Academic Plan` ap ON ap.name = pp.plan
        INNER JOIN `tabMemora Season` s ON s.name = ap.season
        WHERE pp.name = %(player)s
        LIMIT 1
    """, {"player": player_id})
```

Called by: `get_review_overview()`, `get_due_items()`, `submit_reviews()`, `get_items_learned_count()`, `get_memory_mastery()`. A single user opening the review screen triggers this query **3+ times in one page load**.

The FastAPI side (`profile_page.py`) already caches this via `_resolve_season_seq()` with a 24h Redis TTL. The Frappe-side APIs don't — they re-query every time.

At 100k users = hundreds of thousands of redundant 3-table JOINs per minute for a value that almost never changes.

**Fix Strategy:**
Add Frappe-side caching using `frappe.cache()` with a reasonable TTL:

```python
def _get_player_season_seq(player_id: str) -> int:
    cache_key = f"player_season_seq:{player_id}"
    cached = frappe.cache().get_value(cache_key)
    if cached is not None:
        return int(cached)

    result = frappe.db.sql(...)
    value = int(result[0][0]) if result else 1

    frappe.cache().set_value(cache_key, value, expires_in_sec=86400)
    return value
```

Invalidate on plan/season change via doc_events hook.

**Impact:** Eliminates the single most frequent redundant query in the system.

---

## CRITICAL — Will Cause Outages Under Load

*These will break the system under sustained 100k user traffic.*

---

### PERF-05: `flush_interaction_buffer()` — Single-Threaded ORM Insert Loop

**Status:** `[ ] Not Started`
**Priority:** 4 of 21
**Effort:** 1 day
**Risk:** Medium — verify no hooks depend on ORM lifecycle
**File:** `memora_admin/tasks/sync.py`

**Problem:**
Each interaction is inserted via full Frappe ORM lifecycle (validate → before_insert → SQL INSERT → after_insert → trigger), one at a time:

```python
for i, item_bytes in enumerate(items):  # up to 1000
    doc = frappe.get_doc({
        "doctype": "Memora Interaction Log",
        "player": item["player"],
        "lesson": item["lesson"],
        ...
    })
    doc.insert(ignore_permissions=True)
```

At 100k concurrent users generating interactions, the 1-minute cron processes 1000 items but users generate far more. The buffer grows unbounded → Redis OOM.

**Fix Strategy:**
Replace ORM loop with `frappe.db.bulk_insert` or raw SQL multi-row INSERT:

```python
# Option 1: Frappe bulk_insert
rows = []
for item in valid_items:
    rows.append({...})
frappe.db.bulk_insert("Memora Interaction Log", rows, ignore_duplicates=True)

# Option 2: Raw SQL (fastest)
frappe.db.sql("""
    INSERT INTO `tabMemora Interaction Log`
    (name, player, lesson, stage_id, event_type, time_spent, ...)
    VALUES %s
""", [tuple_values])
```

Also increase batch size from 1000 to 5000-10000 (monitor memory).

**Impact:** 1000 individual INSERTs → 1 bulk INSERT. Prevents unbounded buffer growth.

---

### PERF-06: Hydration Thundering Herd After Redis Flush

**Status:** `[ ] Not Started`
**Priority:** 5 of 21
**Effort:** 1-2 days
**Risk:** Medium — architectural change
**Files:**
- `fastapi_app/services/access.py:109-131`
- `fastapi_app/services/progress.py:62-135`
- `fastapi_app/services/wallet.py:148-213`

**Problem:**
After Redis restart/flush, every user's first request triggers a synchronous Frappe HTTP call (~100-300ms) to hydrate their data. With 100k users hitting simultaneously:
- 100k Frappe HTTP calls in seconds
- Frappe worker pool exhaustion (typically 4-16 workers)
- Cascading timeouts → full outage

There is no batching, no backoff, no distributed lock to prevent the storm.

**Fix Strategy (combine):**
1. **Distributed lock per player:** Use Redis `SET NX EX` — only one hydration per player proceeds, others wait on the lock result with short polling
2. **Rate-limited hydration:** Semaphore limiting concurrent Frappe calls (e.g., max 50 concurrent hydrations)
3. **Cache warming on deploy:** Background task pre-loads active players' data after Redis restart

```python
async def ensure_hydrated(self, player_id: str):
    lock_key = f"hydrating:{player_id}"
    acquired = await self.redis.set(lock_key, "1", nx=True, ex=30)
    if not acquired:
        # Another request is hydrating — wait for result
        for _ in range(10):
            await asyncio.sleep(0.5)
            if await self.redis.exists(cache_key):
                return  # Data appeared
        # Timeout — fall through to hydrate ourselves
    # ... hydrate from Frappe ...
```

**Impact:** Prevents multi-minute outage after Redis flush. Critical for operational resilience.

---

### PERF-07: `submit_reviews()` — N+1 Query Pattern on 10B-Row Table

**Status:** `[ ] Not Started`
**Priority:** 6 of 21
**Effort:** 1 day
**Risk:** Medium — FSRS computation stays per-item, but I/O can batch
**File:** `memora_admin/api/reviews.py:131+`

**Problem:**
Each item in a review batch triggers its own SELECT + UPDATE on the partitioned Memory State table:

```python
for item_data in items_list:
    # Query 1: SELECT per item
    memory_state = frappe.db.sql("""
        SELECT name, stability, difficulty, next_review, state, step, last_review
        FROM `tabMemora Memory State`
        WHERE player = %(player)s
          AND item_id = UUID_TO_BIN(%(item_id)s)
          AND season_seq = %(season_seq)s
        LIMIT 1
    """, ...)

    # ... FSRS computation ...

    # Query 2: UPDATE per item
    frappe.db.sql("""
        UPDATE `tabMemora Memory State`
        SET stability = ..., difficulty = ..., ...
        WHERE name = %(name)s AND season_seq = %(season_seq)s
    """, ...)
```

10 items = 20 queries. At 100k concurrent users submitting reviews = millions of individual queries per minute.

**Fix Strategy:**
1. **Batch SELECT:** Fetch all items in one query using `item_id IN (UUID_TO_BIN(...), ...)` — bounded to batch size (typically 10), safe for IN clause
2. **FSRS computation:** Still per-item in Python (unavoidable)
3. **Batch UPDATE:** Use CASE-based multi-row UPDATE or batch individual UPDATEs into a single transaction with explicit `BEGIN`/`COMMIT`

```sql
-- Batch SELECT (1 query instead of 10)
SELECT name, BIN_TO_UUID(item_id) as item_id, stability, difficulty,
       next_review, state, step, last_review
FROM `tabMemora Memory State`
WHERE player = %(player)s
  AND season_seq = %(season_seq)s
  AND item_id IN (UUID_TO_BIN(%(id_0)s), UUID_TO_BIN(%(id_1)s), ...)
```

**Impact:** 20 queries → 2 queries per review submission. Directly reduces MariaDB load.

---

### PERF-08: FSRS Processor N+1 Metadata Lookups

**Status:** `[ ] Not Started`
**Priority:** 7 of 21
**Effort:** Half day
**Risk:** Low
**File:** `memora_admin/memora_admin/tasks/fsrs_processor.py:276-356`

**Problem:**
The scheduled task (runs every 1 minute) fetches interactions, then for **each** interaction fires 3 individual `frappe.db.get_value()` calls:

```python
for interaction in interactions:  # 500 iterations
    stage_row = frappe.db.get_value("Memora Lesson Stage", ...)     # Query 1
    subject = frappe.db.get_value("Memora Lesson", lesson, "subject")  # Query 2
    is_reviewable = frappe.db.get_value("Memora Lesson", lesson, "is_reviewable")  # Query 3
```

**Result:** 1,500 queries per minute. At 100k users generating millions of interactions, this becomes a severe MariaDB bottleneck.

**Additional sub-issue:** `_resolve_player_seasons()` uses an unbounded IN clause. If 10k players interact in one minute, the query has 10k items in the IN clause — degrades optimizer performance.

**Fix Strategy:**
1. Collect all unique `lesson` and `stage_id` values from the batch
2. Batch-fetch: `frappe.get_all("Memora Lesson", filters={"name": ["in", lesson_ids]}, fields=[...])`
3. Batch-fetch stages similarly
4. Build lookup dicts, then iterate
5. Chunk `_resolve_player_seasons()` into batches of 500

```python
# Before loop: batch fetch all metadata
lesson_ids = list({i.lesson for i in interactions})
lessons = frappe.get_all("Memora Lesson",
    filters={"name": ["in", lesson_ids]},
    fields=["name", "subject", "is_reviewable"])
lesson_map = {l.name: l for l in lessons}

stage_ids = list({i.stage_id for i in interactions})
stages = frappe.get_all("Memora Lesson Stage",
    filters={"name": ["in", stage_ids]},
    fields=["name", "stage_type", "parent"])
stage_map = {s.name: s for s in stages}
```

**Impact:** 1,500 queries/min → ~5 queries/min. Also increase batch size from 500 to 2,000-5,000.

---

## HIGH — Performance Degradation

*Slower responses, wasted resources, but not outage-causing.*

---

### PERF-09: Redundant GETBIT Pipelines in Progress Endpoints

**Status:** `[ ] Not Started`
**Priority:** 8 of 21
**Effort:** 30 minutes
**Risk:** Near zero
**File:** `fastapi_app/api/v1/endpoints/progress.py`

**Problem:**
`get_completed_bits()` pipelines 1,000+ GETBIT operations (~5-10ms per call). Several endpoints call it **multiple times in the same request**:

| Endpoint | Calls to `get_completed_bits()` | Redundant |
|----------|-------------------------------|-----------|
| `get_track_detail` | 2 | 1 |
| `get_unit_detail` | 2 | 1 |

At 100k users × 10 progress requests/min = **1M redundant Redis pipelines/minute**.

**Fix Strategy:**
Compute `completed_bits` once, pass as parameter:

```python
# Before: called twice
bits1 = await get_completed_bits(...)  # for unlock check
bits2 = await get_completed_bits(...)  # for stats

# After: called once, passed to both
completed_bits = await get_completed_bits(...)
# pass completed_bits to both unlock check and stats computation
```

**Impact:** 5-15ms per request removed. 1M fewer Redis pipelines/min. 30-minute fix.

---

### PERF-10: Review Overview/Due Items JOIN with Lesson Stage

**Status:** `[ ] Not Started`
**Priority:** 9 of 21
**Effort:** Half day
**Risk:** Low-medium
**File:** `memora_admin/api/reviews.py:47-115`

**Problem:**
Both `get_review_overview()` and `get_due_items()` JOIN `tabMemora Memory State` with `tabMemora Lesson Stage` to validate stage existence:

```sql
SELECT ms.subject, COUNT(*) as due_count
FROM `tabMemora Memory State` ms
INNER JOIN `tabMemora Lesson Stage` ls
    ON ls.name = ms.stage_id AND ls.parent = ms.lesson
WHERE ms.player = %(player)s
  AND ms.next_review <= %(today)s
  AND ms.season_seq = %(season_seq)s
GROUP BY ms.subject
```

The JOIN is against a non-partitioned table (Lesson Stage, millions of rows). For a student with 500+ due items, MariaDB must look up each stage row individually. `get_due_items()` has **zero caching** — fires on every subject review screen open.

**Fix Strategy (options):**
1. **Drop the JOIN:** Trust referential integrity — stages are never deleted without deleting the Memory State. Remove the INNER JOIN and query Memory State directly.
2. **Cache valid stage IDs:** Maintain a Redis set of valid `(stage_id, lesson)` pairs per subject, refreshed on content publish.
3. **Add index on Lesson Stage:** `CREATE INDEX idx_stage_parent ON tabMemora Lesson Stage (name, parent)` — makes the JOIN a covered index lookup.

Option 1 is the fastest and most impactful if the business logic allows it.

**Impact:** Removes the most expensive per-request JOIN in the system.

---

### PERF-11: Leaderboard N+1 ZCOUNT Calls

**Status:** `[ ] Not Started`
**Priority:** 10 of 21
**Effort:** 30 minutes
**Risk:** Near zero
**File:** `fastapi_app/services/leaderboard.py:249-270`

**Problem:**
`get_my_rank()` fires individual ZCOUNT calls per neighbor for dense ranking:

```python
for neighbor_id, neighbor_score in neighbors_raw:
    neighbor_higher = await self.redis.zcount(key, f"({neighbor_score}", "+inf")
    neighbor_rank = neighbor_higher + 1
```

4-5 neighbors = 4-5 extra Redis round-trips per request. At 100k users × 5 leaderboard views/min = **2M ZCOUNT calls/min**.

**Fix Strategy:**
Pipeline all ZCOUNT calls into a single round-trip:

```python
pipe = self.redis.pipeline()
for _, score in neighbors_raw:
    pipe.zcount(key, f"({score}", "+inf")
results = await pipe.execute()

for i, (neighbor_id, neighbor_score) in enumerate(neighbors_raw):
    neighbor_rank = results[i] + 1
```

**Impact:** 4 Redis RTTs → 1 RTT per request. ~12-20ms → ~3-5ms.

---

### PERF-12: Catalog N+1 Grant Queries

**Status:** `[ ] Not Started`
**Priority:** 11 of 21
**Effort:** Half day
**Risk:** Low
**File:** `memora_admin/memora_admin/api/catalog.py:29-100`

**Problem:**
For each product grant in a plan, the code fires individual queries:

```python
for grant in grants:
    item_name = frappe.get_value("Item", grant.item_code, "item_name")           # 1 query
    price = frappe.get_value("Item Price", {...}, "price_list_rate")              # 1 query
    components = frappe.get_all("Memora Grant Component", {"parent": grant.name}) # 1 query
    for comp in components:
        ps = frappe.get_value("Memora Plan Subject", {...}, [...])               # 1+ queries
```

10 grants × 5 components = **50+ queries** per catalog build. Catalog is cached infinitely, so this only hits on first load or invalidation — but when it does, it's 500ms+.

**Fix Strategy:**
1. Collect all `item_code` values → single `frappe.get_all("Item", filters={"name": ["in", codes]})`
2. Collect all grant names → single `frappe.get_all("Memora Grant Component", filters={"parent": ["in", names]})`
3. Collect all subject refs → single `frappe.get_all("Memora Plan Subject", ...)`
4. Build lookup dicts, assemble in Python

**Impact:** 50+ queries → 4-5 queries. Cache miss: ~500ms → ~50ms.

---

### PERF-13: Stats Recompute Storm on Content Update

**Status:** `[ ] Not Started`
**Priority:** 12 of 21
**Effort:** 1 day
**Risk:** Low
**File:** `fastapi_app/api/v1/endpoints/progress.py:283-287`

**Problem:**
When content editors add/remove lessons, ALL users' stats caches become stale simultaneously (content hash mismatch). Each user's next request triggers a full recompute:
- 1,000+ GETBIT pipeline (~5-10ms)
- `compute_stats_from_hierarchy()` traversal (~2-5ms)
- HSET to Redis (~2-3ms)

If 10 users hit the same subject simultaneously = 10 identical recomputes. At 100k active users = avalanche.

**Fix Strategy:**
1. **Distributed lock per `(subject, version)`:** First request computes, others wait for the result
2. **Pre-compute on content push:** Background job computes new stats template after hierarchy rebuild
3. **TTL jitter:** Add random 0-60s to cache TTL so not all caches expire simultaneously

**Impact:** Eliminates thundering herd on content updates.

---

## MEDIUM — Optimization

*Worth doing, lower urgency. Schedule as time allows.*

---

### PERF-14: `frappe.db.exists()` Loops in Subscription/Voucher Code

**Status:** `[ ] Not Started`
**Priority:** 13 of 21
**Effort:** 2 hours
**Risk:** Near zero
**Files:**
- `memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.py:39-45`
- `memora_admin/memora_admin/api/voucher.py:538-550`

**Problem:**
Both files check subscription existence in a loop:

```python
# subscription_transaction.py — 10+ iterations
for access_key in grant_keys:
    existing = frappe.db.exists("Memora Player Subscription",
        {"player": self.player, "access_key": access_key})

# voucher.py preview — nested loop, 50+ iterations
for bg in batch.batch_grants:
    grant_keys = get_grant_keys(bg.product_grant)
    all_owned = all(
        frappe.db.exists("Memora Player Subscription",
            {"player": player_id, "access_key": key})
        for key in grant_keys
    )
```

**Fix:** Single `frappe.get_all()` with `access_key: ["in", all_keys]`, build a set, check membership in Python.

**Impact:** 10-50 queries → 1 per operation.

---

### PERF-15: `sync_dirty_progress` and `sync_dirty_wallets` — Sequential Processing

**Status:** `[ ] Not Started`
**Priority:** 14 of 21
**Effort:** 1 day
**Risk:** Medium
**File:** `memora_admin/tasks/sync.py`

**Problem:**
Dirty set members are processed one at a time. Each wallet sync does a `frappe.db.set_value()` per player, each progress sync does an upsert per player-subject pair. At 100k concurrent users, the dirty sets will contain thousands of entries, and the 1-minute cron may not process them all before the next run.

**Fix Strategy:**
Batch the SQL updates:
```python
# Instead of N individual set_value calls:
for player in dirty_players:
    frappe.db.set_value("Memora Player Wallet", player, {"xp": xp, "streak": streak})

# Use bulk UPDATE with CASE:
frappe.db.sql("""
    UPDATE `tabMemora Player Wallet`
    SET xp = CASE name ... END,
        streak = CASE name ... END
    WHERE name IN %(players)s
""", {"players": player_list})
```

**Impact:** Prevents sync backlog at scale.

---

### PERF-16: `get_memory_mastery()` — Full Partition Scan for Stability Classification

**Status:** `[ ] Not Started`
**Priority:** 15 of 21
**Effort:** 1 day
**Risk:** Medium
**File:** `memora_admin/api/profile.py`

**Problem:**
The mastery query scans all of a player's Memory State rows in a partition to classify by stability thresholds (mature ≥ 21, learning > 0, new = 0). Cached for 5 minutes, but each cache miss is expensive for players with thousands of items.

The codebase already acknowledges this in `setup.py`:
> *"If mastery reads become a bottleneck, use Redis counters (memora:stats:\*) instead."*

**Fix Strategy:**
Maintain Redis counters (`mature`, `learning`, `new`) per player-subject. Increment/decrement on each review submission (already touch the Memory State row, so the classification change is known). Cache miss falls back to the SQL scan.

**Impact:** Eliminates most expensive profile query. 5-min cache → real-time counters.

---

### PERF-17: Full CSV Decryption for Voucher Export

**Status:** `[ ] Not Started`
**Priority:** 16 of 21
**Effort:** 2 hours
**Risk:** Near zero
**File:** `memora_admin/memora_admin/api/voucher.py:261-282`

**Problem:**
Export loads entire encrypted CSV into memory, decrypts fully, parses all rows, then filters to "Available" cards. For a 10k-card batch = ~500KB+ per export request. Available serial numbers are queried **after** decryption — should be queried first for early exit.

**Fix:**
1. Query available serials first (early exit if none)
2. Consider streaming CSV parsing instead of full list comprehension

**Impact:** Memory reduction + potential early exit on empty batches.

---

### PERF-18: Session Redis GET on Every Authenticated Request

**Status:** `[ ] Not Started`
**Priority:** 17 of 21
**Effort:** 2 hours
**Risk:** Low
**File:** `fastapi_app/api/deps.py:101-127`

**Problem:**
Every authenticated endpoint validates the session `family_id` against Redis:
```python
raw = await redis_client.get(session_key)
```

At 100k users × 10 requests/min = **1.67M GET calls/min**. Each adds ~0.5-1ms.

**Fix Strategy:**
Optional in-process LRU cache with short TTL (e.g., 5 seconds):

```python
from functools import lru_cache
# or use cachetools.TTLCache for async-safe TTL caching
session_cache = TTLCache(maxsize=10000, ttl=5)
```

Session invalidation happens via `family_id` mismatch, so a 5s staleness window is acceptable.

**Impact:** 0.5-1ms saved per request. Reduces Redis load by ~90%.

---

### PERF-19: Access Sync Redundant Queries for Free-Content Detection

**Status:** `[ ] Not Started`
**Priority:** 18 of 21
**Effort:** 1 hour
**Risk:** Near zero
**File:** `memora_admin/memora_admin/events/access_sync.py:236-280`

**Problem:**
When checking if a subject has free content:
1. Fetches all tracks for the subject
2. Counts free units (query 1)
3. If zero, re-fetches all units (redundant — could reuse track IDs from step 1)
4. Counts free topics (query 2)

**Fix:** Single SQL:
```sql
SELECT EXISTS(
  SELECT 1 FROM `tabMemora Unit` WHERE track IN (...) AND is_free = 1
  UNION ALL
  SELECT 1 FROM `tabMemora Topic` WHERE unit IN (...) AND is_free = 1
)
```

**Impact:** 4 queries → 1-2. Runs on every `is_free` toggle.

---

### PERF-20: Missing Index on Subscription Transaction Time Queries

**Status:** `[ ] Not Started`
**Priority:** 19 of 21
**Effort:** 10 minutes
**Risk:** Near zero

**Problem:**
Reporting and reconciliation queries filter by `(status, creation)` but no composite index exists.

**Fix:**
```sql
CREATE INDEX idx_status_creation
  ON `tabMemora Subscription Transaction` (status, creation);
```

**Impact:** 60-80% improvement on time-range transaction queries.

---

## Well-Optimized Areas (No Action Needed)

| Area | File | Why It's Good |
|------|------|---------------|
| Dirty-set sync pattern | `tasks/sync.py` | Batch SMEMBERS + atomic SREM |
| Bulk card generation | `api/voucher.py` | Single `bulk_insert` with 10k chunks |
| Consignment billing | `tasks/consignment_billing.py` | Single SQL + `groupby` + bulk UPDATE |
| Memory State partitioning | `setup.py` | RANGE partitioning + composite indexes + partition pruning on all queries |
| Redis pipelines in FastAPI services | `fastapi_app/services/` | Most services use pipelines correctly |
| Profile batch fetch | `fastapi_app/services/profile.py` | MGET for batch lookups, no N+1 |
| Composite leaderboard scores | `fastapi_app/services/leaderboard.py` | Tie-breaking without secondary sorts |
| Weekly activity | `fastapi_app/services/profile_page.py` | Pipeline with 7 ZSCORE calls (single round-trip) |

---

## Execution Plan

| Week | Items | Focus |
|------|-------|-------|
| **Week 1** | PERF-02, PERF-03, PERF-04, PERF-09, PERF-11 | Infrastructure prerequisites + 30-min quick wins |
| **Week 2** | PERF-05, PERF-06, PERF-07, PERF-08 | Outage prevention |
| **Week 3** | PERF-10, PERF-12, PERF-13 | Degradation fixes |
| **Week 4+** | PERF-14 through PERF-20 | Optimization as time allows |

---

## Tracking

- [x] ~~**PERF-01** — N+1 hierarchy/plan/generator builds~~ ✅ FIXED
- [ ] **PERF-02** — FrappeClient connection pooling (CRITICAL)
- [ ] **PERF-03** — Missing composite indexes (CRITICAL)
- [ ] **PERF-04** — `_get_player_season_seq()` caching (CRITICAL)
- [ ] **PERF-05** — `flush_interaction_buffer()` ORM loop (CRITICAL)
- [ ] **PERF-06** — Hydration thundering herd (CRITICAL)
- [ ] **PERF-07** — `submit_reviews()` N+1 (CRITICAL)
- [ ] **PERF-08** — FSRS processor N+1 metadata (CRITICAL)
- [ ] **PERF-09** — Redundant GETBIT pipelines (HIGH)
- [ ] **PERF-10** — Review JOIN with Lesson Stage (HIGH)
- [ ] **PERF-11** — Leaderboard ZCOUNT N+1 (HIGH)
- [ ] **PERF-12** — Catalog N+1 grant queries (HIGH)
- [ ] **PERF-13** — Stats recompute storm (HIGH)
- [ ] **PERF-14** — `exists()` loops in subscription/voucher (MEDIUM)
- [ ] **PERF-15** — Sequential dirty sync processing (MEDIUM)
- [ ] **PERF-16** — `get_memory_mastery()` partition scan (MEDIUM)
- [ ] **PERF-17** — Full CSV decryption for export (MEDIUM)
- [ ] **PERF-18** — Session Redis GET per request (MEDIUM)
- [ ] **PERF-19** — Free-content detection redundant queries (MEDIUM)
- [ ] **PERF-20** — Subscription transaction index (MEDIUM)