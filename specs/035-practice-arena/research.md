# Research: Practice Arena (Phase 035 — Gap Analysis)

**Branch**: `035-practice-arena` | **Date**: 2026-03-02
**Prior art**: Phase 025 research at `specs/025-practice-arena/research.md`

---

## Delta Summary

Phase 025 delivered a complete Practice Arena implementation. Phase 035 refines the specification with 25 FRs and identifies **5 gaps** between spec requirements and the existing codebase:

| Gap | FR | Current State | Required State | Effort |
|-----|-----|---------------|----------------|--------|
| G-001 | FR-002, FR-007 | Synchronous extraction on lesson save | Dirty-set pattern with retry | Medium |
| G-002 | FR-014 | Simple ORDER BY priority LIMIT N | Proportional topic distribution | Small |
| G-003 | FR-016 | Warning only when ALL items exhausted | Warning when ANY item is a repeat | Small |
| G-004 | FR-005 | Content hash checked in sync_review_items() | Same, but within dirty-set consumer | None (inherits) |
| G-005 | — | `all_seen` logic in `continue_session()` | Needs consistent all_seen detection on start + continue | Small |

---

## G-001: Dirty-Set Pattern for Review Item Extraction (FR-002, FR-007)

**Decision**: Convert from synchronous `on_lesson_save` hook to dirty-set + scheduled consumer.

**Rationale**:
- FR-002: "System MUST use a dirty-set pattern for extraction: teacher saves enqueue the lesson, a scheduled job (every 2 minutes) processes the queue."
- FR-007: "System MUST retain dirty set entries on processing failure for automatic retry on the next scheduled run."
- Current `on_lesson_save` → `sync_review_items()` is synchronous. If it fails, the error is logged but extraction is lost — no retry.
- Dirty-set pattern provides: (a) async processing (unblocks lesson save), (b) automatic retry on failure, (c) dedup via SET semantics (10 saves → 1 member).
- Follows established `memora:dirty:progress` / `memora:dirty:wallets` pattern exactly.

**Implementation**:

### Producer: `review_item_sync.py` (modify existing)

```python
def on_lesson_save(doc, method):
    """Enqueue lesson for Review Item extraction via dirty set."""
    from memora_admin.utils.redis_connection import get_memora_redis
    r = get_memora_redis()
    r.sadd("memora:dirty:review_items", doc.name)

    # Immediate delete for non-reviewable (don't defer — items must vanish NOW)
    if not doc.is_reviewable:
        from memora_admin.api.review_items import delete_review_items_for_lesson
        try:
            count = delete_review_items_for_lesson(doc.name)
            if count:
                frappe.logger().info(f"Review Item cleanup for {doc.name}: deleted={count}")
        except Exception:
            frappe.log_error(f"Review Item cleanup failed for {doc.name}")
```

**Design choice**: Non-reviewable lessons still get immediate deletion (not deferred) because students should not see items from a lesson the teacher just marked non-reviewable. The dirty-set entry is added regardless to ensure extraction runs if the lesson is later re-marked as reviewable.

### Consumer: `sync.py` (add new function)

```python
def sync_dirty_review_items():
    """Process dirty set of lessons pending Review Item extraction.

    Reads SMEMBERS, processes each lesson, SREMs on success.
    On failure, entry remains in set for auto-retry on next run.
    """
    r = get_memora_redis()
    dirty_lessons = r.smembers(DIRTY_REVIEW_ITEMS_KEY)
    if not dirty_lessons:
        return

    processed = 0
    failed = 0
    for lesson_name in dirty_lessons:
        try:
            lesson_doc = frappe.get_doc("Memora Lesson", lesson_name)
            result = sync_review_items(lesson_doc)
            r.srem(DIRTY_REVIEW_ITEMS_KEY, lesson_name)
            processed += 1
            if result["created"] or result["updated"] or result["deleted"]:
                logger.info(f"Review Item sync for {lesson_name}: {result}")
        except frappe.DoesNotExistError:
            # Lesson deleted — remove from dirty set
            r.srem(DIRTY_REVIEW_ITEMS_KEY, lesson_name)
        except Exception as e:
            # Leave in dirty set for retry
            failed += 1
            logger.error(f"Review Item sync failed for {lesson_name}: {e}")

    if processed or failed:
        logger.info(f"Review Item dirty sync: processed={processed}, failed={failed}")
    frappe.db.commit()
```

### Redis Key: `redis_keys.py` (add)

```python
def dirty_review_items_key() -> str:
    """Dirty set of lesson IDs pending Review Item extraction.

    Type: SET of lesson names (e.g., "LES-00001")
    Producers: review_item_sync.on_lesson_save() (SADD)
    Consumers: sync.py sync_dirty_review_items() (SMEMBERS + SREM)
    TTL: None (protected — never evicted)
    """
    return "memora:dirty:review_items"
```

### Scheduler: `hooks.py` (add entry)

Add to the `"*/2 * * * *"` schedule (every 2 minutes per FR-002):

```python
"*/2 * * * *": [
    "memora_admin.tasks.sync.sync_dirty_review_items",
],
```

**Note**: Using `*/2` (every 2 minutes) not `* * * * *` (every 1 minute) to match spec requirement. This is different from the other dirty-set consumers (1 minute). The 2-minute interval provides natural dedup for rapid saves while keeping extraction latency acceptable.

**Alternatives Considered**:
- **Keep synchronous**: Rejected — spec FR-002 explicitly requires dirty-set; synchronous has no retry semantics (FR-007).
- **Frappe background job (enqueue)**: Rejected — background jobs don't have natural dedup (same lesson enqueued 10 times = 10 executions). Dirty-set SET semantics provide free dedup.
- **Redis RPUSH list**: Rejected — SET provides dedup (SADD same lesson 10 times = 1 member). LIST would require manual dedup.

### `on_lesson_trash` handling

Keep `on_lesson_trash` synchronous (immediate deletion on lesson delete). Also SREM from dirty set to avoid processing a deleted lesson:

```python
def on_lesson_trash(doc, method):
    """Delete all Review Items when a lesson is deleted."""
    from memora_admin.utils.redis_connection import get_memora_redis
    r = get_memora_redis()
    r.srem("memora:dirty:review_items", doc.name)  # Remove from queue

    from memora_admin.api.review_items import delete_review_items_for_lesson
    try:
        count = delete_review_items_for_lesson(doc.name)
        if count:
            frappe.logger().info(f"Deleted {count} Review Items for trashed lesson {doc.name}")
    except Exception:
        frappe.log_error(f"Review Item cleanup failed for trashed lesson {doc.name}")
```

---

## G-002: Proportional Topic Distribution (FR-014)

**Decision**: Two-phase query — per-topic counts first, then proportional LIMIT per topic, merge results.

**Rationale**:
- FR-014: "System MUST distribute questions proportionally across topics based on content volume within the selected filters."
- Current implementation: single SQL `ORDER BY priority ASC, sort_seen ASC LIMIT :batch_size` — no topic distribution.
- With 3 topics (100, 50, 10 items), current query picks 20 items from the 100-item topic first. Proportional should pick ~12, ~6, ~2.

**Algorithm**:

```python
async def _select_questions_proportional(
    self,
    player_id: str,
    subject_id: str,
    accessible_lessons: list[str],
    selected_topics: list[str],
    served_item_ids: list[str],
    batch_size: int,
) -> tuple[list[PracticeQuestion], int]:
    """Select questions with proportional topic distribution."""
    # Phase 1: Count available items per topic
    counts = await self._count_items_per_topic(subject_id, accessible_lessons, selected_topics)
    total_available = sum(counts.values())

    if total_available == 0:
        return [], 0

    # Phase 2: Calculate per-topic quotas (proportional, min 1 each)
    quotas = _compute_topic_quotas(counts, batch_size)

    # Phase 3: Query per topic with priority ordering
    all_questions = []
    any_repeat = False
    for topic_id, quota in quotas.items():
        questions, has_repeat = await self._select_for_topic(
            player_id, subject_id, accessible_lessons,
            topic_id, served_item_ids, quota,
        )
        all_questions.extend(questions)
        any_repeat = any_repeat or has_repeat

    return all_questions, total_available, any_repeat


def _compute_topic_quotas(counts: dict[str, int], batch_size: int) -> dict[str, int]:
    """Distribute batch_size across topics proportionally (min 1 each)."""
    total = sum(counts.values())
    quotas = {}
    remaining = batch_size

    # First pass: proportional allocation (round down)
    for topic_id, count in counts.items():
        quota = max(1, int(batch_size * count / total))
        quota = min(quota, count)  # Don't exceed available
        quotas[topic_id] = quota
        remaining -= quota

    # Second pass: distribute remainder to largest topics
    if remaining > 0:
        sorted_topics = sorted(counts.keys(), key=lambda t: counts[t], reverse=True)
        for topic_id in sorted_topics:
            if remaining <= 0:
                break
            extra = min(remaining, counts[topic_id] - quotas[topic_id])
            quotas[topic_id] += extra
            remaining -= extra

    return quotas
```

**Performance Impact**: One additional COUNT query per invocation (~2ms). Per-topic SELECT queries execute in parallel concept but sequentially via FrappeClient calls. With 5 topics, worst case is 5 × ~5ms = ~25ms — well within 100ms target.

**Optimization**: If only 1 topic selected, skip proportional logic and use existing single-query path.

**Alternatives Considered**:
- **Window functions (ROW_NUMBER OVER PARTITION BY topic)**: Rejected — MariaDB 10.6 supports window functions, but the FrappeClient proxy adds overhead per query. Simpler to run per-topic queries.
- **Single query with CASE+topic weighting**: Too complex for marginal performance gain.

---

## G-003: `all_seen_warning` Semantics Fix (FR-016)

**Decision**: Detect repeat questions via the priority CASE expression in the SELECT query.

**Rationale**:
- FR-016: "System MUST set all_seen_warning flag to true if ANY question in a batch has been seen before by the student."
- Current: `all_seen = total_available > 0 and len(questions) == 0` — only triggers when zero unseen items remain.
- Spec acceptance scenario #5: "Given a batch contains ANY repeat questions (even one), Then all_seen_warning is true."

**Implementation**:

The `_select_for_topic()` query already returns a `priority` column:
- `0` = never seen
- `1` = seen before, not this session
- `2` = seen this session

A batch has repeats if ANY returned row has `priority > 0`:

```python
# After collecting all questions:
any_repeat = any(q_priority > 0 for q_priority in priorities)
all_seen_warning = any_repeat
```

In practice, the priority is available from the SQL result. We need to thread this information back from `_select_questions()` to the caller. Options:
1. Return a third element `has_any_repeat` from `_select_questions()`.
2. Check if any question's `item_id` appears in the player's Practice Log.

Option 1 is cleaner — the data is already in the SQL result.

**Changes**:
- `_select_questions()` → returns `(questions, total_available, any_repeat)`
- `start_session()` and `continue_session()` → use `any_repeat` for `all_seen_warning`

---

## G-004: Content Hash Dedup Within Dirty-Set Processing (FR-005)

**Decision**: No code change needed — inherits from existing `sync_review_items()`.

**Rationale**:
- `sync_review_items()` already checks `content_hash` at the top and skips if unchanged.
- When the dirty-set consumer calls `sync_review_items(lesson_doc)`, the hash check happens automatically.
- If a teacher saves the same lesson 10 times, 10 SADD calls add the same member once. The consumer runs once, `sync_review_items()` checks hash and skips if unchanged.
- Double dedup: SET dedup (10 saves → 1 member) + hash dedup (content unchanged → skip).

---

## G-005: Consistent `all_seen_warning` on Start and Continue

**Decision**: Unify `all_seen` detection for both `start_session()` and `continue_session()`.

**Rationale**:
- Current `start_session()`: `all_seen = total_available > 0 and len(questions) == 0`
- Current `continue_session()`: Same logic, plus wrap-around re-serve
- Both should use the `any_repeat` flag from G-003

**Implementation**: After G-003, both methods receive `any_repeat` from `_select_questions()` and set:
```python
all_seen_warning = any_repeat
```

For `continue_session()` with wrap-around (all items seen, re-serving from full pool):
```python
if not questions and total_available > 0:
    # All seen — re-serve from full pool
    questions, _, _ = await self._select_questions(..., served_item_ids=[])
    all_seen_warning = True  # Always true when wrapping around
```

---

## Verified FRs (No Changes Needed)

| FR | Description | Verification |
|----|-------------|-------------|
| FR-001 | Extract items from stages into flat table | `review_items.py:sync_review_items()` ✅ |
| FR-003 | Support QUESTION, MATCHING, FILL_BLANK, SENTENCE_BUILDER, MINDMAP | `_extract_question()`, `_extract_fill_blank()`, `_extract_matching()`, `_extract_mindmap()`, `_extract_generic()` ✅ |
| FR-004 | Skip skippable stages | `sync_review_items()` checks both global and per-stage `is_skippable` ✅ |
| FR-006 | Hard-delete items + cascade Practice Log + Memory State | `_delete_review_items_and_memory_state()` ✅ |
| FR-008 | Hierarchy endpoint with accessible flag | `PracticeService.get_practice_hierarchy()` ✅ |
| FR-009 | "Completed only" and "All content" filters | `_get_completed_lesson_ids()` + filter logic ✅ |
| FR-010 | Accessible flag cascades downward | `_check_track_access()` + free content bypass ✅ |
| FR-011 | item_count at each hierarchy level | `_compute_track_item_count()` + meta aggregation ✅ |
| FR-012 | One active session per student | `DEL + HSET` pattern on same key ✅ |
| FR-013 | Ephemeral session with auto-expiry | Redis HASH with `EXPIRE` ✅ |
| FR-015 | 3-tier question priority | `_select_questions()` CASE expression ✅ |
| FR-017 | Immediate batch result persistence | `submit_batch()` calls FrappeClient UPSERT ✅ |
| FR-018 | Idempotency via batch sequence | `submitted_{N}` markers ✅ |
| FR-019 | Silently skip deleted items | `_get_valid_item_ids()` filter ✅ |
| FR-020 | Validate access at session start | `_get_accessible_lessons()` ✅ |
| FR-021 | No re-check on subsequent batches | Uses stored `accessible_lessons` ✅ |
| FR-022 | Free content without subscription | `free_units_set` / `free_topics_set` check ✅ |
| FR-023 | Rate limiting on all endpoints | `require_rate_limit()` on all 4 routes ✅ |
| FR-024 | Configurable batch size and session TTL | `practice_session_size`, `practice_session_ttl` in Settings ✅ |
| FR-025 | Zero connection to FSRS/streaks/leaderboards/XP | Separate endpoints, no XP award, no leaderboard update ✅ |
