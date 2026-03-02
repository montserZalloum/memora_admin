# Feature Specification: Practice Arena (ساحة التدريب)

**Feature Branch**: `025-practice-arena`
**Created**: 2026-02-23
**Updated**: 2026-03-02
**Status**: Final
**Dependencies**: None (introduces new tables; uses existing tables read-only)

---

## Problem Statement

Students currently have no way to freely practice content outside the daily FSRS review system. The daily review is algorithm-driven and mandatory — students cannot choose what to practice or when. A separate, student-initiated practice mode is needed where students can select specific content (by track, unit, or topic) and answer multiple-choice questions without affecting their FSRS state, streaks, leaderboards, or XP.

Additionally, review items (the atomic units of reviewable content) are currently embedded inside `config_json` on each `Memora Lesson Stage` record. There is no standalone, queryable table of items. This makes it impossible to efficiently serve questions for practice sessions. A new `Memora Review Item` table is needed to extract and flatten these items into a searchable format.

---

## Key Concepts

### What is an Item (آيتم)?

The Item is the smallest reviewable content unit inside a stage. Each item has a UUID (`item_id`) generated when the teacher saves content via the stage editor in `game_lesson.js`. The `item_id` is the bridge between educational content and the spaced repetition system — each `item_id` gets its own row in `Memora Memory State` to track student progress independently.

**Examples by stage type:**

- **MATCHING** stage with 5 pairs → 5 items, each pair has its own `item_id`
- **QUESTION** stage with 4 answers → 1 item (the correct answer carries the `item_id`)
- **FILL_BLANK** stage → 1 item per blank
- **SENTENCE_BUILDER** stage → 1 item per stage
- **MINDMAP** stage → 1 item per node (recursive children)
- **INFORMATION** stage → 0 items (skippable, no reviewable content)
- **REVEAL** stage → 0 items (skippable, no reviewable content)

**Current hierarchy:**
```
Subject → Track → Unit → Topic → Lesson → Stage → Item (embedded in config_json)
```

All levels except Item exist as separate DocTypes. Items are currently buried inside `config_json` as JSON — not in a standalone queryable table. This is the core problem that `Memora Review Item` solves.

### What is a Review Item?

A row in `Memora Review Item` that extracts an item from `config_json` and stores it in a flat, queryable format with:
- Full denormalized hierarchy (subject, track, unit, topic, lesson, stage)
- For MCQ stages (QUESTION): individual choice fields (`choice_1`..`choice_4`) and a 1-based `correct_choice` index
- For non-MCQ stages (FILL_BLANK, MATCHING, MINDMAP, SENTENCE_BUILDER): structured data in `content_json`

### What is a Practice Session?

A temporary, Redis-backed state keyed by `player_id` that holds the student's filter selections and tracks which questions have been served. It exists only in Redis with a TTL. If abandoned, it auto-expires with no side effects. Completed batch results are saved to `tabMemora Practice Log` immediately upon each batch completion.

### Key Distinction: Practice vs. Daily Reviews

The Practice Arena is completely separate from the FSRS daily review system:

```
Practice Arena                          Daily Reviews
─────────────                          ─────────────
Student-initiated                       Algorithm-driven (FSRS)
Optional                                Mandatory
No XP, no streak, no leaderboard       Awards XP, affects streak
Stored in: tabMemora Practice Log       Stored in: tabMemora Memory State
Source: Memora Review Item              Source: Memora Memory State
Zero connection between them            Zero connection between them
```

---

## User Scenarios & Testing

### User Story 1 — Review Item Extraction (Priority: P0)

As a system, when a teacher saves or modifies a lesson, I need to extract all reviewable items from the lesson's stages and populate the `Memora Review Item` table, so that items are available for practice sessions and future features.

**Why P0**: Without this table populated, the Practice Arena has no questions to serve. This is the foundational data layer.

**Acceptance Scenarios**:

1. **Given** a teacher saves a lesson with 3 non-skippable stages containing 8 total items, **When** the background sync job runs, **Then** 8 rows exist in `Memora Review Item` with correct hierarchy fields, question text, and choices.
2. **Given** a teacher modifies a lesson and changes one item's content, **When** the sync job runs, **Then** the corresponding `Memora Review Item` row is updated with the new content. Unchanged items are not touched.
3. **Given** a teacher deletes an item from a stage, **When** the sync job runs, **Then** the corresponding `Memora Review Item` row is hard-deleted, AND any related `tabMemora Practice Log` rows for that item are also deleted (cascade).
4. **Given** a teacher adds a new item to an existing stage, **When** the sync job runs, **Then** a new `Memora Review Item` row is created with the correct `item_id` and full hierarchy.
5. **Given** a lesson has `is_reviewable = false`, **When** the sync job runs, **Then** no items from that lesson appear in `Memora Review Item`. If items previously existed, they are deleted.
6. **Given** a stage has `is_skippable = true` (per-stage override or global setting from `Memora Lesson Stage Settings`), **When** the sync job runs, **Then** no items from that stage appear in `Memora Review Item`.
7. **Given** a teacher changes a stage type (e.g., MATCHING → QUESTION), **When** the sync job runs, **Then** old items are deleted and new items are created with new `item_id` values (because changing the stage type changes the `item_id`s).
8. **Given** a teacher saves the same lesson 10 times within 2 minutes, **When** the sync job runs, **Then** the lesson is processed only once (dedup via `content_hash` comparison).
9. **Given** a stage with `config_json` = null or invalid JSON, **When** the sync job runs, **Then** the stage is silently skipped with a warning log. Other stages in the same lesson are processed normally.
10. **Given** a lesson with all stages being skippable, **When** the sync job runs, **Then** no items are generated. If items previously existed for this lesson, they are deleted.

---

### User Story 2 — Hierarchy Selection (Priority: P1)

As a student, I want to choose what to practice by navigating through subject → track → unit → topic, so I can focus on specific content areas.

**Why P1**: This is the entry point to the Practice Arena. Without it, students cannot start a session.

**Acceptance Scenarios**:

1. **Given** a student opens the Practice Arena and selects a subject, **When** the hierarchy API is called, **Then** ALL tracks/units/topics are returned (including locked ones), with an `accessible` flag on each level indicating whether the student has access. The frontend uses this flag to visually distinguish locked vs. unlocked content (e.g., lock icon to encourage purchase).
2. **Given** a student selects "Completed only" filter, **When** the hierarchy is loaded, **Then** only tracks/units/topics where the student has completed at least one lesson are shown. Items with zero completed lessons are hidden entirely (not shown as disabled).
3. **Given** a student selects "All content" filter, **When** the hierarchy is loaded, **Then** all tracks/units/topics are shown, including unstarted content.
4. **Given** a student selects 3 tracks, **When** the UI updates, **Then** unit/topic selection is disabled (multi-selection at one level prevents drilling deeper).
5. **Given** a student selects exactly 1 track, **When** the UI updates, **Then** unit selection becomes available for that track.
6. **Given** a student selects exactly 1 unit, **When** the UI updates, **Then** topic selection becomes available for that unit.
7. **Given** a student selects 2 units, **When** the UI updates, **Then** topic selection is disabled, practice spans both units.
8. **Given** a student with no completed lessons selects "Completed only" filter, **Then** empty hierarchy is returned — nothing to practice.

---

### User Story 3 — Practice Session Flow (Priority: P1)

As a student, I want to start a practice session, answer questions, see my results, and optionally continue with more questions using the same filters.

**Why P1**: This is the core user-facing functionality of the Practice Arena.

**Acceptance Scenarios**:

1. **Given** a student starts a session with valid filters, **When** the backend processes the request, **Then** it returns up to `practice_session_size` questions (default: 20) with proportional distribution across topics based on content volume.
2. **Given** a student's selected filters match only 7 items but `practice_session_size` is 20, **When** the session starts, **Then** only 7 questions are returned. No padding with items from outside the selected filters — the filters must be respected strictly.
3. **Given** a student has never practiced before, **When** questions are selected, **Then** all questions are unseen items (first priority).
4. **Given** a student has seen all items matching the filters, **When** a new batch is requested, **Then** the response includes `all_seen_warning: true` and questions are ordered by `last_seen_at` ascending (oldest seen first).
5. **Given** a batch contains ANY repeat questions (even one), **When** the response is returned, **Then** `all_seen_warning: true` is set. This flag is checked on every batch, not just the first time.
6. **Given** a student completes a batch and requests more, **When** the next batch is served, **Then** priority order is: (1) never seen → (2) seen in previous sessions, oldest first → (3) seen in current session, oldest first.
7. **Given** a student completes a batch, **When** results are submitted, **Then** `tabMemora Practice Log` is updated immediately via raw SQL `INSERT ... ON DUPLICATE KEY UPDATE`. Not deferred — saved right away so data survives if the student leaves.
8. **Given** a student exits mid-batch (closes app/browser), **When** the Redis session expires, **Then** nothing is saved for the incomplete batch. Previously completed batches within the session remain saved (they were saved immediately upon completion).
9. **Given** a student submits the same batch results twice (network retry), **When** the backend processes the duplicate, **Then** the second submission is ignored and the cached response is returned. Idempotency enforced via `batch_seq` number tracked in the Redis session.
10. **Given** a student starts a new session while having an active one, **When** the new session is created, **Then** the old session is auto-expired (one active session per student enforced via the `memora:practice:{player_id}` key in Redis).

---

### User Story 4 — Access Control Enforcement (Priority: P1)

As the system, I need to ensure students can only practice content they have access to, even if the frontend sends invalid requests.

**Why P1**: Security boundary — students must not access paid content they haven't subscribed to.

**Acceptance Scenarios**:

1. **Given** a student starts a session with a track they are NOT subscribed to and is NOT free, **When** the backend validates the request, **Then** the request is rejected with a 403 error.
2. **Given** a student's subscription expires during an active session, **When** they request the next batch (continue), **Then** the session continues normally. Access is checked only once at session start, not on subsequent batch requests.
3. **Given** a student has access key `SUB-SUBJ-00028` (full subject), **When** they select any track within that subject, **Then** access is granted for all tracks.
4. **Given** a student has access key `TRK-MATH-01` (single track), **When** they select a different track in the same subject, **Then** access is denied for the unsubscribed track.
5. **Given** a unit or topic is marked `is_free = true`, **When** a student without any subscription selects it, **Then** access is granted. Free content is practicable without a subscription, consistent with the existing lesson access model.
6. **Given** a subject is in the plan's free subjects set (`memora:plan:{plan_id}:free_subjects`), **When** a student selects tracks in that subject, **Then** access is granted.

---

### Edge Cases

| Scenario | Behavior |
|----------|----------|
| Lesson with no reviewable stages | Silently skipped — no items generated |
| Stage with `config_json` = null or invalid JSON | Silently skipped with warning log |
| Teacher updates content while student is mid-session | Student sees the version loaded at session start. Updated content appears in the next session |
| Item deleted while student has it in active session | When results are submitted, the deleted item is silently skipped. Other results saved normally |
| Teacher deletes an item | Hard delete from `Memora Review Item` + cascade delete from `tabMemora Practice Log` |
| All items in selected filters are from skippable stages | Empty result — session cannot start, return 422 NO_ITEMS |
| Redis goes down during a session | Session state lost. Student must start a new session. Previously saved batches in MariaDB are safe |
| Student with no completed lessons + "Completed only" filter | Empty hierarchy returned |
| Available items < session_size | Return only available items, no padding from outside filters |
| Student starts new session with one already active | Old session auto-expired, new one created |
| Duplicate batch submission (network retry) | Ignored via `batch_seq` idempotency, cached response returned |
| `is_reviewable = false` on lesson | No items extracted for practice, same as daily reviews |

---

## Data Model

### Memora Review Item (Standard Frappe DocType)

Flat, queryable table of all reviewable items extracted from lesson stages. Standard Frappe DocType because the table is small (~200K rows) and ORM overhead is acceptable.

**One row per item, one question per item (for now).**

**Autoname**: `field:item_id` — the UUID string IS the Frappe document `name`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `item_id` | Data | Yes | Original UUID from `config_json`. Unique. Also serves as Frappe PK (`name`). |
| `lesson` | Link → Memora Lesson | Yes | Parent lesson |
| `stage_id` | Data | Yes | `Memora Lesson Stage` child table row name |
| `stage_type` | Link → Memora Lesson Stage Settings | Yes | Stage type (QUESTION, MATCHING, FILL_BLANK, etc.) |
| `subject` | Link → Memora Subject | Yes | Denormalized for fast filtering |
| `track` | Link → Memora Track | Yes | Denormalized for fast filtering |
| `unit` | Link → Memora Unit | Yes | Denormalized for fast filtering |
| `topic` | Link → Memora Topic | Yes | Denormalized for fast filtering |
| `question_text` | Small Text | No | The question to display (populated for MCQ stages, may be null for others) |
| `choice_1` | Small Text | No | First answer choice |
| `choice_2` | Small Text | No | Second answer choice |
| `choice_3` | Small Text | No | Third answer choice (may be null if only 2 choices) |
| `choice_4` | Small Text | No | Fourth answer choice (may be null if only 2–3 choices) |
| `correct_choice` | Int | No | **1-based** index of the correct choice (1–4). Null for non-MCQ stages. |
| `content_json` | Code (JSON) | No | Structured content for non-MCQ stages (FILL_BLANK, MATCHING, MINDMAP, SENTENCE_BUILDER). Null for MCQ stages. |

**Content split by stage type:**

| Stage Type | `choice_1..4` + `correct_choice` | `content_json` |
|------------|----------------------------------|-----------------|
| QUESTION | Populated (MCQ choices, 1-based index) | NULL |
| MATCHING | NULL | `{"left": "...", "right": "..."}` per pair |
| FILL_BLANK | NULL | `{"text": "...", "blank_from": N, "blank_to": N, "distractors": [...]}` |
| SENTENCE_BUILDER | NULL | `{"sentence": "...", "words": [...]}` |
| MINDMAP | NULL | `{"label": "...", "description": "..."}` per node |

**Validation rules** (in `MemoraReviewItem.validate()`):
- `item_id` must be a valid UUID (regex: `^[0-9a-f]{8}-...-[0-9a-f]{12}$`)
- `correct_choice`, if populated, must be between 1 and 4
- At least one of `choice_1` or `content_json` must be provided

**Why denormalize the full hierarchy?** The Practice Arena filters on track/unit/topic level. Without denormalization, every query would JOIN 3-4 tables. With 100K concurrent students, this overhead is unacceptable. The hierarchy data rarely changes, so the redundancy cost is negligible. The background sync job updates hierarchy fields when it processes a changed lesson.

**Why `choice_1..4` instead of `choices_json`?** Individual fields allow Frappe List View column display, admin-panel searching, and direct SQL filtering without JSON extraction functions. The `content_json` field provides a clean fallback for non-MCQ stage types that need structured data (pairs, blanks, tree nodes).

**Indexes**:

| Index | Columns | Purpose |
|-------|---------|---------|
| UNIQUE | `item_id` | One row per item (also Frappe PK via autoname). To support future multi-variant questions, remove this constraint and add a `variant_id`. |
| Composite | (`subject`, `track`) | Primary filter path |
| Composite | (`subject`, `track`, `unit`) | Drill-down filter |
| Composite | (`subject`, `track`, `unit`, `topic`) | Full drill-down filter |
| Index | `lesson` | Sync job: find all items by lesson for delta processing |

**Estimated Size**: ~200,000 rows (4 subjects × 4 tracks × 5 units × 5 topics × 20 lessons × ~5 reviewable items per lesson ÷ accounting for skippable stages). Small table — standard Frappe DocType with indexes is sufficient. No partitioning needed.

---

### tabMemora Practice Log (Raw SQL Table, NOT a Frappe DocType)

One row per student per item. Updated (not inserted) on each encounter via `INSERT ... ON DUPLICATE KEY UPDATE`.

**Why raw SQL?** At ~500 million rows (100K students × 5K items each) with frequent UPSERTs on every batch submit, Frappe ORM overhead is prohibitive. This follows the same pattern as `tabMemora Memory State` — raw SQL, managed via `setup.py`, all access through `frappe.db.sql()`.

**Why NOT a DocType?** No Frappe standard columns (creation, modified, owner, docstatus, etc.), no ORM access, no admin panel visibility needed. Pure data table optimized for write-heavy workloads.

| Column | DB Type | Required | Description |
|--------|---------|----------|-------------|
| `id` | BIGINT AUTO_INCREMENT | Yes | Primary key. Named `id` (not `name`) to clearly signal this is a raw SQL table, not a Frappe DocType. |
| `player_id` | VARCHAR(140) | Yes | Player profile docname (e.g., PLAYER-00001) |
| `item_id` | VARCHAR(36) | Yes | Original item UUID. Matches `Memora Review Item.item_id`. |
| `first_seen_at` | DATETIME | Yes | When the student first saw this question |
| `last_seen_at` | DATETIME | Yes | When the student last saw this question |
| `last_result` | ENUM('Correct', 'Incorrect') | Yes | Most recent attempt result |
| `attempt_count` | INT UNSIGNED | Yes | Total number of times shown to this student |
| `correct_count` | INT UNSIGNED | Yes | Total number of correct answers |

**Why `item_id` (UUID) and not `review_item` (Frappe PK)?** The UUID is the stable canonical identifier assigned at content creation time. Although `ri.name = ri.item_id` today (via `autoname: field:item_id`), keying the Practice Log on the UUID ensures history survives if Review Item records are ever rebuilt or re-synced. The UNIQUE constraint on `Memora Review Item.item_id` guarantees JOIN integrity.

**Indexes**:

| Index | Columns | Purpose |
|-------|---------|---------|
| PRIMARY | `id` | Auto-increment row ID |
| UNIQUE | (`player_id`, `item_id`) | One row per student per item. Also serves as the lookup index for UPSERT and the JOIN key for question selection. |
| Index | `item_id` | Fast cascade deletion when items are removed from Review Item table |

**Why no season/partition?** Practice Log tracks "has this student seen this question?" — this is timeless information that doesn't reset with seasons. The composite unique index on (`player_id`, `item_id`) ensures queries always hit a narrow key range. At 500M rows, a well-indexed InnoDB table performs fine without partitioning.

**Analytics derived from these fields**:
- Accuracy rate per item: `correct_count / attempt_count`
- Hard items identification: items with low accuracy across many students
- Student improvement tracking: accuracy trend over time

**Table creation** (managed via `setup.py`, not Frappe migrations):

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

**Write pattern** (every batch submit):

```sql
INSERT INTO `tabMemora Practice Log`
    (player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
VALUES
    (%(player_id)s, %(item_id)s, %(now)s, %(now)s, %(result)s, 1, %(correct)s)
ON DUPLICATE KEY UPDATE
    last_seen_at = %(now)s,
    last_result = %(result)s,
    attempt_count = attempt_count + 1,
    correct_count = correct_count + %(correct)s
```

---

## Review Item Sync Mechanism

### Trigger: Dirty-Set Pattern

When a teacher saves or modifies a lesson in the Frappe admin, the `on_update` hook marks the lesson as dirty by adding it to a Redis SET. The hook does NOT perform extraction directly — it only enqueues the lesson for processing.

```
on_lesson_save(doc):
    SADD memora:dirty:review_items {lesson_name}

on_lesson_trash(doc):
    # Immediate: delete all Review Items for this lesson (cascade)
    delete_review_items_for_lesson(lesson_name)
    SREM memora:dirty:review_items {lesson_name}
```

**Redis key**: `memora:dirty:review_items`
- **Type**: SET of lesson names
- **TTL**: None (protected — never evicted, same as `memora:dirty:progress` and `memora:dirty:wallets`)
- **Producers**: `on_lesson_save` hook
- **Consumers**: Scheduled sync job (every 2 minutes)

### Scheduled Job

A background job runs every 2 minutes via `hooks.py` `scheduler_events`. Processing flow:

1. **Pop dirty lessons**: `SMEMBERS memora:dirty:review_items` to get all pending lessons
2. **For each lesson**:
   - a. Fetch lesson doc from Frappe
   - b. Check `is_reviewable` — if `false`, delete ALL existing `Memora Review Item` rows for this lesson (cascade deletes Practice Log rows too), `SREM` from dirty set, and skip
   - c. Compare `content_hash` — if unchanged since last sync, `SREM` and skip (dedup)
   - d. Parse each stage's `config_json`
   - e. Check stage skippability: per-stage `is_skippable` override first, then global `Memora Lesson Stage Settings`
   - f. Extract `item_id` values from non-skippable stages
   - g. Compare with existing `Memora Review Item` rows for this lesson
   - h. **New items** → INSERT with hierarchy fields, question data
   - i. **Changed items** → UPDATE question text, choices, content_json
   - j. **Deleted items** → DELETE from `Memora Review Item` AND cascade delete from `tabMemora Practice Log` via raw SQL
   - k. Update `content_hash` on the lesson doc
   - l. **On success**: `SREM memora:dirty:review_items {lesson_name}`
   - m. **On failure**: Keep lesson in dirty set (auto-retried next run), log the error with structlog

3. **Batch processing**: All dirty lessons are processed in a single job run
4. **Dedup**: If a teacher saves the same lesson 10 times in 2 minutes, only one entry exists in the dirty set. The content_hash comparison provides a second layer of dedup within the processing function.

### Item Extraction Per Stage Type

| Stage Type | Item Location in `config_json` | Items Per Stage | Storage Target |
|------------|-------------------------------|-----------------|----------------|
| QUESTION | `answers[].item_id` (correct answer only) | 1 | `choice_1..4` + `correct_choice` |
| MATCHING | `pairs[].item_id` | 1 per pair | `content_json` |
| FILL_BLANK | `blanks[].item_id` | 1 per blank | `content_json` |
| SENTENCE_BUILDER | `words[].item_id` or root `item_id` | 1 | `content_json` |
| MINDMAP | `children[].item_id` (recursive) | 1 per node | `content_json` |
| INFORMATION | Skippable — no items | 0 | N/A |
| REVEAL | Skippable — no items | 0 | N/A |

### Question Data Population

**QUESTION stages** (MCQ — direct mapping):
- `question_text`: Copied from `config_json.question`
- `choice_1..4`: Copied from `config_json.answers[].text` (up to 4)
- `correct_choice`: 1-based index of the answer where `is_correct = true`
- `content_json`: NULL

**Non-MCQ stages** (structured data):
- `question_text`: Derived label (e.g., "ما هو: {pair.left}؟" for MATCHING, or the blank's surrounding text for FILL_BLANK)
- `choice_1..4`: NULL
- `correct_choice`: NULL
- `content_json`: Stage-type-specific JSON structure preserving the original data needed for rendering

**Placeholder questions** (temporary, until AI agent generates proper MCQ variants):
- For MATCHING pairs: `question_text` = "ما هو: {left}؟", `choice_1` = right value, `choice_2..4` = placeholder values, `correct_choice` = 1
- Other non-MCQ types: Use `content_json` only (no synthetic choices)

---

## Session Management

### Redis Key Design

**Session data** (stores filters, served items, batch tracking):
```
Key:    memora:practice:{player_id}
Type:   HASH
TTL:    practice_session_ttl (default 3600 seconds / 1 hour)
```

Hash fields:
```
subject_id          "SUBJ-00001"
filter              "completed"
tracks              '["TRK-001", "TRK-002"]'       (JSON array)
units               '[]'                             (JSON array)
topics              '[]'                             (JSON array)
selected_topics     '["TPC-001", "TPC-002", ...]'   (JSON array — resolved topic IDs for the selected filters)
accessible_lessons  '["LESSON-001", ...]'            (JSON array — lessons the student has access to)
batch_seq           "0"                              (last SERVED batch sequence number)
served_item_ids     '["uuid-1", "uuid-2", ...]'     (JSON array — all items served so far)
created_at          "2026-03-02 14:30:00"
submitted_1         "1"                              (idempotency marker — batch 1 was submitted)
submitted_2         "1"                              (idempotency marker — batch 2 was submitted)
```

**Why a single key per player (not `session:{session_id}` + `active:{player_id}`)?**

Since only one session per player is allowed (D-013), the player_id IS the natural session key. This eliminates the need for a separate active-pointer key and simplifies the session lifecycle. The player is identified by their JWT token — no `session_id` needs to be passed in API requests.

### Session Lifecycle

1. **Start session** (`POST /practice/start`):
   - Check `memora:practice:{player_id}` — if exists, DELETE it (auto-expire old session)
   - Validate access (subscription + free content check, once only)
   - Resolve accessible lessons from filters
   - Create Redis HASH with filters, empty `served_item_ids`, `batch_seq = 0`
   - Set TTL on the key
   - Query and return first batch of questions
   - `batch_seq` in response = 1 (first batch served)

2. **Submit batch results** (`POST /practice/submit`):
   - Load session HASH from Redis — if missing, return 404 NO_ACTIVE_SESSION
   - Check `submitted_{batch_seq}` marker — if present, return cached response (idempotency)
   - Save results to `tabMemora Practice Log` via raw SQL `INSERT ... ON DUPLICATE KEY UPDATE`
   - Silently skip any items that no longer exist in `Memora Review Item` (teacher deleted mid-session)
   - Set `submitted_{batch_seq} = 1` in Redis HASH
   - Return summary (correct_count, total_count, accuracy_percent)

3. **Continue (next batch)** (`POST /practice/continue`):
   - Load session HASH from Redis — if missing, return 404 NO_ACTIVE_SESSION
   - Check that previous batch was submitted (if `batch_seq > 0` and `submitted_{batch_seq}` is missing, return 422 PREVIOUS_BATCH_NOT_SUBMITTED)
   - Extend `served_item_ids` with items from previous batch
   - Query next batch with priority ordering and proportional distribution
   - Increment `batch_seq` in Redis HASH
   - Return new batch

4. **Abandon** (student closes app/browser):
   - Redis TTL expires → session HASH auto-deleted
   - Nothing saved for incomplete batch
   - Previously completed and submitted batches remain in MariaDB

### Why Redis (Not MariaDB) for Sessions?

- Sessions are ephemeral — if Redis loses them, student just starts over (no data loss)
- Native TTL support (auto-cleanup, no cron needed)
- Sub-millisecond reads/writes for session state
- Consistent with existing project patterns (wallet, access grants, progress all use Redis)
- No analytics value in storing abandoned sessions

---

## Question Selection Algorithm

### Input

- Filtered `Memora Review Item` rows (by subject + tracks + units + topics + lesson completion + `is_reviewable`)
- Student's `tabMemora Practice Log` entries (via LEFT JOIN on `item_id`)
- `served_item_ids` from current Redis session
- `practice_session_size` from `Memora Settings`

### Priority Ordering

| Priority | Condition | Description |
|----------|-----------|-------------|
| 0 (highest) | No row in `tabMemora Practice Log` for this student | Never seen — fresh question |
| 1 (medium) | Has row in Practice Log, NOT in `served_item_ids` | Seen before in a previous session, ordered by `last_seen_at` ASC (oldest first) |
| 2 (lowest) | In `served_item_ids` | Already seen in current session, ordered by `last_seen_at` ASC |

### Proportional Distribution

When multiple topics are selected, questions are distributed proportionally to content volume. This prevents a large topic from dominating the batch.

**Algorithm (2-step)**:

1. **Count items per topic** after all filters are applied (subject, tracks, units, topics, accessible lessons). This produces a map: `{topic_id: item_count}`.

2. **Allocate per-topic quotas**:
   ```
   total_items = sum(all topic counts)
   for each topic:
       quota = round(topic_count / total_items * batch_size)

   # Adjust remainder: if sum(quotas) != batch_size, add/remove from the largest topic
   ```

3. **Fetch items per topic**: For each topic, run the priority-ordered selection query with `LIMIT quota`. Merge results across topics.

**Example**: Topic A has 100 items, Topic B has 10 items, batch_size = 20 → Topic A gets ~18 questions, Topic B gets ~2 questions.

### Per-Topic Selection Query

```sql
SELECT ri.item_id, ri.question_text, ri.choice_1, ri.choice_2, ri.choice_3,
       ri.choice_4, ri.correct_choice, ri.content_json, ri.topic, ri.stage_type,
       pl.last_seen_at, pl.attempt_count,
       CASE
           WHEN pl.item_id IS NULL THEN 0
           WHEN ri.item_id NOT IN ({served_ids}) THEN 1
           ELSE 2
       END AS priority
FROM `tabMemora Review Item` ri
LEFT JOIN `tabMemora Practice Log` pl
    ON pl.item_id = ri.item_id AND pl.player_id = %(player_id)s
WHERE ri.subject = %(subject)s
  AND ri.lesson IN (%(accessible_lessons)s)
  AND ri.topic = %(topic)s
ORDER BY priority ASC, pl.last_seen_at ASC NULLS FIRST
LIMIT %(quota)s
```

After filtering on topic, the item count drops to tens or low hundreds — the LEFT JOIN and ORDER BY are fast with proper indexes.

### `has_more` Flag

Derived from the per-topic item counts already computed for proportional distribution (no extra query):

```
total_available = sum(topic_item_counts)  -- already computed in step 1
has_more = total_available > len(served_item_ids) + len(current_batch)
```

This is accurate and free — the per-topic counts are required for quota calculation anyway.

### `unseen_remaining` Count

Computed once per batch with a single COUNT query:

```sql
SELECT COUNT(*) AS unseen_cnt
FROM `tabMemora Review Item` ri
LEFT JOIN `tabMemora Practice Log` pl
    ON pl.item_id = ri.item_id AND pl.player_id = %(player_id)s
WHERE ri.subject = %(subject)s
  AND ri.lesson IN (%(accessible_lessons)s)
  AND ri.topic IN (%(selected_topics)s)
  AND pl.item_id IS NULL
```

Cost: ~3–5ms (same index path as the main selection query). Computed once per batch, not per topic.

### `total_in_batch` Count

Simply `len(questions)` — the number of questions returned in the current batch. Trivially computed.

### `all_seen_warning` Flag

Checked on every batch. If ANY question in the batch has `priority > 0` (i.e., it has a row in Practice Log — the student has seen it before), set `all_seen_warning: true` in the response. The frontend shows a message informing the student that questions include repeats.

---

## Completion Filter Logic

### "Completed only" Filter

1. Load student's `passed_lessons_bitset` from **Redis** (fast path: `memora:progress:{player}:{subject}:v1`) or **MariaDB fallback** (`Memora Structure Progress.passed_lessons_bitset`)
2. Decode hex bitset to list of set bit positions
3. Map bit positions to lesson IDs via `Memora Lesson.bit_index`
4. Filter `Memora Review Item` to only include items from those completed lesson IDs
5. Build the hierarchy response from the filtered items — only show tracks/units/topics that have at least one matching item

### "All content" Filter

1. Skip bitset lookup entirely
2. Return all `Memora Review Item` rows matching the student's accessible content
3. Hierarchy shows everything the student has access to (plus inaccessible content with `accessible: false` for upsell)

---

## Access Control

### Three Levels of Access

Access is determined by checking the student's access keys in Redis (`memora:access:{player_id}`):

| Access Type | Key Format | Grants |
|-------------|------------|--------|
| Subject-level | `SUB-{subject_name}` | All tracks in that subject |
| Track-level | `TRK-{track_name}` | Only that specific track |
| Free content | `is_free = true` on Unit or Topic | That unit/topic, no subscription needed |
| Plan-level free | `memora:plan:{plan_id}:free_subjects` | Full subject, no subscription needed |

### `accessible` Flag (All Hierarchy Levels)

Every level in the hierarchy response carries an `accessible: bool` flag. The logic cascades downward:

| Level | `accessible = true` when |
|-------|--------------------------|
| **Track** | Student has subject-level grant (`SUB-*`) OR track-level grant (`TRK-*`) OR subject is in plan free subjects |
| **Unit** | Parent track is accessible OR `unit.is_free = true` |
| **Topic** | Parent unit is accessible OR `topic.is_free = true` |

The frontend uses this flag to:
- Show lock/unlock icons on each level
- Disable selection of inaccessible content (encourage purchase)
- Allow selection of free content without a subscription

### Hierarchy API (Read — No Access Blocking)

Returns ALL tracks/units/topics for the selected subject, with an `accessible` flag on each level. Nothing is hidden from the hierarchy response — the frontend decides how to render locked vs. unlocked content.

### Session Start API (Write — Access Enforced)

When a student starts a practice session, the backend validates that EVERY selected track is accessible via at least one of the access types above. If ANY selected track is inaccessible, the entire request is rejected with 403. This check happens **once** at session start — not on subsequent batch requests within the same session.

---

## API Design

### 1. Get Practice Hierarchy

```
GET /api/v1/practice/hierarchy?subject_id={id}&filter={completed|all}
```

**Response**:
```json
{
    "subject_id": "SUBJ-00001",
    "subject_title": "الرياضيات",
    "tracks": [
        {
            "track_id": "TRK-00001",
            "track_title": "المسار الأول",
            "accessible": true,
            "item_count": 150,
            "units": [
                {
                    "unit_id": "UNT-00001",
                    "unit_title": "الوحدة الأولى",
                    "accessible": true,
                    "item_count": 45,
                    "topics": [
                        {
                            "topic_id": "TPC-00001",
                            "topic_title": "الموضوع الأول",
                            "accessible": true,
                            "item_count": 20
                        }
                    ]
                }
            ]
        },
        {
            "track_id": "TRK-00002",
            "track_title": "المسار الثاني",
            "accessible": false,
            "item_count": 200,
            "units": [
                {
                    "unit_id": "UNT-00003",
                    "unit_title": "الوحدة الثالثة",
                    "accessible": false,
                    "item_count": 60,
                    "topics": [
                        {
                            "topic_id": "TPC-00005",
                            "topic_title": "موضوع مجاني",
                            "accessible": true,
                            "item_count": 10
                        }
                    ]
                }
            ]
        }
    ]
}
```

**Rate limit**: 30 requests/minute per player.

### 2. Start Practice Session

```
POST /api/v1/practice/start
```

**Request**:
```json
{
    "subject_id": "SUBJ-00001",
    "tracks": ["TRK-00001", "TRK-00002"],
    "units": [],
    "topics": [],
    "filter": "completed"
}
```

**Validation rules**:
- `tracks` must be non-empty
- If `len(tracks) > 1`, then `units` and `topics` must be empty (multi-track disables drill-down)
- If `len(units) > 1`, then `topics` must be empty (multi-unit disables topic drill-down)

**Response**:
```json
{
    "session_active": true,
    "batch_seq": 1,
    "questions": [
        {
            "item_id": "550e8400-e29b-41d4-a716-446655440000",
            "stage_type": "QUESTION",
            "question_text": "ما هو تاريخ قيام الثورة العربية؟",
            "choices": ["1916", "1918", "1920", "1914"],
            "correct_choice": 1,
            "content_json": null
        },
        {
            "item_id": "660f9511-f30c-52e5-b827-557766551111",
            "stage_type": "MATCHING",
            "question_text": "ما هو: القطة؟",
            "choices": [],
            "correct_choice": null,
            "content_json": {"left": "cat", "right": "قطة"}
        }
    ],
    "total_in_batch": 20,
    "total_available": 400,
    "has_more": true,
    "unseen_remaining": 380,
    "all_seen_warning": false
}
```

**Response fields**:
| Field | Type | Description |
|-------|------|-------------|
| `session_active` | bool | Always `true` on successful start |
| `batch_seq` | int | Sequence number of the served batch (starts at 1) |
| `questions` | list | Array of `PracticeQuestion` objects |
| `total_in_batch` | int | Number of questions in this batch (`len(questions)`) |
| `total_available` | int | Total items matching filters across all batches |
| `has_more` | bool | Whether more items exist beyond this batch + previously served items |
| `unseen_remaining` | int | Count of items the student has never seen (no Practice Log entry) |
| `all_seen_warning` | bool | `true` if any question in this batch has been seen before |

**`PracticeQuestion` fields**:
| Field | Type | Description |
|-------|------|-------------|
| `item_id` | str | Item UUID |
| `stage_type` | str | Stage type name (QUESTION, MATCHING, FILL_BLANK, etc.) |
| `question_text` | str or null | Question text (populated for MCQ, may be null for non-MCQ) |
| `choices` | list[str] | Answer choices array (assembled from `choice_1..4`, empty for non-MCQ) |
| `correct_choice` | int or null | 1-based index into `choices` (null for non-MCQ) |
| `content_json` | dict or null | Structured data for non-MCQ stages (null for MCQ) |

**Error Responses**:
- `403 NO_ACCESS`: Student does not have access to one or more selected tracks. Body includes `tracks` array listing denied track IDs.
- `422 NO_ITEMS`: No reviewable items match the selected filters.
- `422`: Invalid request (empty tracks, multi-track with units/topics, etc.)

**Side effect**: If student has an active session, it is auto-expired before creating the new one.

**Rate limit**: 10 requests/minute per player.

### 3. Submit Batch Results

```
POST /api/v1/practice/submit
```

**Request**:
```json
{
    "batch_seq": 1,
    "results": [
        {"item_id": "550e8400-e29b-41d4-a716-446655440000", "correct": true},
        {"item_id": "660f9511-f30c-52e5-b827-557766551111", "correct": false}
    ]
}
```

**Response**:
```json
{
    "accepted": true,
    "batch_seq": 1,
    "correct_count": 15,
    "total_count": 20,
    "accuracy_percent": 75.0,
    "is_duplicate": false
}
```

**Idempotency**: If `submitted_{batch_seq}` marker exists in the Redis session HASH, the cached response is returned with `is_duplicate: true` without re-processing. This prevents double-counting of `attempt_count` and `correct_count` on network retries.

**Deleted items**: If an `item_id` in the results no longer exists in `Memora Review Item` (teacher deleted it mid-session), it is silently skipped. Other results are saved normally. `total_count` reflects only successfully saved results.

**Error Responses**:
- `404 NO_ACTIVE_SESSION`: No active session for this player (expired or never started)
- `409 BATCH_SEQ_MISMATCH`: `batch_seq` doesn't match expected value. Body includes `expected` and `received`.

**Rate limit**: 30 requests/minute per player.

### 4. Continue Session (Next Batch)

```
POST /api/v1/practice/continue
```

**Request body**: Empty (player identified via JWT token).

**Response**: Same format as Start response, with updated `batch_seq`, `total_in_batch`, `has_more`, `unseen_remaining`, and `all_seen_warning`.

**Behavior**: Uses the same filters as the original session start. No re-selection needed. The student continues from where they left off with the next set of questions.

**Error Responses**:
- `404 NO_ACTIVE_SESSION`: No active session for this player
- `422 PREVIOUS_BATCH_NOT_SUBMITTED`: Previous batch must be submitted before requesting the next one. Body includes `batch_seq` of the unsubmitted batch.

**Rate limit**: 30 requests/minute per player.

---

## Rate Limiting

Practice Arena endpoints use the existing `RateLimiter` infrastructure (sliding window, Redis-backed Lua scripts). No new rate limiting system needed.

| Endpoint | Player Limit | Window | Notes |
|----------|-------------|--------|-------|
| `GET /practice/hierarchy` | 30/min | 60s | Read-only, lightweight query |
| `POST /practice/start` | 10/min | 60s | Heavier: access validation + DB query + Redis write |
| `POST /practice/submit` | 30/min | 60s | Matches normal session pace |
| `POST /practice/continue` | 30/min | 60s | Matches normal session pace |

---

## Configurable Settings

| Setting | Description | Default | Stored In |
|---------|-------------|---------|-----------|
| `practice_session_size` | Number of questions per batch | 20 | Memora Settings |
| `practice_session_ttl` | Redis session TTL in seconds | 3600 | Memora Settings |
| `review_item_sync_interval` | Sync job frequency | Every 2 minutes | Scheduler config (`hooks.py`) |

---

## System Relationship Map

```
Student opens Practice Arena
  │
  ├── Memora Player Subscription → determines accessible tracks
  │     (Redis: memora:access:{player_id})
  │     Also checks: is_free on Unit/Topic, plan-level free subjects
  │
  ├── Memora Structure Progress → determines completed lessons (for filter)
  │     (Redis: memora:progress:{player}:{subject}:v1  OR  MariaDB fallback)
  │
  ├── Memora Review Item (Frappe DocType, ~200K rows)
  │     Source of all practice questions
  │     Flat table with denormalized hierarchy for fast filtering
  │     Fields: choice_1..4 + correct_choice (MCQ) OR content_json (non-MCQ)
  │     Populated by dirty-set background sync from lesson config_json
  │
  ├── Redis Session (TEMPORARY — memora:practice:{player_id})
  │     One HASH per student (keyed by player_id, no separate session_id)
  │     Holds: filters, served_item_ids, batch_seq, idempotency markers
  │     TTL: 1 hour, auto-expires on abandon
  │
  ├── Redis Dirty Set (memora:dirty:review_items)
  │     Protected (no TTL). Holds lesson names awaiting sync.
  │     Producer: on_lesson_save hook. Consumer: scheduled job (every 2 min).
  │     On failure: lesson stays in set for auto-retry.
  │
  └── tabMemora Practice Log (Raw SQL, ~500M rows)
        NOT a Frappe DocType
        Managed via setup.py, accessed via frappe.db.sql() only
        One row per student per item (keyed on player_id + item_id UUID)
        UPSERT on each batch submit
        Tracks: first_seen_at, last_seen_at, last_result, attempt_count, correct_count

ZERO connection to:
  ✗ Memora Memory State (FSRS — daily reviews only)
  ✗ Streak system
  ✗ Leaderboard
  ✗ XP / Wallet
```

---

## Implementation Phases

### Phase 1: Review Item Table & Sync Job
1. Create `Memora Review Item` DocType JSON with `choice_1..4`, `correct_choice` (1-based), `content_json`, and full hierarchy fields
2. Implement background sync job using dirty-set pattern (`memora:dirty:review_items`)
3. `on_lesson_save` hook: `SADD` lesson to dirty set (no direct extraction)
4. `on_lesson_trash` hook: immediate deletion of Review Items + cascade to Practice Log
5. Scheduled job (every 2 minutes): process dirty set, extract items per stage type, handle insert/update/delete with `content_hash` dedup
6. On sync failure: keep lesson in dirty set for auto-retry, log error
7. Register dirty set key in `redis_keys.py` (no TTL — protected)
8. **Deliverable**: Table populated with all existing reviewable items

### Phase 2: Practice Log Table & Session Infrastructure
1. Create `tabMemora Practice Log` via `setup.py` DDL (raw SQL table, columns: `player_id`, `item_id`)
2. Add table creation to `after_migrate` hook (idempotent)
3. Implement Redis session create/read/expire using `memora:practice:{player_id}` key
4. Implement `batch_seq` idempotency logic with `submitted_{N}` markers
5. **Deliverable**: Storage and session infrastructure ready

### Phase 3: Core APIs
1. Hierarchy API with `accessible` flag on all levels (track, unit, topic), completion filter, item counts
2. Start session API with access validation, proportional topic distribution, question selection
3. Submit results API with Practice Log UPSERT, idempotency, deleted item handling
4. Continue session API with dedup, priority ordering, `all_seen_warning`
5. Response fields: `has_more`, `total_in_batch`, `unseen_remaining`, `all_seen_warning`
6. Rate limiting on all endpoints using existing `RateLimiter`
7. **Deliverable**: Full API suite functional

### Phase 4: Integration & Testing
1. End-to-end testing with realistic data volumes
2. Load testing with concurrent sessions (target: 100K concurrent students)
3. Edge case testing (deleted items, expired sessions, network retries, free content access)
4. Sync job testing (content changes, stage type changes, skippable stages, failure retry)
5. Proportional distribution testing (verify topic fairness with varying content volumes)
6. **Deliverable**: Production-ready feature

---

## Out of Scope

- Offline practice mode (online only)
- XP or rewards for practice sessions
- Impact on streaks or leaderboards
- Integration with FSRS / Memory State (zero connection)
- AI-generated question variants (future phase — placeholders used for now)
- Frontend UI/UX design details (frontend team handles this)
- Real-time multiplayer practice modes
- Multiple simultaneous sessions per student

---

## Success Criteria

| ID | Criterion | Target |
|----|-----------|--------|
| SC-001 | Action latency (start session, submit, continue) | < 2 seconds P95 |
| SC-002 | Review Item sync job for 100 changed lessons | < 30 seconds |
| SC-003 | Question selection query (student with 5K practice log entries) | < 100ms |
| SC-004 | Concurrent students without degradation to daily reviews | 100K |
| SC-005 | Duplicate batch submissions do not corrupt Practice Log | Verified by test |
| SC-006 | Students cannot practice inaccessible paid content | Verified by backend enforcement |
| SC-007 | Free content accessible for practice without subscription | Verified by test |

---

## Decisions Log

| # | Decision | Rationale |
|---|----------|-----------|
| D-001 | One item = one question (for now) | Simplicity. Future multi-variant support via removing UNIQUE on `item_id` and adding `variant_id`. |
| D-002 | Questions pre-generated by AI agent (placeholders for now) | Decouples question generation from practice feature. |
| D-003 | Sync via dirty-set + scheduled job, not on-save hook | `on_save` only marks dirty (`SADD`). Job runs every 2 min, processes each lesson once via `content_hash` dedup. Prevents 10 saves in 1 minute from hitting the DB 10 times. On failure, lesson stays in dirty set for auto-retry. |
| D-004 | Hard delete items (not soft delete) | Deleted items have no future value. Cascade to Practice Log. |
| D-005 | Full hierarchy denormalization in Review Item | Performance: avoids 3-4 JOINs per query with 100K concurrent users. |
| D-006 | Practice Log not tied to seasons | Practice history is timeless — "have you seen this?" doesn't reset. |
| D-007 | Per-topic selection queries merged for proportional distribution | Avoids complex SQL window functions. Each topic query is fast after filtering narrows to tens/hundreds of items. |
| D-008 | Proportional distribution across topics | Fair representation of content volume. Topic A (100 items) + Topic B (10 items) + batch_size 20 → ~18 from A, ~2 from B. |
| D-009 | Save results per batch, not per session | Prevents data loss if student leaves after batch 2 of 5. |
| D-010 | Redis for sessions (not MariaDB) | Ephemeral data, native TTL, sub-ms latency, existing pattern. |
| D-011 | Raw SQL for Practice Log (not DocType) | 500M rows with frequent UPSERTs — ORM overhead prohibitive. |
| D-012 | `id` BIGINT (not `name`) for Practice Log PK | Clearly signals raw SQL table, not Frappe-managed. |
| D-013 | One active session per student | Simpler UX and implementation, avoids confusion. |
| D-014 | Free content practicable without subscription | Consistent with existing lesson access model. |
| D-015 | Access checked once at session start | Simplicity, no mid-session disruption. |
| D-016 | `choice_1..4` (individual fields) not `choices_json` | Allows Frappe List View column display, admin searching, and direct SQL filtering. `content_json` field handles non-MCQ structured data. |
| D-017 | `correct_choice` is 1-based (1–4) | Matches Frappe DocType validation (1–4 range). API response preserves this indexing. |
| D-018 | `all_seen_warning` on every batch with repeats | Student always informed, not just first time. |
| D-019 | If items < session_size, return only available items | Filters must be respected strictly, no padding. |
| D-020 | `is_reviewable = false` skips practice too | Consistent behavior across daily reviews and practice. |
| D-021 | No `session_id` in API — player-keyed sessions | Since one session per player (D-013), `memora:practice:{player_id}` is the natural key. Player identified via JWT. Eliminates separate active-pointer key. |
| D-022 | Practice Log keys on `item_id` (UUID), not `review_item` (Frappe PK) | UUID is the stable canonical identifier assigned at content creation. Survives Review Item table rebuilds. JOIN via `pl.item_id = ri.item_id`. |
| D-023 | `has_more` derived from per-topic counts (no extra query) | Per-topic counts are already computed for proportional distribution. `has_more = total_available > len(served_item_ids) + total_in_batch`. Zero additional cost. |
| D-024 | `unseen_remaining` via single COUNT per batch | One LEFT JOIN COUNT query (~3-5ms). Computed once per batch, not per topic. Provides useful UX signal without expensive per-request overhead. |
| D-025 | `accessible` flag on all hierarchy levels (track, unit, topic) | Enables per-level lock/upsell UI. Cascading logic: track access flows down, free content overrides upward. |
| D-026 | `correct` field name in submit results (not `is_correct`) | Concise, matches original PRD intent. |
| D-027 | `content_json` for non-MCQ stages | FILL_BLANK, MATCHING, MINDMAP, SENTENCE_BUILDER need structured data that cannot be represented as simple choice fields. Single JSON field keeps schema clean. |
