# PRD: Stats Cache Staleness Detection via Hierarchy Content Hash

**Version:** 1.2
**Date:** 2026-02-18
**Status:** Approved
**Branch:** TBD (e.g., `019-stats-content-hash`)

---

## 1. Problem Statement

### Current Behavior

When content editors add, remove, or reorganize lessons in a subject, the hierarchy cache is invalidated correctly. However, the per-user **stats cache** (`memora:stats:{user}:{subject}:v{version}`) retains stale aggregated totals until its 1-hour TTL expires.

**Example (observed 2026-02-18):**

| Time | Event | Stats Cache State |
|------|-------|-------------------|
| T0 | Subject has 2 lessons, player completes both | `total=2, completed=2` (100%) |
| T1 | Editor adds LES-00476 (3rd lesson) | Stats still says `total=2` |
| T2 | Player opens progress page | **Shows 100%** instead of 66.7% |
| T0+1h | Stats TTL expires, cold-start recompute | Correct: `total=3, completed=2` (66.7%) |

**Impact:** For up to 1 hour after content changes, every user who already has a cached stats hash sees incorrect totals and percentages.

### Why Not Eagerly Invalidate?

With 100k+ concurrent users per subject:
- Proactive invalidation = 100k `DEL` or `HSET` operations per content change
- Redis write storm blocks the <20ms SLA for all other operations
- Most users won't open the app during that window anyway

### Chosen Approach

**Option 3: Content hash embedded in stats cache.** Zero writes on content change. Each user's stats are lazily validated on their next read via an O(1) string comparison. No write storm, fully scalable.

---

## 2. Solution Design

### 2.1 Content Hash Definition

A **content hash** is a short deterministic fingerprint derived from the hierarchy's structural properties that affect stats totals.

**Algorithm:** Incremental MD5 using `hashlib.md5()` with `.update()` calls, truncated to 8 hex characters (32 bits).

```python
import hashlib

def _compute_content_hash(hierarchy: dict) -> str:
    """Compute structural fingerprint from hierarchy dict.

    Called once during hierarchy build on the Frappe side.
    Uses incremental hash.update() for constant memory overhead.
    """
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

**Why incremental `hash.update()`:**
- Feeds data directly into the MD5 state — no intermediate string allocation
- Cleaner, idiomatic Python for streaming hash computation
- Constant memory overhead regardless of hierarchy size (only the 128-byte MD5 state)

**Why these fields are hashed:**
- `bit_range` — changes when lessons are added/removed
- `excluded_bits` — changes when lessons are deleted (sorted for determinism since this is a set)
- Lesson count per topic — directly affects `{topic}:total` stats
- `lesson_id` + `bit_index` — changes when lessons are reorganized or re-indexed

**Why these fields are NOT hashed:**
- `is_linear`, `is_free`, `xp`, `max_hearts` — do not affect completion totals
- `free_units`, `free_topics` — affect access, not stats counts

**Why lists are NOT sorted (except `excluded_bits`):**
- Tracks, units, topics, and lessons have a natural pedagogical order from the Frappe API (`ORDER BY idx asc`)
- This order is deterministic: Frappe → Redis JSON → Python deserialization all preserve array order
- Sorting would mask legitimate structural changes (admin reordering content IS a meaningful change)
- `excluded_bits` IS sorted because it's a set with no inherent order
- False negatives (same structure → different hash) cannot occur unless the Frappe API changes its query ordering — which would be a separate bug

**Collision risk:** Negligible. We compare one subject's structure across time (not across subjects). A 32-bit hash space with single-digit changes per day has effectively zero collision probability.

### 2.2 Where the Hash Is Computed and Stored

**Precomputed on the Frappe side** during hierarchy build in `memora_admin/api/hierarchy.py`. The hash is computed once when the hierarchy is built (on cache miss or content change), then stored as a field in the hierarchy JSON. Every subsequent deserialization on the FastAPI side simply reads the field — zero computation per request.

```python
# In memora_admin/api/hierarchy.py — get_subject_hierarchy()
# After building the full hierarchy dict:
hierarchy["content_hash"] = _compute_content_hash(hierarchy)
return hierarchy
```

**FastAPI model** receives it as a regular field:

```python
# In fastapi_app/models/progress.py — SubjectHierarchy
class SubjectHierarchy(BaseModel):
    subject_id: str
    version: int = 1
    bit_range: int
    excluded_bits: list[int] = []
    is_linear: bool = True
    free_units: list[str] = []
    free_topics: list[str] = []
    content_hash: str = ""    # ← NEW: precomputed by Frappe
    tracks: list[TrackInfo]
```

**Why precompute always (not `@computed_field`):**
- The hierarchy is built once on the Frappe side and cached as JSON in Redis (1h TTL)
- Using `@computed_field` would recompute the hash on every deserialization — wasteful
- With precomputation, the hash cost is paid **once per hierarchy build**, not once per user request
- At 50K lessons, that's ~10ms paid once vs ~10ms paid per request for every user — massive difference at 100k users
- No conditional logic needed, no escape hatch, no scale limits

**Stats side:** Stored as an additional field in the stats Redis hash:

```
memora:stats:PLAYER-00272:SUBJ-00704:v1
├── completed: "2"
├── total: "3"
├── TPC-00499:completed: "2"
├── TPC-00499:total: "3"
├── ...
└── _content_hash: "a1b2c3d4"     ← NEW FIELD
```

### 2.3 Read Path (Stats Validation)

Current cold-start check:
```python
stats = await stats_service.get_stats(user.sub, subject, hierarchy.version)
if stats is None or "total" not in stats:
    # cold start: recompute
```

New check:
```python
stats = await stats_service.get_stats(user.sub, subject, hierarchy.version)
if (
    stats is None
    or "total" not in stats
    or stats.get("_content_hash") != hierarchy.content_hash    # ← NEW
):
    # stale or missing: recompute from bitmap
    completed_bits = await progress_service.get_completed_bits(...)
    stats = compute_stats_from_hierarchy(hierarchy, completed_bits)
    stats["_content_hash"] = hierarchy.content_hash              # ← NEW
    await stats_service.set_stats(...)
```

**Performance impact:** One additional `dict.get()` + 8-char string comparison per request. O(1), ~0 measurable latency. Hash itself is already available as a plain string field on the model — no computation needed.

### 2.4 Write Path (Lesson Completion)

When a lesson is completed (`sessions.py` end_session), the existing warm-path HINCRBY logic remains unchanged:

```python
if stats_exists:
    pipe.hincrby(stats_key, "completed", 1)
    pipe.hincrby(stats_key, f"{track_id}:completed", 1)
    pipe.hincrby(stats_key, f"{unit_id}:completed", 1)
    pipe.hincrby(stats_key, f"{topic_id}:completed", 1)
    pipe.expire(stats_key, 3600)
```

**No changes needed** — HINCRBY only touches `:completed` fields, never `:total` fields. The `_content_hash` persists in the hash and remains valid as long as the structure hasn't changed.

### 2.5 Content Change Path (Zero Writes)

When an editor adds/removes/reorganizes lessons:

1. Hierarchy cache is invalidated (existing `on_content_updated` flow)
2. Next hierarchy fetch rebuilds from Frappe — `_compute_content_hash()` runs once during build
3. New hierarchy JSON contains a **different `content_hash`**
4. Each user's next stats read detects the mismatch → lazy recompute
5. **Zero writes to stats keys on content change** — no write storm

### 2.6 Cold Start Recompute Cost

When `_content_hash` mismatches, the recompute involves:

| Operation | Cost | Notes |
|-----------|------|-------|
| `get_completed_bits()` | ~2ms | Pipeline GETBIT across bitmap |
| `compute_stats_from_hierarchy()` | <1ms | In-memory tree walk, typically <500 nodes |
| `set_stats()` | ~1ms | HSET + EXPIRE |
| **Total** | **~4ms** | Well within 20ms SLA |

**Amortization at scale:** With 100k users, content changes happen infrequently (a few times per day). Each user recomputes once on their next request, spread naturally across minutes/hours. No thundering herd — different users hit the endpoint at different times.

---

## 3. Affected Components

### 3.1 Files to Modify

| File | Change | Impact |
|------|--------|--------|
| `memora_admin/api/hierarchy.py` | Add `_compute_content_hash()` function; call it at end of `get_subject_hierarchy()` | Low — one-time computation during build |
| `fastapi_app/models/progress.py` | Add `content_hash: str = ""` field to `SubjectHierarchy` | Low — plain string field |
| `fastapi_app/services/stats.py` | Include `_content_hash` in `compute_stats_from_hierarchy()` output | Low — additional hash field |
| `fastapi_app/api/v1/endpoints/progress.py` | Update staleness check in 5 endpoints | Medium — all progress endpoints need the new check |
| `fastapi_app/api/v1/endpoints/sessions.py` | Include `_content_hash` in cold-start path | Low — only the cold-start branch |

### 3.2 Files NOT Modified

| File | Reason |
|------|--------|
| `services/hierarchy.py` | No changes — it serializes/deserializes the model as-is; new field flows through automatically |
| `events/build_trigger.py` | No changes — no new invalidation needed (that's the point) |
| `services/progress.py` | No changes — bitmap logic unchanged |

### 3.3 Endpoints Affected

All endpoints that read from stats cache:

| Endpoint | Current Staleness Check | New Check |
|----------|------------------------|-----------|
| `GET /progress/` | `completed = min(completed, total)` clamp | No change (uses BITCOUNT, not stats) |
| `GET /progress/{subject}` | `stats is None or "total" not in stats` | + `_content_hash` mismatch |
| `GET /progress/{subject}/tracks` | Same | + `_content_hash` mismatch |
| `GET /progress/{subject}/tracks/{track}` | Same | + `_content_hash` mismatch |
| `GET /progress/{subject}/tracks/{track}/units/{unit}` | Same | + `_content_hash` mismatch |
| `GET /progress/{subject}/topics/{topic}/lessons` | Direct GETBIT (no stats) | No change (already correct) |

---

## 4. Edge Cases & Invariants

### 4.1 Edge Cases

| Case | Behavior | Correct? |
|------|----------|----------|
| Stats cache empty (cold start) | `stats is None` → full recompute with hash | Yes |
| Stats cache has no `_content_hash` (pre-migration) | `stats.get("_content_hash")` returns `None`, mismatches any hash → recompute | Yes — self-healing migration |
| Hierarchy cached without `content_hash` (pre-migration) | `hierarchy.content_hash` is `""` (default), mismatches any real hash → recompute; self-heals on next hierarchy rebuild | Yes — one-time recompute per user |
| Content changes while user is mid-session | Session completion HINCRBY updates old stats. Next read detects hash mismatch → recompute | Yes — at most one stale response |
| Two content changes in quick succession | Hash changes twice. User recomputes once on next read (gets latest) | Yes |
| Redis FLUSHDB | Stats gone, hierarchy gone → both re-fetched, hash computed fresh on Frappe side | Yes — existing self-healing |
| Hierarchy TTL expires, refetched with same structure | Same `content_hash` → stats remain valid, no recompute | Yes — optimal |
| Admin reorders tracks (no lesson change) | Track IDs fed in new order → hash changes → recompute | Acceptable — false positive but harmless (~4ms), and reordering is rare |

### 4.2 Invariants

1. **`content_hash` is deterministic** — same hierarchy structure + same order always produces same hash
2. **`content_hash` changes if stats totals would change** — no false negatives
3. **False positives are rare and harmless** — only occur on structural reordering without total changes, cost is ~4ms recompute
4. **No writes to stats keys on content change** — zero write amplification
5. **Backward compatible** — old stats without `_content_hash` trigger recompute (self-healing migration)
6. **HINCRBY warm path unchanged** — lesson completion performance unaffected
7. **Zero read-path computation** — hash is precomputed on Frappe side, read as a plain field on FastAPI side

---

## 5. Performance Analysis

### 5.1 At 100k Concurrent Users

| Scenario | Before (current) | After (with hash) |
|----------|-------------------|---------------------|
| Content change: admin writes | 0 writes | 0 writes |
| Content change: user reads (stale window) | Wrong totals for up to 1h | Correct on next read (~4ms recompute) |
| Normal read (no content change) | HGETALL ~1ms | HGETALL ~1ms + O(1) string compare |
| Lesson completion (warm) | 4x HINCRBY ~1ms | 4x HINCRBY ~1ms (unchanged) |
| Lesson completion (cold) | Full recompute ~4ms | Full recompute ~4ms (unchanged) |

### 5.2 Worst Case: All 100k Users Hit Stale Stats Simultaneously

- Each user's recompute: ~4ms (pipeline GETBIT + tree walk + HSET)
- These are independent per-user operations, not serialized
- Redis handles ~100k ops/sec for HSET → all recomputes complete within seconds
- No single-point bottleneck — load is distributed by natural user arrival patterns

### 5.3 Hash Computation Cost

The hash is **precomputed once** during hierarchy build on the Frappe side. It is NOT computed per request.

| Lesson Count | Hash Build Time | When Paid | Per-Request Cost |
|-------------|-----------------|-----------|------------------|
| 3 (current) | 0.026ms | Once per hierarchy build | 0ms (read field) |
| 100 (typical) | 0.085ms | Once per hierarchy build | 0ms (read field) |
| 500 (large) | 0.16ms | Once per hierarchy build | 0ms (read field) |
| 5,000 | 1.1ms | Once per hierarchy build | 0ms (read field) |
| 50,000 (upper bound) | 10.2ms | Once per hierarchy build | 0ms (read field) |

Even at 50K lessons, the 10ms cost is paid once when the hierarchy is built (on cache miss or content change) — not multiplied by 100k users. The per-request cost is always zero (just reading a string field from the deserialized model).

---

## 6. Testing Strategy

### 6.1 Unit Tests

| Test | Description |
|------|-------------|
| `test_content_hash_deterministic` | Same hierarchy → same hash (build twice, compare) |
| `test_content_hash_changes_on_lesson_add` | Adding a lesson changes the hash |
| `test_content_hash_changes_on_lesson_remove` | Removing a lesson changes the hash |
| `test_content_hash_changes_on_reorder` | Changing bit_index assignments changes the hash |
| `test_content_hash_stable_on_irrelevant_change` | Changing `is_linear`, `xp`, etc. does NOT change the hash |
| `test_content_hash_included_in_hierarchy_json` | Verify `get_subject_hierarchy()` output contains `content_hash` field |
| `test_stale_stats_detected` | Stats with old hash triggers recompute |
| `test_fresh_stats_not_recomputed` | Stats with matching hash skips recompute |
| `test_missing_hash_triggers_recompute` | Pre-migration stats (no `_content_hash`) recompute |
| `test_empty_content_hash_triggers_recompute` | Pre-migration hierarchy (`content_hash=""`) triggers recompute |

### 6.2 Integration Tests

| Test | Description |
|------|-------------|
| `test_add_lesson_updates_totals` | Add lesson to hierarchy → next progress read shows correct total |
| `test_hincrby_preserves_hash` | Complete a lesson → `_content_hash` field survives HINCRBY |
| `test_concurrent_recompute` | Multiple users hit stale stats → each gets correct result |
| `test_hierarchy_round_trip` | Build hierarchy → cache as JSON → deserialize → `content_hash` field matches |

### 6.3 Manual Verification

1. Complete all lessons in a subject → verify 100%
2. Add a new lesson via admin panel
3. Immediately call progress API → should show N/(N+1) percentage, not 100%
4. Complete the new lesson → should show 100% again

---

## 7. Rollout & Migration

### 7.1 Deployment

1. Deploy Frappe code changes (adds `_compute_content_hash` to hierarchy builder)
2. Restart Frappe workers (`bench restart`) — required for Frappe API changes
3. Deploy FastAPI code changes (new `content_hash` field + staleness check)
4. Restart FastAPI (`pkill -f "uvicorn fastapi_app.main:app"`)
5. Invalidate hierarchy caches to force rebuild with hash: `redis-cli -p 13000 DEL memora:hierarchy:SUBJ-00704` (or wait for 1h TTL)
6. **No stats data migration needed** — existing stats without `_content_hash` will self-heal on next read

### 7.2 Rollback

1. Revert code to previous version
2. The `content_hash` field in hierarchy JSON is harmless — ignored by old model (Pydantic `model_validate` ignores extra fields by default, but we added it as a field with default so old code without the field works too)
3. The `_content_hash` field in existing stats hashes is harmless — ignored by old code
4. Stats will revert to TTL-based staleness (pre-existing behavior)

### 7.3 Monitoring

| Metric | How to Track | Alert Threshold |
|--------|-------------|-----------------|
| Stats recompute rate | Log `stats_recomputed` with `reason=content_hash_mismatch` | Spike > 1000/min (indicates frequent content changes) |
| Recompute latency | Log duration of `compute_stats_from_hierarchy` | p99 > 10ms |

---

## 8. Future Considerations

### 8.1 Scaling to Millions of Users

At 1M+ users, the lazy recompute pattern remains optimal:
- Each user pays ~4ms once after content change
- No coordination between users needed
- Redis throughput is the only concern — at 1M users x 4 Redis ops each = 4M ops, spread over hours

### 8.2 Hierarchy Version Bumps

If `version` is bumped (structural migration with bitmap re-indexing), the stats key changes entirely (`v1` → `v2`). The content hash is redundant in this case but harmless — both mechanisms coexist.

### 8.3 Real-Time Stats Accuracy

For use cases requiring real-time accuracy (e.g., leaderboards, certificates), consider:
- Reading directly from bitmap + hierarchy (bypass stats cache)
- The `GET /progress/{subject}/topics/{topic}/lessons` endpoint already does this

### 8.4 Content Hash Versioning

If the hash algorithm changes in the future (e.g., adding new fields), old hashes will simply mismatch → trigger recompute. No migration needed.

### 8.5 MariaDB `completion_percentage` Staleness

This PRD addresses Redis stats cache only. The `completion_percentage` field in `tabMemora Structure Progress` (MariaDB) will also be stale after content changes. This is lower priority since:
- It's not used by the FastAPI progress APIs (they use Redis stats)
- It's only visible in the Frappe admin panel
- The periodic sync task could be extended to recalculate it using current hierarchy totals

---

## 9. Summary

| Aspect | Detail |
|--------|--------|
| **Approach** | Content hash in stats cache, lazy validation on read |
| **Hash method** | Incremental `hashlib.md5().update()`, truncated to 8 hex chars |
| **Hash computation** | Precomputed once on Frappe side during hierarchy build — zero per-request cost |
| **Ordering** | Natural hierarchy order (deterministic from Frappe API `ORDER BY idx asc`); `excluded_bits` sorted |
| **Write cost on content change** | Zero |
| **Read cost overhead** | O(1) string compare (~0ms) |
| **Staleness window** | 0 (detected on next read) |
| **Migration** | Self-healing (no data migration) |
| **Backward compatible** | Yes (missing hash triggers recompute) |
| **Files changed** | 5 files |
| **Risk** | Low (additive change, no existing behavior modified) |
