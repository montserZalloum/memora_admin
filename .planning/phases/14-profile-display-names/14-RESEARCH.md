# Phase 14: Profile Display Names - Research

**Researched:** 2026-02-03
**Domain:** Profile caching, batch Redis operations, Frappe/FastAPI integration
**Confidence:** HIGH

## Summary

This phase enriches leaderboard API responses with human-readable display names and avatars from Memora Player Profile. The existing leaderboard infrastructure (Phase 10) returns placeholders; this phase adds a ProfileService to cache and batch-fetch profile data.

The standard approach is:
1. **Redis hash per player** for profile cache with 1-hour TTL on the entire key
2. **Redis pipeline** for batch HMGET operations (100 profiles in single round-trip)
3. **Frappe doc_events hook** for cache push on profile update
4. **On-demand fetch** for cache misses with graceful fallback
5. **Scheduled pre-warming job** for active leaderboard players

**Primary recommendation:** Use individual Redis keys per profile (`memora:profile:{player_id}`) with pipeline MGET for batch fetch. This is simpler and more compatible than hash-field TTL which requires Redis 7.4+.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | >=5.0.0 | Async Redis client with pipeline support | Already in requirements.txt, async/await native |
| structlog | >=24.0.0 | Structured logging | Consistent with existing services |
| Pydantic | v2 | Data validation for profile models | Already used throughout FastAPI app |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | >=0.27.0 | Frappe API calls via FrappeClient | Batch profile fetch on cache miss |
| frappe | v15 | Doc events for cache invalidation hooks | Push cache updates on profile save |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Individual keys (MGET) | Single hash (HGETALL) | Single hash with HEXPIRE requires Redis 7.4+; individual keys work on all versions |
| String keys | Hash fields | Hash fields can't have per-field TTL on Redis <7.4; string keys with MGET equally fast |
| Frappe hook push | Pull-only caching | Push ensures sub-second invalidation per success criteria |

**Installation:**
No new packages needed - all dependencies already in requirements.txt.

## Architecture Patterns

### Recommended Project Structure
```
fastapi_app/
├── services/
│   └── profile.py         # ProfileService - NEW
├── models/
│   └── profile.py         # PlayerProfile Pydantic model - NEW (or extend leaderboard.py)
└── api/v1/endpoints/
    └── leaderboard.py     # Modified to use ProfileService

memora_admin/
├── events/
│   └── profile_sync.py    # Cache push on profile update - NEW
├── api/
│   └── profile.py         # Batch profile fetch API - NEW
└── tasks/
    └── profile_cache.py   # Hourly pre-warming job - NEW
```

### Pattern 1: Individual Keys with Pipeline MGET
**What:** Store each profile as a JSON string at `memora:profile:{player_id}`, fetch multiple profiles in single round-trip using pipeline.
**When to use:** Always (compatible with all Redis versions, simple TTL management)
**Example:**
```python
# Source: Established pattern in codebase (HierarchyService, PlanService)
class ProfileService:
    CACHE_TTL = 3600  # 1 hour per success criteria

    def _cache_key(self, player_id: str) -> str:
        return f"{self.prefix}profile:{player_id}"

    async def get_profiles_batch(
        self, player_ids: list[str]
    ) -> dict[str, PlayerProfile | None]:
        """Batch fetch profiles using Redis pipeline.

        Per research: Pipeline batches reduce RTT from N to 1.
        Target: <25ms for 100 entries (success criteria).
        """
        if not player_ids:
            return {}

        keys = [self._cache_key(pid) for pid in player_ids]

        # Pipeline MGET - single round-trip for all keys
        pipe = self.redis.pipeline()
        pipe.mget(keys)
        results = await pipe.execute()

        profiles = {}
        cache_misses = []

        for pid, data in zip(player_ids, results[0]):
            if data:
                profiles[pid] = PlayerProfile.model_validate_json(data)
            else:
                cache_misses.append(pid)

        # Fill cache misses from Frappe
        if cache_misses:
            fetched = await self._fetch_from_frappe_batch(cache_misses)
            profiles.update(fetched)

        return profiles
```

### Pattern 2: Frappe Hook for Cache Push
**What:** Push profile to Redis cache immediately when admin updates Memora Player Profile
**When to use:** Always (ensures sub-second invalidation per success criteria)
**Example:**
```python
# Source: Established pattern in memora_admin/events/access_sync.py
def on_player_profile_updated(doc, method):
    """Push profile to Redis cache on update.

    Per CONTEXT.md: "Profile cache invalidates within seconds"
    """
    cache = frappe.cache()
    redis_key = f"memora:profile:{doc.user}"

    profile_data = {
        "player_id": doc.user,
        "display_name": doc.display_name or "",
        "avatar": doc.avatar or "default_avatar",
    }

    # Set with TTL
    cache.set(
        redis_key,
        json.dumps(profile_data),
        ex=3600,  # 1 hour TTL
    )
```

### Pattern 3: Graceful Fallback
**What:** Generate anonymous display name when profile is missing or empty
**When to use:** Always (per CONTEXT.md decision: "Anonymous {last 4 digits}")
**Example:**
```python
def _apply_fallback(self, player_id: str) -> PlayerProfile:
    """Generate fallback profile for missing data.

    Per CONTEXT.md: "Anonymous 1234" format.
    """
    last_four = player_id[-4:] if len(player_id) >= 4 else player_id
    return PlayerProfile(
        player_id=player_id,
        display_name=f"Anonymous {last_four}",
        avatar="default_avatar",
    )
```

### Anti-Patterns to Avoid
- **N+1 queries:** Never loop through player_ids calling Redis individually - use pipeline MGET
- **Blocking Frappe calls:** Never call Frappe synchronously in the request path for cache misses of 100 entries - use async and consider timeouts
- **Hash with field TTL on Redis <7.4:** HEXPIRE only available since Redis 7.4.0 - don't assume it's available
- **Unbounded batch fetches:** Limit Frappe batch fetch to reasonable size (e.g., 50 at a time) to avoid timeouts

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Pipeline batching | Manual request batching | redis.pipeline() | Built-in, handles async correctly |
| JSON serialization | Manual dict conversion | Pydantic model_dump_json() | Type safety, consistent with codebase |
| Cache key naming | Ad-hoc strings | Prefix pattern from other services | Consistent key namespace (`memora:profile:`) |
| Frappe batch fetch | Loop of get_doc | frappe.get_all with filters | Single DB query vs N queries |
| TTL management | Manual expiry tracking | Redis SET with EX parameter | Atomic, server-managed |

**Key insight:** The codebase has established patterns (HierarchyService, PlanService, SettingsService) that handle caching. ProfileService should follow the same structure.

## Common Pitfalls

### Pitfall 1: N+1 Redis Calls in Leaderboard Endpoint
**What goes wrong:** Calling `get_profile(player_id)` in a loop for 100 leaderboard entries
**Why it happens:** Natural programming instinct to iterate
**How to avoid:** Always use `get_profiles_batch(player_ids)` with pipeline MGET
**Warning signs:** Leaderboard response time grows linearly with entry count

### Pitfall 2: Synchronous Frappe Calls Blocking FastAPI
**What goes wrong:** `await frappe_client.call()` for 100 cache misses takes >25ms
**Why it happens:** Cold cache or batch Frappe API that's slow
**How to avoid:**
1. Pre-warm cache with scheduled job (hourly)
2. Limit batch Frappe fetch to 50 profiles, apply fallback for remaining
3. Set timeout on Frappe batch calls (5 seconds max)
**Warning signs:** Spike in latency when cache is cold

### Pitfall 3: Hook Runs But FastAPI Doesn't See Update
**What goes wrong:** Frappe hook pushes to Redis but FastAPI reads stale data
**Why it happens:** Different Redis connections or key mismatch
**How to avoid:**
1. Use exact same key pattern in hook and service
2. Use `frappe.cache()` which connects to same Redis as FastAPI
3. Log the key in both places for debugging
**Warning signs:** Profile updates visible in Frappe but not in API

### Pitfall 4: Empty Display Name Treated as Valid
**What goes wrong:** Profile exists but display_name is empty string, API returns ""
**Why it happens:** Profile created without display_name, or cleared later
**How to avoid:** Per CONTEXT.md decision: "Empty display_name treated as missing, use fallback"
**Warning signs:** Blank names in leaderboard UI

### Pitfall 5: Scheduled Task Fetches Too Many Profiles
**What goes wrong:** Hourly pre-warm tries to cache all 10K profiles, times out
**Why it happens:** Over-eager warming
**How to avoid:** Only pre-warm profiles of players currently in top 100 leaderboards
**Warning signs:** Scheduled task duration >1 minute, Frappe API errors

## Code Examples

Verified patterns from official sources and existing codebase:

### Redis Pipeline MGET (Batch Fetch)
```python
# Source: redis-py docs + codebase pattern
async def get_profiles_batch(self, player_ids: list[str]) -> dict[str, str | None]:
    """Get multiple profiles in single Redis round-trip."""
    if not player_ids:
        return {}

    keys = [self._cache_key(pid) for pid in player_ids]

    # Pipeline batches multiple commands
    async with self.redis.pipeline() as pipe:
        for key in keys:
            pipe.get(key)
        results = await pipe.execute()

    return dict(zip(player_ids, results))
```

### Frappe Batch Get (For Cache Miss)
```python
# Source: Established pattern in memora_admin/api/hierarchy.py
@frappe.whitelist(allow_guest=False)
def get_profiles_batch(player_ids: list[str]) -> list[dict]:
    """Batch fetch profiles from Memora Player Profile.

    Args:
        player_ids: List of user IDs to fetch

    Returns:
        List of profile dicts with player_id, display_name, avatar
    """
    if not player_ids:
        return []

    # Single query for all profiles
    profiles = frappe.get_all(
        "Memora Player Profile",
        filters={"user": ["in", player_ids]},
        fields=["user", "display_name", "avatar"],
    )

    return [
        {
            "player_id": p.user,
            "display_name": p.display_name or "",
            "avatar": p.avatar or "default_avatar",
        }
        for p in profiles
    ]
```

### Leaderboard Endpoint Integration
```python
# Source: Pattern from fastapi_app/api/v1/endpoints/leaderboard.py
@router.get("/{lb_type}", response_model=LeaderboardResponse)
async def get_leaderboard(
    lb_type: LeaderboardTypeParam,
    user: CurrentUser,
    leaderboard_service: LeaderboardServiceDep,
    profile_service: ProfileServiceDep,  # NEW dependency
    limit: int = Query(10, ge=1, le=100),
    subject_id: str | None = Query(None),
) -> LeaderboardResponse:
    # Fetch top players from leaderboard
    raw_entries = await leaderboard_service.get_top(lb_type, limit, subject_id)

    # Batch fetch all profiles in single operation
    player_ids = [entry["player_id"] for entry in raw_entries]
    profiles = await profile_service.get_profiles_batch(player_ids)

    # Build response with profile data
    entries = [
        LeaderboardEntry(
            rank=entry["rank"],
            player_id=entry["player_id"],
            display_name=profiles.get(entry["player_id"]).display_name,
            xp=entry["xp"],
            avatar=profiles.get(entry["player_id"]).avatar,
            is_me=entry["player_id"] == user.sub,
        )
        for entry in raw_entries
    ]

    return LeaderboardResponse(...)
```

### Scheduled Pre-Warming Task
```python
# Source: Pattern from memora_admin/tasks/leaderboard_reset.py
def warm_profile_cache():
    """Pre-warm profile cache for active leaderboard players.

    Scheduled: Hourly at :30 (after leaderboard archival at :10)

    Strategy: Get top 100 from each active leaderboard, dedupe, cache all.
    """
    r = get_redis()

    # Collect unique player_ids from all active leaderboards
    player_ids = set()

    for lb_key in ["memora:lb:alltime", _today_daily_key(), _current_weekly_key()]:
        # Get top 100 player_ids
        top_players = r.zrange(lb_key, 0, 99, desc=True)
        player_ids.update(p.decode() if isinstance(p, bytes) else p for p in top_players)

    # Batch fetch from Frappe and cache
    if player_ids:
        profiles = frappe.get_all(
            "Memora Player Profile",
            filters={"user": ["in", list(player_ids)]},
            fields=["user", "display_name", "avatar"],
        )

        pipe = r.pipeline()
        for p in profiles:
            key = f"memora:profile:{p.user}"
            data = json.dumps({
                "player_id": p.user,
                "display_name": p.display_name or "",
                "avatar": p.avatar or "default_avatar",
            })
            pipe.set(key, data, ex=3600)
        pipe.execute()

        logger.info(f"Pre-warmed {len(profiles)} profiles")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hash with per-key TTL | Hash with per-field TTL (HEXPIRE) | Redis 7.4.0 (Oct 2024) | Can set TTL on individual hash fields now, but requires Redis upgrade |
| Sync redis-py | Async redis-py | redis-py 4.2+ | All operations should use async/await |
| python-jose for JWT | PyJWT | Project decision | Lighter, cleaner API (already in codebase) |

**Deprecated/outdated:**
- ZREVRANGE: Deprecated in Redis 6.2+, use `ZRANGE ... DESC` instead (already done in leaderboard.py)
- Sync Redis client: Project uses async redis-py exclusively

**Note on Redis 7.4 HEXPIRE:** While HEXPIRE allows per-field TTL in hashes, the project should NOT assume Redis 7.4+ is available. Use individual string keys with MGET pipeline for compatibility.

## Open Questions

Things that couldn't be fully resolved:

1. **Redis Server Version**
   - What we know: Project uses redis-py >=5.0.0, which supports HEXPIRE if server is 7.4+
   - What's unclear: Actual deployed Redis version
   - Recommendation: Use individual keys with TTL (works on all versions), not HEXPIRE

2. **Frappe Batch API Timeout**
   - What we know: FrappeClient has 30s timeout
   - What's unclear: How many profiles can be fetched in <5 seconds
   - Recommendation: Limit batch to 50 profiles, apply fallback for remainder if timeout

3. **Pre-warming Job Frequency**
   - What we know: Decision says "hourly scheduled job"
   - What's unclear: Exact timing relative to other scheduled tasks
   - Recommendation: Run at :30 (after leaderboard archive at :10, before hourly session cleanup at :15)

## Sources

### Primary (HIGH confidence)
- Existing codebase: `services/leaderboard.py`, `services/hierarchy.py`, `services/plan.py` - established patterns
- Existing codebase: `events/access_sync.py`, `events/device_sync.py` - Frappe hook patterns
- Existing codebase: `tasks/leaderboard_reset.py` - scheduled task patterns
- Redis official docs: [Pipelining](https://redis.io/docs/latest/develop/using-commands/pipelining/)
- Redis official docs: [HEXPIRE](https://redis.io/docs/latest/commands/hexpire/) - Redis 7.4.0+ only

### Secondary (MEDIUM confidence)
- [redis-py GitHub Issue #3360](https://github.com/redis/redis-py/issues/3360) - HEXPIRE support in v5.0.8+
- [Last9 Redis Pipeline Guide](https://last9.io/blog/how-to-make-the-most-of-redis-pipeline/) - Performance benefits

### Tertiary (LOW confidence)
- General caching best practices from web search - should validate against actual codebase patterns

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All components already in requirements.txt and used in codebase
- Architecture: HIGH - Follows established patterns from HierarchyService, PlanService
- Pitfalls: HIGH - Derived from actual codebase constraints and research notes
- Redis version compatibility: MEDIUM - Assumed <7.4, recommend conservative approach

**Research date:** 2026-02-03
**Valid until:** 2026-03-03 (30 days - stable domain, established patterns)

---

*Phase: 14-profile-display-names*
*Research completed: 2026-02-03*
