# Research: Stats Cache Staleness Detection

**Feature**: 019-stats-content-hash
**Date**: 2026-02-18
**Status**: Complete

## Research Questions

### RQ-1: Hash Algorithm Selection

**Question**: Which hash algorithm should be used for the structural fingerprint?

**Decision**: `hashlib.md5()` with incremental `.update()` calls, truncated to 8 hex characters (32 bits).

**Rationale**:
- MD5 is available in Python stdlib (`hashlib`) — no additional dependency
- Incremental `hash.update()` avoids intermediate string allocation — constant memory overhead (128-byte MD5 state)
- 8 hex chars (32 bits) is sufficient for collision resistance in this use case: we compare one subject's structure across time (not across subjects), with single-digit changes per day
- MD5 is not used for security here — only as a structural fingerprint
- At 50K lessons, hash computation takes ~10ms — paid once per hierarchy build, never per request

**Alternatives considered**:
- `xxhash` (faster): Would require an external dependency (`xxhash` pip package). MD5 is fast enough for our use case (≤10ms for 50K lessons, paid once per build)
- `sha256`: Overkill for non-security fingerprinting. More expensive than MD5 with no practical benefit
- CRC32: Faster but higher collision risk. MD5 provides a better balance
- Full string concatenation + hash: Memory-intensive for large hierarchies. Incremental `.update()` is strictly better

### RQ-2: Which Hierarchy Fields Affect Stats Totals?

**Question**: What fields must be included in the content hash to ensure no false negatives (stale stats always detected)?

**Decision**: Hash these fields:
- `bit_range` (top-level) — changes when lessons added/removed
- `excluded_bits` (top-level, sorted) — changes when lessons deleted
- `track_id` per track — track identity
- `unit_id` per unit — unit identity
- `topic_id` per topic — topic identity
- Lesson count per topic — `len(topic["lessons"])` directly affects `{topic}:total`
- `lesson_id` per lesson — lesson identity
- `bit_index` per lesson — changes when lessons reorganized or re-indexed

**Do NOT hash these fields** (they don't affect completion totals):
- `is_linear` — affects navigation, not counts
- `is_free` — affects access control, not counts
- `xp` — affects XP calculations, not completion counts
- `max_hearts` — affects session mechanics, not completion counts
- `free_units`, `free_topics` — affects access, not stats counts
- `is_sold_separately` — affects purchasing, not stats

**Rationale**: Stats caches contain `completed` and `total` counts at subject/track/unit/topic levels. Only fields that affect the lesson count or lesson identity within these aggregation levels should trigger a hash change.

### RQ-3: Hash Computation Location

**Question**: Should the hash be computed on the Frappe side (hierarchy build) or FastAPI side (on demand)?

**Decision**: Precompute on the Frappe side in `memora_admin/api/hierarchy.py`, stored as `hierarchy["content_hash"]`.

**Rationale**:
- The hierarchy is built once on cache miss and cached as JSON in Redis (1h TTL)
- Precomputing pays the hash cost once per build, not once per user request
- At 100k users, this avoids 100k redundant hash computations per hierarchy TTL cycle
- The FastAPI `SubjectHierarchy` model simply reads the field — zero per-request cost
- `@computed_field` alternative would recompute on every Pydantic deserialization — wasteful

**Alternatives considered**:
- `@computed_field` on `SubjectHierarchy`: Would recompute hash on every `model_validate_json()` call. At 100k users per subject, this wastes ~10ms × 100k = 1M ms per hour
- Lazy computation with caching: More complex; the hierarchy dict is already the natural place to embed the hash
- Separate Redis key for hash: Adds a second cache to manage; embedding in the hierarchy JSON is simpler and atomic

### RQ-4: List Ordering and Determinism

**Question**: Should lists (tracks, units, topics, lessons) be sorted before hashing?

**Decision**: Do NOT sort — use natural order from Frappe API (`ORDER BY idx asc`).

**Rationale**:
- Frappe queries all hierarchy levels with `ORDER BY idx asc`, producing deterministic order
- This order is preserved through: Frappe → Redis JSON → Python deserialization (JSON arrays maintain order)
- Sorting would mask legitimate structural changes — if an admin reorders tracks, this IS a meaningful change that should trigger recompute
- Only `excluded_bits` is sorted because it's a set with no inherent order
- False negatives (same structure → different hash) cannot occur unless Frappe API changes its query ordering — which would be a separate bug

**Alternatives considered**:
- Sort all lists by ID: Would mask reordering changes. Also adds O(n log n) overhead per hierarchy build

### RQ-5: Staleness Check Integration Pattern

**Question**: How should the staleness check be integrated into the 4 stats-reading progress endpoints?

**Decision**: Extend the existing cold-start check condition:

```python
# Before (current):
if stats is None or "total" not in stats:

# After (new):
if stats is None or "total" not in stats or stats.get("_content_hash") != hierarchy.content_hash:
```

**Rationale**:
- Minimal code change — one additional `or` clause per endpoint
- `dict.get("_content_hash")` returns `None` for pre-migration stats → `None != hash` → recompute (self-healing)
- O(1) string comparison (~0ms) — no performance impact
- The recompute path already exists; we just trigger it more precisely

**Alternatives considered**:
- Middleware-based check: Over-engineered for a simple condition extension
- Decorator pattern: Would require refactoring all 4 endpoints — higher risk
- Separate validation function: Adds indirection without benefit — the check is a single line

### RQ-6: Pre-Migration Behavior

**Question**: How do existing stats caches (without `_content_hash`) behave after deployment?

**Decision**: Self-healing — pre-migration stats trigger recompute on next read.

**Rationale**:
- `stats.get("_content_hash")` returns `None` for old stats
- `None != hierarchy.content_hash` (any non-empty string) → evaluates as stale → recompute
- Recompute writes fresh stats including `_content_hash` — user is healed
- No bulk migration needed — healing is lazy and distributed across user requests
- At 100k users, the recompute storm is naturally amortized (users arrive at different times)

### RQ-7: Impact on HINCRBY Warm Path

**Question**: Does the `_content_hash` field survive HINCRBY operations on the stats hash?

**Decision**: Yes — HINCRBY only touches `:completed` fields; `_content_hash` persists.

**Rationale**:
- Redis `HINCRBY` operates on individual hash fields: `completed`, `{track}:completed`, `{unit}:completed`, `{topic}:completed`
- It does NOT delete or modify other fields in the hash
- `EXPIRE` resets TTL for the entire hash but does not modify field values
- Therefore `_content_hash` persists through any number of HINCRBY operations
- Verified by reading the current warm path in `sessions.py` lines 316-353

## Summary

All research questions resolved. No NEEDS CLARIFICATION items remain. The feature is well-specified by the PRD and can proceed directly to Phase 1 design.
