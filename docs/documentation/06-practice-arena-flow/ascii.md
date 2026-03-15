# Practice Arena V2 — System Design

## Executive Summary

A complete redesign of the Practice Arena backend to eliminate database bottlenecks, remove real-time dependency on Frappe, and serve any number of concurrent players with minimal server load.

**Core Principles:**

- Zero database queries during active gameplay
- CDN-first content delivery
- Background-only writes to database
- Client-side question rendering
- One-row-per-track player summary for instant session startup

---

## 1. Content Hierarchy (unchanged)

```
Subject
└── Track
    └── Unit
        └── Topic
            └── Lesson
                └── Stage
                    ├── QUESTION
                    ├── FILL_BLANK
                    ├── MATCHING
                    └── INFORMATION
                        └── Review Item extraction
                            └── tabMemora Review Item
```

---

## 2. New Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│                        CDN (Layer 1)                        │
│                                                             │
│   Map files + Content chunks per subject                    │
│   Served directly to the client                             │
│   Zero server load                                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │  Client fetches map + chunks
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   Client App (Layer 2)                      │
│                                                             │
│   - Loads map file from CDN                                 │
│   - Loads content chunks from CDN                           │
│   - Renders questions locally                               │
│   - Submits answers to FastAPI                              │
│   - Handles access filtering (which tracks are unlocked)    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │  POST /submit (answers only)
                           │  POST /start  (get question IDs)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                 FastAPI Server (Layer 3)                     │
│                                                             │
│   - Reads player summary from Redis (or DB on cache miss)   │
│   - Selects question IDs using map + player summary         │
│   - Applies priority logic in memory                        │
│   - Returns selected question IDs + chunk references        │
│   - Pushes results to write queue                           │
│   - Never queries Review Items table                        │
└────────────┬───────────────────────────────┬────────────────┘
             │                               │
             │  Cache read/write             │  Enqueue results
             ▼                               ▼
┌─────────────────────┐       ┌───────────────────────────────┐
│   Redis (Layer 4)   │       │     Write Queue (Layer 5)     │
│                     │       │                               │
│  - Player summary   │       │  - Buffered answer batches    │
│  - Active sessions  │       │  - Processed by background    │
│  - Rate limit       │       │    worker                     │
│    counters         │       │                               │
└─────────────────────┘       └──────────────┬────────────────┘
                                             │
                                             │  Batch write
                                             ▼
                              ┌───────────────────────────────┐
                              │   Frappe / MariaDB (Layer 6)  │
                              │                               │
                              │  - tabMemora Practice Log     │
                              │    (full history, reports)     │
                              │                               │
                              │  - tabPlayer Practice Summary  │
                              │    (one row per player+track)  │
                              │                               │
                              │  Background only — never in    │
                              │  the player's request path     │
                              └───────────────────────────────┘
```

---

## 3. CDN Content Files

### 3.1 Map File (per subject)

**Location:** `cdn://practice/maps/{subject_id}.json`

A lightweight index of all questions in a subject, organized hierarchically. Contains **no question content** — only IDs and chunk references.

```json
{
  "subject_id": "SUBJ-001",
  "generated_at": "2026-03-14T12:00:00Z",
  "total_questions": 8500,
  "tracks": {
    "TRACK-A": {
      "title": "Algebra",
      "units": {
        "UNIT-1": {
          "title": "Linear Equations",
          "topics": {
            "TOPIC-1A": {
              "title": "Solving for X",
              "questions": [
                { "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "chunk": 3 },
                { "id": "ffffffff-gggg-hhhh-iiii-jjjjjjjjjjjj", "chunk": 3 },
                ...
              ]
            }
          }
        }
      }
    }
  }
}
```

**Size estimate:** ~300–500 KB for 10,000 questions (UUIDs + chunk refs only).

### 3.2 Content Chunks (per subject)

**Location:** `cdn://practice/chunks/{subject_id}/chunk_{N}.json`

Each chunk contains full question details for ~100 questions grouped by topic.

```json
{
  "subject_id": "SUBJ-001",
  "chunk_id": 3,
  "questions": {
    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee": {
      "type": "QUESTION",
      "topic_id": "TOPIC-1A",
      "stem": "Solve for x: 2x + 5 = 15",
      "choices": ["x = 3", "x = 5", "x = 7", "x = 10"],
      "correct": 1,
      "explanation": "2x = 10, so x = 5"
    },
    ...
  }
}
```

**Size estimate:** ~50–150 KB per chunk (100 questions with full content).

### 3.3 Generation Workflow

```
Content team publishes / edits
        │
        ▼
Frappe after_save hook fires
        │
        ▼
Background worker triggered
        │
        ├── Reads affected lessons from tabMemora Review Item
        ├── Regenerates ONLY the affected chunks
        ├── Regenerates map file (lightweight — just ID + chunk refs)
        ├── Uploads to CDN storage
        └── Invalidates old CDN cache
              │
              ▼
        New content live within seconds
```

**Key rule:** Only regenerate chunks that changed. If one lesson is edited, one chunk is regenerated — not all 100 chunks.

---

## 4. New Database Table: Player Practice Summary

### 4.1 Schema

```
tabPlayer Practice Summary
├── player_id          VARCHAR(140)
├── track_id           VARCHAR(140)
├── subject_id         VARCHAR(140)
├── question_history   LONGTEXT (JSON)
├── total_seen         INT UNSIGNED
├── total_correct      INT UNSIGNED
├── last_session_at    DATETIME
├── updated_at         DATETIME
│
├── PRIMARY KEY (player_id, track_id)
└── INDEX idx_player_subject (player_id, subject_id)
```

### 4.2 question_history JSON Structure

```json
{
  "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee": {
    "lr": "C",
    "ac": 3,
    "cc": 2,
    "ls": "2026-03-14T10:00:00Z"
  },
  "ffffffff-gggg-hhhh-iiii-jjjjjjjjjjjj": {
    "lr": "I",
    "ac": 1,
    "cc": 0,
    "ls": "2026-03-13T15:30:00Z"
  }
}
```

| Field | Meaning              | Maps to Practice Log     |
|-------|----------------------|--------------------------|
| `lr`  | Last result (C/I)    | `last_result`            |
| `ac`  | Attempt count        | `attempt_count`          |
| `cc`  | Correct count        | `correct_count`          |
| `ls`  | Last seen at         | `last_seen_at`           |

**Short keys to minimize JSON size** — with 5,000 UUIDs per track, every byte matters.

### 4.3 Size Estimate

| Questions Seen | JSON Size (approx) |
|----------------|---------------------|
| 500            | ~50 KB              |
| 1,000          | ~100 KB             |
| 3,000          | ~300 KB             |
| 5,000          | ~500 KB             |

Split by track keeps each row manageable. A subject with 10 tracks and 10,000 total questions = ~10 rows averaging 50–100 KB each.

---

## 5. Redis Schema

### 5.1 Player Summary Cache

```
Key:    memora:practice:summary:{player_id}:{track_id}
Value:  JSON string (same as question_history from DB)
TTL:    2 hours after last activity
```

### 5.2 Active Session

```
Key:    memora:practice:v2:session:{player_id}
Value:  Hash
Fields:
  - session_id         UUID (unique per session)
  - subject_id         Subject being practiced
  - track_ids          JSON array of selected track IDs
  - unit_ids           JSON array of unit filter (or "")
  - topic_ids          JSON array of topic filter (or "")
  - scope_hash         MD5 hash of full scope for validation
  - batch_seq          Current batch number (0-indexed)
  - current_batch      JSON array of question UUIDs in current batch
  - submitted          "0" or "1" — whether current batch has been submitted
  - batch_stats        JSON — cached stats for last submitted batch (duplicate detection)
  - served_ids         JSON array of all question IDs served in session (repeat avoidance)
  - chunk_refs         JSON array of chunk IDs for current batch
  - created_at         ISO 8601 timestamp
  - last_activity_at   ISO 8601 timestamp (refreshed on submit/continue)
TTL:    1 hour after last activity (refreshed on submit/continue)
Scan:   memora:practice:v2:session:*
```

### 5.3 Rate Limit Counter

```
Key:    memora:practice:rate:{player_id}:sessions
Value:  Integer counter
TTL:    1 hour (auto-expires)
Max:    5 sessions per hour
```

### 5.4 Write Queue (Redis Stream)

```
Key:              memora:practice:write_queue
Type:             Stream
Consumer Group:   practice-writers
Consumer Name:    writer-{pid}
Max Stream Len:   ~100,000 (MAXLEN ~, auto-trim on XADD)
Visibility Timeout: 60 seconds (XAUTOCLAIM)
Max Retries:      5 (then dead-lettered)
Message Fields:
  - player_id      Player identifier
  - track_id       Track identifier
  - subject_id     Subject (denormalized for logging)
  - submitted_at   ISO 8601 timestamp (immutable — used for idempotency)
  - batch_seq      Batch sequence number
  - session_id     Session UUID for tracing
  - results        JSON array: [{"item_id": "uuid", "is_correct": bool}, ...]
```

### 5.5 Dead Letter Stream

```
Key:    memora:practice:write_queue:dead
Type:   Stream
Fields: All original fields + original_id, error, delivery_count
```

### 5.6 Map Invalidation (Pubsub)

```
Channel:  memora:practice:map_invalidation
Message:  subject_id string (or "*" for all)
Purpose:  FastAPI workers evict in-process map cache on content changes
```

### 5.7 Content Debounce

```
Key:    memora:practice:content:pending:{subject_id}
Value:  Timestamp
TTL:    10 seconds (SET NX EX)
Purpose: Batch rapid content edits into a single regeneration
```

---

## 6. Runtime Flow

### 6.1 Start Session

```
Client                    FastAPI                   Redis              DB
  │                          │                        │                 │
  │  POST /v2/practice/start │                        │                 │
  │  {subject_id, track_ids, │                        │                 │
  │   unit_ids?, topic_ids?} │                        │                 │
  │ ─────────────────────►   │                        │                 │
  │                          │                        │                 │
  │                          │  Check rate limit      │                 │
  │                          │ ──────────────────►    │                 │
  │                          │  ◄────────────────     │                 │
  │                          │                        │                 │
  │                          │  Load player summaries │                 │
  │                          │  for selected tracks   │                 │
  │                          │ ──────────────────►    │                 │
  │                          │                        │                 │
  │                          │         ┌──────────────┤                 │
  │                          │         │ Cache hit?    │                 │
  │                          │         │ YES: return   │                 │
  │                          │         │ NO: ─────────────────────────► │
  │                          │         │              read 1 row/track  │
  │                          │         │ ◄─────────────────────────────│
  │                          │         │ cache it     │                 │
  │                          │  ◄──────┘              │                 │
  │                          │                        │                 │
  │                          │  Select question IDs:                    │
  │                          │  1. Map file already                     │
  │                          │     known by client                      │
  │                          │  2. Filter by scope                      │
  │                          │     (tracks/units/topics)                │
  │                          │  3. Exclude seen (from                   │
  │                          │     player summary)                      │
  │                          │  4. Sort by priority:                    │
  │                          │     a. Never seen                        │
  │                          │     b. Last result = I                   │
  │                          │     c. Oldest last_seen                  │
  │                          │     d. Lowest correct %                  │
  │                          │  5. Pick top 20                          │
  │                          │                        │                 │
  │                          │  Create session        │                 │
  │                          │ ──────────────────►    │                 │
  │                          │                        │                 │
  │  ◄─────────────────────  │                        │                 │
  │  {session_active,        │                        │                 │
  │   question_ids[],        │                        │                 │
  │   chunk_refs[],          │                        │                 │
  │   total_available}       │                        │                 │
  │                          │                        │                 │
  │  Client loads chunks     │                        │                 │
  │  from CDN directly       │                        │                 │
  │  ◄──── CDN ────►         │                        │                 │
```

**Important:** The server returns question IDs and which chunks they belong to. The client fetches the actual question content from CDN. The server never reads question content.

### 6.2 Submit Answers

```
Client                    FastAPI                   Redis           Stream
  │                          │                        │               │
  │  POST /v2/practice/submit│                        │               │
  │  {batch_seq,             │                        │               │
  │   results: [             │                        │               │
  │    {item_id, is_correct} │                        │               │
  │   ]}                     │                        │               │
  │ ─────────────────────►   │                        │               │
  │                          │                        │               │
  │                          │  Read session hash     │               │
  │                          │ ──────────────────►    │               │
  │                          │  ◄────────────────     │               │
  │                          │                        │               │
  │                          │  Check submitted=="1"  │               │
  │                          │  (duplicate detection) │               │
  │                          │  If duplicate: return  │               │
  │                          │  cached batch_stats    │               │
  │                          │                        │               │
  │                          │  Validate: batch_seq,  │               │
  │                          │  item_ids, no dupes    │               │
  │                          │                        │               │
  │                          │  Update player summary │               │
  │                          │  in Redis cache        │               │
  │                          │ ──────────────────►    │               │
  │                          │                        │               │
  │                          │  XADD write queue      │               │
  │                          │  (MAXLEN ~100000)      │               │
  │                          │ ──────────────────────────────────►    │
  │                          │                        │               │
  │                          │  HSET session:         │               │
  │                          │  submitted=1,          │               │
  │                          │  batch_stats=JSON      │               │
  │                          │  + refresh TTL         │               │
  │                          │ ──────────────────►    │               │
  │                          │                        │               │
  │  ◄─────────────────────  │                        │               │
  │  {accepted, batch_seq,   │                        │               │
  │   correct_count,         │                        │               │
  │   total_count,           │                        │               │
  │   accuracy_percent,      │                        │               │
  │   is_duplicate}          │                        │               │
  │                          │                        │               │
```

### 6.3 Continue (Next Batch)

Same as Start Session selection logic, but player summary in Redis is already updated with the latest answers — so repeat avoidance is automatic.

### 6.4 Background Write Worker (Redis Streams)

```
Stream                    Worker (consumer group)   DB
  │                          │                        │
  │  XREADGROUP GROUP        │                        │
  │  practice-writers        │                        │
  │  COUNT 10 BLOCK 5000     │                        │
  │ ─────────────────────►   │                        │
  │                          │                        │
  │                          │  For each result:      │
  │                          │  UPSERT Practice Log   │
  │                          │  (timestamp guard:     │
  │                          │   IF new > existing)   │
  │                          │ ──────────────────►    │
  │                          │                        │
  │                          │  UPDATE Player Summary │
  │                          │  (merge question_      │
  │                          │   history JSON with    │
  │                          │   same timestamp guard)│
  │                          │ ──────────────────►    │
  │                          │                        │
  │  XACK (success)          │                        │
  │  ◄─────────────────────  │                        │
  │                          │                        │
  │  On failure: stays in    │                        │
  │  PEL → XAUTOCLAIM after  │                        │
  │  60s → retry with        │                        │
  │  exponential backoff     │                        │
  │  (2s → 4s → 8s → 16s    │                        │
  │  → 32s max)              │                        │
  │                          │                        │
  │  After 5 failures:       │                        │
  │  XADD to dead-letter     │                        │
  │  stream + XACK original  │                        │
```

**Scheduling:** Frappe scheduler calls `process_write_queue` every minute + `reclaim_stale_messages` in the same cycle. Each cycle processes up to 10 messages.

---

## 7. Question Selection Algorithm

```python
def select_questions(map_file, scope, player_summaries, batch_size=20):
    """
    All in-memory — no database queries.
    
    map_file:          loaded from CDN (cached on server if needed)
    scope:             {track_ids, unit_ids?, topic_ids?}
    player_summaries:  dict of {question_id: {lr, ac, cc, ls}} from Redis
    """
    
    # 1. Collect all question IDs matching the scope
    candidates = filter_by_scope(map_file, scope)
    
    # 2. Classify and score each candidate
    scored = []
    for q_id in candidates:
        history = player_summaries.get(q_id)
        
        if history is None:
            # Never seen — highest priority
            priority = 0
            sort_key = (0, "0000-00-00", q_id)
        else:
            priority = 1
            # Sub-sort: incorrect > correct, oldest > newest
            result_score = 0 if history["lr"] == "I" else 1
            correct_ratio = history["cc"] / max(history["ac"], 1)
            sort_key = (1, result_score, correct_ratio, history["ls"], q_id)
        
        scored.append((sort_key, q_id))
    
    # 3. Sort and pick top N
    scored.sort(key=lambda x: x[0])
    selected = [q_id for _, q_id in scored[:batch_size]]
    
    # 4. Determine which chunks the client needs
    chunk_refs = deduplicate([map_file.get_chunk(q) for q in selected])
    
    return selected, chunk_refs
```

---

## 8. Session Lifecycle & Cleanup

```
Session Created (POST /v2/practice/start)
    │
    │  TTL = 1 hour from last activity
    │
    ├── Player submits batch (POST /v2/practice/submit)
    │       │
    │       ├── Stats returned immediately
    │       ├── Player summary updated in Redis cache
    │       ├── Results enqueued to write queue (XADD)
    │       └── TTL refreshed
    │
    ├── Player requests next batch (POST /v2/practice/continue)
    │       │
    │       ├── New batch selected using updated summary
    │       ├── served_ids extended (no in-session repeats)
    │       └── TTL refreshed
    │
    ├── Player idle > 1 hour
    │       │
    │       ▼
    │   Redis expires the session key.
    │   No data loss — results were pushed to write queue
    │   on submit. Unsubmitted batches are discarded.
    │
    └── Player starts new session
            │
            ▼
        Old session DELETED and replaced (one session per player)
```

**Player Summary TTL:** 2 hours. Outlives the session so that back-to-back sessions don't re-read from DB.

**Safety net:** Hourly `cleanup_orphaned_sessions` task scans for session keys with TTL == -1 (no expiry) and deletes them.

---

## 9. Handling Content Updates

```
Content team edits/adds/deletes a Review Item
        │
        ▼
Frappe doc_events fire (on_update / after_insert / on_trash)
        │
        ▼
practice_content_trigger.py handler
        │
        ▼
Redis debounce (SET NX EX 10s per subject)
        │
        ├── Key already exists → skip (regeneration pending)
        │
        └── Key created → enqueue background job:
                │
                ▼
        generate_practice_content(subject_id):
                │
                ├── Fetch full hierarchy (Tracks → Units → Topics → Review Items)
                ├── Build map file + content chunks (~100 questions/chunk)
                ├── Atomic upload to CDN (temp → swap → cleanup)
                ├── Purge CDN cache for affected files
                └── Publish to Redis pubsub: memora:practice:map_invalidation
                        │
                        ▼
                FastAPI workers evict in-process map cache
                        │
                        ▼
                New sessions use updated content (< 60 seconds)

Active sessions:
        │
        ├── Client requests a deleted question
        │       └── Question not found in chunk → skip it
        │
        └── Client requests a modified question
                └── Shows latest version (new chunk already on CDN)

No versioning. No multi-version management.
Single source of truth on CDN — always the latest.
```

---

## 10. Repeat Avoidance (V2 vs V1 Comparison)

| Aspect              | V1 (Current)                                | V2 (New)                                     |
|---------------------|---------------------------------------------|----------------------------------------------|
| **Mechanism**       | SQL: `last_seen_at >= session_started_at`    | In-memory: question ID exists in summary     |
| **Depends on**      | Clock sync between servers                  | Nothing — pure ID comparison                 |
| **Fragility**       | Clock skew breaks it                         | Cannot break                                 |
| **Performance**     | JOIN query per batch                         | Dictionary lookup in memory                  |
| **Wrap-around**     | Clears exclusion, resets to all items        | Same — when all seen, start from oldest      |

---

## 11. Failure Scenarios

| Failure                    | Impact                                                  | Recovery                                              |
|----------------------------|---------------------------------------------------------|-------------------------------------------------------|
| **CDN down**               | Clients can't load questions                            | CDN has multi-region redundancy; near-zero downtime    |
| **Redis down**             | Can't start new sessions or submit answers              | Sessions lost; players restart; summaries re-read from DB |
| **Redis flush**            | Active sessions lost                                    | Players lose at most 1 session (20 questions)          |
| **Frappe down**            | Active sessions unaffected; new players without cached summary can't start | Queue holds results; when Frappe returns, everything writes |
| **Write queue backlog**    | Results delayed in DB; no player-facing impact           | Worker catches up; Practice Log updated eventually     |
| **Worker crash**           | Queue accumulates                                        | Worker restarts; processes backlog                     |

---

## 12. Data Flow Summary

```
READ PATH (player-facing, must be fast):

    CDN ──► Client (map + chunks)
    Redis ──► FastAPI (player summary, session)
    In-memory ──► FastAPI (question selection)

    Database involvement: ZERO (except cold-start cache miss)

WRITE PATH (background, can be slow):

    FastAPI ──► Queue ──► Worker ──► DB (Practice Log + Player Summary)

    Player never waits for DB writes.
```

---

## 13. Migration Strategy

### Phase 1: Build Player Summary Table
- Create `tabPlayer Practice Summary`
- Backfill from existing `tabMemora Practice Log` (one-time migration script)
- Each `(player_id, track_id)` gets one row with aggregated history

### Phase 2: Build CDN Content Pipeline
- Create map file generator
- Create chunk generator
- Set up CDN storage and invalidation
- Hook into Frappe content publish events

### Phase 3: Build New FastAPI Endpoints
- New `/v2/practice/start` — reads from Redis + map file
- New `/v2/practice/submit` — writes to queue + updates Redis
- New `/v2/practice/continue` — same selection logic, no DB
- Write queue worker for background DB writes

### Phase 4: Client Migration
- Update client to fetch map + chunks from CDN
- Update client to render questions locally
- Update client to handle missing/deleted questions gracefully
- Update client to call V2 endpoints

### Phase 5: Deprecate V1
- Monitor V2 stability
- Remove V1 endpoints
- Remove old Redis session schema

---

## 14. Key Files

```
# FastAPI sidecar (gameplay endpoints, services, models)
fastapi_app/
├── api/v2/endpoints/practice.py        # V2 HTTP entrypoints (start, submit, continue, session)
├── services/
│   ├── practice_v2.py                  # Session logic: selection algorithm, Redis summary,
│   │                                   #   rate limiting, submit/continue flow
│   ├── practice_map.py                 # Map file loader + in-process cache + pubsub invalidation
│   └── practice_writer.py             # Background write worker (Redis Streams consumer)
├── models/practice_v2.py              # Pydantic request/response models
└── core/redis_keys.py                 # V2 key patterns (summary, session, rate, queue, pubsub)

# Frappe admin backend (content pipeline, scheduler, admin)
memora_admin/memora_admin/
├── setup.py                           # Table creation: tabPlayer Practice Summary
├── services/build/
│   └── practice_content.py            # Map file + chunk generator + CDN publish
├── tasks/
│   └── practice_writer.py             # Frappe scheduler tasks: write queue + session cleanup
├── events/
│   └── practice_content_trigger.py    # Hooks for Review Item changes → debounced regeneration
└── api/
    └── practice_summary.py            # Backfill script + admin utilities (force-expire, dead-letter)

# Frappe hooks
memora_admin/hooks.py
└── doc_events: Memora Review Item (after_insert, on_update, on_trash)
    scheduler_events: process_write_queue (*/min), cleanup_orphaned_sessions (hourly)
```

---

## 15. Existing Table (Unchanged)

```
tabMemora Practice Log
├── PRIMARY KEY (player_id, item_id)
├── player_id            VARCHAR(140)
├── item_id              VARCHAR(36)
├── first_seen_at        DATETIME
├── last_seen_at         DATETIME
├── last_result          ENUM('Correct', 'Incorrect')
├── attempt_count        INT UNSIGNED
├── correct_count        INT UNSIGNED
├── idx_item_id
└── idx_player_seen_item (player_id, last_seen_at, item_id)

Purpose: Full history for reports and analytics.
No longer queried during gameplay.
```