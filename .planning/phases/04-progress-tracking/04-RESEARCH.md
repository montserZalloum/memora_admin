# Phase 4: Progress Tracking - Research

**Researched:** 2026-02-02
**Domain:** Redis bitmap progress tracking, linear unlock state calculation, completion queue patterns
**Confidence:** HIGH

## Summary

This research covers implementing bitmap-based lesson completion tracking with linear unlock enforcement for the Memora platform. The system uses Redis bitmaps (SETBIT/GETBIT operations) to track per-player, per-subject lesson completion with O(1) time complexity for both reads and writes. Unlock state calculation respects is_linear flags at Track, Unit, and Topic levels, requiring 100% completion of previous items before the next unlocks (first item always accessible).

The standard approach uses Redis bitmap operations directly through redis-py's async API for lesson completion tracking. Progress percentages are computed server-side since clients lack access to _b.json structure data. A completion queue using Redis lists (LPUSH/BRPOP) handles failure scenarios with eventual 202 Accepted responses per CONTEXT.md. Bitmap versioning maintains separate bitmaps per content version when structure changes.

**Primary recommendation:** Use redis-py async bitmap operations (setbit/getbit/bitcount) with key pattern `memora:progress:{user_id}:{subject_id}:{version}`, compute unlock states using hierarchy cache with is_linear flags, and implement completion queue via Redis LPUSH/BRPOP for reliability.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | 5.0+ | Bitmap operations (setbit/getbit/bitcount) | Already in project, async support, O(1) bit operations |
| FastAPI | 0.115+ | Progress endpoints with nested response models | Established in project, Pydantic integration |
| Pydantic | 2.0+ | Progress response models with computed fields | Type-safe nested JSON structures |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | 24.0+ | Progress operation logging | Debug slow operations, audit completion events |
| httpx | 0.27+ | Frappe API calls (via FrappeClient) | Fetching hierarchy data for percentage calculations |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Redis bitmaps | Redis strings with JSON | Bitmaps are 8x more memory efficient for boolean states |
| SETBIT per lesson | BITFIELD batch | SETBIT is simpler; BITFIELD better for multiple bits in one call |
| Redis list queue | RQ/Celery | Redis list is simpler, no extra infrastructure needed |
| Per-request hierarchy fetch | Cached hierarchy in Redis | Cache adds complexity but critical for <20ms target |

**Installation:**
No new dependencies required - all libraries already in project from Phase 1-3.

## Architecture Patterns

### Recommended Project Structure
```
fastapi_app/
├── api/
│   └── v1/
│       └── endpoints/
│           └── progress.py         # Progress endpoints
├── services/
│   └── progress.py                 # ProgressService (bitmaps, unlock calc)
└── models/
    └── progress.py                 # Progress request/response models

memora_admin/memora_admin/
└── api/
    └── progress.py                 # Frappe whitelisted methods for hierarchy
```

### Pattern 1: Redis Bitmap for Lesson Completion
**What:** Store lesson completion as individual bits, one bitmap per player-subject-version
**When to use:** All progress tracking operations

```python
# Source: Redis official docs (SETBIT, GETBIT, BITCOUNT)
# Key pattern: memora:progress:{user_id}:{subject_id}:{version}

class ProgressService:
    """Manages lesson completion via Redis bitmaps."""

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _progress_key(self, user_id: str, subject_id: str, version: int = 1) -> str:
        """Generate Redis key for player's progress bitmap."""
        return f"{self.prefix}progress:{user_id}:{subject_id}:v{version}"

    async def mark_lesson_complete(
        self,
        user_id: str,
        subject_id: str,
        bit_index: int,
        version: int = 1,
    ) -> bool:
        """
        Mark a lesson as complete using SETBIT.
        O(1) operation. Idempotent - setting same bit twice is safe.

        Returns:
            Previous value of the bit (0 if first completion, 1 if replay)
        """
        key = self._progress_key(user_id, subject_id, version)
        previous = await self.redis.setbit(key, bit_index, 1)
        return bool(previous)  # True = replay, False = first completion

    async def is_lesson_complete(
        self,
        user_id: str,
        subject_id: str,
        bit_index: int,
        version: int = 1,
    ) -> bool:
        """Check if lesson is complete using GETBIT. O(1) operation."""
        key = self._progress_key(user_id, subject_id, version)
        return bool(await self.redis.getbit(key, bit_index))

    async def count_completed(
        self,
        user_id: str,
        subject_id: str,
        version: int = 1,
    ) -> int:
        """Count total completed lessons using BITCOUNT. O(N) on bitmap size."""
        key = self._progress_key(user_id, subject_id, version)
        return await self.redis.bitcount(key)
```

### Pattern 2: Linear Unlock State Calculation
**What:** Compute unlock states respecting is_linear flags at each hierarchy level
**When to use:** Progress endpoint response, pre-flight check for completion

```python
# Source: CONTEXT.md unlock rules + research analysis

def calculate_unlock_state(
    hierarchy: SubjectHierarchy,  # Cached from _h.json or Redis
    completed_bits: set[int],     # Set of completed lesson bit_indexes
) -> dict[str, bool]:
    """
    Calculate unlock state for all lessons in subject.

    Rules per CONTEXT.md:
    - First item in any sequence is ALWAYS unlocked
    - is_linear at Track level: units must complete in order
    - is_linear at Unit level: topics must complete in order
    - is_linear at Topic level: lessons must complete in order
    - Unlock requires 100% completion of previous item

    Returns:
        Dict mapping lesson_id to unlocked status
    """
    unlock_states = {}

    for track_idx, track in enumerate(hierarchy.tracks):
        # Track-level: first track always unlocked
        track_unlocked = track_idx == 0 or not hierarchy.is_linear

        if track_idx > 0 and hierarchy.is_linear:
            # Previous track must be 100% complete
            prev_track = hierarchy.tracks[track_idx - 1]
            track_unlocked = _is_track_complete(prev_track, completed_bits)

        for unit_idx, unit in enumerate(track.units):
            # Unit-level: first unit always unlocked if track unlocked
            unit_unlocked = track_unlocked and (
                unit_idx == 0 or not track.is_linear
            )

            if unit_idx > 0 and track.is_linear:
                prev_unit = track.units[unit_idx - 1]
                unit_unlocked = track_unlocked and _is_unit_complete(prev_unit, completed_bits)

            for topic_idx, topic in enumerate(unit.topics):
                # Topic-level: first topic always unlocked if unit unlocked
                topic_unlocked = unit_unlocked and (
                    topic_idx == 0 or not unit.is_linear
                )

                if topic_idx > 0 and unit.is_linear:
                    prev_topic = unit.topics[topic_idx - 1]
                    topic_unlocked = unit_unlocked and _is_topic_complete(prev_topic, completed_bits)

                for lesson_idx, lesson in enumerate(topic.lessons):
                    # Lesson-level: first lesson always unlocked if topic unlocked
                    lesson_unlocked = topic_unlocked and (
                        lesson_idx == 0 or not topic.is_linear
                    )

                    if lesson_idx > 0 and topic.is_linear:
                        prev_lesson = topic.lessons[lesson_idx - 1]
                        lesson_unlocked = topic_unlocked and prev_lesson.bit_index in completed_bits

                    unlock_states[lesson.lesson_id] = lesson_unlocked

    return unlock_states

def _is_topic_complete(topic: TopicInfo, completed_bits: set[int]) -> bool:
    """Check if all lessons in topic are complete."""
    return all(lesson.bit_index in completed_bits for lesson in topic.lessons)

def _is_unit_complete(unit: UnitInfo, completed_bits: set[int]) -> bool:
    """Check if all topics in unit are complete."""
    return all(_is_topic_complete(topic, completed_bits) for topic in unit.topics)

def _is_track_complete(track: TrackInfo, completed_bits: set[int]) -> bool:
    """Check if all units in track are complete."""
    return all(_is_unit_complete(unit, completed_bits) for unit in track.units)
```

### Pattern 3: Progress Response with Percentage Breakdown
**What:** Nested Pydantic models for progress percentages at each hierarchy level
**When to use:** GET /progress/{subject} endpoint

```python
# Source: FastAPI nested models + CONTEXT.md decisions

from pydantic import BaseModel, computed_field

class TopicProgress(BaseModel):
    """Progress for a single topic."""
    topic_id: str
    completed: int
    total: int

    @computed_field
    @property
    def percentage(self) -> float:
        return round(self.completed / self.total * 100, 1) if self.total > 0 else 0.0

class UnitProgress(BaseModel):
    """Progress for a single unit."""
    unit_id: str
    completed: int
    total: int
    topics: list[TopicProgress]

    @computed_field
    @property
    def percentage(self) -> float:
        return round(self.completed / self.total * 100, 1) if self.total > 0 else 0.0

class TrackProgress(BaseModel):
    """Progress for a single track."""
    track_id: str
    completed: int
    total: int
    units: list[UnitProgress]

    @computed_field
    @property
    def percentage(self) -> float:
        return round(self.completed / self.total * 100, 1) if self.total > 0 else 0.0

class SubjectProgress(BaseModel):
    """Full progress breakdown for a subject."""
    subject_id: str
    completed: int
    total: int
    tracks: list[TrackProgress]

    @computed_field
    @property
    def percentage(self) -> float:
        return round(self.completed / self.total * 100, 1) if self.total > 0 else 0.0

class SubjectSummary(BaseModel):
    """Summary progress for GET /progress listing."""
    subject_id: str
    subject_name: str
    percentage: float
    completed: int
    total: int
```

### Pattern 4: Completion Queue for Reliability
**What:** Redis list-based queue for completion requests with retry on failure
**When to use:** POST /complete endpoint returning 202 Accepted

```python
# Source: Redis lists docs (LPUSH/BRPOP) + CONTEXT.md failure handling

COMPLETION_QUEUE_KEY = "memora:completion:queue"
COMPLETION_PROCESSING_KEY = "memora:completion:processing"

class CompletionQueueService:
    """Reliable completion queue using Redis lists."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def enqueue_completion(
        self,
        user_id: str,
        subject_id: str,
        lesson_id: str,
        bit_index: int,
    ) -> str:
        """
        Add completion request to queue.
        Returns request ID for tracking.
        """
        import uuid
        import json

        request_id = str(uuid.uuid4())
        payload = json.dumps({
            "request_id": request_id,
            "user_id": user_id,
            "subject_id": subject_id,
            "lesson_id": lesson_id,
            "bit_index": bit_index,
            "timestamp": datetime.utcnow().isoformat(),
        })

        await self.redis.lpush(COMPLETION_QUEUE_KEY, payload)
        return request_id

    async def process_completion(self) -> dict | None:
        """
        Process one completion from queue.
        Uses BRPOPLPUSH for reliability.
        """
        # Move item to processing list atomically
        item = await self.redis.brpoplpush(
            COMPLETION_QUEUE_KEY,
            COMPLETION_PROCESSING_KEY,
            timeout=5,
        )

        if not item:
            return None

        payload = json.loads(item)
        return payload

    async def ack_completion(self, payload: dict) -> None:
        """Remove processed item from processing list."""
        await self.redis.lrem(COMPLETION_PROCESSING_KEY, 1, json.dumps(payload))
```

### Pattern 5: Hierarchy Cache for Fast Unlock Calculation
**What:** Cache subject hierarchy in Redis for unlock calculations
**When to use:** Progress endpoint, completion validation

```python
# Source: Performance requirements + CONTEXT.md <20ms target

class HierarchyCache:
    """Cache subject hierarchy for fast unlock calculations."""

    CACHE_TTL = 3600  # 1 hour TTL, invalidated on build

    def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient):
        self.redis = redis_client
        self.frappe = frappe_client

    def _cache_key(self, subject_id: str) -> str:
        return f"memora:hierarchy:{subject_id}"

    async def get_hierarchy(self, subject_id: str) -> SubjectHierarchy | None:
        """Get hierarchy from cache or fetch from Frappe."""
        key = self._cache_key(subject_id)
        cached = await self.redis.get(key)

        if cached:
            return SubjectHierarchy.model_validate_json(cached)

        # Fetch from Frappe and cache
        hierarchy = await self.frappe.get_subject_hierarchy(subject_id)
        if hierarchy:
            await self.redis.set(
                key,
                hierarchy.model_dump_json(),
                ex=self.CACHE_TTL,
            )

        return hierarchy

    async def invalidate(self, subject_id: str) -> None:
        """Invalidate cache on content change."""
        key = self._cache_key(subject_id)
        await self.redis.delete(key)
```

### Pattern 6: Replay Detection and Counter
**What:** Track replay count for analytics, detect replay via SETBIT return value
**When to use:** Completion endpoint for XP calculation

```python
# Source: CONTEXT.md replay handling + Redis SETBIT semantics

REPLAY_COUNTER_KEY = "memora:replays:{user_id}:{subject_id}"

async def record_completion(
    self,
    user_id: str,
    subject_id: str,
    lesson_id: str,
    bit_index: int,
) -> tuple[bool, int]:
    """
    Record lesson completion with replay detection.

    Returns:
        Tuple of (is_replay: bool, replay_count: int)
    """
    # SETBIT returns previous value: 0 = first time, 1 = replay
    progress_key = self._progress_key(user_id, subject_id)
    previous = await self.redis.setbit(progress_key, bit_index, 1)
    is_replay = bool(previous)

    replay_count = 0
    if is_replay:
        # Increment replay counter for analytics
        replay_key = f"memora:replays:{user_id}:{subject_id}:{lesson_id}"
        replay_count = await self.redis.incr(replay_key)

    return (is_replay, replay_count)
```

### Anti-Patterns to Avoid
- **Fetching full bitmap with GET:** Use GETBIT for individual checks, BITCOUNT for totals
- **Storing unlock states in Redis:** Compute on-the-fly from bitmap + hierarchy
- **Synchronous completion processing:** Use queue for reliability per CONTEXT.md
- **Fetching hierarchy per request without cache:** Will exceed <20ms target
- **Using SMEMBERS to get completed lessons:** Bitmaps with bit_index are more efficient
- **Separate Redis calls per lesson for progress:** Batch with pipeline or BITFIELD

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Boolean state tracking | JSON arrays or sets | Redis bitmaps (SETBIT/GETBIT) | O(1) operations, 8x memory savings |
| Replay detection | Check-then-set logic | SETBIT return value | Atomic, returns previous state |
| Completion totals | Loop over bits manually | Redis BITCOUNT | Optimized C implementation |
| Reliable queue | Custom MariaDB table | Redis BRPOPLPUSH | Atomic move, no lost messages |
| Cache invalidation | Manual TTL management | Redis pub/sub from build pipeline | Real-time, decoupled |

**Key insight:** Redis bitmap operations (SETBIT, GETBIT, BITCOUNT) are purpose-built for tracking binary states. They provide O(1) individual bit operations and highly optimized counting. The SETBIT return value naturally handles replay detection without extra queries.

## Common Pitfalls

### Pitfall 1: Exceeding Bitmap Size Limit
**What goes wrong:** Bitmap grows beyond 512MB (2^32 bits max offset)
**Why it happens:** Bit indexes not properly assigned, gaps in sequence
**How to avoid:** Use dense bit allocation starting from 0; excluded_bits handles deleted lessons
**Warning signs:** Redis memory spikes, SETBIT errors

### Pitfall 2: Stale Hierarchy Cache After Content Change
**What goes wrong:** Unlock states calculated against old structure
**Why it happens:** Build pipeline doesn't invalidate hierarchy cache
**How to avoid:** Publish cache invalidation message when build completes
**Warning signs:** Wrong unlock states after content updates

### Pitfall 3: Slow Progress Endpoint from Hierarchy Fetch
**What goes wrong:** Progress endpoint exceeds <20ms target
**Why it happens:** Fetching hierarchy from MariaDB on each request
**How to avoid:** Cache hierarchy in Redis with TTL; invalidate on build
**Warning signs:** p99 latency > 20ms on progress endpoints

### Pitfall 4: Lost Completions on Failure
**What goes wrong:** Player completes lesson but completion not recorded
**Why it happens:** Server crashes between receiving request and writing bitmap
**How to avoid:** Use completion queue with BRPOPLPUSH; return 202 Accepted
**Warning signs:** Player complains about lost progress

### Pitfall 5: Incorrect Percentage from Excluded Bits
**What goes wrong:** Progress percentage doesn't account for deleted lessons
**Why it happens:** Dividing completed count by total bit_range without excluding
**How to avoid:** Use _b.json's excluded_bits to compute actual total lessons
**Warning signs:** Percentages exceed 100% or don't reach 100%

### Pitfall 6: Bypass of Unlock State Check on Completion
**What goes wrong:** Player marks locked lesson complete
**Why it happens:** Completion endpoint doesn't validate unlock state
**How to avoid:** Check unlock state BEFORE setting bit; 403 if locked
**Warning signs:** Progress shows impossible completion patterns

## Code Examples

Verified patterns from official sources:

### Redis Bitmap Operations (Async)
```python
# Source: redis-py async documentation + Redis SETBIT/GETBIT docs
import redis.asyncio as redis

async def bitmap_example():
    r = await redis.from_url("redis://localhost", decode_responses=True)

    # Set bit at offset 7 to 1
    previous = await r.setbit("user:123:progress", 7, 1)
    print(f"Previous value: {previous}")  # 0 = first time, 1 = replay

    # Get bit at offset 7
    value = await r.getbit("user:123:progress", 7)
    print(f"Current value: {value}")  # 1

    # Count all set bits
    count = await r.bitcount("user:123:progress")
    print(f"Completed lessons: {count}")

    await r.aclose()
```

### Complete Progress Service
```python
# Source: Redis docs + project patterns from Phase 3
from typing import Optional
import redis.asyncio as redis

class ProgressService:
    """Manages lesson completion via Redis bitmaps."""

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _progress_key(self, user_id: str, subject_id: str, version: int = 1) -> str:
        """Generate Redis key for player's progress bitmap."""
        return f"{self.prefix}progress:{user_id}:{subject_id}:v{version}"

    async def complete_lesson(
        self,
        user_id: str,
        subject_id: str,
        bit_index: int,
        version: int = 1,
    ) -> bool:
        """
        Mark lesson complete. Returns True if this was a replay.
        O(1) operation via SETBIT.
        """
        key = self._progress_key(user_id, subject_id, version)
        previous = await self.redis.setbit(key, bit_index, 1)
        return bool(previous)

    async def is_complete(
        self,
        user_id: str,
        subject_id: str,
        bit_index: int,
        version: int = 1,
    ) -> bool:
        """Check if lesson is complete. O(1) via GETBIT."""
        key = self._progress_key(user_id, subject_id, version)
        return bool(await self.redis.getbit(key, bit_index))

    async def get_completed_count(
        self,
        user_id: str,
        subject_id: str,
        version: int = 1,
    ) -> int:
        """Count completed lessons. O(N) on bitmap size via BITCOUNT."""
        key = self._progress_key(user_id, subject_id, version)
        return await self.redis.bitcount(key)

    async def get_completed_bits(
        self,
        user_id: str,
        subject_id: str,
        bit_range: int,
        version: int = 1,
    ) -> set[int]:
        """
        Get set of completed bit indexes.
        Used for unlock state calculation.
        """
        key = self._progress_key(user_id, subject_id, version)

        # Use pipeline to batch GETBIT calls
        completed = set()
        async with self.redis.pipeline() as pipe:
            for i in range(bit_range):
                pipe.getbit(key, i)
            results = await pipe.execute()

        for i, is_set in enumerate(results):
            if is_set:
                completed.add(i)

        return completed
```

### Completion Endpoint with Double-Gate and Unlock Check
```python
# Source: CONTEXT.md requirements + FastAPI patterns
from fastapi import APIRouter, HTTPException, status, Response
from pydantic import BaseModel

router = APIRouter(prefix="/progress", tags=["progress"])

class CompleteRequest(BaseModel):
    """Request body for lesson completion."""
    subject: str  # e.g., "MATH-G5"
    lesson: str   # e.g., "LESSON-001"

@router.post("/complete", status_code=status.HTTP_200_OK)
async def complete_lesson(
    request: CompleteRequest,
    user: CurrentUser,
    progress_service: ProgressServiceDep,
    hierarchy_cache: HierarchyCacheDep,
    access_service: AccessServiceDep,
):
    """
    Mark a lesson as complete.

    Per CONTEXT.md:
    - Enforces Double-Gate (validated by dependency)
    - Enforces unlock state (403 if locked)
    - Returns minimal response: { success: true }
    - Idempotent: re-completing returns 200 OK
    """
    # Get hierarchy for unlock validation
    hierarchy = await hierarchy_cache.get_hierarchy(request.subject)
    if not hierarchy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
        )

    # Find lesson info
    lesson_info = hierarchy.find_lesson(request.lesson)
    if not lesson_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "LESSON_NOT_FOUND", "message": "Lesson not found"},
        )

    # Check unlock state
    completed_bits = await progress_service.get_completed_bits(
        user_id=user.sub,
        subject_id=request.subject,
        bit_range=hierarchy.bit_range,
    )

    unlock_states = calculate_unlock_state(hierarchy, completed_bits)
    if not unlock_states.get(request.lesson, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "LESSON_LOCKED", "message": "Lesson is locked"},
        )

    # Mark complete (idempotent)
    is_replay = await progress_service.complete_lesson(
        user_id=user.sub,
        subject_id=request.subject,
        bit_index=lesson_info.bit_index,
    )

    # Note: XP/wallet updates are Phase 5 responsibility

    return {"success": True}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Database boolean columns per lesson | Redis bitmaps | Redis 2.2 (2010) | O(1) vs O(N) query, 8x memory savings |
| JSON progress arrays | Bitmap bit indexes | Standard practice | Compact storage, fast operations |
| Synchronous completion | Queue-based async | Reliability requirement | No lost completions |
| Full hierarchy query | Cached hierarchy | Performance requirement | Meets <20ms target |

**Deprecated/outdated:**
- `RPOPLPUSH` command: Still works but `LMOVE` is the newer equivalent (Redis 6.2+)
- Synchronous doc fetches for hierarchy: Too slow for <20ms target; must cache

## Open Questions

Things that couldn't be fully resolved:

1. **Bitmap Versioning Key Format**
   - What we know: CONTEXT.md says "keep separate bitmaps per content version"
   - Options: `progress:{user}:{subject}:v{version}` vs `progress:{user}:{subject}:{build_id}`
   - Recommendation: Use version number from _b.json's metadata; increment on structural changes
   - Rationale: Version number is simpler, build_id could accumulate many bitmaps

2. **Unlock State in Progress Response vs Separate Endpoint**
   - What we know: CONTEXT.md marks as Claude's discretion
   - Options: Include in progress response OR separate `/unlock/{subject}` endpoint
   - Recommendation: Include unlock flags in progress response (single round-trip)
   - Rationale: Client needs both for UI; separate endpoint doubles latency

3. **Completion Queue vs Direct Write**
   - What we know: CONTEXT.md says "server queues completion requests and returns 202"
   - What's unclear: Always queue, or only on failure?
   - Recommendation: Direct write first, queue only on Redis failure
   - Rationale: Fast path for happy case; queue provides reliability for edge cases

4. **Hierarchy Cache Invalidation Mechanism**
   - What we know: Build pipeline will invalidate via pub/sub (Phase 6)
   - What's unclear: Exact pub/sub channel and message format
   - Recommendation: Plan for channel `memora:cache:invalidate` with `{subject_id, type: "hierarchy"}`
   - Rationale: Standard pub/sub pattern, will align with Phase 6

## Sources

### Primary (HIGH confidence)
- [Redis Bitmaps Documentation](https://redis.io/docs/latest/develop/data-types/bitmaps/) - SETBIT, GETBIT, BITCOUNT operations and O(1) complexity
- [Redis SETBIT Command](https://redis.io/docs/latest/commands/setbit/) - Returns previous bit value for replay detection
- [redis-py GitHub](https://github.com/redis/redis-py/blob/master/doctests/dt_bitmap.py) - Async bitmap operations
- [FastAPI Nested Models](https://fastapi.tiangolo.com/tutorial/body-nested-models/) - Pydantic nested response structures

### Secondary (MEDIUM confidence)
- [Redis Lists for Queues](https://redis.io/docs/latest/develop/data-types/lists/) - LPUSH/BRPOP queue patterns
- [Redis Reliable Queue Pattern](https://redis.io/docs/latest/commands/rpoplpush/) - RPOPLPUSH for reliability
- [ScaleGrid Redis Bitmaps](https://scalegrid.io/blog/introduction-to-redis-data-structure-bitmaps/) - Progress tracking use case

### Tertiary (LOW confidence)
- [University XP Game Progression](https://www.universityxp.com/blog/2024/1/16/what-are-progression-systems-in-games) - Linear progression patterns in games

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH - Redis bitmap operations verified via official docs
- Architecture: HIGH - Follows established project patterns from Phase 1-3
- Bitmap Operations: HIGH - Redis official documentation confirms O(1) complexity
- Unlock Calculation: MEDIUM - Algorithm designed from CONTEXT.md rules, not external source
- Completion Queue: MEDIUM - Redis queue pattern is standard, exact implementation is project-specific
- Progress Response: HIGH - FastAPI nested models from official tutorial

**Research date:** 2026-02-02
**Valid until:** 2026-03-02 (30 days - stable Redis bitmap API, established patterns)
