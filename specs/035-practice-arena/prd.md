# Product Requirements Document: Practice Arena Mobile

**Feature Name**: `practice-arena` (user request referenced `practice-arent`)
**Audience**: Mobile team AI agent
**Primary Sources**:
- `specs/025-practice-arena/spec.md`
- `specs/025-practice-arena/prd.md`
- `specs/025-practice-arena/contracts/practice-api.md`
- `specs/035-practice-arena/spec.md`
- `specs/035-practice-arena/contracts/practice-api.md`
**Status**: Ready for mobile implementation
**Date**: 2026-03-02

---

## 1. Purpose

Build the student-facing mobile implementation of Practice Arena: an optional practice mode where students choose content by hierarchy (subject -> track -> unit -> topic), answer questions in batches, review their batch results, and continue practicing without affecting FSRS reviews, streaks, XP, wallet, or leaderboards.

This PRD is for the mobile client only. Backend contracts already exist. Phase 035 changes response behavior, not API shapes.

---

## 2. Product Summary

Practice Arena is a separate practice flow that lets students:
- choose a subject and filter content,
- narrow practice by track, unit, and topic,
- start a session and receive a batch of questions,
- submit the batch,
- optionally continue with another batch using the same filters.

The experience must feel lightweight and repeatable. Students should be able to start quickly, finish one batch, and continue until they choose to stop or the session expires.

---

## 3. Scope

### In Scope

- Hierarchy browsing for a selected subject
- Content filter toggle: `all` vs `completed`
- Track multi-select
- Conditional unit and topic drill-down
- Start session
- Question batch rendering
- Batch submission
- Batch results summary
- Continue session
- Error and empty states for access denial, no items, expired session, and retry-safe submission

### Out of Scope

- Review item extraction / sync job UX
- Teacher/admin workflows
- Any FSRS review logic
- XP, wallet, streaks, leaderboard, rewards
- Purchase/paywall flow beyond showing locked state
- Schema changes to the existing backend API

---

## 4. Phase Alignment

### Phase 025 Baseline

Mobile should treat Phase 025 as the base feature:
- all four endpoints already exist,
- sessions are batch-based,
- submission is idempotent via `batch_seq`,
- one active session per student,
- hierarchy shows locked and unlocked content.

### Phase 035 Delta

Mobile must explicitly account for these Phase 035 changes:
- `all_seen_warning` is now `true` when any question in the current batch is a repeat, not only when the full pool is exhausted.
- Questions are now distributed proportionally across selected topics by content volume.
- No request or response schema changed.

Client implication: if mobile already supports the Phase 025 flow, it should not change payload parsing, but it must update the meaning of the repeat-warning banner and avoid assuming batches are sorted by a single global priority only.

---

## 5. User Persona

### Primary User

- Student with a valid authenticated session in the mobile app

### User Need

- "I want to freely practice the exact content I choose without touching my daily review progress."

---

## 6. Core User Flow

1. Student opens Practice Arena from the learning area.
2. Student chooses a subject.
3. Mobile loads hierarchy with `GET /practice/hierarchy`.
4. Student chooses filter mode:
   - `All content`
   - `Completed only`
5. Student selects one or more tracks.
6. If exactly one track is selected, unit selection becomes available.
7. If exactly one unit is selected, topic selection becomes available.
8. Student taps `Start Practice`.
9. Mobile calls `POST /practice/start`.
10. Mobile renders the returned batch of questions.
11. Student answers all visible questions in the batch.
12. Mobile computes `is_correct` for each item and calls `POST /practice/submit` with the same `batch_seq`.
13. Mobile shows batch results summary.
14. Student chooses:
   - `Continue` -> `POST /practice/continue`
   - `Finish` -> leave the flow
15. If the student starts a new session later, the backend automatically replaces any older active session.

---

## 7. Mobile Experience Requirements

### 7.1 Entry Screen

The entry screen must:
- clearly communicate that Practice Arena is optional and separate from daily reviews,
- allow subject selection,
- expose the content filter toggle (`All content`, `Completed only`),
- display track list with locked vs unlocked visual treatment,
- show item counts where available,
- prevent invalid drill-down combinations.

### 7.2 Hierarchy Selection Rules

The client must enforce the same selection rules as the backend:
- At least one track is required.
- If more than one track is selected, unit and topic selection must be disabled and cleared.
- If exactly one track is selected, unit selection is enabled.
- If more than one unit is selected, topic selection must be disabled and cleared.
- If exactly one unit is selected, topic selection is enabled.

The client should not rely only on backend validation. It should prevent invalid requests before submission.

### 7.3 Locked Content UX

Hierarchy results include tracks that the student cannot access.

Mobile must:
- show locked tracks in the list,
- visually distinguish them from accessible tracks,
- block selection of locked tracks for starting a session,
- avoid hiding them entirely, because locked visibility is intentional.

For inaccessible tracks in the hierarchy response:
- `has_access = false`
- `units` and `topics` may be empty

### 7.4 Empty States

The app must provide distinct empty states for:
- no completed content when `Completed only` is selected,
- no reviewable items matching the chosen filters,
- subject not found,
- expired session,
- access denied for selected content.

These states should explain what the student can do next:
- switch to `All content`,
- change selection,
- restart session,
- return to the previous screen.

---

## 8. Question Rendering Requirements

### 8.1 Batch Rules

- A batch contains up to the configured backend batch size (default `20`).
- If fewer items match the filters, mobile must render the smaller batch without padding.
- Mobile must treat the returned list as the full source of truth for the current batch.
- Mobile must not request another batch until the current batch is submitted successfully.

### 8.2 Question Payload

Each question contains:
- `item_id`
- `stage_type`
- `question_text`
- `choices`
- `correct_choice`
- `content_json`

### 8.3 Rendering by Payload

Required client behavior:
- If `choices.length > 0` and `correct_choice` is present, render a standard answer-selection UI.
- If `choices.length == 0`, render a stage-aware fallback using `question_text`, `stage_type`, and `content_json`.

Because backend payloads support non-MCQ stage types (`MATCHING`, `FILL_BLANK`, `SENTENCE_BUILDER`, `MINDMAP`) and may return no `choices`, the mobile implementation must not assume all questions are simple MCQs.

### 8.4 Correctness Calculation

For MCQ-style questions:
- `correct_choice` is `1`-based.
- Mobile should convert the student's selected answer into a boolean `is_correct`.

For non-MCQ payloads:
- Mobile still must submit `is_correct` per item.
- The mobile agent should implement a renderer that can derive a boolean result from the interaction state for the returned `stage_type`.
- If the mobile app cannot support a rich interaction for a non-MCQ payload immediately, it must still fail safely (do not crash, do not submit malformed data).

### 8.5 Repeat Warning

When `all_seen_warning = true`, mobile must show a subtle but visible warning that at least one question in the batch has been seen before.

Important Phase 035 rule:
- this warning now means "one or more repeated questions in this batch,"
- not "the entire question pool is exhausted."

Recommended copy:
- "Some questions in this batch are repeats."

Do not describe the batch as fully exhausted unless the product team adds a separate signal later.

---

## 9. Results and Continuation

### 9.1 Submit Behavior

After the student answers all questions:
- mobile sends `POST /practice/submit`,
- uses the current `batch_seq`,
- includes one result object per item:
  - `item_id`
  - `is_correct`

### 9.2 Results Summary Screen

On successful submit, show:
- correct answer count
- total answer count
- accuracy percentage

The results screen must provide:
- `Continue`
- `Finish`

### 9.3 Continue Rules

When the student taps `Continue`:
- call `POST /practice/continue`,
- expect the next `batch_seq`,
- render the new batch as a fresh interaction state.

The app must not allow `Continue` before the previous batch is successfully submitted.

If backend returns `PREVIOUS_BATCH_NOT_SUBMITTED`, the client should:
- return the student to the pending batch summary or batch screen,
- retry submit instead of requesting another batch.

---

## 10. API Integration Contract

### 10.1 Authentication

- All endpoints require JWT bearer auth.
- The mobile client should reuse the app's existing authenticated API client.

### 10.2 `GET /api/v1/practice/hierarchy`

Purpose:
- load tracks, units, topics, access flags, and item counts for one subject

Query params:
- `subject_id` (required)
- `filter` = `all` or `completed`

Success handling:
- render full hierarchy tree for the subject
- show locked tracks using `has_access`
- respect returned `item_count` values

Error handling:
- `404 SUBJECT_NOT_FOUND`

### 10.3 `POST /api/v1/practice/start`

Purpose:
- validate access
- start or replace current session
- return first batch

Body:
```json
{
  "subject_id": "SUB-00001",
  "filter": "all",
  "tracks": ["TRK-00001"],
  "units": [],
  "topics": []
}
```

Success handling:
- persist current batch context locally
- store `batch_seq`
- render `questions`
- show repeat warning if `all_seen_warning` is true

Error handling:
- `403 {"code":"NO_ACCESS","tracks":[...]}`
- `422 {"code":"NO_ITEMS", ...}`
- `422` validation string for invalid selection combinations

### 10.4 `POST /api/v1/practice/submit`

Purpose:
- submit batch outcome
- receive canonical batch summary

Body:
```json
{
  "batch_seq": 0,
  "results": [
    {"item_id": "uuid-1", "is_correct": true}
  ]
}
```

Success handling:
- show summary screen
- if `is_duplicate = true`, treat the response as already accepted and do not re-submit again automatically

Error handling:
- `404 NO_ACTIVE_SESSION`
- `409 {"code":"BATCH_SEQ_MISMATCH", ...}`

### 10.5 `POST /api/v1/practice/continue`

Purpose:
- fetch next batch in the same session

Request body:
- empty

Success handling:
- replace local batch state with the new batch
- update stored `batch_seq`

Error handling:
- `404 NO_ACTIVE_SESSION`
- `422 {"code":"PREVIOUS_BATCH_NOT_SUBMITTED","batch_seq":...}`

---

## 11. Local Client State Requirements

The mobile client should maintain local ephemeral state for:
- selected `subject_id`
- selected filter mode
- selected track IDs
- selected unit IDs
- selected topic IDs
- current `batch_seq`
- current batch question list
- current answer state per `item_id`
- submission status (`idle`, `submitting`, `submitted`)

This state should be reset when:
- the student finishes and exits,
- the session expires,
- a new session is started,
- the start request fails with `NO_ITEMS` or `NO_ACCESS`.

The app should not try to reconstruct a session locally after process death unless a completed submit response has already been received. Backend session state is authoritative.

---

## 12. Retry and Failure Handling

### 12.1 Safe Retries

`POST /practice/submit` is idempotent by `batch_seq`.

Client requirement:
- if the submit request times out or the network drops after send, retry the same payload with the same `batch_seq`,
- do not increment `batch_seq` on the client,
- do not mutate the result set between retries.

### 12.2 Session Expiry

If any call returns `NO_ACTIVE_SESSION`:
- discard local session state,
- inform the student the practice session expired,
- return them to the entry screen so they can start again.

### 12.3 New Session Overwrites Old Session

Starting a new session replaces the previous active session server-side.

Client implication:
- do not offer "resume old batch" after a fresh `start`,
- always treat `POST /practice/start` as the beginning of a new canonical session.

### 12.4 Rate Limits

Mobile should gracefully handle `429` responses:
- `hierarchy`: 30/min
- `start`: 10/min
- `submit`: 30/min
- `continue`: 30/min

Recommended behavior:
- short user-facing message,
- temporary action cooldown,
- no silent retry loop.

---

## 13. Analytics and Instrumentation

Track these client events if analytics exist:
- `practice_arena_opened`
- `practice_subject_selected`
- `practice_filter_changed`
- `practice_selection_changed`
- `practice_start_requested`
- `practice_batch_loaded`
- `practice_repeat_warning_shown`
- `practice_batch_submitted`
- `practice_results_viewed`
- `practice_continue_requested`
- `practice_session_expired`
- `practice_no_items`
- `practice_access_denied`

These events are useful for validating adoption, failure rates, and whether the repeat-warning behavior is understandable.

---

## 14. Acceptance Criteria for Mobile

The mobile implementation is complete when:

1. A student can browse a subject hierarchy, see locked and unlocked tracks, and switch between `All content` and `Completed only`.
2. The app prevents invalid selection combinations before calling `POST /practice/start`.
3. The app starts a session and renders the returned batch without assuming a fixed question count.
4. The app correctly interprets `correct_choice` as `1`-based for MCQ-style questions.
5. The app shows a repeat warning whenever `all_seen_warning = true`, using the Phase 035 meaning.
6. The app submits one boolean result per item with the same `batch_seq` it received for the active batch.
7. The app treats duplicate submit success (`is_duplicate = true`) as a successful submit, not as an error.
8. The app blocks `Continue` until submit succeeds.
9. The app recovers cleanly from `NO_ACTIVE_SESSION`, `NO_ITEMS`, `NO_ACCESS`, and rate-limit responses.
10. The app keeps Practice Arena isolated in messaging and UX from FSRS daily review features.

---

## 15. Implementation Notes for the Mobile AI Agent

- Reuse the app's existing authenticated API stack.
- Treat backend as already implemented; do not design new payloads.
- Use the response schema exactly as defined in the existing contracts.
- Do not add client assumptions that `all_seen_warning` means full exhaustion.
- Do not add XP, rewards, streak, or wallet UI inside this flow.
- Keep the flow restartable. If state becomes ambiguous, reset to entry and start a new session.

---

## 16. Open Product Constraint

The backend contract allows non-MCQ question payloads with empty `choices` and populated `content_json`. The mobile app therefore needs a renderer that can handle question data beyond a simple four-choice card.

If the mobile team wants to ship only a pure-MCQ UI first, that is a product decision that must be explicitly validated against backend payload reality before release. This PRD assumes the client will handle the contract as-is and fail safely for unsupported shapes.
