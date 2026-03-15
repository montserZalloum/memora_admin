# Write Queue Contract (Redis Streams)

**Feature Branch**: `049-practice-arena-v2`
**Date**: 2026-03-14

---

## Stream Configuration

| Property | Value |
|---|---|
| Stream key | `memora:practice:write_queue` |
| Consumer group | `practice-writers` |
| Dead-letter stream | `memora:practice:write_queue:dead` |
| Max stream length | ~100,000 entries (MAXLEN ~) |
| Visibility timeout | 60 seconds |
| Max retries | 5 |

---

## Message Schema

### Fields (XADD)

```
XADD memora:practice:write_queue MAXLEN ~ 100000 *
  player_id   "PLR-00001"
  track_id    "TRACK-A"
  subject_id  "SUBJ-001"
  submitted_at "2026-03-14T12:00:00Z"
  batch_seq   "0"
  session_id  "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  results     '[{"item_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","is_correct":true},{"item_id":"ffffffff-gggg-hhhh-iiii-jjjjjjjjjjjj","is_correct":false}]'
```

| Field | Type | Description |
|---|---|---|
| `player_id` | string | Player identifier |
| `track_id` | string | Track for this batch |
| `subject_id` | string | Subject (denormalized for logging) |
| `submitted_at` | string (ISO 8601) | Submission timestamp — immutable, used for idempotency |
| `batch_seq` | string (integer) | Batch sequence number (all Redis Stream values are strings) |
| `session_id` | string (UUID) | Session UUID for distributed tracing |
| `results` | string (JSON array) | Array of `{"item_id": "uuid", "is_correct": bool}` |

---

## Consumer Protocol

### 1. Create Consumer Group (on startup)

```
XGROUP CREATE memora:practice:write_queue practice-writers 0 MKSTREAM
```

### 2. Read Messages

```
XREADGROUP GROUP practice-writers writer-{instance_id}
  COUNT 10
  BLOCK 5000
  STREAMS memora:practice:write_queue >
```

- `COUNT 10`: Process up to 10 messages per read
- `BLOCK 5000`: Wait up to 5 seconds for new messages
- `>`: Only undelivered messages

### 3. Process Message

For each message:
1. Parse `results` JSON
2. For each result item:
   a. UPSERT into `tabMemora Practice Log` (conditional on `submitted_at > last_seen_at`)
   b. Update `tabPlayer Practice Summary` question_history JSON
3. Update `total_seen` and `total_correct` counters

### 4. Acknowledge

```
XACK memora:practice:write_queue practice-writers {message_id}
```

### 5. Handle Failures

On processing error:
1. Log the error with message_id and attempt count
2. Do NOT XACK — message remains in PEL (Pending Entries List)
3. Claim stale messages:

```
XAUTOCLAIM memora:practice:write_queue practice-writers writer-{instance_id}
  60000   # 60 second visibility timeout
  0-0     # Start from beginning of PEL
  COUNT 10
```

4. Check delivery count from XPENDING:
   - If `delivery_count >= 5`: Move to dead-letter and XACK original

```
# Move to dead-letter
XADD memora:practice:write_queue:dead *
  original_id {message_id}
  error {error_message}
  delivery_count "5"
  player_id {player_id}
  ...all original fields...

# Acknowledge original
XACK memora:practice:write_queue practice-writers {message_id}
```

---

## Idempotency Contract

The worker MUST handle duplicate message delivery safely:

### Practice Log UPSERT

```sql
INSERT INTO `tabMemora Practice Log`
    (player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
VALUES (%s, %s, %s, %s, %s, 1, %s)
ON DUPLICATE KEY UPDATE
    last_seen_at = IF(VALUES(last_seen_at) > last_seen_at, VALUES(last_seen_at), last_seen_at),
    last_result = IF(VALUES(last_seen_at) > last_seen_at, VALUES(last_result), last_result),
    attempt_count = IF(VALUES(last_seen_at) > last_seen_at, attempt_count + 1, attempt_count),
    correct_count = IF(VALUES(last_seen_at) > last_seen_at, correct_count + VALUES(correct_count), correct_count)
```

**Key**: The `IF(VALUES(last_seen_at) > last_seen_at, ...)` guard prevents double-counting. If the same message is processed twice, `submitted_at` (used as `last_seen_at` value) will NOT be greater than the already-written `last_seen_at`, so `attempt_count` and `correct_count` remain unchanged.

### Player Summary Update

```python
# Read current question_history
history = json.loads(row["question_history"])

for result in results:
    item_id = result["item_id"]
    existing = history.get(item_id)

    if existing and existing["ls"] >= submitted_at:
        continue  # Already processed — skip (idempotency guard)

    history[item_id] = {
        "lr": "C" if result["is_correct"] else "I",
        "ac": (existing["ac"] + 1) if existing else 1,
        "cc": (existing["cc"] + (1 if result["is_correct"] else 0)) if existing else (1 if result["is_correct"] else 0),
        "ls": submitted_at,
    }

# Write back
UPDATE `tabPlayer Practice Summary`
SET question_history = %s, total_seen = %s, total_correct = %s, last_session_at = %s
WHERE player_id = %s AND track_id = %s
```

---

## Monitoring

| Metric | Command | Alert Threshold |
|---|---|---|
| Queue depth | `XLEN memora:practice:write_queue` | > 1,000 |
| Pending messages | `XPENDING memora:practice:write_queue practice-writers` | > 100 |
| Dead-letter count | `XLEN memora:practice:write_queue:dead` | > 0 (any dead-letter triggers alert) |
| Consumer lag | `XINFO GROUPS memora:practice:write_queue` → `lag` | > 500 |
| Processing rate | Application metric (messages/second) | < 10/s sustained |

---

## Stream Maintenance

### Trim Old Entries

The `MAXLEN ~ 100000` on XADD auto-trims. For explicit maintenance:

```
XTRIM memora:practice:write_queue MAXLEN ~ 100000
```

### Dead-Letter Review

Dead-letter messages should be reviewed and resolved manually:

```
# List dead-letter messages
XRANGE memora:practice:write_queue:dead - + COUNT 10

# After resolution, trim
XTRIM memora:practice:write_queue:dead MAXLEN 0
```
