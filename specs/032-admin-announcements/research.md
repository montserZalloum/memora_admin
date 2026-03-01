# Research: Admin Announcement System

**Feature Branch**: `032-admin-announcements`
**Date**: 2026-02-28

## Research Tasks & Findings

### R1: Redis Caching Strategy for Announcements

**Decision**: Single-key cache with all active announcements, filtered at read time.

**Rationale**: The spec assumes < 20 active announcements at any time. A single Redis STRING key holding a JSON array of all active announcements is sufficient. Filtering by plan and date at read time is O(n) where n < 20 — negligible cost. This avoids maintaining multiple cache keys (per-plan) and simplifies invalidation to a single `DEL`.

**Alternatives considered**:
- **Per-plan keys** (`memora:announcements:plan:{plan_id}` + `memora:announcements:global`): More cache keys to invalidate, complex when announcements change targeting. Unnecessary at < 20 announcements.
- **Hash per announcement**: Over-engineered for the data volume. HGETALL + filtering is slower than a single GET + JSON parse for small datasets.

**Key design**:
```
memora:announcements:active   # STRING (JSON array)
                               # TTL: 5 min (short — handles date-based expiry naturally)
                               # Producers: Frappe event hook on Announcement DocType
                               # Consumers: AnnouncementService.get_for_player()
```

**TTL rationale**: 5-minute TTL provides natural handling of expired announcements (effective_end_date) without requiring a scheduled cleanup task. Admin actions (create/edit/delete) trigger immediate invalidation via two-pronged pattern (DEL + pubsub), so the 5-min TTL is only a safety net for date-based expiry.

---

### R2: Player Language Resolution

**Decision**: Accept `lang` as a required query parameter from the mobile app.

**Rationale**: The JWT `TokenPayload` does not include `preferred_lang`. Looking it up from Redis session or profile cache adds an extra round-trip per request. Since the mobile app already knows the player's language preference (it's used for all UI rendering), passing it as a query param is the most performant approach. This keeps the announcement endpoint at a single Redis GET + JSON parse + in-memory filter — well under 10ms.

**Alternatives considered**:
- **Redis session lookup**: Extra `HGET memora:session:{user} preferred_lang` per request. Adds ~0.5ms but creates a dependency on session data being populated.
- **Store both languages in response**: Client picks the right one. Doubles payload size unnecessarily. The spec says "return content in the player's preferred language only."
- **Add preferred_lang to JWT**: Would require token refresh for language changes. Over-scoped for this feature.

**Validation**: `lang` parameter validated to `ar` or `en`. Invalid values default to `ar` (matching the profile field default).

---

### R3: Cache Invalidation Pattern

**Decision**: Two-pronged invalidation (direct DEL + pubsub) following the existing `build_trigger.py` / `catalog_sync.py` pattern.

**Rationale**: Consistent with established patterns. Direct DEL ensures immediate cache clear. Pubsub notifies FastAPI's in-process services (if any local caching is added later). Since announcements have no in-process local cache (unlike hierarchy), the pubsub is primarily for consistency and future-proofing.

**Implementation**:
1. Frappe hook on `Memora Announcement` (after_insert, on_update, on_trash) → calls `_invalidate_announcements_cache()`
2. `_invalidate_announcements_cache()`: DEL `memora:announcements:active` + publish to `memora:cache:invalidate` channel with `{"type": "announcements"}`
3. FastAPI pubsub listener handles `"announcements"` type → calls `AnnouncementService.invalidate()`

**No debouncing needed**: Unlike content builds that cascade to multiple plans, announcement invalidation is a single key DEL — cheap and idempotent.

---

### R4: Self-Healing Hydration Pattern

**Decision**: Implement cache-miss hydration via Frappe API, following `CatalogService.get_catalog()` pattern (not the `ensure_hydrated()` + `guarded_hydrate()` pattern).

**Rationale**: The `ensure_hydrated()` pattern is for per-player data where thundering herd is a concern (100K players all hydrating simultaneously). Announcements are shared data — one cache key for ALL users. A single Redis miss triggers one Frappe API call that rebuilds the cache for everyone. The `CatalogService` pattern (inline cache-miss fetch) is more appropriate.

**Implementation**:
```python
async def get_active_announcements(self) -> list[dict]:
    cached = await self.redis.get(announcements_active_key())
    if cached is not None:
        return json.loads(cached)

    # Cache miss: fetch from Frappe
    result = await self.frappe.call(
        "memora_admin.api.announcements.get_active_announcements"
    )
    announcements = result or []

    # Cache with 5-min TTL
    await self.redis.set(
        announcements_active_key(),
        json.dumps(announcements),
        ex=ANNOUNCEMENTS_CACHE_TTL
    )
    return announcements
```

**Thundering herd mitigation**: For the first request after cache miss, multiple concurrent requests may all attempt hydration. This is acceptable because:
1. The Frappe API call is idempotent (SELECT query)
2. The Redis SET is idempotent (last write wins, all identical)
3. Duration is brief (single DB query, < 50ms)
4. Happens at most once per 5 minutes under normal load

If needed later, we can add `SET NX` locking, but YAGNI for now.

---

### R5: DocType Design Decisions

**Decision**: Two DocTypes — `Memora Announcement` (parent) + `Memora Announcement Target Plan` (child table).

**Rationale**: Follows the existing `Memora Academic Plan` + `Memora Plan Subject` child table pattern. The child table holds plan references when `target_audience = "Specific Plans"`. Using a child table (vs. a separate linked DocType) keeps the admin UX simple — plans are edited inline on the announcement form.

**Naming**: `ANN-.#####.` (consistent with `PLAYER-.#####.`, `PLAN-.#####.`, `SEAS-.#####.`)

**Field decisions**:
- `title_ar` / `title_en` / `body_ar` / `body_en` as separate fields (not a child table per language) — simpler for admin UX with only 2 languages
- `body_*` as `Text Editor` fieldtype — plain text per spec ("Announcements are plain text only for v1"), but Text Editor gives basic formatting if needed later. Actually, spec says "plain text only" so use `Small Text` instead.
- `effective_start_date` / `effective_end_date` as read-only computed fields — set by `validate()` based on duration type
- `display_frequency` stored as string enum and returned in API response — client-side enforcement per spec

---

### R6: Frappe API for Cache Hydration

**Decision**: Create a Frappe whitelist API at `memora_admin.api.announcements.get_active_announcements` that returns all currently active announcements.

**Rationale**: This is the Frappe-side data source for FastAPI cache-miss hydration. Returns pre-filtered data (published, within date range) with target plan IDs embedded, so the FastAPI service only needs plan-matching and language selection.

**Query approach**: Use `frappe.get_all()` with filters on `is_published=1`, then filter by date range in Python (MariaDB date comparison is fine for < 100 total announcements). Include child table data via `frappe.get_doc()` for each matching announcement.

**Performance**: This runs only on cache miss (every 5 min at worst). With < 100 total announcements and < 20 active, the query is trivially fast.

---

### R7: Form UX (depends_on / mandatory_depends_on)

**Decision**: Use Frappe's `depends_on` and `mandatory_depends_on` field properties for conditional visibility and validation.

**Rationale**: Frappe v15 supports `depends_on` expressions that show/hide fields based on other field values. This handles FR-019 (contextual field visibility) without custom JavaScript.

**Implementation**:
- `target_plans` table: `depends_on = "eval:doc.target_audience=='Specific Plans'"`
- `start_date` / `end_date`: `depends_on = "eval:doc.duration_type=='Date Range'"`
- `duration_days`: `depends_on = "eval:doc.duration_type=='Fixed Duration'"`
- `target_plans` mandatory: enforced in Python `validate()` (Frappe's `mandatory_depends_on` can also handle this)
