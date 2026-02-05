# Phase 17: Progress API Optimization - Research

**Researched:** 2026-02-05
**Domain:** Redis caching, SSE streaming, FastAPI performance
**Confidence:** HIGH

## Summary

This phase optimizes the progress API for large-scale subjects (50K+ lessons) by introducing a pre-computed stats caching layer and SSE streaming for progressive data delivery. The current implementation uses O(N) operations (BITCOUNT, pipeline GETBIT) that scale poorly with subject size. The optimization strategy uses Redis hashes with atomic HINCRBY updates to maintain pre-computed completion counts at each hierarchy level (subject/track/unit/topic), reducing GET /progress/{subject} from O(N) to O(1).

The SSE streaming endpoint using sse-starlette delivers progress data progressively, allowing the client to receive the subject header within 10ms while track/unit/topic details stream incrementally. This approach provides a responsive UX even for massive subjects.

**Primary recommendation:** Use Redis hash for stats caching with atomic HINCRBY on lesson completion, plus sse-starlette EventSourceResponse for progressive streaming.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | 5.0.0+ | Async Redis operations (HSET, HINCRBY, HGETALL) | Already in project, native async support |
| sse-starlette | 3.2.0 | Server-Sent Events for FastAPI | Production-ready, W3C compliant, active maintenance |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | 24.0.0+ | Structured logging | Already in project, for SSE connection events |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| sse-starlette | FastAPI StreamingResponse | StreamingResponse lacks SSE event formatting, client disconnect detection |
| Redis hash | Separate keys per stat | Hash is more memory-efficient, single HGETALL vs multiple GET |

**Installation:**
```bash
pip install sse-starlette>=3.2.0
```

## Architecture Patterns

### Recommended Redis Key Structure
```
memora:stats:{user_id}:{subject_id}:v{version}  # Hash
  - completed: <int>           # Total lessons completed
  - total: <int>               # Total lessons (from hierarchy)
  - TRK-001:completed: <int>   # Track completed count
  - TRK-001:total: <int>       # Track total
  - UNIT-001:completed: <int>  # Unit completed count
  - UNIT-001:total: <int>      # Unit total
  - TOPIC-001:completed: <int> # Topic completed count
  - TOPIC-001:total: <int>     # Topic total
```

### Pattern 1: Stats Caching with Atomic Updates
**What:** Pre-compute and cache completion counts in Redis hash, update atomically on lesson completion
**When to use:** Progress endpoints need fast response (<10ms) for large subjects
**Example:**
```python
# Source: Redis HINCRBY documentation
# On lesson completion, atomically increment all parent counters
async def update_stats_on_completion(
    redis: redis.Redis,
    user_id: str,
    subject_id: str,
    version: int,
    lesson_path: LessonPath,  # track_id, unit_id, topic_id
    is_replay: bool,
) -> None:
    """Update cached stats atomically when lesson completes."""
    if is_replay:
        return  # Don't increment on replay (already counted)

    key = f"memora:stats:{user_id}:{subject_id}:v{version}"

    # Use pipeline for atomic multi-field update
    pipe = redis.pipeline()
    pipe.hincrby(key, "completed", 1)
    pipe.hincrby(key, f"{lesson_path.track_id}:completed", 1)
    pipe.hincrby(key, f"{lesson_path.unit_id}:completed", 1)
    pipe.hincrby(key, f"{lesson_path.topic_id}:completed", 1)
    await pipe.execute()
```

### Pattern 2: Lazy Stats Initialization
**What:** Initialize stats hash from bitmap on first access, cache for subsequent reads
**When to use:** First progress request for a subject (cold start)
**Example:**
```python
# Source: Project pattern from HierarchyService
async def get_or_create_stats(
    redis: redis.Redis,
    user_id: str,
    subject_id: str,
    hierarchy: SubjectHierarchy,
    progress_service: ProgressService,
) -> dict:
    """Get cached stats or compute from bitmap if missing."""
    key = f"memora:stats:{user_id}:{subject_id}:v{hierarchy.version}"

    # Check if stats exist
    exists = await redis.exists(key)
    if exists:
        return await redis.hgetall(key)

    # Cold start: compute from bitmap and cache
    completed_bits = await progress_service.get_completed_bits(
        user_id=user_id,
        subject_id=subject_id,
        bit_range=hierarchy.bit_range,
        version=hierarchy.version,
    )

    stats = compute_stats_from_hierarchy(hierarchy, completed_bits)

    # Cache with TTL matching hierarchy cache (1 hour)
    await redis.hset(key, mapping=stats)
    await redis.expire(key, 3600)

    return stats
```

### Pattern 3: SSE Progressive Streaming
**What:** Stream progress data in chunks: subject summary first, then tracks one-by-one
**When to use:** Large subjects where client benefits from incremental UI updates
**Example:**
```python
# Source: sse-starlette GitHub documentation
from sse_starlette import EventSourceResponse

async def stream_progress(
    request: Request,
    user_id: str,
    subject_id: str,
    stats_service: StatsService,
    hierarchy_service: HierarchyService,
):
    """Stream progress data progressively via SSE."""
    async def event_generator():
        # First event: subject header (within 10ms)
        hierarchy = await hierarchy_service.get_hierarchy(subject_id)
        stats = await stats_service.get_stats(user_id, subject_id)

        yield {
            "event": "subject",
            "data": json.dumps({
                "subject_id": subject_id,
                "completed": stats["completed"],
                "total": stats["total"],
                "percentage": round(int(stats["completed"]) / int(stats["total"]) * 100, 1),
            }),
        }

        # Stream tracks progressively
        for track in hierarchy.tracks:
            if await request.is_disconnected():
                break

            track_stats = {
                "track_id": track.track_id,
                "completed": int(stats.get(f"{track.track_id}:completed", 0)),
                "total": int(stats.get(f"{track.track_id}:total", 0)),
                "units": [],  # Include unit details
            }

            # Include unit/topic details for this track
            for unit in track.units:
                unit_stats = {
                    "unit_id": unit.unit_id,
                    "completed": int(stats.get(f"{unit.unit_id}:completed", 0)),
                    "total": int(stats.get(f"{unit.unit_id}:total", 0)),
                    "topics": [],
                }
                for topic in unit.topics:
                    unit_stats["topics"].append({
                        "topic_id": topic.topic_id,
                        "completed": int(stats.get(f"{topic.topic_id}:completed", 0)),
                        "total": int(stats.get(f"{topic.topic_id}:total", 0)),
                    })
                track_stats["units"].append(unit_stats)

            yield {
                "event": "track",
                "data": json.dumps(track_stats),
            }

        # Final event signals completion
        yield {"event": "complete", "data": ""}

    return EventSourceResponse(event_generator())
```

### Anti-Patterns to Avoid
- **Recomputing stats on every request:** Current O(N) BITCOUNT/pipeline GETBIT pattern. Use cached stats instead.
- **Modifying bitmap structure:** Success criteria requires backward compatibility. Stats cache is additive layer.
- **Blocking all data on slow computation:** Use SSE streaming to deliver first chunk immediately.
- **Forgetting to update stats on lesson completion:** Hook must be wired into existing complete_lesson flow.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SSE event formatting | Custom text/event-stream response | sse-starlette EventSourceResponse | Handles W3C spec compliance, client disconnect, ping/keepalive |
| Atomic counter increment | GET + increment + SET | Redis HINCRBY | Race condition free, O(1), returns new value |
| Stats cache initialization | Manual sync logic | Lazy init on first read | Avoids migration, handles new users automatically |
| Connection monitoring | Polling request state | sse-starlette `request.is_disconnected()` | Built-in async detection |

**Key insight:** The complexity in this phase is orchestration (when to update stats, SSE event sequencing), not low-level implementation. Use established libraries for Redis operations and SSE streaming.

## Common Pitfalls

### Pitfall 1: Stats Cache Drift from Bitmap
**What goes wrong:** Stats hash gets out of sync with actual bitmap state
**Why it happens:** Missed update on completion path, manual bitmap modification, version migration
**How to avoid:**
- Hook stats update into ProgressService.complete_lesson() return path (same transaction boundary)
- Include version in stats key to auto-invalidate on hierarchy rebuild
- Add periodic reconciliation in scheduled tasks (optional safety net)
**Warning signs:** Stats show X completed but bitmap has Y bits set

### Pitfall 2: Cold Start Timeout
**What goes wrong:** First request for large subject (50K lessons) times out computing stats from bitmap
**Why it happens:** O(N) pipeline GETBIT for 50K bits takes significant time
**How to avoid:**
- SSE streaming returns subject header immediately, streams details
- Consider background job for pre-computing stats on first login
- Set reasonable Redis pipeline batch size (10K per batch)
**Warning signs:** First progress request for new subject takes >1s

### Pitfall 3: Nginx Buffering SSE
**What goes wrong:** Client receives events in batches instead of progressively
**Why it happens:** Nginx default response buffering (16KB threshold)
**How to avoid:**
- Set `X-Accel-Buffering: no` header on SSE responses
- Configure nginx: `proxy_buffering off` for SSE endpoint
**Warning signs:** All events arrive at once instead of progressively

### Pitfall 4: Missing Stats Update on Replay
**What goes wrong:** Stats count increases even for replay completions
**Why it happens:** Not checking is_replay before incrementing
**How to avoid:** ProgressService.complete_lesson() returns is_replay boolean; only increment stats when is_replay=False
**Warning signs:** Stats completed count exceeds total lessons

### Pitfall 5: Stats Hash Memory Growth
**What goes wrong:** Stats hashes for inactive users consume memory indefinitely
**Why it happens:** No TTL on stats hash
**How to avoid:** Set TTL matching hierarchy cache (1 hour) and refresh on access
**Warning signs:** Redis memory growth correlated with user base, not active users

## Code Examples

Verified patterns from official sources:

### StatsService Class Structure
```python
# Source: Project pattern from existing services
class StatsService:
    """Manages pre-computed progress statistics in Redis hash.

    Key pattern: memora:stats:{user_id}:{subject_id}:v{version}

    Fields stored:
    - completed: total lessons completed
    - total: total lessons in subject
    - {track_id}:completed, {track_id}:total
    - {unit_id}:completed, {unit_id}:total
    - {topic_id}:completed, {topic_id}:total
    """

    CACHE_TTL = 3600  # 1 hour, matches HierarchyService

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _stats_key(self, user_id: str, subject_id: str, version: int) -> str:
        return f"{self.prefix}stats:{user_id}:{subject_id}:v{version}"
```

### HINCRBY Pipeline for Atomic Update
```python
# Source: Redis HINCRBY documentation (https://redis.io/docs/latest/commands/hincrby/)
async def increment_completion_stats(
    self,
    user_id: str,
    subject_id: str,
    version: int,
    track_id: str,
    unit_id: str,
    topic_id: str,
) -> None:
    """Atomically increment all completion counters.

    O(1) per field, O(4) total. Returns immediately.
    """
    key = self._stats_key(user_id, subject_id, version)

    pipe = self.redis.pipeline()
    pipe.hincrby(key, "completed", 1)
    pipe.hincrby(key, f"{track_id}:completed", 1)
    pipe.hincrby(key, f"{unit_id}:completed", 1)
    pipe.hincrby(key, f"{topic_id}:completed", 1)
    # Refresh TTL on update
    pipe.expire(key, self.CACHE_TTL)
    await pipe.execute()
```

### SSE Endpoint with EventSourceResponse
```python
# Source: sse-starlette documentation (https://github.com/sysid/sse-starlette)
from sse_starlette import EventSourceResponse
from starlette.requests import Request

@router.get("/stream/{subject}")
async def stream_subject_progress(
    subject: str,
    request: Request,
    user: CurrentUser,
    stats_service: StatsServiceDep,
    hierarchy_service: HierarchyServiceDep,
) -> EventSourceResponse:
    """Stream progress data via Server-Sent Events.

    Events:
    - subject: {subject_id, completed, total, percentage}
    - track: {track_id, completed, total, units: [...]}
    - complete: signals end of stream
    """
    async def generate():
        # Implementation as shown in Pattern 3 above
        pass

    return EventSourceResponse(
        generate(),
        headers={"X-Accel-Buffering": "no"},  # Disable nginx buffering
    )
```

### Computing Stats from Bitmap (Cold Start)
```python
# Source: Project pattern from existing progress endpoint
def compute_stats_from_hierarchy(
    hierarchy: SubjectHierarchy,
    completed_bits: set[int],
) -> dict[str, str]:
    """Compute all stats from hierarchy and bitmap.

    Used for cold start initialization.
    Returns dict suitable for HSET mapping.
    """
    stats: dict[str, str] = {}

    subject_completed = 0
    subject_total = 0

    for track in hierarchy.tracks:
        track_completed = 0
        track_total = 0

        for unit in track.units:
            unit_completed = 0
            unit_total = 0

            for topic in unit.topics:
                topic_completed = sum(
                    1 for lesson in topic.lessons
                    if lesson.bit_index in completed_bits
                )
                topic_total = len(topic.lessons)

                stats[f"{topic.topic_id}:completed"] = str(topic_completed)
                stats[f"{topic.topic_id}:total"] = str(topic_total)

                unit_completed += topic_completed
                unit_total += topic_total

            stats[f"{unit.unit_id}:completed"] = str(unit_completed)
            stats[f"{unit.unit_id}:total"] = str(unit_total)

            track_completed += unit_completed
            track_total += unit_total

        stats[f"{track.track_id}:completed"] = str(track_completed)
        stats[f"{track.track_id}:total"] = str(track_total)

        subject_completed += track_completed
        subject_total += track_total

    stats["completed"] = str(subject_completed)
    stats["total"] = str(subject_total)

    return stats
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| BITCOUNT for total | Pre-computed stats hash | This phase | O(N) -> O(1) |
| Full JSON response | SSE progressive streaming | This phase | TTFB from N*ms to <10ms |
| Recompute on every request | Atomic increment on completion | This phase | Scales to 50K+ lessons |

**Deprecated/outdated:**
- None - this is additive optimization, not replacement

## Integration Points

### Hook into Lesson Completion
The stats update must be wired into the existing completion flow:

1. **sessions.py end_session endpoint** - After `progress_service.complete_lesson()`, call stats update
2. **progress.py complete endpoint** - Alternative completion path, also needs hook

Both paths return `is_replay` boolean - only increment stats when `is_replay=False`.

### LessonPath Resolution
To update stats atomically, need track_id/unit_id/topic_id for the completed lesson. Options:
1. Store in GameSession (adds session fields)
2. Look up from hierarchy (already cached, minimal overhead)
3. Add to LessonInfo model (extend bitmap JSON)

**Recommendation:** Look up from cached hierarchy - hierarchy is already fetched in completion flow for bit_index.

### Endpoint Routing
New SSE endpoint at `/api/v1/progress/stream/{subject}` alongside existing REST endpoint.
Existing `GET /progress/{subject}` remains unchanged (backward compatible) but now reads from stats cache.

## Open Questions

Things that couldn't be fully resolved:

1. **Stats reconciliation strategy**
   - What we know: Stats can drift from bitmap on edge cases (manual fixes, version migration)
   - What's unclear: How often reconciliation should run, acceptable drift window
   - Recommendation: Scheduled task weekly, or on-demand via admin API

2. **SSE client reconnection handling**
   - What we know: sse-starlette handles server-side cleanup; client must reconnect
   - What's unclear: Whether to maintain last-event-id tracking for resume
   - Recommendation: Keep simple - client re-requests full stream on disconnect (progress data is small)

## Sources

### Primary (HIGH confidence)
- Redis HINCRBY documentation - https://redis.io/docs/latest/commands/hincrby/
- Redis HSET documentation - https://redis.io/docs/latest/commands/hset/
- Redis pipelining documentation - https://redis.io/docs/latest/develop/using-commands/pipelining/
- sse-starlette GitHub - https://github.com/sysid/sse-starlette
- sse-starlette PyPI - https://pypi.org/project/sse-starlette/

### Secondary (MEDIUM confidence)
- Redis hash memory-efficient storage - https://oneuptime.com/blog/post/2026-01-21-redis-hashes-memory-efficient/view
- Redis distributed counters - https://oneuptime.com/blog/post/2026-01-27-redis-distributed-counters/view
- FastAPI streaming response patterns - https://fastapi.tiangolo.com/advanced/custom-response/

### Tertiary (LOW confidence)
- General SSE best practices from web search (WebSearch only)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - redis-py already in project, sse-starlette is well-documented PyPI package
- Architecture: HIGH - patterns follow existing codebase conventions (HierarchyService, ProgressService)
- Pitfalls: MEDIUM - based on Redis documentation and SSE infrastructure knowledge

**Research date:** 2026-02-05
**Valid until:** 2026-03-05 (30 days - stable patterns)
