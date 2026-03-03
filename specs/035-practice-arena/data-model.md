# Data Model: Practice Arena (Phase 035 — Gap Fix)

**Branch**: `035-practice-arena` | **Date**: 2026-03-02
**Prior art**: Phase 025 data model at `specs/025-practice-arena/data-model.md`

---

## Delta Summary

Phase 025 created all entities. Phase 035 requires **zero schema changes** — only behavioral modifications to existing code. This document is provided for completeness and as a reference for the task phase.

---

## Entities (All Existing)

### 1. Memora Review Item (EXISTS — Phase 024)

**Storage**: Frappe DocType (`tabMemora Review Item`)
**Autoname**: `field:item_id`
**Estimated rows**: ~200,000

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| item_id | Data (UUID) | Yes | PK via autoname, unique |
| subject | Link (Memora Subject) | Yes | Denormalized |
| track | Link (Memora Track) | Yes | Denormalized |
| unit | Link (Memora Unit) | Yes | Denormalized |
| topic | Link (Memora Topic) | Yes | Denormalized |
| lesson | Link (Memora Lesson) | Yes | Parent reference |
| stage_id | Data | Yes | Lesson Stage child row name |
| stage_type | Link (Memora Lesson Stage Settings) | Yes | QUESTION, FILL_BLANK, MATCHING, etc. |
| question_text | Small Text | No | MCQ question or instruction |
| choice_1..4 | Small Text | No | MCQ choices |
| correct_choice | Int | No | 1-based index (1–4) |
| content_json | Code (JSON) | No | For non-MCQ stages |

**Phase 035 changes**: None to schema. Extraction timing moves from synchronous to dirty-set (G-001).

---

### 2. Practice Log (EXISTS — Phase 025, Raw SQL Table)

**Storage**: Raw SQL (`tabMemora Practice Log`) — NOT a Frappe DocType
**Estimated rows**: ~500M at scale (100K students x 5K items)

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| id | BIGINT AUTO_INCREMENT | No | PK |
| player_id | VARCHAR(140) | No | Player docname (PLAYER-#####) |
| item_id | VARCHAR(36) | No | Review Item UUID |
| first_seen_at | DATETIME | No | First encounter timestamp |
| last_seen_at | DATETIME | No | Most recent encounter |
| last_result | ENUM('Correct','Incorrect') | No | Most recent answer |
| attempt_count | INT UNSIGNED | No | Total attempts (default 1) |
| correct_count | INT UNSIGNED | No | Correct answers (default 0) |

**Indexes**:
```
PRIMARY KEY (id)
UNIQUE KEY uq_player_item (player_id, item_id)
KEY idx_item_id (item_id)
```

**Phase 035 changes**: None.

---

### 3. Practice Session (EXISTS — Phase 025, Redis HASH)

**Storage**: Redis HASH with TTL
**Key**: `memora:practice:{player_id}`
**TTL**: Configurable (default 3600s)

| Field | Type | Notes |
|-------|------|-------|
| subject_id | string | Subject being practiced |
| filter | string | "completed" or "all" |
| tracks | JSON string | Selected track IDs (array) |
| units | JSON string | Selected unit IDs (empty if multi-track) |
| topics | JSON string | Selected topic IDs (empty if multi-level) |
| batch_seq | string (int) | Current batch number (0-based) |
| served_item_ids | JSON string | Array of all served item UUIDs |
| accessible_lessons | JSON string | Lesson IDs validated at session start |
| selected_topics | JSON string | Topic IDs for proportional distribution |
| created_at | string | ISO timestamp |
| submitted_{N} | string | Idempotency marker — "1" when batch N submitted |

**Phase 035 changes**: None.

---

### 4. Dirty Set: Review Item Extraction (NEW KEY — Phase 035)

**Storage**: Redis SET (protected, no TTL)
**Key**: `memora:dirty:review_items`
**Estimated members**: 0–100 at any time (teacher saves between sync runs)

| Aspect | Value |
|--------|-------|
| Redis type | SET |
| Members | Lesson names (e.g., `LES-00001`) |
| Producers | `review_item_sync.on_lesson_save()` via SADD |
| Consumers | `sync.py sync_dirty_review_items()` via SMEMBERS + SREM |
| TTL | **None** (protected — never evicted) |
| Schedule | Every 2 minutes (`*/2 * * * *`) |
| Retry | On failure, member remains in set; retried on next run |

**Key rules**:
- Protected key: NO TTL, `volatile-ttl` eviction policy skips it
- SREM only after successful processing
- `on_lesson_trash` does SREM to avoid processing deleted lessons
- `on_lesson_save` with `is_reviewable=0` still adds to dirty set + does immediate delete

---

### 5. Memora Settings (EXISTS — Phase 025, Additions)

**Storage**: Frappe Single DocType (`Memora Settings`)

**Fields added in Phase 025** (no changes in 035):

| Field | Type | Default | Section |
|-------|------|---------|---------|
| practice_session_size | Int | 20 | Practice Arena |
| practice_session_ttl | Int | 3600 | Practice Arena |

---

## Entity Relationships (Unchanged from 025)

```
                    +----------------------+
                    |   Memora Lesson      |
                    |   (is_reviewable)    |
                    +----------+-----------+
                               | on_save hook → SADD dirty set
                               v
                    +----------------------+     */2 * * * *
   dirty set ------>| sync_dirty_review_   |---> sync_review_items()
   (Redis SET)      | items() consumer     |
                    +----------+-----------+
                               |
                               v
                    +----------------------+
                    | Memora Review Item   |<---- Question source for
                    | (Frappe DocType)     |      Practice Arena
                    +------+------+--------+
                           |      |
            cascade delete |      | LEFT JOIN (question selection)
                           v      v
              +---------------------------+
              | Practice Log              |<---- Submit results
              | (raw SQL table)           |      (UPSERT)
              +---------------------------+
                           ^
                           | player_id
              +---------------------------+
              | Practice Session          |---- Tracks served items,
              | (Redis HASH, TTL)         |     validates batches
              +---------------------------+
```

---

## State Transitions (Unchanged from 025)

### Practice Session Lifecycle

```
[No Session] --POST /start--> [Active]
                                 |
                    +------------+------------+
                    |            |            |
              POST /continue  POST /submit  TTL expires
                    |            |            |
                    v            v            v
               [Active]     [Active]    [No Session]
              (next batch) (results     (abandoned)
                           saved)
```

### Practice Log Record Lifecycle

```
[Not Exists] --first encounter--> [Exists]
                                      |
                                 next encounter
                                      |
                                      v
                                  [Updated]
                                  (attempt_count++,
                                   correct_count += correct?,
                                   last_seen_at = now,
                                   last_result = result)
```

### Dirty Set Entry Lifecycle (NEW in 035)

```
[Not in set] --lesson save--> [In set]
                                  |
                    +-------------+-------------+
                    |                           |
              sync success                sync failure
                    |                           |
                    v                           v
               [SREM'd]                   [Still in set]
              (processed)                (retry next run)
```

---

## Validation Rules (Unchanged from 025)

### Practice Session Start
- `subject_id`: Must exist in hierarchy cache
- `filter`: Must be "completed" or "all"
- `tracks`: Non-empty array of valid track IDs within subject
- If len(tracks) > 1: `units` and `topics` must be empty
- If len(tracks) == 1: `units` optionally filters to specific units
- If len(units) == 1: `topics` optionally filters to specific topics
- Access: All selected tracks must be accessible

### Batch Submit
- `batch_seq`: Must match session's current `batch_seq`
- `results`: Array of `{item_id, is_correct}`
- Duplicate `batch_seq`: silently accepted, no duplicate writes

### Practice Log UPSERT (Unchanged)
```sql
INSERT INTO `tabMemora Practice Log`
    (player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
VALUES (:player, :item, :now, :now, :result, 1, :correct_int)
ON DUPLICATE KEY UPDATE
    last_seen_at = VALUES(last_seen_at),
    last_result = VALUES(last_result),
    attempt_count = attempt_count + 1,
    correct_count = correct_count + VALUES(correct_count);
```
