# Phase 18: Lesson Completion Status API - Research

**Researched:** 2026-02-06
**Domain:** FastAPI endpoints, Redis bitmap operations, per-lesson completion lookups
**Confidence:** HIGH

## Summary

This phase implements a dedicated endpoint for fetching lesson completion status within a topic. The existing infrastructure from Phase 17 provides all the building blocks needed: cached hierarchy (with topic->lessons mapping including `bit_index`), Redis bitmaps for completion state, and optimized O(1) bit lookups via GETBIT.

The endpoint `GET /progress/{subject}/topics/{topic_id}/lessons` returns completion status for all lessons in a topic. The key insight is that this is a lightweight operation: (1) get cached hierarchy O(1), (2) filter to find the topic O(T) where T is small, (3) pipeline GETBIT for each lesson O(L) where L is lessons in topic, and (4) return results. Total time target <5ms is easily achievable because hierarchies are cached, topics typically have 5-20 lessons, and GETBIT is O(1) per bit.

No new Redis storage is required. The existing bitmap key `memora:progress:{user_id}:{subject_id}:v{version}` already stores completion state. The hierarchy already maps lesson_id to bit_index. This is purely an endpoint addition.

**Primary recommendation:** Add a single endpoint that filters the cached hierarchy to the target topic, then uses a Redis pipeline to batch GETBIT operations for all lessons in that topic.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | 5.0.0+ | Async Redis GETBIT via pipeline | Already in project, sub-ms per operation |
| FastAPI | 0.109.0+ | Endpoint definition | Already in project |
| Pydantic | 2.0+ | Response models | Already in project |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | 24.0.0+ | Structured logging | Already in project, for request logging |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Pipeline GETBIT | Redis BITFIELD GET | BITFIELD can get multiple bits in one command, but pipeline GETBIT is simpler and fast enough for 100 lessons |
| Filtering cached hierarchy | New topic->lessons index | Would add complexity, hierarchy is already cached and O(T) search is negligible |

**Installation:**
```bash
# No new dependencies required - all libraries already in project
```

## Architecture Patterns

### Existing Infrastructure (No Changes Needed)

The following already exists and will be reused:

1. **SubjectHierarchy model** (`models/progress.py`):
   - Contains `tracks[].units[].topics[].lessons[]`
   - Each lesson has `lesson_id` and `bit_index`
   - Cached in Redis with 1 hour TTL via HierarchyService

2. **ProgressService.is_complete()** (`services/progress.py`):
   - Single bit lookup via GETBIT O(1)
   - Key: `memora:progress:{user_id}:{subject_id}:v{version}`

3. **Progress endpoint patterns** (`api/v1/endpoints/progress.py`):
   - Access control via `access_service.check_access_with_plan()`
   - Hierarchy fetching via `hierarchy_service.get_hierarchy()`
   - Unlock state calculation patterns

### Pattern 1: Topic Lookup from Cached Hierarchy
**What:** Find topic within cached hierarchy by topic_id
**When to use:** Need to get lessons for a specific topic
**Example:**
```python
# Source: Existing pattern from progress.py
def find_topic_in_hierarchy(
    hierarchy: SubjectHierarchy,
    topic_id: str,
) -> TopicInfo | None:
    """Find topic by ID within cached hierarchy.

    O(T * U * To) worst case, but typically <100 iterations total.
    """
    for track in hierarchy.tracks:
        for unit in track.units:
            for topic in unit.topics:
                if topic.topic_id == topic_id:
                    return topic
    return None
```

### Pattern 2: Batch GETBIT via Pipeline
**What:** Check completion status for multiple lessons in one round-trip
**When to use:** Need completion status for all lessons in a topic (5-100 lessons)
**Example:**
```python
# Source: Existing pattern from ProgressService.get_completed_bits()
async def get_lesson_completion_status(
    redis_client: redis.Redis,
    user_id: str,
    subject_id: str,
    version: int,
    lessons: list[LessonInfo],
) -> list[bool]:
    """Get completion status for a list of lessons.

    Uses pipeline for O(N) operations in single round-trip.
    N = number of lessons (typically 5-100 per topic).
    """
    key = f"memora:progress:{user_id}:{subject_id}:v{version}"

    pipe = redis_client.pipeline()
    for lesson in lessons:
        pipe.getbit(key, lesson.bit_index)

    results = await pipe.execute()
    return [bool(r) for r in results]
```

### Pattern 3: Response Model with Full Lesson Info
**What:** Return lesson_id, bit_index, and completed for each lesson
**When to use:** Frontend needs to display lesson list with completion status
**Example:**
```python
# Source: Project pattern from existing models
class LessonCompletionStatus(BaseModel):
    """Completion status for a single lesson."""
    lesson_id: str
    bit_index: int
    completed: bool


class TopicLessonsResponse(BaseModel):
    """All lessons in a topic with completion status."""
    topic_id: str
    total: int
    completed: int
    lessons: list[LessonCompletionStatus]

    @computed_field
    @property
    def percentage(self) -> float:
        """Calculate completion percentage."""
        if self.total == 0:
            return 0.0
        return round(self.completed / self.total * 100, 1)
```

### Recommended Endpoint Structure
```python
@router.get("/{subject}/topics/{topic_id}/lessons", response_model=TopicLessonsResponse)
async def get_topic_lessons(
    subject: str,
    topic_id: str,
    user: CurrentUser,
    hierarchy_service: HierarchyServiceDep,
    access_service: AccessServiceDep,
    progress_service: ProgressServiceDep,
) -> TopicLessonsResponse:
    """
    Get completion status for all lessons in a topic.

    Performance: <5ms for topics with up to 100 lessons.
    - Hierarchy fetch: O(1) from Redis cache
    - Topic lookup: O(T*U*To) but <1ms for typical subjects
    - GETBIT pipeline: O(L) in single round-trip, ~1ms
    """
```

### Anti-Patterns to Avoid
- **Fetching hierarchy from Frappe:** Always use cached HierarchyService.get_hierarchy()
- **Individual GETBIT calls:** Use pipeline for batch operation
- **New storage structures:** Reuse existing bitmap, no new Redis keys needed
- **Traversing full hierarchy for stats:** Just filter to topic, count locally

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Topic lookup in hierarchy | Custom index/cache | `find_topic_in_hierarchy()` | Hierarchy already cached, search is <1ms |
| Batch bit checks | Loop of individual GETBIT | Redis pipeline | Single round-trip vs L round-trips |
| Access control | Custom checks | `access_service.check_access_with_plan()` | Already handles grants + plan membership |
| Hierarchy caching | Custom caching | `hierarchy_service.get_hierarchy()` | Already cached with 1hr TTL |

**Key insight:** This endpoint is almost entirely composed of existing primitives. The new code is primarily glue and response formatting.

## Common Pitfalls

### Pitfall 1: Not Using Cached Hierarchy
**What goes wrong:** Endpoint calls Frappe API directly, adding 100-500ms latency
**Why it happens:** Developer forgets hierarchy is cached
**How to avoid:** Always use `hierarchy_service.get_hierarchy()` - it returns cached data in <1ms
**Warning signs:** Endpoint latency >10ms, Frappe API calls in progress endpoint

### Pitfall 2: Forgetting Access Control
**What goes wrong:** Endpoint returns lesson data for subjects user doesn't have access to
**Why it happens:** Copy-paste without access check
**How to avoid:** Follow existing progress.py patterns - check access before returning data
**Warning signs:** No `access_service.check_access_with_plan()` call

### Pitfall 3: Topic Not Found Returns 500
**What goes wrong:** Exception thrown when topic_id doesn't exist in hierarchy
**Why it happens:** Missing None check after find_topic_in_hierarchy()
**How to avoid:** Return 404 with structured error if topic not found
**Warning signs:** 500 errors in logs for invalid topic_ids

### Pitfall 4: Missing Version in Bitmap Key
**What goes wrong:** Reading from wrong bitmap version after hierarchy update
**Why it happens:** Hardcoded version=1 instead of using hierarchy.version
**How to avoid:** Always use `hierarchy.version` for bitmap key construction
**Warning signs:** Completion status doesn't match actual progress

### Pitfall 5: Endpoint Route Order in FastAPI
**What goes wrong:** `/{subject}/topics/{topic_id}/lessons` captured by `/{subject}` catch-all
**Why it happens:** More specific routes must be defined before generic ones
**How to avoid:** Define new endpoint BEFORE `/{subject}` endpoint in router
**Warning signs:** 404 or wrong response when calling new endpoint

## Code Examples

Verified patterns from existing codebase:

### Complete Endpoint Implementation Pattern
```python
# Source: Following existing progress.py patterns

from fastapi_app.models.progress import TopicInfo, LessonInfo


class LessonCompletionStatus(BaseModel):
    """Completion status for a single lesson."""
    lesson_id: str
    bit_index: int
    completed: bool


class TopicLessonsResponse(BaseModel):
    """All lessons in a topic with completion status."""
    topic_id: str
    total: int
    completed: int
    lessons: list[LessonCompletionStatus]

    @computed_field
    @property
    def percentage(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.completed / self.total * 100, 1)


def find_topic_in_hierarchy(hierarchy: SubjectHierarchy, topic_id: str) -> TopicInfo | None:
    """Find topic by ID within hierarchy."""
    for track in hierarchy.tracks:
        for unit in track.units:
            for topic in unit.topics:
                if topic.topic_id == topic_id:
                    return topic
    return None


@router.get("/{subject}/topics/{topic_id}/lessons", response_model=TopicLessonsResponse)
async def get_topic_lessons(
    subject: str,
    topic_id: str,
    user: CurrentUser,
    hierarchy_service: HierarchyServiceDep,
    access_service: AccessServiceDep,
    progress_service: ProgressServiceDep,
) -> TopicLessonsResponse:
    """Get completion status for all lessons in a topic."""
    # Get cached hierarchy
    hierarchy = await hierarchy_service.get_hierarchy(subject)
    if not hierarchy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBJECT_NOT_FOUND", "message": "Subject not found"},
        )

    # Find topic in hierarchy
    topic = find_topic_in_hierarchy(hierarchy, topic_id)
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TOPIC_NOT_FOUND", "message": "Topic not found"},
        )

    # Check access (same as existing endpoints)
    content_key = f"SUB-{subject}"
    has_access = await access_service.check_access_with_plan(user.sub, content_key, user.plan)
    if not has_access:
        if not hierarchy.has_any_free_content():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "NO_ACCESS", "message": "Content access required"},
            )

    # Get completion status for all lessons via pipeline
    lessons_status = []
    completed_count = 0

    if topic.lessons:
        # Use pipeline for batch GETBIT
        key = f"memora:progress:{user.sub}:{subject}:v{hierarchy.version}"
        pipe = progress_service.redis.pipeline()
        for lesson in topic.lessons:
            pipe.getbit(key, lesson.bit_index)
        results = await pipe.execute()

        for lesson, is_completed in zip(topic.lessons, results):
            completed = bool(is_completed)
            if completed:
                completed_count += 1
            lessons_status.append(
                LessonCompletionStatus(
                    lesson_id=lesson.lesson_id,
                    bit_index=lesson.bit_index,
                    completed=completed,
                )
            )

    return TopicLessonsResponse(
        topic_id=topic_id,
        total=len(topic.lessons),
        completed=completed_count,
        lessons=lessons_status,
    )
```

### Alternative: Using ProgressService Method
```python
# If we want to encapsulate the batch GETBIT in ProgressService

# In services/progress.py, add:
async def get_bits_status(
    self,
    user_id: str,
    subject_id: str,
    bit_indexes: list[int],
    version: int = 1,
) -> list[bool]:
    """Get completion status for specific bit indexes.

    Uses pipeline for O(N) operations in single round-trip.

    Args:
        user_id: Player's user ID
        subject_id: Subject identifier
        bit_indexes: List of bit positions to check
        version: Bitmap version

    Returns:
        List of booleans in same order as bit_indexes
    """
    key = self._progress_key(user_id, subject_id, version)

    pipe = self.redis.pipeline()
    for idx in bit_indexes:
        pipe.getbit(key, idx)

    results = await pipe.execute()
    return [bool(r) for r in results]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Individual GETBIT calls | Pipeline GETBIT | Phase 4 | O(N) round-trips to O(1) round-trip |
| Frappe DB for hierarchy | Redis cached hierarchy | Phase 4 | 100-500ms to <1ms |
| N/A | Per-topic lesson listing | This phase | New endpoint, <5ms target |

**Deprecated/outdated:**
- None - this is a new endpoint building on stable infrastructure

## Performance Analysis

### Target: <5ms for any topic size

**Breakdown:**
1. **Hierarchy fetch:** ~0.5ms (Redis GET, cached)
2. **Topic lookup:** ~0.2ms (in-memory tree traversal, <100 iterations)
3. **Access check:** ~0.5ms (Redis SISMEMBER)
4. **Pipeline GETBIT:** ~1ms for 100 lessons (single round-trip)
5. **Response serialization:** ~0.5ms (Pydantic)
6. **Total:** ~2.7ms typical, <5ms worst case

**Scalability:**
- 100K concurrent players: Each request is stateless, Redis-only
- 100+ lessons per topic: Pipeline handles efficiently, still <3ms
- No connection pooling issues: Using existing app.state.redis_pool

### Verification Points
- [ ] Endpoint responds in <5ms for topic with 100 lessons
- [ ] Works with 100K concurrent players (stateless design)
- [ ] Returns correct completion status matching bitmap
- [ ] Handles edge cases: empty topic, invalid topic_id, no access

## Open Questions

Things that couldn't be fully resolved:

1. **Unlock state inclusion**
   - What we know: Existing granular endpoints include `unlocked` boolean
   - What's unclear: Should lesson completion endpoint also include unlock state per lesson?
   - Recommendation: Include it - matches existing pattern, minimal overhead (already have completed_bits logic in codebase)

2. **Free content handling**
   - What we know: Existing endpoints allow access to topics in free units/topics
   - What's unclear: Should this endpoint check topic.is_free OR unit.is_free for bypass?
   - Recommendation: Follow existing pattern - check `hierarchy.has_any_free_content()` as fallback

## Sources

### Primary (HIGH confidence)
- Existing codebase: `fastapi_app/services/progress.py` - GETBIT patterns
- Existing codebase: `fastapi_app/api/v1/endpoints/progress.py` - Endpoint patterns
- Existing codebase: `fastapi_app/models/progress.py` - Response models
- Existing codebase: `fastapi_app/services/hierarchy.py` - Cached hierarchy
- Redis GETBIT documentation - https://redis.io/docs/latest/commands/getbit/
- Redis pipelining documentation - https://redis.io/docs/latest/develop/using-commands/pipelining/

### Secondary (MEDIUM confidence)
- Phase 17 RESEARCH.md - Established patterns for granular progress endpoints
- CLAUDE.md - Performance targets (<20ms for progress fetch)

### Tertiary (LOW confidence)
- None - all patterns verified from existing codebase

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Using only existing libraries, no new dependencies
- Architecture: HIGH - Patterns copied from existing progress.py endpoints
- Pitfalls: HIGH - Based on actual codebase patterns and common FastAPI issues

**Research date:** 2026-02-06
**Valid until:** 2026-03-06 (30 days - stable patterns, no external dependencies)
