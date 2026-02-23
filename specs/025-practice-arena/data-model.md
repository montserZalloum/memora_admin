# Data Model: Practice Arena

**Branch**: `025-practice-arena` | **Date**: 2026-02-23

---

## Entities

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

**Changes for Phase 025**: None to schema. Gap-filling changes to extraction logic only.

---

### 2. Practice Log (NEW — Raw SQL Table)

**Storage**: Raw SQL (`tabMemora Practice Log`) — NOT a Frappe DocType
**Estimated rows**: ~500M at scale (100K students × 5K items)

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

**Access pattern**:
- UPSERT on batch submit: `INSERT ... ON DUPLICATE KEY UPDATE`
- LEFT JOIN with Review Item for question selection
- DELETE by item_id on Review Item cascade

**DDL**:
```sql
CREATE TABLE IF NOT EXISTS `tabMemora Practice Log` (
    `id` BIGINT AUTO_INCREMENT,
    `player_id` VARCHAR(140) NOT NULL,
    `item_id` VARCHAR(36) NOT NULL,
    `first_seen_at` DATETIME NOT NULL,
    `last_seen_at` DATETIME NOT NULL,
    `last_result` ENUM('Correct', 'Incorrect') NOT NULL,
    `attempt_count` INT UNSIGNED NOT NULL DEFAULT 1,
    `correct_count` INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_player_item` (`player_id`, `item_id`),
    KEY `idx_item_id` (`item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

---

### 3. Practice Session (TEMPORARY — Redis HASH)

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
| created_at | string | ISO timestamp |
| submitted_{N} | string | Idempotency marker — set to "1" when batch N is submitted. Dynamic keys (e.g., `submitted_0`, `submitted_1`). Used by `continue_session()` to verify previous batch was submitted, and by `submit_batch()` to detect duplicate submissions. |

**Lifecycle**:
1. **Start**: DEL old key → HSET all fields → EXPIRE with TTL
2. **Continue**: HGETALL → compute next batch → HSET batch_seq + served_item_ids → reset EXPIRE
3. **Submit**: HGET batch_seq (idempotency check) → save to Practice Log
4. **Expire**: TTL fires → key auto-deleted → nothing saved

**One session per player**: Same key pattern (`memora:practice:{player_id}`) ensures only one active session.

---

### 4. Memora Settings (EXISTING — Additions)

**Storage**: Frappe Single DocType (`Memora Settings`)

**New fields**:

| Field | Type | Default | Section |
|-------|------|---------|---------|
| practice_session_size | Int | 20 | Practice Arena |
| practice_session_ttl | Int | 3600 | Practice Arena |

---

## Entity Relationships

```
                    ┌──────────────────────┐
                    │   Memora Lesson      │
                    │   (is_reviewable)    │
                    └──────────┬───────────┘
                               │ on_save hook
                               ▼
                    ┌──────────────────────┐
                    │ Memora Review Item   │◄──── Question source for
                    │ (Frappe DocType)     │      Practice Arena
                    └──────┬──────┬────────┘
                           │      │
            cascade delete │      │ LEFT JOIN (question selection)
                           ▼      ▼
              ┌───────────────────────────┐
              │ Practice Log              │◄──── Submit results
              │ (raw SQL table)           │      (UPSERT)
              └───────────────────────────┘
                           ▲
                           │ player_id
              ┌───────────────────────────┐
              │ Practice Session          │──── Tracks served items,
              │ (Redis HASH, TTL)         │     validates batches
              └───────────────────────────┘
```

---

## State Transitions

### Practice Session Lifecycle

```
[No Session] ──POST /start──► [Active]
                                 │
                    ┌────────────┼────────────┐
                    │            │            │
              POST /continue  POST /submit  TTL expires
                    │            │            │
                    ▼            ▼            ▼
               [Active]     [Active]    [No Session]
              (next batch) (results     (abandoned)
                           saved)
```

- **Active → Active**: Both `/continue` and `/submit` keep session alive (EXPIRE reset)
- **Active → No Session**: TTL expiry or new `/start` (replaces old session)
- **No state machine enforcement needed**: Redis TTL handles expiry; idempotency via batch_seq

### Practice Log Record Lifecycle

```
[Not Exists] ──first encounter──► [Exists]
                                      │
                                 next encounter
                                      │
                                      ▼
                                  [Updated]
                                  (attempt_count++,
                                   correct_count += correct?,
                                   last_seen_at = now,
                                   last_result = result)
```

- **Immutable after creation**: Only `last_seen_at`, `last_result`, `attempt_count`, `correct_count` change
- **Deletion**: Only via cascade when Review Item is deleted

---

## Validation Rules

### Practice Session Start
- `subject_id`: Must exist in hierarchy cache
- `filter`: Must be "completed" or "all"
- `tracks`: Non-empty array of valid track IDs within subject
- If len(tracks) > 1: `units` and `topics` must be empty (multi-track constraint)
- If len(tracks) == 1: `units` optionally filters to specific units
- If len(units) == 1: `topics` optionally filters to specific topics
- Access: All selected tracks must be accessible (grant, plan membership, or free)

### Batch Submit
- `batch_seq`: Must match session's current `batch_seq` (idempotency)
- `results`: Array of `{item_id, is_correct}` — each item_id must be in session's served_item_ids
- Duplicate `batch_seq` (retry): silently accepted, no duplicate writes

### Practice Log UPSERT
```sql
INSERT INTO `tabMemora Practice Log`
    (player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
VALUES
    (:player, :item, :now, :now, :result, 1, :correct_int)
ON DUPLICATE KEY UPDATE
    last_seen_at = :now,
    last_result = :result,
    attempt_count = attempt_count + 1,
    correct_count = correct_count + :correct_int;
```
