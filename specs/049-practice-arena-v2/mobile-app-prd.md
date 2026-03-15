# Product Requirements Document: Mobile App Integration for Practice Arena Update

**Feature:** Practice Arena session and content-loading redesign

## 1. Purpose

This document defines exactly what the mobile app must change to support the new Practice Arena backend now present in this branch.

The backend no longer returns full question objects from the practice session APIs. It now returns:

- ordered `question_ids`
- deduplicated `chunk_refs`
- session metadata

The mobile app must fetch the question content itself from CDN chunk files, render the batch locally, collect self-assessed results, and submit only `{item_id, is_correct}` back to the API.


## 2. Critical Source-of-Truth Notes

The mobile implementation must follow these current branch realities:

1. the actual changed FastAPI router currently exposes the new flow under `/api/v1/practice`.
2. The implemented endpoints currently available for this flow are:
   - `POST /api/v1/practice/start`
   - `POST /api/v1/practice/submit`
   - `POST /api/v1/practice/continue`
   - `GET /api/v1/practice/session`
3. There is no `POST /api/v1/practice/submit-continue` in the current implementation.
4. The current changed `practice.py` endpoint file does not include a hierarchy endpoint. The mobile app should continue using its existing source for track/unit/topic selection unless backend adds a new hierarchy route later.
5. `GET /api/v1/practice/session` does not return `unit_ids`, `topic_ids`, `total_available`, or `all_seen_warning`. If the app needs these for resume UX, it must persist them locally.
6. The selector returns question IDs in the required render order. The client must preserve that order after loading chunks.

## 3. Why Mobile Must Change

The old practice contract assumed the backend could return ready-to-render question objects. That is no longer the model.

The new model is:

- FastAPI chooses the batch.
- CDN stores the actual question payloads.
- Mobile resolves `question_ids` against the returned chunk files.
- Mobile evaluates correctness locally.
- Backend only receives the boolean result per item.

This is a breaking integration change on the mobile side even if the feature stays under the same `/api/v1/practice` base path.

## 4. Breaking Changes From The Old Mobile Contract

| Area | Old expectation | New expectation | Required mobile change |
|---|---|---|---|
| Start request | `filter`, `tracks`, `units`, `topics` | `subject_id`, `track_ids`, `unit_ids`, `topic_ids` | Rename request fields and remove `filter` from request payload |
| Start response | Full `questions[]` objects | `question_ids[]` + `chunk_refs[]` only | Add CDN chunk loader and local question resolution |
| Continue request | Could be called without a body in older flow | Must send `{ "batch_seq": currentBatchSeq }` | Track current batch seq locally and send it explicitly |
| Resume | No dedicated session snapshot route | `GET /api/v1/practice/session` | Implement resume/reconnect logic |
| Submit flow | Looser coupling to returned question objects | Must submit results for the exact current batch IDs | Keep exact current batch IDs in local state |
| Submission payload | Could be derived from rich objects | Must be `[{ item_id, is_correct }]` | Build payload from current batch IDs only |
| Question content source | API | CDN chunk files | Add practice chunk repository/cache |
| Combined submit+next | Older code/tests referenced `submit-continue` | Not implemented here | Use two-step `submit` then `continue` |

## 5. Product Goals For Mobile

The mobile app implementation must satisfy these goals:

1. A student can start a practice session and see a fully rendered batch without the API returning full questions.
2. A student can answer the batch, self-check locally, and submit only correctness booleans.
3. A student can continue to the next batch using the exact `batch_seq` contract required by the backend.
4. A student can reopen the app and recover an active practice session through `GET /session`.
5. The app behaves safely when the backend expires or replaces sessions.

## 6. Non-Goals For Mobile

These are not part of the mobile implementation:

1. Re-implementing backend prioritization logic locally.
2. Reconstructing player history or queue behavior on the client.
3. Sending selected answers to the backend.
4. Depending on `submit-continue`.
5. Waiting for database persistence before showing submit success.

## 7. Required End-to-End Mobile Flow

### 7.1 Scope Selection

The app must still allow the user to choose:

- subject
- one or more tracks
- optional units
- optional topics

The mobile side must enforce the same scope validation rules before calling `start`:

1. If more than one track is selected, `unit_ids` and `topic_ids` must both be omitted or `null`.
2. If more than one unit is selected, `topic_ids` must be omitted or `null`.

If the app does not validate this locally, the backend will reject the request with HTTP `400`.

### 7.2 Start Session

The app starts a session by calling:

`POST /api/v1/practice/start`

Request body:

```json
{
  "subject_id": "SUBJ-001",
  "track_ids": ["TRK-001"],
  "unit_ids": ["UNIT-001"],
  "topic_ids": null
}
```

Success response shape:

```json
{
  "session_active": true,
  "batch_seq": 0,
  "question_ids": ["uuid-1", "uuid-2", "uuid-3"],
  "chunk_refs": [3, 7],
  "total_available": 35,
  "all_seen_warning": false
}
```

After a successful start, the app must immediately do all of the following:

1. Persist a local practice session snapshot.
2. Store the exact returned `batch_seq`.
3. Store the exact returned `question_ids` in order.
4. Fetch every chunk referenced by `chunk_refs`.
5. Resolve each `question_id` to a question payload from the fetched chunks.
6. Render questions in `question_ids` order, not in chunk order.
7. Show a repeat-warning banner if `all_seen_warning` is `true`.

### 7.3 Load Content From CDN

The API no longer returns question content. The app must fetch content files directly from CDN.

#### Required chunk URL pattern

Files are published at:

- `/files/cdn/practice/chunks/{subject_id}/chunk_{chunk_id}.json`

Example:

- `/files/cdn/practice/chunks/SUBJ-001/chunk_3.json`

If the mobile app uses a full CDN base URL, the effective URL format is:

- `{cdn_base_url}/files/cdn/practice/chunks/{subject_id}/chunk_{chunk_id}.json`

#### Optional map URL pattern

Map files are published at:

- `/files/cdn/practice/maps/{subject_id}.json`

The current start/continue flow does not require the client to fetch the map file in order to render a batch, because the API already returns `chunk_refs`. The mobile app may still fetch the map file for caching, diagnostics, or future optimization, but it is not required for batch rendering.

#### Chunk file schema

Each chunk file looks like this:

```json
{
  "schema_version": 1,
  "subject_id": "SUBJ-001",
  "chunk_id": 3,
  "question_count": 97,
  "questions": {
    "uuid-1": {
      "type": "QUESTION",
      "topic_id": "TOP-001",
      "stem": "Solve for x: 2x + 5 = 15",
      "choices": ["x = 3", "x = 5", "x = 7", "x = 10"],
      "correct": 1,
      "explanation": "2x = 10, so x = 5"
    }
  }
}
```

Important client behavior:

1. `correct` is `0`-based.
2. `choices` may not always be length 4 in future content; do not hardcode a required size.
3. `type` must be preserved because the system may serve more than one review-item style.
4. The app must not rely on chunk iteration order for display order.
5. The app must build the rendered batch by mapping each `question_id` to `chunk.questions[question_id]`.

### 7.4 Local Question Resolution

The app must implement a resolver like this:

1. Fetch all required chunks.
2. Build a dictionary keyed by `question_id`.
3. Iterate through the returned `question_ids`.
4. For each ID, pull the matching question object from the chunk dictionary.
5. Produce the final ordered render list.

If any `question_id` cannot be found in the fetched chunk payloads, the app must fail closed:

1. Do not attempt to partially render the batch.
2. Do not invent or skip a result for the missing question.
3. Show a recoverable error state.
4. Offer the user a "Restart Practice" action that starts a fresh session.

This fallback is required because the current backend contract still expects submit results for the full batch, and there is no dedicated deleted-item recovery API exposed in the current implementation.

### 7.5 Answering And Self-Assessment

The backend does not grade answers. The mobile app must determine `is_correct` locally from the chunk data and the user's interaction.

The app must collect exactly one result for every question in the current batch:

```json
[
  { "item_id": "uuid-1", "is_correct": true },
  { "item_id": "uuid-2", "is_correct": false }
]
```

Requirements:

1. Results must cover the entire current batch.
2. No `item_id` may appear twice in the payload.
3. The app must submit only IDs from the current batch.
4. The app should disable the submit button until all current questions have been answered or explicitly marked.

### 7.6 Submit Batch

The app submits results by calling:

`POST /api/v1/practice/submit`

Request body:

```json
{
  "batch_seq": 0,
  "results": [
    { "item_id": "uuid-1", "is_correct": true },
    { "item_id": "uuid-2", "is_correct": false }
  ]
}
```

Success response shape:

```json
{
  "accepted": true,
  "batch_seq": 0,
  "correct_count": 1,
  "total_count": 2,
  "accuracy_percent": 50.0,
  "is_duplicate": false
}
```

After a successful submit, the app must:

1. Mark the current local session as `submitted = true`.
2. Store the returned stats locally so the results screen can be rebuilt on resume if needed.
3. Show the result summary immediately.
4. Enable the "Continue" CTA only after submit succeeds.

Important behavioral notes:

1. The backend updates player history in cache immediately on submit.
2. Database persistence is asynchronous and must not block the UI.
3. If the same `batch_seq` is submitted again, the backend can return the cached stats with `is_duplicate = true`.
4. Duplicate submit success must not create duplicate success UI or double navigation.

### 7.7 Continue To Next Batch

The app requests the next batch by calling:

`POST /api/v1/practice/continue`

Request body:

```json
{
  "batch_seq": 0
}
```

Important rule:

The request must send the batch sequence that was just submitted. The backend compares this against the current session batch.

Continue success response uses the same shape as start:

```json
{
  "session_active": true,
  "batch_seq": 1,
  "question_ids": ["uuid-11", "uuid-12"],
  "chunk_refs": [8],
  "total_available": 35,
  "all_seen_warning": false
}
```

After a successful continue, the app must:

1. Replace local `question_ids`, `chunk_refs`, and `batch_seq`.
2. Reset local `submitted` state to `false`.
3. Clear transient answer state from the previous batch.
4. Fetch any missing chunks for the new batch.
5. Render the next batch.

### 7.8 Resume Active Session

The app must support recovery of an active session through:

`GET /api/v1/practice/session`

Success response shape:

```json
{
  "session_active": true,
  "subject_id": "SUBJ-001",
  "track_ids": ["TRK-001"],
  "batch_seq": 1,
  "submitted": false,
  "question_ids": ["uuid-11", "uuid-12"],
  "chunk_refs": [8]
}
```

Resume rules:

1. When opening the Practice screen, first check whether the app has a locally stored active practice snapshot.
2. If yes, call `GET /session` to confirm the server still has the session.
3. If the endpoint returns `200`, rebuild the current batch from `question_ids` and `chunk_refs`.
4. If `submitted` is `false`, return the user to the current question batch.
5. If `submitted` is `true`, return the user to the post-submit state and allow `continue`.
6. If the endpoint returns `404`, clear the local session and return the user to scope selection.

Important limitation:

The current session endpoint does not return `unit_ids` or `topic_ids`. If the app wants to show exact filter pills or let the user restart the same scope, it must persist the selected scope locally from the original start request.

## 8. API Contract Details

### 8.1 `POST /api/v1/practice/start`

#### Request fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `subject_id` | string | yes | subject to practice |
| `track_ids` | string[] | yes | must contain at least one track |
| `unit_ids` | string[] or null | no | only allowed when one track is selected |
| `topic_ids` | string[] or null | no | only allowed when one track and at most one unit are selected |

#### Success fields

| Field | Type | Notes |
|---|---|---|
| `session_active` | boolean | always `true` on success |
| `batch_seq` | integer | always starts at `0` |
| `question_ids` | string[] | ordered IDs for render order |
| `chunk_refs` | integer[] | deduplicated chunk IDs to load |
| `total_available` | integer | total number of in-scope questions |
| `all_seen_warning` | boolean | `true` only when no unserved questions remained in the active session scope and the selector wrapped |

#### Mobile handling notes

1. A successful start replaces any previous active session on the backend.
2. The app should not auto-call `start` if it is trying to resume an existing session.
3. If the user starts a new session intentionally, the app must discard local state for the old one.

### 8.2 `POST /api/v1/practice/submit`

#### Request fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `batch_seq` | integer | yes | must equal current session batch |
| `results` | object[] | yes | min 1, max 20, but must exactly match current batch length in practice |

Each result item:

| Field | Type | Required | Notes |
|---|---|---|---|
| `item_id` | string | yes | must belong to current batch |
| `is_correct` | boolean | yes | client-computed correctness |

#### Success fields

| Field | Type | Notes |
|---|---|---|
| `accepted` | boolean | success flag |
| `batch_seq` | integer | echoes submitted batch |
| `correct_count` | integer | count of `true` results |
| `total_count` | integer | total result count |
| `accuracy_percent` | float | `correct_count / total_count * 100` |
| `is_duplicate` | boolean | `true` if backend already processed this batch |

#### Mobile handling notes

1. Treat `is_duplicate = true` as a successful submit of the same batch, not as an error.
2. Never optimistically advance to the next batch before submit returns success.

### 8.3 `POST /api/v1/practice/continue`

#### Request fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `batch_seq` | integer | yes | must match the current submitted batch |

#### Success fields

Same as `start`.

#### Mobile handling notes

1. Continue is a second API call after submit.
2. The app must not call continue for an unsubmitted batch.
3. If continue fails with `404`, the session is gone and must be cleared locally.

### 8.4 `GET /api/v1/practice/session`

#### Success fields

| Field | Type | Notes |
|---|---|---|
| `session_active` | boolean | always `true` on success |
| `subject_id` | string | current session subject |
| `track_ids` | string[] | selected tracks |
| `batch_seq` | integer | current batch number |
| `submitted` | boolean | whether current batch already has a successful submit |
| `question_ids` | string[] | current batch IDs |
| `chunk_refs` | integer[] | current batch chunk references |

#### Mobile handling notes

1. This endpoint is for state recovery, not scope reconstruction.
2. Persist extra local metadata if the UX needs it.

## 9. Required Mobile Data Model

The mobile app should introduce or update these local models.

### 9.1 PracticeScope

```ts
type PracticeScope = {
  subjectId: string;
  trackIds: string[];
  unitIds?: string[] | null;
  topicIds?: string[] | null;
};
```

### 9.2 PracticeSessionSnapshot

```ts
type PracticeSessionSnapshot = {
  subjectId: string;
  trackIds: string[];
  unitIds?: string[] | null;
  topicIds?: string[] | null;
  batchSeq: number;
  submitted: boolean;
  questionIds: string[];
  chunkRefs: number[];
  totalAvailable?: number;
  allSeenWarning?: boolean;
  lastSubmitStats?: {
    correctCount: number;
    totalCount: number;
    accuracyPercent: number;
    isDuplicate: boolean;
  } | null;
};
```

### 9.3 PracticeChunk

```ts
type PracticeChunk = {
  schema_version: number;
  subject_id: string;
  chunk_id: number;
  question_count: number;
  questions: Record<string, PracticeQuestionPayload>;
};
```

### 9.4 PracticeQuestionPayload

```ts
type PracticeQuestionPayload = {
  type: string;
  topic_id: string;
  stem: string;
  choices: string[];
  correct: number;
  explanation?: string;
};
```

### 9.5 RenderedPracticeQuestion

This is the final UI object after resolution:

```ts
type RenderedPracticeQuestion = {
  itemId: string;
  order: number;
  payload: PracticeQuestionPayload;
};
```

## 10. Local Storage And Cache Requirements

The mobile app must persist two different things separately:

1. Session snapshot state
2. CDN chunk content cache

### 10.1 Session snapshot persistence

Persist:

- selected scope
- current batch seq
- submitted flag
- question IDs
- chunk refs
- latest submit stats if available

Use this to recover quickly after app restart, then validate with `GET /session`.

### 10.2 Chunk cache persistence

Cache chunks by:

- `subject_id`
- `chunk_id`

Recommended behavior:

1. Reuse already-downloaded chunks across batches in the same subject.
2. Prefer HTTP cache semantics if available.
3. Allow manual in-memory reuse within the same practice session to avoid repeated parsing.

## 11. Required UX Behavior

### 11.1 Start screen

The app must:

1. Prevent invalid multi-track and multi-unit combinations locally.
2. Show a blocking loading state while `start` and chunk fetches are in flight.
3. Not display the batch until all required chunks are loaded and all question IDs resolve.

### 11.2 Practice question screen

The app must:

1. Render questions in the exact `question_ids` order.
2. Track one answer result per question.
3. Keep the current batch immutable until submit succeeds or the session is cleared.

### 11.3 Submit result screen

The app must:

1. Display `correct_count`, `total_count`, and `accuracy_percent`.
2. Preserve this screen on resume when `submitted = true`.
3. Offer a "Continue" CTA that calls the next endpoint only after submit success.

### 11.4 Resume UX

If a saved local session exists, the app should:

1. Check `/session`.
2. Resume automatically if the backend still has the session.
3. Clear local data and show a friendly restart path if the backend does not.

### 11.5 All-seen warning

When `all_seen_warning = true`, the app should show a non-blocking banner such as:

"You have practiced all available questions in this scope. Some questions may repeat."

The app must not interpret this as an error.

## 12. Error Handling Matrix

| Scenario | Typical status | Example detail | Required mobile action |
|---|---|---|---|
| Invalid start scope | `400` | `Cannot filter by units or topics when multiple tracks are selected` | Show validation error and keep user on scope picker |
| Unknown IDs in scope | `400` | `Unknown track_ids: [...]` | Treat as stale client hierarchy state; refresh local scope source |
| No available questions | `400` | `No questions available for the selected scope` | Show empty-state message |
| No active session on submit | `404` | `No active practice session` | Clear local session and return to start |
| No active session on continue | `404` | `No active practice session` | Clear local session and return to start |
| Batch mismatch on submit/continue | `400` | `batch_seq X does not match current batch Y` | Re-sync with `GET /session`; if mismatch remains, clear local session |
| Missing or foreign item in submit | `400` | `item_id '...' is not in the current batch` | Bug in mobile state; stop and re-sync |
| Duplicate items in submit | `400` | `Duplicate item_ids in submission` | Bug in mobile payload creation; do not retry unchanged payload |
| Continue before submit | `400` | `batch_seq 0 has not been submitted yet` | Prevent by UX; if received, keep user on result step |
| Rate limit on start | `429` | `Maximum 5 sessions per hour exceeded` | Show cooldown UI and honor `Retry-After` header |
| Maps directory unavailable | `503` | `Practice maps directory not configured` | Show temporary service-unavailable message |
| Subject map missing on continue | `500` | `Map file not found for subject: ...` | Show recoverable error and allow restart |
| Chunk file load failure | CDN/network | request fails or JSON malformed | Retry chunk fetch, then show restart option if unresolved |
| Question ID missing from chunk content | client resolution failure | local failure | Fail closed and force restart flow |

## 13. Mobile Guardrails And Implementation Rules

The mobile agent must follow these rules during implementation:

1. Never assume the API returns full question content.
2. Never render based on chunk order.
3. Never submit a partial batch.
4. Never auto-start a new session when trying to resume an old one.
5. Never call `continue` before a successful submit.
6. Never depend on `/submit-continue`.
7. Persist the original scope locally because `/session` does not return full scope filters.
8. Treat submit success as authoritative even though database persistence is asynchronous.

## 14. Known Backend Limitations The Mobile App Must Design Around

These are current branch realities and should be treated as implementation constraints:

- Current code is under `/api/v1/practice`
2. No combined submit-and-continue route exists.
3. No new hierarchy route is present in the changed endpoint file.
4. No explicit deleted-question recovery route exists for active sessions.
5. Session snapshot response is intentionally minimal and does not fully rebuild the original filter UI state.

## 15. Suggested Mobile Implementation Plan

### Phase 1: Networking contract update

1. Replace old start request model with `subject_id`, `track_ids`, `unit_ids`, `topic_ids`.
2. Update start response parsing to `question_ids` and `chunk_refs`.
3. Add `GET /api/v1/practice/session`.
4. Update continue call to send `{batch_seq}`.
5. Remove any dependency on `submit-continue`.

### Phase 2: CDN content loader

1. Add a chunk repository that downloads `/files/cdn/practice/chunks/{subject_id}/chunk_{chunk_id}.json`.
2. Parse chunk files into a local cache.
3. Add ordered question resolution based on returned `question_ids`.

### Phase 3: Session state management

1. Add local persistence for the active practice snapshot.
2. Add resume validation through `/session`.
3. Add session clearing behavior for `404` and batch mismatches.

### Phase 4: UX hardening

1. Add client-side scope validation.
2. Add rate-limit cooldown handling.
3. Add all-seen warning UI.
4. Add fail-closed handling for unresolved question IDs or chunk failures.

## 16. Acceptance Criteria For The Mobile Agent

The mobile implementation is complete only when all of the following are true:

1. Starting practice with the new request fields succeeds and renders questions loaded from chunk files.
2. The app no longer expects `questions[]` from the API.
3. Questions render in `question_ids` order.
4. The app can submit a full batch and show returned accuracy stats.
5. The app can continue by sending the current `batch_seq`.
6. The app can restore an in-progress session using `GET /session`.
7. The app clears stale local state when the backend session no longer exists.
8. The app shows a clear message when the user is rate-limited on session start.
9. The app does not break if `total_available` is smaller than 20.
10. The app can handle `all_seen_warning = true` without treating it as an error.
11. The app fails safely if any question ID cannot be resolved from fetched chunks.

## 17. QA Scenarios For Mobile

QA must verify at minimum:

1. Single-track start with no filters.
2. Single-track start with one or more topic filters.
3. Multi-track start where unit/topic filters are blocked before the request.
4. Submit of a full batch with mixed correct and incorrect answers.
5. Duplicate submit of the same batch does not break the UI.
6. Continue after submit returns a new batch.
7. Resume after app kill using `GET /session`.
8. Session-expired path where submit or continue returns `404`.
9. Start path with a `429` response and visible cooldown messaging.
10. Chunk fetch failure and missing-question fallback path.

## 18. Final Instruction To The Mobile AI Agent

Implement against the current backend code in this branch, not against older practice API assumptions.

The single most important change is this:

The practice APIs now return batch identity, not batch content.

The mobile app must become responsible for:

1. loading chunk files
2. resolving question content locally
3. preserving exact batch identity for submit and continue
4. recovering session state with `/session`
