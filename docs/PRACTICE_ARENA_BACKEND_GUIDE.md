# Practice Arena Backend Guide

## Purpose

Practice Arena is the student-facing "free practice" flow that lets a player:

- browse a subject hierarchy,
- choose a scope (track, unit, topic),
- receive batched questions,
- submit answers,
- continue through more batches,
- do all of that without touching the normal FSRS review flow, streaks, XP, wallet, or leaderboards.

This document describes the current backend implementation in this repository, including:

- request flow,
- data flow,
- Redis session model,
- question selection logic,
- access rules,
- error handling,
- legacy rollout behavior,
- edge cases already handled in code.

This is an implementation guide, not a product PRD.

---

## Where The Logic Lives

### FastAPI layer

- `fastapi_app/api/v1/endpoints/practice.py`
- `fastapi_app/services/practice.py`
- `fastapi_app/models/practice.py`

### Frappe bridge

- `memora_admin/api/practice.py`

### Tests

- `fastapi_app/tests/test_practice.py`

### Related specs

- `specs/025-practice-arena/`
- `specs/035-practice-arena/`

---

## High-Level Architecture

Practice Arena is split across two runtime layers:

1. FastAPI handles authentication, request validation, Redis session state, flow control, and response shaping.
2. Frappe exposes whitelisted methods that FastAPI calls for:
   - hierarchy metadata,
   - read-only SQL over `tabMemora Review Item` and `tabMemora Practice Log`,
   - Practice Log upserts.

### Core data dependencies

- Redis:
  - active session state,
  - cached hierarchy metadata.
- `tabMemora Review Item`:
  - source of questions and topic-level availability counts.
- `tabMemora Practice Log`:
  - per-player practice history and prioritization source.
- cached `SubjectHierarchy`:
  - structure, free-content markers, lesson mapping, completed-bit mapping.

---

## API Surface

### 1. `GET /api/v1/practice/hierarchy`

Purpose:

- Returns a browsable subject tree with item counts and access visibility.

Request:

- Query params:
  - `subject_id` (required)
  - `filter=all|completed` (default `all`)

Response model:

- `PracticeHierarchyResponse`

Primary failure:

- `404 SUBJECT_NOT_FOUND`

### 2. `POST /api/v1/practice/start`

Purpose:

- Starts a new canonical session and returns batch 0.

Request body:

- `StartPracticeRequest`
- Fields:
  - `subject_id`
  - `filter`
  - `tracks`
  - `units`
  - `topics`

Primary failures:

- `403 {"code": "NO_ACCESS", "tracks": [...]}`
- `422 {"code": "NO_ITEMS", ...}`
- `422` validation errors for invalid selection combinations

### 3. `POST /api/v1/practice/submit`

Purpose:

- Submits results for one batch.

Request body:

- `SubmitPracticeRequest`
- Fields:
  - `batch_seq`
  - `results[]`

Primary failures:

- `404 NO_ACTIVE_SESSION`
- `409 {"code": "BATCH_SEQ_MISMATCH", ...}`
- `422 {"code": "OFF_BATCH_ITEMS", ...}`
- `409 {"code": "INVALID_SESSION_STATE", ...}`

### 4. `POST /api/v1/practice/continue`

Purpose:

- Requests the next batch from the active session.

Primary failures:

- `404 NO_ACTIVE_SESSION`
- `422 {"code": "PREVIOUS_BATCH_NOT_SUBMITTED", ...}`

---

## End-To-End Flow

## Step 1: Hierarchy Browse

Flow:

1. FastAPI authenticates the player.
2. `PracticeService.get_practice_hierarchy()` loads cached `SubjectHierarchy`.
3. The service loads flat practice metadata:
   - subject title,
   - track/unit/topic titles,
   - topic-level Review Item counts.
4. For each track:
   - compute full access from grants/plan,
   - compute whether the track contains any free content,
   - shape the visible tree.
5. If `filter=completed`:
   - decode the player's completed bits,
   - map bits back to lesson IDs,
   - prune nodes with no completed lessons.

### Important access behavior in hierarchy

- If the player has full access, the full track is shown.
- If the player has no access and the track has no free content:
  - the track is still returned,
  - `has_access=false`,
  - `units=[]`.
- If the player only has free-content access:
  - `has_access=true`,
  - only free units/topics are included,
  - paid-only nodes are filtered out of the response.

This matters because the hierarchy API is used by the client to decide what the player can actually drill into.

---

## Step 2: Start Session

Flow:

1. Endpoint validates request shape:
   - `tracks` must be non-empty.
   - multi-track requests cannot include `units` or `topics`.
   - single-track + multi-unit requests cannot include `topics`.
2. `PracticeService.start_session()` loads the subject hierarchy.
3. The service resolves `accessible_lessons`:
   - full-access tracks include all lessons,
   - otherwise only free units/topics are included,
   - `filter=completed` further reduces the lesson set,
   - inaccessible paid tracks with zero free lessons are collected into `denied_tracks`.
4. If `denied_tracks` is non-empty:
   - the request fails with `NO_ACCESS`.
5. If the resulting lesson set is empty:
   - the request fails with `NO_ITEMS`.
6. The service derives selected topic IDs from the chosen lessons.
7. `_select_questions()` returns the first batch.
8. Redis session state is overwritten (one active session per player).
9. Batch 0 is returned to the client.

### Session reset rule

Starting a new session always replaces any previous active practice session for that player.

There is no multi-session support.

---

## Step 3: Submit Batch

Flow:

1. `PracticeService.submit_batch()` loads the Redis session.
2. If no session exists:
   - return `NO_ACTIVE_SESSION`.
3. If `batch_seq` is ahead of the session's current `batch_seq`:
   - return `BATCH_SEQ_MISMATCH`.
4. If `submitted_{batch_seq}` already exists:
   - treat as duplicate,
   - return cached response for current-format sessions,
   - for legacy markers (`"1"`), recompute stats from the incoming payload.
5. Otherwise validate batch membership:
   - current-format sessions validate against `batch_{n}_item_ids`,
   - malformed current sessions missing the required key fail with `INVALID_SESSION_STATE`,
   - legacy sessions (no `schema_version` / older schema) skip per-batch validation for compatibility.
6. Validate submitted item IDs still exist in `tabMemora Review Item`.
7. Skip deleted items silently.
8. Bulk upsert the Practice Log.
9. Cache the submit result in Redis as JSON under `submitted_{batch_seq}`.
10. Return accuracy stats.

### Idempotency model

Current-format sessions:

- `submitted_{n}` stores JSON:
  - `correct_count`
  - `total_count`
  - `accuracy_percent`
- duplicate submits return the same logical response regardless of payload tampering.

Legacy sessions:

- older sessions may have `submitted_{n} = "1"`.
- in that case:
  - the system does not crash,
  - duplicate status is still respected,
  - stats are recomputed from the retry payload because the original stats were never stored.

This legacy behavior is intentionally temporary and only applies while old sessions still exist.

---

## Step 4: Continue Session

Flow:

1. Load Redis session.
2. If absent:
   - return `NO_ACTIVE_SESSION`.
3. Check that the current batch has been submitted:
   - `submitted_{current_seq}` must exist.
4. Increment `batch_seq`.
5. Reuse stored session context:
   - `accessible_lessons`
   - `selected_topics`
   - `served_item_ids`
6. `_select_questions()` builds the next batch while excluding already-served items.
7. If no questions are returned but `total_available > 0`:
   - the service wraps around,
   - it clears the served exclusion,
   - reselects from the full pool,
   - forces `all_seen_warning=true`.
8. Save:
   - new `batch_seq`
   - updated `served_item_ids`
   - `batch_{next_seq}_item_ids`
9. Return the next batch.

---

## Question Selection Logic

## Inputs

Question selection depends on:

- `player_id`
- `subject_id`
- `accessible_lessons`
- `selected_topics`
- `served_item_ids`
- configured `practice_session_size`

## Priority

Current priority model:

- `0`: item has no Practice Log row for this player (never seen)
- `1`: item has a Practice Log row (seen before)

Within seen items:

- older `last_seen_at` is served first.

Note:

- current-session repeats are not a third explicit priority tier anymore.
- they are excluded entirely during normal selection and only reappear on wrap-around.

Because a new session starts with an empty `served_item_ids` list, cross-session memory comes from `tabMemora Practice Log`: in a single-topic pool, if a student has seen 20 of 200 items, the next new session will serve the remaining 180 unseen items before those 20 seen items. In multi-topic selections, that same priority still applies within each topic, but per-topic quotas can surface a previously seen item from one topic before every unseen item in another topic has been exhausted, so the "all unseen first" guarantee is per topic, not strictly global across the whole batch.

## Topic distribution

The service counts available Review Items per topic, then computes quotas:

- each topic gets at least 1 question if it has items,
- quotas are proportional to topic size,
- quotas are capped by topic availability.

## Redistribution

Selection is two-pass:

1. First pass:
   - each topic is queried for its quota.
2. Redistribution pass:
   - if a topic returns fewer than requested because it is exhausted by `served_item_ids`,
   - the unfilled slots are reassigned to topics that still returned their full quota,
   - larger topics are tried first.

This prevents underfilled batches when one selected topic is exhausted but another still has unseen items.

## Repeat warning

`all_seen_warning` is `true` when:

- any returned question is already in Practice Log, or
- the batch is produced by wrap-around after the unseen-in-session pool is exhausted.

It is `false` only when every returned question is brand new to the player.

---

## Redis Data Model

Active session key:

- `memora:practice:{player_id}`

Type:

- Redis hash

TTL:

- `practice_session_ttl` (configurable, default intended as 3600s)

### Current session schema (version 2)

Stored fields:

- `schema_version`
- `subject_id`
- `filter`
- `tracks` (JSON array)
- `units` (JSON array)
- `topics` (JSON array)
- `batch_seq`
- `served_item_ids` (JSON array)
- `batch_0_item_ids` (JSON array)
- `batch_1_item_ids`, `batch_2_item_ids`, ... (JSON arrays)
- `accessible_lessons` (JSON array)
- `selected_topics` (JSON array)
- `created_at`
- `submitted_0`, `submitted_1`, ... (JSON payloads after submit)

### Why both `served_item_ids` and `batch_{n}_item_ids` exist

- `served_item_ids`:
  - used for dedup/exclusion across the whole active session.
- `batch_{n}_item_ids`:
  - used to validate that a submit payload belongs to the exact batch being submitted.

### Legacy compatibility

Older sessions may not contain:

- `schema_version`
- `batch_{n}_item_ids`

Older duplicate markers may also be:

- `submitted_{n} = "1"`

Compatibility rules:

- legacy duplicate markers are accepted,
- legacy sessions skip per-batch validation,
- current-format sessions must not skip validation if required state is missing.

This is a rollout bridge, not the long-term target behavior.

---

## Frappe-Side Responsibilities

FastAPI does not query MariaDB directly. It calls Frappe methods:

### `get_practice_hierarchy_meta(subject_id)`

Returns:

- subject title,
- track/unit/topic titles,
- topic-level Review Item counts.

### `execute_practice_query(sql, params)`

Used for:

- grouped topic counts,
- question selection queries,
- item existence checks.

Restriction:

- `System Manager` only.

### `execute_practice_log_upsert(sql, params)`

Used for:

- bulk upsert into `tabMemora Practice Log`.

Behavior:

- executes SQL,
- commits immediately.

---

## Error Handling Reference

## Browse

- `404 SUBJECT_NOT_FOUND`
  - subject hierarchy or metadata not found.

## Start

- `422 tracks must be non-empty`
- `422 units and topics must be empty when selecting multiple tracks`
- `422 topics must be empty when selecting multiple units`
- `403 NO_ACCESS`
  - at least one selected track is fully inaccessible and has no free lesson path.
- `422 NO_ITEMS`
  - selection resolves to zero reviewable lessons/items.

## Submit

- `404 NO_ACTIVE_SESSION`
  - session expired or was never created.
- `409 BATCH_SEQ_MISMATCH`
  - client tried to skip ahead.
- `422 OFF_BATCH_ITEMS`
  - client submitted item IDs not served in the exact batch.
- `409 INVALID_SESSION_STATE`
  - current-format session is missing required per-batch state.

## Continue

- `404 NO_ACTIVE_SESSION`
- `422 PREVIOUS_BATCH_NOT_SUBMITTED`

---

## Edge Cases Covered In Code

## Access and selection

- Paid track with no grant and no free content:
  - start fails with `NO_ACCESS`.
- Free unit/topic inside an otherwise paid track:
  - hierarchy shows only free nodes,
  - start only includes free lessons.
- Completed-only filter with no completed lessons:
  - hierarchy can return empty tracks,
  - start can return `NO_ITEMS`.
- Available items less than batch size:
  - batch is returned partially filled,
  - no padding outside the selected scope.

## Session lifecycle

- Starting a new session replaces the old one.
- Continue before submit:
  - blocked with `PREVIOUS_BATCH_NOT_SUBMITTED`.
- Session key deleted or TTL expired:
  - submit/continue return `NO_ACTIVE_SESSION`.

## Submit safety

- Duplicate submit on current-format sessions:
  - returns cached result,
  - does not double-write Practice Log.
- Duplicate submit on legacy sessions:
  - accepted,
  - stats recomputed from retry payload.
- Deleted item during active session:
  - silently skipped,
  - other items still persist.
- Database write failure during upsert:
  - exception is allowed to propagate,
  - batch is not marked submitted,
  - client can retry.
- Current-format session missing required `batch_{n}_item_ids`:
  - fail closed with `INVALID_SESSION_STATE`.
- Legacy session missing per-batch keys:
  - validation is skipped to preserve in-flight compatibility.

## Selection behavior

- Already-served items are excluded from normal next-batch selection.
- If all unseen-in-session items are exhausted:
  - the service wraps around,
  - repeat questions become allowed again,
  - `all_seen_warning=true`.
- If one topic exhausts before others:
  - redistribution fills the gap from remaining topics when possible.

---

## Known Operational Tradeoffs

These are intentional and should be understood by anyone maintaining the feature.

### 1. Legacy compatibility is temporary

Sessions without `schema_version` are treated as legacy and skip strict per-batch validation.

This is acceptable only because:

- they are in-flight sessions,
- the TTL is limited,
- the goal is safe deployment across schema evolution.

If this compatibility window is no longer needed, remove the legacy branch and enforce strict validation for all sessions.

### 2. Practice Log write path is authoritative

The submit path does not swallow DB write failures anymore.

That is correct, because:

- silent acceptance would corrupt user-visible results,
- retries must remain possible,
- the Redis "submitted" marker must reflect successful persistence only.

### 3. Session state integrity matters

Current-format sessions depend on:

- `schema_version`
- `batch_{n}_item_ids`

If those fields are missing, the system now fails closed instead of continuing with undefined behavior.

That is the correct safety posture.

---

## Observability And Log Events

The service emits structured logs for major events:

- `practice_session_started`
- `practice_access_denied`
- `practice_no_items`
- `practice_batch_duplicate`
- `practice_off_batch_items`
- `practice_items_deleted_during_session`
- `practice_batch_submitted`
- `practice_session_continued`
- `practice_session_expired`
- `practice_meta_fetch_failed`
- `practice_count_per_topic_failed`
- `practice_topic_select_failed`
- `practice_legacy_session_skip_validation`
- `practice_session_missing_batch_key`

Operationally useful checks:

- spikes in `practice_legacy_session_skip_validation`
  - should be temporary after deploy.
- any occurrence of `practice_session_missing_batch_key`
  - indicates current-session corruption or a write bug.
- repeated `practice_topic_select_failed` / `practice_count_per_topic_failed`
  - indicates Frappe bridge or SQL path instability.

---

## Test Coverage Summary

`fastapi_app/tests/test_practice.py` covers:

- hierarchy success and filtering,
- start validation,
- access control,
- submit accuracy and idempotency,
- continue sequencing,
- session expiry,
- deleted items during submit,
- `all_seen_warning` semantics,
- free-content visibility,
- mixed paid/free track handling,
- quota redistribution,
- legacy session compatibility,
- current-session malformed-state rejection.

At the time of writing, the practice backend suite passes with the current implementation.

---

## Recommended Maintenance Rules

If you change this feature, keep these invariants intact:

1. Never mark a batch as submitted unless the Practice Log write succeeded.
2. For current-format sessions, always validate submit payloads against the exact batch, not the whole session.
3. If current session state is incomplete, fail closed.
4. Only legacy sessions may skip strict validation, and only as a temporary compatibility measure.
5. Hierarchy visibility must match actual selectable content as closely as possible.
6. Continue must never advance if the previous batch was not submitted.
7. Wrap-around must be explicit and must set `all_seen_warning=true`.

---

## Quick Debug Checklist

If a player reports Practice Arena is broken:

1. Check whether `memora:practice:{player_id}` exists in Redis.
2. Inspect:
   - `schema_version`
   - `batch_seq`
   - `batch_{n}_item_ids`
   - `submitted_{n}`
3. Confirm the selected hierarchy path actually contains Review Items.
4. Check access grants vs. free-content flags in hierarchy.
5. Check logs for:
   - `practice_session_missing_batch_key`
   - `practice_legacy_session_skip_validation`
   - `practice_topic_select_failed`
   - `practice_count_per_topic_failed`
6. If duplicates behave unexpectedly, inspect the stored `submitted_{n}` marker format:
   - JSON payload = current session
   - `"1"` = legacy session

---

## Bottom Line

Practice Arena is a Redis-backed, batch-oriented practice flow with:

- hierarchy browse,
- access-aware lesson resolution,
- proportional question selection,
- session-scoped dedup,
- exact-batch submit validation,
- idempotent result handling,
- explicit wrap-around semantics,
- guarded legacy compatibility.

The implementation is stable as long as session-state integrity in Redis is preserved and the Frappe SQL bridge remains healthy.
