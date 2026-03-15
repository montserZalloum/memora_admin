# Product Requirements Document: Practice Arena V2

**Product:** Practice Arena V2
**Author:** Engineering Team
**Date:** March 14, 2026
**Status:** Draft
**Version:** 1.0

---

## Table of Contents

1. [Overview](#1-overview)
2. [Problem Statement](#2-problem-statement)
3. [Goals & Success Metrics](#3-goals--success-metrics)
4. [Scope](#4-scope)
5. [User Stories](#5-user-stories)
6. [Functional Requirements](#6-functional-requirements)
7. [Non-Functional Requirements](#7-non-functional-requirements)
8. [System Architecture](#8-system-architecture)
9. [Data Models](#9-data-models)
10. [API Contracts](#10-api-contracts)
11. [Content Pipeline](#11-content-pipeline)
12. [Background Processing](#12-background-processing)
13. [Caching Strategy](#13-caching-strategy)
14. [Failure Handling](#14-failure-handling)
15. [Migration Plan](#15-migration-plan)
16. [Dependencies & Risks](#16-dependencies--risks)
17. [Open Questions](#17-open-questions)

---

## 1. Overview

Practice Arena is a training feature that allows students to practice questions from their enrolled subjects. Students select a scope (full subject, specific tracks, units, or topics) and receive batches of questions drawn from the `tabMemora Review Item` pool.

**V2** is a full backend redesign that eliminates real-time database queries during gameplay, introduces CDN-based content delivery, and decouples the write path from the read path to support high concurrency.

---

## 2. Problem Statement

### 2.1 Current State (V1)

The current Practice Arena relies on synchronous Frappe RPC calls for every operation — starting a session, selecting questions, and submitting answers. Each question selection involves a `LEFT JOIN` between `tabMemora Review Item` and `tabMemora Practice Log`, sorted by priority.

### 2.2 Pain Points

| #  | Problem                              | Impact                                                        |
|----|--------------------------------------|---------------------------------------------------------------|
| P1 | Single point of failure (Frappe)     | If Frappe is slow or down, all players are blocked             |
| P2 | Heavy SQL queries per session        | `LEFT JOIN` + `ORDER BY` degrades as Practice Log grows        |
| P3 | No session TTL                       | Abandoned sessions accumulate in Redis indefinitely            |
| P4 | Clock-based repeat avoidance         | `last_seen_at >= session_started_at` breaks with clock skew    |
| P5 | No deduplication safety net          | Redis flush causes duplicate `attempt_count` increments        |
| P6 | No content update propagation        | Cache invalidation is either too slow or too aggressive        |
| P7 | Insufficient rate limiting           | No protection against excessive session creation               |

### 2.3 Why Now

Traffic is expected to grow significantly. The current architecture cannot sustain high concurrency without degraded response times and increased database load.

---

## 3. Goals & Success Metrics

### 3.1 Goals

| Priority | Goal                                                                 |
|----------|----------------------------------------------------------------------|
| G1       | Zero database queries during active gameplay                         |
| G2       | Session start latency < 200ms (p95) regardless of concurrent users   |
| G3       | Support 10,000+ concurrent players without performance degradation   |
| G4       | Content updates propagate to players within 60 seconds               |
| G5       | Graceful degradation — Frappe outage does not block active sessions  |

### 3.2 Success Metrics

| Metric                          | V1 Baseline      | V2 Target         |
|---------------------------------|-------------------|--------------------|
| Session start latency (p95)     | ~800ms            | < 200ms            |
| Submit latency (p95)            | ~500ms            | < 100ms            |
| DB queries per session          | 3–5 per batch     | 0 (except cold start) |
| Max concurrent players          | ~500 estimated    | 10,000+            |
| Redis memory per idle session   | Unbounded         | Auto-expires in 1h |
| Content update delay            | Minutes (TTL)     | < 60 seconds       |

### 3.3 Non-Goals

- Changing the question format or stage types
- Modifying the content hierarchy (Subject → Track → Unit → Topic → Lesson)
- Changing the client-side UI/UX of the Practice Arena
- Real-time multiplayer or leaderboard features
- Modifying `tabMemora Review Item` structure

---

## 4. Scope

### 4.1 In Scope

| Item                                          | Description                                                  |
|-----------------------------------------------|--------------------------------------------------------------|
| CDN content pipeline                          | Map files and content chunks generated and served via CDN     |
| Player Practice Summary table                 | New table: one row per player + track with JSON history       |
| V2 API endpoints                              | `/v2/practice/start`, `/submit`, `/continue`                  |
| Background write worker                       | Queue-based async writes to Practice Log and Summary          |
| Question selection algorithm                  | In-memory priority-based selection using map + summary        |
| Session lifecycle management                  | TTL-based expiry with pending result flush                    |
| Rate limiting on session creation             | Max 5 sessions per player per hour                            |
| Migration tooling                             | Backfill script for Player Summary from existing Practice Log |
| Content generation hooks                      | Frappe hooks to regenerate affected chunks on publish/edit    |

### 4.2 Out of Scope

| Item                                          | Reason                                                       |
|-----------------------------------------------|--------------------------------------------------------------|
| Client-side UI changes                        | V2 is a backend redesign; client adapts to new API            |
| Answer verification on server                 | Training platform — student self-assessment is acceptable     |
| Access control enforcement on server          | Client handles access filtering; acceptable trade-off         |
| Changes to Practice Log table schema          | Remains unchanged as the historical record                    |
| Reporting or analytics dashboards             | Out of scope for this PRD; Practice Log still available       |

---

## 5. User Stories

### 5.1 Student

| ID    | Story                                                                                              | Acceptance Criteria                                                             |
|-------|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| US-01 | As a student, I want to start a practice session so I can review questions from my selected scope   | Session starts in < 200ms; questions load from CDN                              |
| US-02 | As a student, I want questions prioritized by what I haven't seen or got wrong                      | Unseen questions appear first, then incorrect, then oldest seen                 |
| US-03 | As a student, I want to continue practicing without seeing repeated questions                       | No repeats until all in-scope questions have been seen                          |
| US-04 | As a student, I want to submit my answers and see my accuracy immediately                          | Submit returns stats within 100ms; DB write happens in background               |
| US-05 | As a student, I want to continue to the next batch after submitting                                | Next batch is available immediately with fresh questions                        |
| US-06 | As a student, I want my progress to persist across sessions                                        | Starting a new session reflects all previous answers                            |
| US-07 | As a student, I want to see newly added questions when I start a new session                        | New content appears within 60 seconds of publication                            |
| US-08 | As a student, I want my session to handle deleted questions gracefully                              | Deleted questions are skipped; session continues without error                  |

### 5.2 Content Team

| ID    | Story                                                                                              | Acceptance Criteria                                                             |
|-------|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| US-09 | As a content editor, I want to add/edit/delete questions and have changes go live quickly           | Changes propagate to CDN within 60 seconds                                      |
| US-10 | As a content editor, I want to add questions without worrying about breaking active sessions        | Active sessions skip missing questions; no errors or crashes                    |
| US-11 | As a content editor, I want to see how many questions exist per topic in the hierarchy              | Map file reflects accurate counts after regeneration                            |

### 5.3 Operations

| ID    | Story                                                                                              | Acceptance Criteria                                                             |
|-------|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| US-12 | As an ops engineer, I want abandoned sessions to be cleaned up automatically                        | Sessions expire after 1 hour of inactivity; pending results flushed before expiry |
| US-13 | As an ops engineer, I want to monitor write queue depth and worker health                           | Queue depth and worker status are observable via metrics/logs                   |
| US-14 | As an ops engineer, I want the system to recover gracefully from Redis restarts                     | Players lose at most one session; summaries re-read from DB on next start       |

---

## 6. Functional Requirements

### 6.1 Content Delivery

| ID     | Requirement                                                                                     | Priority |
|--------|-------------------------------------------------------------------------------------------------|----------|
| FR-01  | System shall generate a map file per subject containing all question IDs and chunk references    | Must     |
| FR-02  | System shall generate content chunks (~100 questions each) grouped by topic                     | Must     |
| FR-03  | Map files and chunks shall be hosted on a CDN                                                   | Must     |
| FR-04  | Content generation shall be triggered automatically on Review Item create/update/delete          | Must     |
| FR-05  | Only affected chunks shall be regenerated on content changes (not all chunks)                    | Must     |
| FR-06  | CDN cache shall be invalidated when new content is uploaded                                      | Must     |
| FR-07  | Map file shall include subject-level metadata: total question count, generation timestamp        | Should   |

### 6.2 Session Management

| ID     | Requirement                                                                                     | Priority |
|--------|-------------------------------------------------------------------------------------------------|----------|
| FR-08  | Player can start a session with scope: subject + track(s), optionally filtered by unit(s)/topic(s) | Must   |
| FR-09  | Only one active session per player at a time                                                     | Must     |
| FR-10  | Starting a new session replaces any existing session                                             | Must     |
| FR-11  | Session shall expire after 1 hour of inactivity                                                  | Must     |
| FR-12  | Before session expiry, any pending (unsubmitted) results in the session shall be discarded       | Must     |
| FR-13  | Session creation shall be rate-limited to 5 per player per hour                                  | Must     |

### 6.3 Question Selection

| ID     | Requirement                                                                                     | Priority |
|--------|-------------------------------------------------------------------------------------------------|----------|
| FR-14  | Questions shall be selected using the map file and player summary — no database queries          | Must     |
| FR-15  | Selection shall filter by the player's chosen scope (tracks, units, topics)                      | Must     |
| FR-16  | Selection priority order: never seen > last incorrect > lowest correct ratio > oldest seen       | Must     |
| FR-17  | Batch size shall be 20 questions                                                                 | Must     |
| FR-18  | No question shall repeat within a session until all in-scope questions have been served           | Must     |
| FR-19  | When all questions have been seen, wrap around to oldest-seen questions with a warning flag       | Must     |
| FR-20  | Response shall include question IDs and the chunk references needed by the client                 | Must     |

### 6.4 Answer Submission

| ID     | Requirement                                                                                     | Priority |
|--------|-------------------------------------------------------------------------------------------------|----------|
| FR-21  | Player submits a batch of results: `[{item_id, is_correct}]`                                     | Must     |
| FR-22  | Submitted item IDs must match the current batch — reject unknown IDs                             | Must     |
| FR-23  | Duplicate submissions for the same batch shall return cached stats without re-processing          | Must     |
| FR-24  | Player summary in Redis shall be updated immediately on submit                                    | Must     |
| FR-25  | Results shall be pushed to a write queue for background persistence                               | Must     |
| FR-26  | Submit response shall include: correct_count, total_count, accuracy_percent                      | Must     |

### 6.5 Continue (Next Batch)

| ID     | Requirement                                                                                     | Priority |
|--------|-------------------------------------------------------------------------------------------------|----------|
| FR-27  | Continue shall require that the current batch has been submitted                                  | Must     |
| FR-28  | Continue shall use the updated player summary (reflecting latest submission) for selection        | Must     |
| FR-29  | Continue shall return the next batch in the same format as start                                  | Must     |

### 6.6 Player Practice Summary

| ID     | Requirement                                                                                     | Priority |
|--------|-------------------------------------------------------------------------------------------------|----------|
| FR-30  | A new table `tabPlayer Practice Summary` shall store one row per (player_id, track_id)           | Must     |
| FR-31  | Each row shall contain a JSON field with per-question history (last result, attempts, correct count, last seen) | Must |
| FR-32  | Summary shall be the source of truth for question selection priority                              | Must     |
| FR-33  | Summary shall be cached in Redis with a 2-hour TTL                                               | Must     |
| FR-34  | On cache miss, summary shall be read from the database (single row read)                         | Must     |

### 6.7 Background Write Worker

| ID     | Requirement                                                                                     | Priority |
|--------|-------------------------------------------------------------------------------------------------|----------|
| FR-35  | Worker shall consume from the write queue and batch-write to the database                        | Must     |
| FR-36  | Worker shall UPSERT into `tabMemora Practice Log` (existing behavior preserved)                  | Must     |
| FR-37  | Worker shall UPDATE the corresponding `tabPlayer Practice Summary` row                           | Must     |
| FR-38  | Worker shall be idempotent — processing the same message twice shall not corrupt data            | Must     |
| FR-39  | Worker shall handle Frappe/DB unavailability with retry and backoff                              | Must     |

---

## 7. Non-Functional Requirements

### 7.1 Performance

| ID     | Requirement                                                                    | Target         |
|--------|--------------------------------------------------------------------------------|----------------|
| NFR-01 | Session start latency (p95)                                                    | < 200ms        |
| NFR-02 | Submit latency (p95)                                                           | < 100ms        |
| NFR-03 | Continue latency (p95)                                                         | < 150ms        |
| NFR-04 | Concurrent active sessions supported                                           | 10,000+        |
| NFR-05 | Map file load time from CDN (p95)                                              | < 500ms        |
| NFR-06 | Content chunk load time from CDN (p95)                                         | < 300ms        |

### 7.2 Reliability

| ID     | Requirement                                                                    | Target         |
|--------|--------------------------------------------------------------------------------|----------------|
| NFR-07 | Active sessions shall survive Frappe outages                                   | 100%           |
| NFR-08 | Maximum data loss on Redis failure                                             | 1 session (20 questions) |
| NFR-09 | Write queue shall persist messages until processed                             | No message loss |
| NFR-10 | Background worker shall auto-recover from crashes                              | < 30s restart  |

### 7.3 Scalability

| ID     | Requirement                                                                    | Target         |
|--------|--------------------------------------------------------------------------------|----------------|
| NFR-11 | System shall scale horizontally by adding FastAPI instances                     | Linear scaling |
| NFR-12 | CDN shall handle content delivery without server involvement                   | Zero server load for content |
| NFR-13 | Redis memory per active player                                                 | < 1 MB         |
| NFR-14 | Player Summary row size (5,000 questions per track)                            | < 500 KB       |

### 7.4 Data Integrity

| ID     | Requirement                                                                    | Target         |
|--------|--------------------------------------------------------------------------------|----------------|
| NFR-15 | Practice Log shall remain the complete historical record                       | No data loss   |
| NFR-16 | Player Summary shall be eventually consistent with Practice Log                | < 5 min lag    |
| NFR-17 | Duplicate submissions shall not inflate attempt_count                          | Idempotent     |

### 7.5 Observability

| ID     | Requirement                                                                    | Target         |
|--------|--------------------------------------------------------------------------------|----------------|
| NFR-18 | Write queue depth shall be monitored                                           | Alert if > 1000 |
| NFR-19 | Worker processing rate shall be logged                                         | Per-minute     |
| NFR-20 | Cache hit/miss ratio for Player Summary shall be tracked                       | Per-endpoint   |
| NFR-21 | CDN cache hit ratio shall be monitored                                         | > 95%          |

---

## 8. System Architecture

### 8.1 Architecture Layers

```
┌──────────────────────────────────────────────────────────┐
│  Layer 1: CDN                                            │
│  Map files + Content chunks (static, cached globally)    │
└────────────────────────┬─────────────────────────────────┘
                         │ Client reads directly
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Layer 2: Client App                                     │
│  Loads map + chunks from CDN, renders questions,         │
│  submits answers to FastAPI                              │
└────────────────────────┬─────────────────────────────────┘
                         │ API calls (start, submit, continue)
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Layer 3: FastAPI Server                                 │
│  Session management, question selection (in-memory),     │
│  result validation, queue dispatch                       │
└──────────┬─────────────────────────────────┬─────────────┘
           │ Read/write cache                │ Enqueue
           ▼                                 ▼
┌────────────────────┐         ┌─────────────────────────┐
│  Layer 4: Redis    │         │  Layer 5: Write Queue   │
│  Player summaries, │         │  Buffered results for   │
│  sessions, rate    │         │  background processing  │
│  limit counters    │         │                         │
└────────────────────┘         └────────────┬────────────┘
                                            │ Batch write
                                            ▼
                               ┌─────────────────────────┐
                               │  Layer 6: Frappe / DB   │
                               │  Practice Log (history) │
                               │  Player Summary (sync)  │
                               │  Background only        │
                               └─────────────────────────┘
```

### 8.2 Read Path (Player-Facing)

All player-facing operations use only CDN, Redis, and in-memory computation. No database queries in the hot path.

### 8.3 Write Path (Background)

All database writes go through a queue and are processed asynchronously by a background worker. The player never waits for a database write.

---

## 9. Data Models

### 9.1 New Table: tabPlayer Practice Summary

| Column            | Type           | Description                                      |
|-------------------|----------------|--------------------------------------------------|
| player_id         | VARCHAR(140)   | Player identifier                                |
| track_id          | VARCHAR(140)   | Track identifier                                 |
| subject_id        | VARCHAR(140)   | Subject identifier (for index efficiency)        |
| question_history  | LONGTEXT       | JSON object with per-question history             |
| total_seen        | INT UNSIGNED   | Total unique questions seen in this track         |
| total_correct     | INT UNSIGNED   | Total correct answers in this track               |
| last_session_at   | DATETIME       | Timestamp of last session activity                |
| updated_at        | DATETIME       | Last update timestamp                             |

**Primary Key:** `(player_id, track_id)`
**Index:** `idx_player_subject (player_id, subject_id)`

### 9.2 question_history JSON Schema

```json
{
  "<uuid>": {
    "lr": "C|I",
    "ac": <int>,
    "cc": <int>,
    "ls": "<ISO datetime>"
  }
}
```

| Key  | Type     | Description                              |
|------|----------|------------------------------------------|
| lr   | string   | Last result: "C" (Correct) or "I" (Incorrect) |
| ac   | integer  | Attempt count                            |
| cc   | integer  | Correct count                            |
| ls   | string   | Last seen at (ISO 8601)                  |

Short keys are used intentionally to minimize JSON payload size for rows with thousands of entries.

### 9.3 Existing Table: tabMemora Practice Log (Unchanged)

| Column          | Type           | Description                                |
|-----------------|----------------|--------------------------------------------|
| player_id       | VARCHAR(140)   | Player identifier                          |
| item_id         | VARCHAR(36)    | Question UUID                              |
| first_seen_at   | DATETIME       | First time player saw this question        |
| last_seen_at    | DATETIME       | Most recent time player saw this question  |
| last_result     | ENUM           | 'Correct' or 'Incorrect'                  |
| attempt_count   | INT UNSIGNED   | Total attempts                             |
| correct_count   | INT UNSIGNED   | Total correct answers                      |

**Primary Key:** `(player_id, item_id)`

This table is preserved as the historical record for reporting and analytics. It is no longer queried during gameplay.

### 9.4 CDN Map File Schema

```json
{
  "subject_id": "<string>",
  "generated_at": "<ISO datetime>",
  "total_questions": <int>,
  "tracks": {
    "<track_id>": {
      "title": "<string>",
      "units": {
        "<unit_id>": {
          "title": "<string>",
          "topics": {
            "<topic_id>": {
              "title": "<string>",
              "questions": [
                { "id": "<uuid>", "chunk": <int> }
              ]
            }
          }
        }
      }
    }
  }
}
```

### 9.5 CDN Content Chunk Schema

```json
{
  "subject_id": "<string>",
  "chunk_id": <int>,
  "questions": {
    "<uuid>": {
      "type": "<stage_type>",
      "topic_id": "<string>",
      "stem": "<string>",
      "choices": ["<string>"],
      "correct": <int>,
      "explanation": "<string>"
    }
  }
}
```

---

## 10. API Contracts

### 10.1 POST /api/v2/practice/start

**Request:**

```json
{
  "subject_id": "SUBJ-001",
  "track_ids": ["TRACK-A", "TRACK-B"],
  "unit_ids": null,
  "topic_ids": null,
  "filter": "all"
}
```

**Validation Rules:**
- `track_ids` must be non-empty
- If multiple tracks, `unit_ids` and `topic_ids` must be null
- If multiple units, `topic_ids` must be null
- Rate limit: max 5 sessions per player per hour

**Response (200):**

```json
{
  "session_active": true,
  "batch_seq": 0,
  "question_ids": ["uuid-1", "uuid-2", "..."],
  "chunk_refs": [3, 7, 12],
  "total_available": 8500,
  "all_seen_warning": false
}
```

**Error Responses:**

| Status | Condition                  | Body                                           |
|--------|----------------------------|-------------------------------------------------|
| 400    | Invalid scope              | `{"detail": "multi-track cannot send units"}`   |
| 429    | Rate limit exceeded        | `{"detail": "max 5 sessions per hour"}`         |
| 503    | Player summary unavailable | `{"detail": "unable to load player history"}`   |

### 10.2 POST /api/v2/practice/submit

**Request:**

```json
{
  "batch_seq": 0,
  "results": [
    { "item_id": "uuid-1", "is_correct": true },
    { "item_id": "uuid-2", "is_correct": false }
  ]
}
```

**Validation Rules:**
- `batch_seq` must match current session batch
- All `item_id` values must belong to the current batch
- No duplicate `item_id` in one payload
- Duplicate submission of same `batch_seq` returns cached response

**Response (200):**

```json
{
  "accepted": true,
  "batch_seq": 0,
  "correct_count": 14,
  "total_count": 20,
  "accuracy_percent": 70.0,
  "is_duplicate": false
}
```

### 10.3 POST /api/v2/practice/continue

**Request:**

```json
{
  "batch_seq": 0
}
```

**Validation Rules:**
- Current `batch_seq` must have been submitted

**Response (200):**

```json
{
  "session_active": true,
  "batch_seq": 1,
  "question_ids": ["uuid-21", "uuid-22", "..."],
  "chunk_refs": [5, 8],
  "total_available": 8500,
  "all_seen_warning": false
}
```

### 10.4 GET /api/v2/practice/hierarchy

**Unchanged from V1.** The hierarchy endpoint reads from cached metadata and is not part of the gameplay hot path.

---

## 11. Content Pipeline

### 11.1 Generation Trigger

```
Review Item saved/deleted in Frappe
        │
        ▼
after_save / after_delete hook
        │
        ▼
Enqueue content generation job
        │
        ▼
Background worker executes:
        │
        ├── 1. Identify affected topic(s)
        ├── 2. Query all Review Items for affected topic(s)
        ├── 3. Regenerate affected chunk file(s)
        ├── 4. Regenerate subject map file
        ├── 5. Upload to CDN storage
        └── 6. Invalidate CDN cache for affected files
```

### 11.2 Chunk Assignment Rules

- Questions are grouped by topic into chunks
- Each chunk contains approximately 100 questions
- A topic with > 100 questions spans multiple chunks
- A topic with < 100 questions may share a chunk with adjacent topics in the same unit
- Chunk IDs are stable — adding questions to an existing topic appends to the last chunk or creates a new one

### 11.3 Map File Regeneration

The map file is lightweight (IDs + chunk references only) and can be regenerated in full on every change. Estimated generation time for 10,000 questions: < 2 seconds.

---

## 12. Background Processing

### 12.1 Write Queue Message Schema

```json
{
  "player_id": "PLAYER-001",
  "track_id": "TRACK-A",
  "subject_id": "SUBJ-001",
  "submitted_at": "2026-03-14T12:00:00Z",
  "results": [
    { "item_id": "uuid-1", "is_correct": true },
    { "item_id": "uuid-2", "is_correct": false }
  ]
}
```

### 12.2 Worker Processing Steps

```
For each message:
│
├── 1. UPSERT into tabMemora Practice Log
│      INSERT ... ON DUPLICATE KEY UPDATE
│      (preserves existing V1 behavior)
│
├── 2. UPDATE tabPlayer Practice Summary
│      Read current question_history JSON
│      Merge new results into JSON
│      Update total_seen, total_correct
│      Write back
│
└── 3. Acknowledge message
```

### 12.3 Idempotency

The worker must handle duplicate messages safely:

- Practice Log UPSERT is naturally idempotent for `last_result` and `last_seen_at`
- `attempt_count` and `correct_count` increments are **not** idempotent
- Worker shall check `last_seen_at` in Player Summary: if `submitted_at <= last_seen_at` for a given question, skip the update
- This prevents double-counting from reprocessed queue messages

### 12.4 Error Handling

| Scenario                    | Behavior                                      |
|-----------------------------|-----------------------------------------------|
| DB connection failure       | Retry with exponential backoff (max 5 retries)|
| Malformed message           | Log error, move to dead-letter queue          |
| Partial batch failure       | Retry entire batch (UPSERT is safe to repeat) |
| Worker crash                | Unacknowledged messages re-delivered by queue  |

---

## 13. Caching Strategy

### 13.1 Redis Keys

| Key Pattern                                          | Value                | TTL      | Purpose                           |
|------------------------------------------------------|----------------------|----------|-----------------------------------|
| `memora:practice:summary:{player_id}:{track_id}`    | JSON (question_history) | 2 hours  | Player's practice history per track |
| `memora:practice:session:{player_id}`                | Hash                 | 1 hour   | Active session state               |
| `memora:practice:rate:{player_id}:sessions`          | Integer              | 1 hour   | Session creation rate counter      |

### 13.2 Cache Population

| Event                        | Action                                                  |
|------------------------------|----------------------------------------------------------|
| Session start (cache miss)   | Read from DB → populate Redis → set 2h TTL               |
| Session start (cache hit)    | Read from Redis directly                                  |
| Answer submitted             | Update Redis cache in-place (no DB read)                  |
| TTL expires                  | Key auto-deleted; next session re-reads from DB           |
| New session after idle       | Summary still in cache (2h > 1h session TTL); reuse it   |

### 13.3 CDN Caching

| File Type       | Cache Duration | Invalidation                          |
|-----------------|----------------|----------------------------------------|
| Map file        | 1 year         | Invalidated on content change          |
| Content chunk   | 1 year         | Invalidated on content change          |

Long cache TTLs are safe because invalidation is explicit on every content change.

---

## 14. Failure Handling

### 14.1 Failure Matrix

| Component      | Failure Mode            | Active Sessions Impact        | New Sessions Impact            | Recovery                                   |
|----------------|-------------------------|-------------------------------|--------------------------------|--------------------------------------------|
| CDN            | Region outage           | None (chunks already loaded)  | Cannot load questions          | CDN multi-region failover                  |
| Redis          | Full restart            | All sessions lost             | Cannot start                   | Players restart; summaries re-read from DB |
| Redis          | Memory pressure         | Oldest keys evicted           | May need DB read               | Scale Redis or reduce TTLs                 |
| Frappe / DB    | Down                    | Zero impact                   | Cold-start players blocked     | Queue holds; resumes on recovery           |
| Write queue    | Backlog                 | Zero impact                   | Zero impact                    | Worker catches up                          |
| Write worker   | Crash                   | Zero impact                   | Zero impact                    | Auto-restart; reprocess unacked messages   |
| FastAPI        | Instance crash          | Affected requests fail        | Affected requests fail         | Load balancer routes to healthy instance   |

### 14.2 Data Loss Boundaries

| Scenario                              | Maximum Data Loss                          |
|---------------------------------------|--------------------------------------------|
| Redis flush during active session     | 1 session (up to 20 unanswered questions)  |
| Worker crash mid-processing           | 0 (messages re-delivered from queue)       |
| Frappe down for 1 hour               | 0 (queue holds all results until recovery) |
| CDN invalidation delay                | Players see stale content for up to 60s    |

---

## 15. Migration Plan

### Phase 1: Database Preparation (Week 1)

| Task                                                        | Owner     | Dependency |
|-------------------------------------------------------------|-----------|------------|
| Create `tabPlayer Practice Summary` table in Frappe         | Backend   | None       |
| Write backfill script: Practice Log → Player Summary        | Backend   | Table      |
| Run backfill on staging                                     | Backend   | Script     |
| Validate backfill accuracy                                  | QA        | Staging    |
| Run backfill on production                                  | DevOps    | Validation |

### Phase 2: Content Pipeline (Week 2)

| Task                                                        | Owner     | Dependency |
|-------------------------------------------------------------|-----------|------------|
| Build map file generator                                    | Backend   | None       |
| Build content chunk generator                               | Backend   | None       |
| Set up CDN storage bucket and access                        | DevOps    | None       |
| Implement Frappe hooks for auto-regeneration                | Backend   | Generators |
| Generate initial map files and chunks for all subjects      | Backend   | Pipeline   |
| Validate CDN content against Review Items                   | QA        | Content    |

### Phase 3: V2 API & Worker (Week 3)

| Task                                                        | Owner     | Dependency |
|-------------------------------------------------------------|-----------|------------|
| Implement V2 start endpoint                                 | Backend   | Phase 1, 2 |
| Implement V2 submit endpoint                                | Backend   | Phase 1    |
| Implement V2 continue endpoint                              | Backend   | Submit     |
| Implement write queue and background worker                 | Backend   | Phase 1    |
| Implement session TTL and cleanup logic                     | Backend   | Start      |
| Implement rate limiting                                     | Backend   | Start      |
| Write integration tests for all V2 endpoints                | Backend   | All above  |

### Phase 4: Client Migration (Week 4)

| Task                                                        | Owner     | Dependency |
|-------------------------------------------------------------|-----------|------------|
| Update client to fetch map + chunks from CDN                | Frontend  | Phase 2    |
| Update client to call V2 endpoints                          | Frontend  | Phase 3    |
| Handle deleted/missing question gracefully (skip)           | Frontend  | Phase 2    |
| End-to-end testing on staging                               | QA        | All above  |

### Phase 5: Rollout & Deprecation (Week 5)

| Task                                                        | Owner     | Dependency |
|-------------------------------------------------------------|-----------|------------|
| Deploy V2 to production behind feature flag                 | DevOps    | Phase 4    |
| Gradual rollout: 10% → 50% → 100%                          | DevOps    | Deploy     |
| Monitor metrics (latency, queue depth, errors)              | DevOps    | Rollout    |
| Deprecate V1 endpoints                                      | Backend   | 100% V2    |
| Remove old Redis session schema and V1 code                 | Backend   | Deprecation|

---

## 16. Dependencies & Risks

### 16.1 Dependencies

| Dependency                | Type       | Risk Level | Mitigation                                  |
|---------------------------|------------|------------|----------------------------------------------|
| CDN provider              | External   | Low        | Multi-region; standard providers are reliable |
| Redis availability        | Internal   | Medium     | Sentinel/cluster for HA; graceful degradation |
| Write queue system        | Internal   | Medium     | Use proven queue (Redis Streams, RabbitMQ)    |
| Frappe hook system        | Internal   | Low        | Well-established pattern in codebase          |
| Client-side update        | Internal   | Medium     | Feature flag for gradual rollout              |

### 16.2 Risks

| Risk                                            | Likelihood | Impact | Mitigation                                           |
|-------------------------------------------------|------------|--------|------------------------------------------------------|
| Player Summary JSON too large for some tracks   | Medium     | Medium | Monitor row sizes; split further by unit if needed    |
| CDN invalidation slower than expected            | Low        | Low    | Acceptable — 60s delay is tolerable                   |
| Queue backlog during traffic spike               | Medium     | Low    | Scale workers horizontally; no player-facing impact   |
| Backfill script takes too long                   | Low        | Medium | Run off-peak; batch processing with progress tracking |
| Client caches old chunk after CDN invalidation   | Low        | Low    | Client handles missing questions gracefully (skip)    |

---

## 17. Open Questions

| #  | Question                                                                                  | Status   | Decision |
|----|-------------------------------------------------------------------------------------------|----------|----------|
| Q1 | Which CDN provider? (CloudFront, Cloudflare, Bunny, etc.)                                 | Open     |          |
| Q2 | Which queue system? (Redis Streams, RabbitMQ, Celery, etc.)                               | Open     |          |
| Q3 | Should the map file be loaded by the server too, or purely client-side?                    | Open     |          |
| Q4 | Do we need a `/v2/practice/hierarchy` or can the client build hierarchy from the map file? | Open     |          |
| Q5 | What is the maximum acceptable Player Summary row size before we split further?            | Open     |          |
| Q6 | Should we add server-side answer verification as a future enhancement?                     | Deferred |          |
| Q7 | Do we need a mechanism to force-expire all active sessions (e.g., for maintenance)?        | Open     |          |

---

## Appendix A: Question Selection Algorithm (Pseudocode)

```python
def select_questions(map_file, scope, player_summaries, batch_size=20):
    # 1. Collect candidate question IDs from map file
    candidates = filter_by_scope(map_file, scope)

    # 2. Score each candidate
    scored = []
    for q_id in candidates:
        history = player_summaries.get(q_id)

        if history is None:
            # Never seen — highest priority
            sort_key = (0, 0, 0.0, "0000-00-00", q_id)
        else:
            result_score = 0 if history["lr"] == "I" else 1
            correct_ratio = history["cc"] / max(history["ac"], 1)
            sort_key = (1, result_score, correct_ratio, history["ls"], q_id)

        scored.append((sort_key, q_id))

    # 3. Sort and pick top N
    scored.sort()
    selected = [q_id for _, q_id in scored[:batch_size]]

    # 4. Resolve chunk references
    chunk_refs = list(set(map_file.get_chunk(q) for q in selected))

    return selected, chunk_refs
```

## Appendix B: V1 vs V2 Comparison Matrix

| Dimension                | V1                                    | V2                                      |
|--------------------------|---------------------------------------|-----------------------------------------|
| Content source           | DB query (Review Item + Practice Log) | CDN (map file + chunks)                 |
| Question selection       | SQL with LEFT JOIN                    | In-memory from map + Redis summary      |
| Repeat avoidance         | Timestamp comparison                  | UUID presence in summary                |
| Answer persistence       | Synchronous DB write                  | Async via write queue                   |
| Frappe dependency         | Every request                        | Background only (+ cold-start reads)    |
| Session storage          | Redis (no TTL)                        | Redis (1h TTL, auto-cleanup)            |
| Content updates          | Cache TTL or manual invalidation      | CDN invalidation on publish (< 60s)     |
| Max concurrent players   | ~500 (estimated)                      | 10,000+                                 |
| DB queries per batch     | 3–5                                   | 0                                       |