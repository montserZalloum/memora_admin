# Research: Practice Arena V2

**Feature Branch**: `049-practice-arena-v2`
**Date**: 2026-03-14

---

## R1. Write Queue Technology

### Decision: Redis Streams

### Rationale

Redis is already the shared state layer between Frappe and FastAPI. Redis Streams provide:
- **Consumer groups**: Multiple worker instances can process messages in parallel with automatic load balancing
- **At-least-once delivery**: Messages are not removed until explicitly acknowledged (XACK)
- **Visibility timeout**: XCLAIM allows reclaiming unacknowledged messages after a timeout (dead consumer recovery)
- **Persistence**: Survives Redis restarts (AOF/RDB) — unlike Redis Lists which lose unprocessed items
- **Dead-letter**: Unprocessable messages (>5 retries) can be moved to a separate stream
- **Backpressure visibility**: XLEN gives instant queue depth for monitoring

### Alternatives Considered

| Alternative | Why Rejected |
|---|---|
| **RabbitMQ** | Adds new infrastructure dependency; operational overhead for a single queue use case; the team has no RabbitMQ expertise |
| **Celery** | Frappe uses RQ internally; adding Celery creates confusion between two task frameworks; Celery is overkill for ordered message processing |
| **Frappe Background Jobs (RQ)** | No consumer groups, no dead-letter, no reliable ordering; RQ jobs are fire-and-forget with limited retry semantics |
| **Redis Lists (LPUSH/BRPOP)** | No consumer groups (only one consumer); no visibility timeout (message lost if consumer crashes mid-processing); no built-in retry |
| **Redis dirty sets** | Unordered; no message-level tracking; no retry/dead-letter; suitable for periodic flush but not for ordered event processing |

### Implementation Notes

- Stream key: `memora:practice:write_queue`
- Consumer group: `practice-writers`
- Consumer name: `writer-{instance_id}` (one per FastAPI/Frappe worker)
- Max stream length: MAXLEN ~100,000 (auto-trim old acknowledged messages)
- Dead-letter stream: `memora:practice:write_queue:dead`
- Visibility timeout: 60 seconds (XCLAIM pending messages older than 60s)
- Max retries: 5 (after which message moves to dead-letter stream)

---

## R2. Map File Server-Side Loading

### Decision: Server loads and caches map file in-memory

### Rationale

The FastAPI server needs the map file to run the question selection algorithm (filter by scope, resolve chunk references). The map file is ~300-500KB per subject and changes infrequently (only on content edits). In-memory caching with pubsub invalidation follows the same pattern as the existing hierarchy cache.

### Alternatives Considered

| Alternative | Why Rejected |
|---|---|
| **Client sends map data in request** | Adds ~500KB to every request payload; creates trust issues (client could manipulate map data); violates thin-client principle |
| **Server queries DB for question IDs** | Violates the zero-DB-queries goal; returns to V1 performance problems |
| **Server fetches map from CDN on every request** | Adds CDN latency to every request; unnecessary when map data changes rarely |

### Implementation Notes

- Cache storage: Local process-level dict (per uvicorn worker), same as hierarchy cache
- Cache key: `{subject_id}` → parsed map data
- Invalidation: Redis pubsub channel `memora:practice:map_invalidation`
- TTL: 1 hour (safety net; pubsub handles real-time invalidation)
- Cold-start: First request for a subject reads map from CDN/local storage and caches it
- Memory estimate: 10 subjects × 500KB = ~5MB per worker — well within limits

---

## R3. CDN Provider & Storage

### Decision: Cloudflare R2 (existing infrastructure) with local storage backend for development

### Rationale

The project already uses Cloudflare R2 for content delivery (lesson JSON, challenge questions). The storage abstraction layer (`StorageBackend` ABC) supports multiple backends. Practice Arena content files follow the same upload/invalidation pattern.

### Alternatives Considered

| Alternative | Why Rejected |
|---|---|
| **AWS CloudFront + S3** | Would require new AWS infrastructure; no existing integration; additional cost |
| **Bunny CDN** | No existing integration; would need new storage abstraction implementation |
| **Nginx direct-serve** | No CDN edge caching; all traffic hits origin server; no multi-region redundancy |

### Implementation Notes

- Practice content files stored alongside existing content:
  - Map files: `practice/maps/{subject_id}.json`
  - Chunks: `practice/chunks/{subject_id}/chunk_{N}.json`
- Uses existing `publisher.py` atomic upload pattern (temp → swap → cleanup)
- Uses existing `CloudflarePurgeService` for cache invalidation
- Local storage backend for development (same as existing lesson/challenge files)

---

## R4. Content Chunk Strategy

### Decision: Topic-based grouping, ~100 questions per chunk, stable chunk IDs

### Rationale

Topics are the natural grouping unit in the content hierarchy. Students typically practice within a topic, so topic-based chunks maximize cache hits. Stable chunk IDs prevent unnecessary CDN invalidation when unrelated content changes.

### Chunk Assignment Algorithm

```
For each subject:
  chunk_id = 0
  current_chunk = []

  For each track (sorted by sort_order):
    For each unit (sorted by sort_order):
      For each topic (sorted by sort_order):
        questions = get_review_items(topic)

        If current_chunk.count + questions.count > 100 AND current_chunk is not empty:
          write_chunk(chunk_id, current_chunk)
          chunk_id += 1
          current_chunk = []

        current_chunk.extend(questions)
        # Record chunk_id for each question in map file

  If current_chunk is not empty:
    write_chunk(chunk_id, current_chunk)
```

### Alternatives Considered

| Alternative | Why Rejected |
|---|---|
| **Random distribution** | Breaks locality; a topic-scoped session would need to load many chunks |
| **Unit-based chunks** | Units can have 500+ questions; chunks would be too large (>500KB) |
| **Lesson-based chunks** | Too many small files; CDN request overhead; each lesson has only 5-20 questions |
| **Fixed-size chunks ignoring topic boundaries** | A single question edit could cascade chunk ID changes across the entire subject |

### Selective Regeneration

When a Review Item changes:
1. Identify the topic of the changed item
2. Find which chunk(s) contain questions from that topic
3. Regenerate only those chunk(s)
4. Regenerate the map file (lightweight — only IDs + chunk refs)
5. Invalidate CDN cache for affected chunk file(s) + map file

---

## R5. Idempotency Strategy for Background Writer

### Decision: Timestamp-based deduplication with conditional updates

### Rationale

The write queue uses at-least-once delivery, meaning duplicate messages are possible. The worker must handle duplicates without corrupting data (double-counting attempts or correct answers).

### Strategy

```python
# For each result in the message:
def process_result(player_id, item_id, is_correct, submitted_at):
    # 1. Practice Log UPSERT — naturally idempotent for lr/ls
    #    Conditional increment: only if submitted_at > current last_seen_at
    sql = """
        INSERT INTO `tabMemora Practice Log`
            (player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
        VALUES (%s, %s, %s, %s, %s, 1, %s)
        ON DUPLICATE KEY UPDATE
            last_seen_at = IF(VALUES(last_seen_at) > last_seen_at, VALUES(last_seen_at), last_seen_at),
            last_result = IF(VALUES(last_seen_at) > last_seen_at, VALUES(last_result), last_result),
            attempt_count = IF(VALUES(last_seen_at) > last_seen_at, attempt_count + 1, attempt_count),
            correct_count = IF(VALUES(last_seen_at) > last_seen_at, correct_count + VALUES(correct_count), correct_count)
    """

    # 2. Player Summary — merge into JSON with same timestamp guard
    #    Read current question_history, check ls for each item_id
    #    Only update if submitted_at > existing ls
```

### Why This Works

- `submitted_at` is set once when the player submits (immutable per message)
- If the same message is processed twice, `submitted_at` will NOT be greater than `last_seen_at` (which was set to `submitted_at` on first processing)
- Therefore, `attempt_count` and `correct_count` are not double-incremented
- This is the same pattern used by the existing `upsert_practice_results` in V1

### Alternatives Considered

| Alternative | Why Rejected |
|---|---|
| **Message dedup ID** | Redis Streams don't have native dedup; would need a separate dedup store |
| **Exactly-once processing** | Not achievable with Redis Streams without external transaction coordinator |
| **Version column on Practice Log** | Adds schema modification to a table we're not allowed to change (C-001) |

---

## R6. Session Expiry and Cleanup

### Decision: Scheduled cleanup task (hourly) + Redis key TTL

### Rationale

Redis TTL handles automatic session expiry. A scheduled cleanup task (already proven pattern for game sessions) acts as a safety net for edge cases.

### Mechanism

1. **Primary**: Redis key TTL (1 hour) — session keys auto-expire on inactivity
2. **TTL refresh**: Every submit/continue operation refreshes the session TTL
3. **Safety net**: Hourly scheduled task scans for orphaned sessions (same pattern as `cleanup_expired_sessions`)
4. **Pending results on expiry**: Since results are pushed to write queue on submit, there are no pending results to flush. Unsubmitted batches are simply discarded (per spec FR-009)

### Alternatives Considered

| Alternative | Why Rejected |
|---|---|
| **Redis keyspace notifications** | Unreliable under load; can miss events; requires `notify-keyspace-events` config; not all Redis setups enable this |
| **Client-side heartbeat** | Adds complexity to client; still needs server-side TTL as fallback |
| **No cleanup** | TTL handles it; but safety net is good practice (consistent with existing patterns) |

---

## R7. V1/V2 Coexistence Strategy

### Decision: API versioning with `/api/v2/practice/*` prefix + client-side feature flag

### Rationale

Clean separation between V1 and V2 endpoints. Both share the same Redis and DB infrastructure. The client determines which version to call based on a feature flag. No server-side routing or A/B testing complexity.

### Coexistence Rules

1. V1 endpoints remain at `/api/v1/practice/*` — unchanged
2. V2 endpoints at `/api/v2/practice/*` — new code
3. V1 writes to Practice Log synchronously (existing behavior)
4. V2 writes to Practice Log via write queue (new behavior)
5. Both write to the same Practice Log table — data is compatible
6. Player Summary table is only read/written by V2 code
7. V1 deprecation happens after V2 reaches 100% traffic

### Migration Safety

- V2 sessions and V1 sessions are independent (different Redis key patterns)
- A player switching from V1 to V2 mid-session starts a new V2 session (old V1 session expires)
- Practice Log data written by V1 is visible to V2 (via backfilled Player Summary)
- Practice Log data written by V2 worker follows the same UPSERT pattern as V1

---

## R8. Map File vs Existing Build Pipeline

### Decision: Separate practice content pipeline alongside existing build pipeline

### Rationale

The existing build pipeline generates lesson-centric content (hierarchy JSON, unit content, lesson stages) for the main learning flow. Practice Arena needs a different structure: question-centric map files and chunks organized for batch selection. These are separate concerns with different triggers and formats.

### Key Differences

| Aspect | Existing Build Pipeline | Practice Content Pipeline |
|---|---|---|
| Trigger | Lesson/Subject/Plan changes | Review Item changes |
| Output | Hierarchy + unit + lesson JSON | Map file + question chunks |
| Scope | Per-plan (includes plan overrides) | Per-subject (no plan overrides) |
| Content | Full lesson stages with configs | Question stem, choices, correct answer |
| Consumer | Client (lesson playback) | Client (practice question rendering) + Server (question selection) |

### Shared Infrastructure

- Same `StorageBackend` abstraction for file upload
- Same `CloudflarePurgeService` for CDN invalidation
- Same `Memora Build Queue` DocType for job tracking (with `target_type = "Practice Content"`)
- Same debounce pattern via Redis SET NX EX

---

## R9. Player Practice Summary Backfill

### Decision: One-time batch migration script with progress tracking

### Rationale

Existing Practice Log data must be aggregated into the new Player Summary table so that returning players see their full history when V2 launches.

### Backfill Algorithm

```python
def backfill_player_summaries(batch_size=1000):
    """
    Read from tabMemora Practice Log, aggregate by (player_id, track_id),
    write to tabPlayer Practice Summary.
    """
    # 1. Get distinct player_ids from Practice Log
    # 2. For each player (in batches of 1000):
    #    a. JOIN Practice Log with Review Item to get track_id per item
    #    b. Group by track_id
    #    c. Build question_history JSON
    #    d. UPSERT into Player Summary
    # 3. Track progress in a marker table or log
```

### Estimated Duration

- ~500M Practice Log rows
- With batch processing (1000 players per batch): ~2-4 hours
- Can run off-peak without affecting production
- Idempotent (UPSERT) — safe to re-run

### Risk Mitigation

- Run on staging first, validate counts match
- Use `ON DUPLICATE KEY UPDATE` so re-runs are safe
- Progress tracking allows resume on failure
- V2 endpoints check for empty summary and fall back to "new player" behavior

---

## R10. Force-Expire Sessions (Admin Utility)

### Decision: Admin endpoint to flush all practice sessions

### Rationale

Operations engineers need the ability to force-expire all sessions during maintenance windows or emergency situations.

### Implementation

- Frappe whitelisted function: `force_expire_all_practice_sessions()`
- Uses `SCAN` with pattern `memora:practice:session:*` to find all session keys
- Deletes in batches of 100 to avoid blocking Redis
- Logs the count of expired sessions
- No data loss — unsubmitted results are simply discarded (same as natural expiry)
