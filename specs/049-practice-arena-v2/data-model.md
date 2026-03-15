# Data Model: Practice Arena V2

**Feature Branch**: `049-practice-arena-v2`
**Date**: 2026-03-14

---

## Entity Relationship Overview

```
┌───────────────────┐      ┌───────────────────┐
│   Memora Subject   │──1:N─│   Memora Track     │
└───────────────────┘      └───────────────────┘
                                    │
                                    │ 1:N
                                    ▼
                           ┌───────────────────┐
                           │   Memora Unit      │
                           └───────────────────┘
                                    │
                                    │ 1:N
                                    ▼
                           ┌───────────────────┐
                           │   Memora Topic     │
                           └───────────────────┘
                                    │
                                    │ 1:N
                                    ▼
                           ┌───────────────────┐
                           │ Memora Review Item │ (existing, unchanged)
                           └───────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │ referenced in │ referenced in │
                    ▼               ▼               ▼
          ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐
          │  Map File    │  │Content Chunk │  │ Practice Log        │
          │  (CDN)       │  │  (CDN)       │  │ (existing, unchanged)│
          └─────────────┘  └──────────────┘  └─────────────────────┘
                                                       │
                                              aggregated into
                                                       ▼
                                            ┌──────────────────────┐
                                            │ Player Practice      │
                                            │ Summary (NEW)        │
                                            └──────────────────────┘
```

---

## 1. tabPlayer Practice Summary (NEW)

### Purpose

Stores a denormalized summary of each player's practice history per track. One row per `(player_id, track_id)` pair. The `question_history` JSON field contains per-question statistics used for priority-based question selection. This table is the source of truth for the question selection algorithm and is cached in Redis with a 2-hour TTL.

### Schema (Raw SQL — NOT a Frappe DocType)

```sql
CREATE TABLE IF NOT EXISTS `tabPlayer Practice Summary` (
    `player_id`         VARCHAR(140) NOT NULL,
    `track_id`          VARCHAR(140) NOT NULL,
    `subject_id`        VARCHAR(140) NOT NULL,
    `question_history`  LONGTEXT NOT NULL DEFAULT '{}',
    `total_seen`        INT UNSIGNED NOT NULL DEFAULT 0,
    `total_correct`     INT UNSIGNED NOT NULL DEFAULT 0,
    `last_session_at`   DATETIME NULL,
    `updated_at`        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`player_id`, `track_id`),
    KEY `idx_player_subject` (`player_id`, `subject_id`),
    KEY `idx_updated_at` (`updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### Column Details

| Column | Type | Nullable | Description |
|---|---|---|---|
| `player_id` | VARCHAR(140) | NOT NULL | Player identifier (matches `tabMemora Practice Log.player_id`) |
| `track_id` | VARCHAR(140) | NOT NULL | Track identifier (Link to Memora Track) |
| `subject_id` | VARCHAR(140) | NOT NULL | Subject identifier (denormalized from track for index efficiency) |
| `question_history` | LONGTEXT | NOT NULL | JSON object with per-question history (see schema below) |
| `total_seen` | INT UNSIGNED | NOT NULL | Count of unique questions seen in this track (derived from `question_history` keys count) |
| `total_correct` | INT UNSIGNED | NOT NULL | Total correct answers across all questions in this track |
| `last_session_at` | DATETIME | NULL | Timestamp of last session activity |
| `updated_at` | DATETIME | NOT NULL | Last update timestamp (auto-updated by MySQL) |

### question_history JSON Schema

```json
{
  "<item_id (UUID)>": {
    "lr": "C",
    "ac": 3,
    "cc": 2,
    "ls": "2026-03-14T10:00:00Z"
  }
}
```

| Key | Type | Description | Maps to Practice Log |
|---|---|---|---|
| `lr` | string | Last result: `"C"` (Correct) or `"I"` (Incorrect) | `last_result` |
| `ac` | integer | Attempt count | `attempt_count` |
| `cc` | integer | Correct count | `correct_count` |
| `ls` | string | Last seen at (ISO 8601) | `last_seen_at` |

Short keys are used intentionally to minimize JSON payload size. With 5,000 questions per track, each entry is ~80 bytes → ~400KB total, well within the 500KB target.

### Size Estimates

| Questions per Track | Estimated JSON Size | Total Row Size |
|---|---|---|
| 100 | ~8 KB | ~10 KB |
| 500 | ~40 KB | ~42 KB |
| 1,000 | ~80 KB | ~82 KB |
| 3,000 | ~240 KB | ~242 KB |
| 5,000 | ~400 KB | ~402 KB |

### Why Not a Frappe DocType

Same rationale as `tabMemora Practice Log`:
- Composite primary key `(player_id, track_id)` — Frappe requires single `name` column as PK
- No need for Frappe standard columns (owner, creation, modified, etc.)
- Raw SQL gives full control over indexes and JSON operations
- Avoids Frappe ORM overhead for high-frequency background writes

---

## 2. tabMemora Practice Log (EXISTING — UNCHANGED)

### Schema Reference

```sql
CREATE TABLE `tabMemora Practice Log` (
    `player_id`     VARCHAR(140) NOT NULL,
    `item_id`       VARCHAR(36) NOT NULL,
    `first_seen_at` DATETIME NOT NULL,
    `last_seen_at`  DATETIME NOT NULL,
    `last_result`   ENUM('Correct', 'Incorrect') NOT NULL,
    `attempt_count` INT UNSIGNED NOT NULL DEFAULT 1,
    `correct_count` INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (`player_id`, `item_id`),
    KEY `idx_item_id` (`item_id`),
    KEY `idx_player_seen_item` (`player_id`, `last_seen_at`, `item_id`),
    KEY `idx_last_seen_at` (`last_seen_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### Role in V2

- Continues to serve as the **historical record** for reporting and analytics
- **No longer queried during gameplay** — Player Summary replaces it for question selection
- Background write worker performs UPSERT into this table asynchronously
- Constraint C-001: Schema MUST NOT be modified

---

## 3. CDN Map File (per subject)

### Purpose

A lightweight index of all reviewable questions in a subject, organized by the content hierarchy. Contains only question IDs and chunk references — no question content. Used by both the FastAPI server (question selection) and the client (chunk resolution).

### Location

`practice/maps/{subject_id}.json`

### Schema

```json
{
  "schema_version": 1,
  "subject_id": "SUBJ-001",
  "generated_at": "2026-03-14T12:00:00Z",
  "total_questions": 8500,
  "tracks": {
    "<track_id>": {
      "title": "Track Title",
      "question_count": 2000,
      "units": {
        "<unit_id>": {
          "title": "Unit Title",
          "topics": {
            "<topic_id>": {
              "title": "Topic Title",
              "questions": [
                { "id": "<uuid>", "chunk": 3 },
                { "id": "<uuid>", "chunk": 3 }
              ]
            }
          }
        }
      }
    }
  }
}
```

### Field Descriptions

| Field | Type | Description |
|---|---|---|
| `schema_version` | integer | Schema version for forward compatibility |
| `subject_id` | string | Subject identifier |
| `generated_at` | string (ISO 8601) | Generation timestamp — used by client for cache validation |
| `total_questions` | integer | Total reviewable questions in the subject |
| `tracks.{id}.title` | string | Track display title |
| `tracks.{id}.question_count` | integer | Total questions in this track |
| `tracks.{id}.units.{id}.title` | string | Unit display title |
| `tracks.{id}.units.{id}.topics.{id}.title` | string | Topic display title |
| `tracks.{id}.units.{id}.topics.{id}.questions` | array | Questions in this topic |
| `questions[].id` | string (UUID) | Review Item `item_id` |
| `questions[].chunk` | integer | Chunk number containing this question's full content |

### Size Estimate

~300-500 KB for a subject with 10,000 questions (36-byte UUIDs + integer chunk refs + hierarchy keys).

---

## 4. CDN Content Chunk (per subject)

### Purpose

Contains full question content for approximately 100 questions, grouped by topic. The client loads chunks on demand based on chunk references from the map file.

### Location

`practice/chunks/{subject_id}/chunk_{N}.json`

### Schema

```json
{
  "schema_version": 1,
  "subject_id": "SUBJ-001",
  "chunk_id": 3,
  "question_count": 97,
  "questions": {
    "<uuid>": {
      "type": "QUESTION",
      "topic_id": "TOPIC-1A",
      "stem": "Solve for x: 2x + 5 = 15",
      "choices": ["x = 3", "x = 5", "x = 7", "x = 10"],
      "correct": 1,
      "explanation": "2x = 10, so x = 5"
    },
    "<uuid>": {
      "type": "FILL_BLANK",
      "topic_id": "TOPIC-1A",
      "stem": "The capital of Jordan is ___",
      "choices": ["Amman", "Irbid", "Zarqa", "Aqaba"],
      "correct": 0,
      "explanation": "Amman is the capital city of Jordan"
    }
  }
}
```

### Field Descriptions

| Field | Type | Description |
|---|---|---|
| `schema_version` | integer | Schema version |
| `subject_id` | string | Subject identifier |
| `chunk_id` | integer | Chunk number (stable — matches map file references) |
| `question_count` | integer | Number of questions in this chunk |
| `questions` | object | Keyed by question UUID |
| `questions.{uuid}.type` | string | Stage type: QUESTION, FILL_BLANK, MATCHING, INFORMATION |
| `questions.{uuid}.topic_id` | string | Topic this question belongs to |
| `questions.{uuid}.stem` | string | Question text |
| `questions.{uuid}.choices` | array[string] | Answer choices |
| `questions.{uuid}.correct` | integer | 0-based index of correct choice |
| `questions.{uuid}.explanation` | string | Explanation shown after answering |

### Size Estimate

~50-150 KB per chunk (100 questions with full content). Compressed (gzip): ~15-50 KB.

---

## 5. Redis State

### 5.1 Player Summary Cache

```
Key:     memora:practice:summary:{player_id}:{track_id}
Type:    String (JSON)
Value:   Same as question_history from tabPlayer Practice Summary
TTL:     7200 seconds (2 hours)
```

### 5.2 Active Practice Session

```
Key:     memora:practice:v2:session:{player_id}
Type:    Hash
Fields:
  session_id        UUID (unique per session)
  subject_id        Subject being practiced
  track_ids         JSON array of selected track IDs
  scope_hash        Hash of full scope for validation
  batch_seq         Current batch number (0-indexed)
  current_batch     JSON array of question UUIDs in current batch
  submitted         "0" or "1" — whether current batch has been submitted
  batch_stats       JSON — cached stats for last submitted batch (for duplicate detection)
  served_ids        JSON array of all question IDs served in this session (for repeat avoidance)
  created_at        ISO 8601 timestamp
  last_activity_at  ISO 8601 timestamp
TTL:     3600 seconds (1 hour), refreshed on submit/continue
```

### 5.3 Rate Limit Counter

```
Key:     memora:practice:rate:{player_id}:sessions
Type:    String (integer counter)
TTL:     3600 seconds (1 hour, sliding window)
Max:     5 (reject session creation if counter >= 5)
```

### 5.4 Map File Cache (in-process)

```
Storage: Python dict in FastAPI worker process memory
Key:     subject_id
Value:   Parsed map file data (Python dict)
TTL:     3600 seconds (1 hour, safety net)
Invalidation: Redis pubsub on channel memora:practice:map_invalidation
```

### 5.5 Write Queue (Redis Stream)

```
Key:     memora:practice:write_queue
Type:    Stream
Consumer Group: practice-writers
Message Fields:
  player_id      Player identifier
  track_id       Track identifier
  subject_id     Subject identifier
  submitted_at   ISO 8601 timestamp
  results        JSON array: [{"item_id": "uuid", "is_correct": true}, ...]
  batch_seq      Batch sequence number (for logging/debugging)
  session_id     Session UUID (for tracing)
```

### 5.6 Dead Letter Stream

```
Key:     memora:practice:write_queue:dead
Type:    Stream
Purpose: Messages that failed processing after 5 retries
```

---

## 6. Write Queue Message Schema

### Message Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `player_id` | string | Yes | Player identifier |
| `track_id` | string | Yes | Track for this batch of results |
| `subject_id` | string | Yes | Subject (denormalized for logging) |
| `submitted_at` | string (ISO 8601) | Yes | When the player submitted (immutable — used for idempotency) |
| `results` | JSON array | Yes | Array of `{item_id, is_correct}` objects |
| `batch_seq` | integer | Yes | Batch sequence number |
| `session_id` | string (UUID) | Yes | Session UUID for tracing |

### Example

```json
{
  "player_id": "PLR-00001",
  "track_id": "TRACK-A",
  "subject_id": "SUBJ-001",
  "submitted_at": "2026-03-14T12:00:00Z",
  "batch_seq": 0,
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "results": [
    {"item_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "is_correct": true},
    {"item_id": "ffffffff-gggg-hhhh-iiii-jjjjjjjjjjjj", "is_correct": false}
  ]
}
```

---

## 7. State Transitions

### 7.1 Session State Machine

```
                    POST /start
                        │
                        ▼
                    ┌────────┐
            ┌──────│ CREATED │──────┐
            │      └────────┘      │
            │          │           │
            │    (implicit)        │
            │          │           │
            │          ▼           │
            │     ┌─────────┐     │
            │     │ ACTIVE  │     │  POST /start (new session)
            │     │ batch_0 │     │  → replaces this session
            │     └─────────┘     │
            │          │           │
            │   POST /submit      │
            │          │           │
            │          ▼           │
            │     ┌───────────┐   │
            │     │ SUBMITTED │   │
            │     │ batch_0   │   │
            │     └───────────┘   │
            │          │           │
            │   POST /continue    │
            │          │           │
            │          ▼           │
            │     ┌─────────┐     │
            │     │ ACTIVE  │─────┘
            │     │ batch_1 │
            │     └─────────┘
            │          │
            │     ... (repeat) ...
            │          │
            │    TTL expires (1h idle)
            │          │
            │          ▼
            │     ┌─────────┐
            └────►│ EXPIRED │
                  └─────────┘
```

### 7.2 Write Queue Message State Machine

```
    Enqueued (XADD)
        │
        ▼
    ┌─────────┐
    │ PENDING │
    └─────────┘
        │
        │  XREADGROUP (consumer picks up)
        ▼
    ┌────────────┐
    │ PROCESSING │
    └────────────┘
        │
        ├─── Success ──► XACK ──► ┌───────────┐
        │                         │ COMPLETED │ (removed from PEL)
        │                         └───────────┘
        │
        └─── Failure ──► Retry (up to 5x)
                             │
                             └─── 5th failure ──► XADD to dead-letter ──► XACK original
                                                        │
                                                        ▼
                                                  ┌─────────────┐
                                                  │ DEAD-LETTER │
                                                  └─────────────┘
```

---

## 8. Validation Rules

### Scope Validation (FR-035, FR-036)

```
IF len(track_ids) > 1:
    unit_ids MUST be null
    topic_ids MUST be null

IF len(unit_ids) > 1:
    topic_ids MUST be null

track_ids MUST be non-empty
All IDs MUST exist in the map file for the given subject
```

### Submission Validation (FR-018)

```
batch_seq MUST match session.batch_seq
All item_ids MUST exist in session.current_batch
No duplicate item_ids in payload
len(results) MUST equal len(session.current_batch)
```

### Rate Limit Validation (FR-010)

```
INCR memora:practice:rate:{player_id}:sessions
IF counter > 5: reject with 429
SET TTL 3600 if first increment (NX)
```
