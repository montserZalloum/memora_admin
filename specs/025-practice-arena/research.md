# Research: Practice Arena

**Branch**: `025-practice-arena` | **Date**: 2026-02-23

---

## R-001: Practice Log Table Design (Raw SQL vs Frappe DocType)

**Decision**: Raw SQL table (`tabMemora Practice Log`) — NOT a Frappe DocType.

**Rationale**:
- Expected 500M rows at scale (100K students × 5K items each)
- `Memory State` table (phase 018) set the precedent: raw SQL with `frappe.db.sql()` for RANGE-partitioned, high-volume tables
- Frappe ORM overhead is prohibitive at this scale (autoname, modified_by tracking, permission checks)
- Simple UPSERT pattern (ON DUPLICATE KEY UPDATE) is natural in raw SQL

**Alternatives Considered**:
- **Frappe DocType**: Rejected — ORM overhead at 500M rows degrades INSERT/UPDATE performance; no need for admin list view
- **Redis-only**: Rejected — Practice Log is source-of-truth data that must survive Redis loss; doesn't match self-healing architecture
- **Partitioned table**: Deferred — spec A-006 assumes proper indexing is sufficient without partitioning initially. Can add RANGE partitioning by `player_id` hash later if needed

**Schema Design**:
```sql
CREATE TABLE `tabMemora Practice Log` (
    `name` BIGINT AUTO_INCREMENT,
    `player_id` VARCHAR(140) NOT NULL,
    `item_id` VARCHAR(36) NOT NULL,  -- UUID string (not BINARY, Review Item uses string)
    `first_seen_at` DATETIME NOT NULL,
    `last_seen_at` DATETIME NOT NULL,
    `last_result` ENUM('Correct', 'Incorrect') NOT NULL,
    `attempt_count` INT UNSIGNED NOT NULL DEFAULT 1,
    `correct_count` INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (`name`),
    UNIQUE KEY `uq_player_item` (`player_id`, `item_id`),
    KEY `idx_item_id` (`item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Key indexes**:
- `uq_player_item (player_id, item_id)` — UPSERT target, also serves question selection JOINs
- `idx_item_id (item_id)` — for cascade deletion when Review Item is deleted (FR-004)

**No BIGINT PK concern**: Unlike Memory State which needs partition-compatible PK, Practice Log uses simple AUTO_INCREMENT. The `player_id + item_id` composite unique handles dedup.

---

## R-002: Question Selection Algorithm Performance

**Decision**: SQL JOIN (Review Item × Practice Log) with LEFT JOIN for priority calculation.

**Rationale**:
- Target: <100ms for student with 5K practice log entries (SC-003)
- Review Items table: ~200K rows with indexes on hierarchy fields
- LEFT JOIN with Practice Log on `(player_id, item_id)` gives NULL for unseen items (priority 0)
- ORDER BY priority ASC, last_seen_at ASC handles the 3-tier priority
- SQL engine handles proportional distribution via weighted LIMIT per topic

**Query Pattern**:
```sql
SELECT ri.item_id, ri.question_text, ri.choice_1, ri.choice_2,
       ri.choice_3, ri.choice_4, ri.correct_choice, ri.content_json,
       ri.stage_type, ri.lesson, ri.topic,
       CASE
           WHEN pl.item_id IS NULL THEN 0          -- never seen
           WHEN pl.item_id NOT IN (:served) THEN 1 -- seen before, not this session
           ELSE 2                                   -- seen this session
       END AS priority,
       COALESCE(pl.last_seen_at, '1970-01-01') AS sort_seen
FROM `tabMemora Review Item` ri
LEFT JOIN `tabMemora Practice Log` pl
    ON pl.item_id = ri.item_id AND pl.player_id = :player_id
WHERE ri.subject = :subject_id
    AND ri.lesson IN (:accessible_lessons)
    AND ri.topic IN (:selected_topics)
ORDER BY priority ASC, sort_seen ASC
LIMIT :batch_size
```

**Performance Estimate**:
- Index scan on `tabMemora Review Item` (subject + lesson filters): ~1ms
- LEFT JOIN with `tabMemora Practice Log` via `uq_player_item`: ~5ms (5K rows per player)
- Sort + LIMIT: ~1ms
- Total: ~10-20ms — well under 100ms target

**Alternatives Considered**:
- **Redis sorted sets for priority**: Rejected — adds complexity, data duplication, and cache invalidation overhead. SQL is fast enough.
- **Pre-computed question pools**: Rejected — stale pool problem; real-time query is simple and fast enough.

**Proportional Distribution**:
- Count items per topic first (`GROUP BY topic`), then distribute batch_size proportionally
- For small batch sizes or few topics, round-robin ensures each topic gets at least 1 question (if available)
- Implementation: calculate per-topic quota, query with per-topic LIMIT, merge

---

## R-003: Practice Session Redis Design

**Decision**: Redis HASH with native TTL expiry.

**Rationale**:
- Sessions are ephemeral — lost on Redis flush is acceptable (student restarts)
- HASH provides O(1) field access for session metadata
- Native EXPIRE handles auto-cleanup (no scheduled job needed)
- One session per player enforced by overwriting the same key

**Redis Key**: `memora:practice:{player_id}` (HASH)

**Hash Fields**:
```
subject_id        → "SUB-00001"
filter            → "completed" | "all"
tracks            → JSON array ["TRK-001", "TRK-002"]
units             → JSON array ["UNI-001"] (empty if multi-track)
topics            → JSON array ["TOP-001"] (empty if multi-track or multi-unit)
batch_seq         → "0" (incremented on each batch)
served_item_ids   → JSON array of served item_ids across all batches
created_at        → ISO timestamp
```

**TTL**: `practice_session_ttl` from settings (default 3600s = 1 hour).

**One session per player**: Starting a new session DELETEs the old key before creating the new HASH. No need for Lua script atomicity — DELETE+HSET+EXPIRE is safe because the old session is abandoned.

**Session Size Concern**: `served_item_ids` grows with each batch. At 20 items per batch × 10 batches = 200 UUIDs ≈ 7KB. Acceptable for a 1-hour TTL key.

**Alternatives Considered**:
- **Redis STRING (JSON)**: Rejected — HASH provides atomic field updates without full JSON serialize/deserialize
- **MariaDB table**: Rejected — ephemeral data doesn't need durability; Redis latency is <1ms vs ~5ms for SQL
- **Lua-based atomic start**: Deferred — single-player sessions don't have race conditions worth protecting

---

## R-004: Content Hash Debounce Mechanism

**Decision**: Compare lesson's `content_hash` field (already exists on `Memora Lesson` DocType) before running extraction.

**Rationale**:
- `content_hash` is a read-only Data field on `Memora Lesson` (confirmed in schema)
- Currently NOT used in the `on_lesson_save` → `sync_review_items` flow
- When teacher saves the same lesson 10 times in 2 minutes, each save triggers `on_lesson_save`
- Debounce: compute hash of `stages` config, compare with stored `content_hash`, skip if unchanged

**Implementation**:
1. In `sync_review_items()`, compute hash of all stages' `config_json` (sorted by stage name for determinism)
2. Compare with `lesson_doc.content_hash`
3. If same: return `{"created": 0, "updated": 0, "deleted": 0}` immediately
4. If different: proceed with extraction, then update `content_hash` on the lesson doc

**Hash Computation**:
```python
import hashlib, json

def _compute_lesson_content_hash(stages) -> str:
    """Deterministic hash of stage configs for debounce."""
    parts = []
    for stage in sorted(stages or [], key=lambda s: s.name):
        parts.append(f"{stage.name}:{stage.stage_type}:{stage.is_skippable}:{stage.config_json or ''}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:8]
```

**Alternatives Considered**:
- **Redis debounce key with TTL**: Rejected — adds Redis dependency to Frappe-side sync; content_hash is simpler and already exists as a field
- **Background job queue dedup**: Rejected — sync runs synchronously in on_save; queuing adds latency and complexity

---

## R-005: Practice Log Cascade Deletion (FR-004)

**Decision**: Raw SQL DELETE in the existing `_delete_review_items_and_memory_state()` function.

**Rationale**:
- When a Review Item is deleted, its Practice Log rows must also be deleted
- Practice Log is a raw SQL table (no Frappe ORM), so cascade must be explicit
- The existing deletion function already handles Memory State cascade — add Practice Log as a third step
- `idx_item_id` index on Practice Log ensures efficient DELETE

**Implementation**:
```python
# In _delete_review_items_and_memory_state(), add before Review Item deletion:

# Step 1.5: Delete Practice Log entries for these items
if item_ids:
    placeholders = ", ".join(["%s"] * len(item_ids))
    frappe.db.sql(
        f"DELETE FROM `tabMemora Practice Log` WHERE item_id IN ({placeholders})",
        tuple(item_ids),
    )
```

**No FK constraint**: Raw SQL table doesn't support Frappe FK constraints. Explicit deletion in the cascade function is the established pattern (see Memory State cascade).

---

## R-006: is_reviewable Lesson Filtering

**Decision**: Add `is_reviewable` check to `on_lesson_save` hook flow.

**Rationale**:
- `is_reviewable` field exists on `Memora Lesson` (Check, default 1)
- Currently NOT checked in `sync_review_items()` or `on_lesson_save()`
- Gap: lessons with `is_reviewable=0` still get their items extracted into Review Item table
- Fix: if `is_reviewable=0`, delete all existing Review Items for that lesson and skip extraction

**Implementation**:
```python
# In sync_review_items(), at the top:
if not lesson_doc.is_reviewable:
    # Delete any existing items (lesson was previously reviewable)
    count = delete_review_items_for_lesson(lesson_doc.name)
    return {"created": 0, "updated": 0, "deleted": count}
```

---

## R-007: SENTENCE_BUILDER and MINDMAP Extraction

**Decision**: Currently handled by generic fallback — adequate for MVP but specific extractors improve question quality.

**Rationale**:
- Generic fallback (`_extract_generic()`) finds `item_id` fields in common locations
- SENTENCE_BUILDER has a single `item_id` at root level with `words[]` array → generic handles this
- MINDMAP has recursive `children[]` with per-node `item_id` → generic handles flat lists but NOT recursive children

**SENTENCE_BUILDER config_json**:
```json
{
    "instruction": "رتب الكلمات",
    "item_id": "UUID",
    "words": [
        {"text": "كلمة", "position": 0},
        {"text": "أخرى", "position": 1}
    ]
}
```
→ Generic fallback works (finds root `item_id`, stores in `content_json`)

**MINDMAP config_json**:
```json
{
    "instruction": "أكمل خريطة المفاهيم",
    "central": "الموضوع",
    "children": [
        {"text": "فرع1", "item_id": "UUID-1", "children": [
            {"text": "فرع1.1", "item_id": "UUID-2"}
        ]},
        {"text": "فرع2", "item_id": "UUID-3"}
    ]
}
```
→ Generic fallback only finds top-level `children`, misses nested children.

**Implementation**: Add `_extract_mindmap()` with recursive traversal. SENTENCE_BUILDER can remain on generic fallback.

```python
def _extract_mindmap(config: dict, stage_type: str) -> list[dict]:
    """Recursively extract items from MINDMAP children."""
    items = []
    def _walk(nodes):
        for node in (nodes or []):
            if "item_id" in node:
                items.append({
                    "item_id": node["item_id"],
                    "stage_type": stage_type,
                    "question_text": config.get("instruction") or config.get("central"),
                    "content_json": json.dumps(node),
                    # MCQ fields left None
                })
            _walk(node.get("children"))
    _walk(config.get("children"))
    return items
```

---

## R-008: Settings Configuration Approach

**Decision**: Add practice fields to existing `Memora Settings` Single DocType.

**Rationale**:
- `Memora Settings` already has `review_session_size` (direct parallel to `practice_session_size`)
- Follows established pattern: Single DocType → event hook → Redis cache → FastAPI service
- No new DocType needed — reduces schema bloat

**Fields to Add**:
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `practice_session_size` | Int | 20 | Questions per batch |
| `practice_session_ttl` | Int | 3600 | Session timeout (seconds) |

**Note**: `review_item_sync_interval` from FR-017 is NOT needed as a runtime setting. The sync runs synchronously on lesson save (via hook), not on a timer. The 2-minute interval in the spec refers to debounce behavior, which is handled by content_hash comparison (R-004).

---

## R-009: Completion Filter — "Completed Only" Implementation

**Decision**: Decode student's progress bitmap to get completed lesson IDs, then filter Review Items.

**Rationale**:
- `ProgressService.get_completed_bits()` returns a set of completed bit_indices
- Hierarchy maps bit_indices to lesson IDs
- Filter: only include Review Items whose `lesson` is in the completed set

**Flow**:
1. Load hierarchy for subject → get full lesson list with bit_indices
2. Load student's progress bitmap → `get_completed_bits()`
3. Map completed bit_indices to lesson IDs via hierarchy
4. Query Review Items with `WHERE lesson IN (:completed_lessons)`

**Performance**: Bitmap decode is O(bit_range) ≈ O(1000) per subject. Hierarchy is cached (1ms). Total overhead: ~2ms.

---

## R-010: Access Control for Practice Arena

**Decision**: Reuse existing `AccessService.check_access_with_plan()` at session start only.

**Rationale**:
- Spec FR-008: Access checked at session start
- Spec FR-009: No re-check on subsequent batches
- Existing access model: Subject grant OR plan membership OR track grant
- Free topics/units bypass Gate 2 (existing `is_lesson_free()`)

**Implementation**:
- At session start: validate each selected track's access
- Filter accessible lessons based on grants + free content
- Store accessible lesson IDs in session hash
- Subsequent batches use stored lesson IDs (no re-check)

**Multi-Track Selection**:
- Student selects 3 tracks → check access for each
- Reject request if ANY selected track is inaccessible
- Alternative: silently filter to accessible tracks only → rejected (spec says "rejected with error")
