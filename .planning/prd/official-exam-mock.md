# PRD: Official Exam (Mock Exam Mode)

## 1. Overview

The platform currently offers lesson-based learning, spaced-repetition reviews (FSRS), a practice arena,
and live challenge events. However, there is no way for students to practice with **official exam-style
questions** — questions authored independently from lesson content, structured as full mock exams for a
specific subject.

This feature introduces **Official Exams** — a repeatable mock exam experience where students take
full-length exams composed of independently authored questions, receive instant results with per-question
breakdown, and track their improvement across retakes.

## 2. Problem Statement

- Students preparing for official/standardized exams have no dedicated practice environment.
- The existing Practice Arena serves lesson-derived review items — not exam-specific content.
- Live Challenges are one-shot timed events, not repeatable self-paced mock exams.
- There is no separate question bank for exam-style content, and no way to monetize exam access
  independently from lesson subscriptions.

## 3. Goals

- Allow admins to create official exams per subject with a dedicated question bank (20–60 questions).
- Allow admins to author questions manually or import them in bulk via Excel/CSV.
- Students can browse available exams without a subscription (titles + question count only).
- Students with an exam grant (`EXAM-SUB-{subject_id}`) can start, complete, and retake exams freely.
- Correct answers are sent with questions — client handles scoring UI locally.
- Server records attempt count and best score per player-exam pair.
- Server stores last attempt's per-question results so students can see what they got wrong.
- Exam JSON is pre-built on admin save and served from CDN (no DB hit on the hot path).

## 4. Non-Goals

- No FSRS integration — exam questions are independent from the spaced-repetition system.
- No Review Item creation — exam questions do not feed into the review pipeline.
- No hierarchy mapping below Subject level — exams are flat ("Math Exam 1", not "Algebra Exam").
- No topic-level weakness breakdown — only overall score per exam.
- No pause/resume — exams are completed in a single sitting.
- No proctoring, time limits, or attempt limits — this is a self-paced mock exam.
- No real-time infrastructure (WebSocket, waiting room) — unlike Live Challenges.
- No global "exam pass" (all subjects at once) — grants are per-subject.

## 5. Access Key Design

### New key prefix

```
EXAM-SUB-{subject_id}
```

Examples:
- `EXAM-SUB-SUBJ-00001`
- `EXAM-SUB-MATH-GRADE-5`

This key lives in the same Redis set as existing access keys:
`memora:access:{player_id}` (type: SET).

### Access rules

| Action | Grant required |
|--------|---------------|
| Browse exam list (titles, question count) | **No** — public |
| Start exam (receive questions + answers) | `EXAM-SUB-{subject_id}` |
| Submit exam results | `EXAM-SUB-{subject_id}` |

### Access priority order (exam endpoints)

1. **Exam grant**: player has `EXAM-SUB-{subject_id}` → exam access granted
2. **No grant**: 403 Forbidden

Note: A full `SUB-{subject_id}` subscription does **not** implicitly include exam access.
Exam access is a separate, independently sold product.

## 6. User Experience

### Student flow

```
1. Browse: GET /exams/{subject_id}
   → See list of exams (title, question count, attempt stats)
   → No grant needed

2. Start: POST /exams/{exam_id}/start
   → Receive all questions with correct answers (shuffled order)
   → Grant required (EXAM-SUB-*)

3. Answer: Client-side
   → Student answers all 20-60 questions locally
   → Client scores answers using provided correct_choice values
   → No server interaction during the exam

4. Submit: POST /exams/{exam_id}/submit
   → Client sends per-question results (item index + is_correct)
   → Server records attempt, updates best score
   → Server stores last attempt's per-question results
   → Response: attempt_count, best_score, best_total

5. Review: Client-side
   → Student reviews which questions they got wrong (from submit response or next start)
   → Can retake immediately — unlimited retakes
```

### Admin flow

1. Create a `Memora Official Exam` in Frappe admin panel.
2. Set title, subject, publish status, sort order.
3. Add questions manually via child table (question_text, 4 choices, correct_choice).
4. Or bulk import questions from Excel/CSV file.
5. Save → JSON automatically built and pushed to CDN.
6. Create a `Memora Product Grant` with `EXAM-SUB-{subject_id}` grant component.
7. Create voucher batches linked to this grant for distribution.

## 7. Architecture

### 7.1 DocType: `Memora Official Exam`

**File:** `memora_admin/memora_admin/doctype/memora_official_exam/`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `exam_title` | Data | Yes | Display name |
| `subject` | Link → Memora Subject | Yes | Subject this exam belongs to |
| `is_published` | Check | No | Only published exams appear in listings |
| `sort_order` | Int | No | Display ordering within a subject |
| `questions` | Table → Memora Official Exam Question | Yes | Child table, 20–60 rows |

**Naming:** Autoname (e.g., `OEXAM-00001`)

**Validation:**
- At least 1 question required on save.
- Subject must exist and be published.

### 7.2 Child Table: `Memora Official Exam Question`

**File:** `memora_admin/memora_admin/doctype/memora_official_exam_question/`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `question_text` | Small Text | Yes | The question stem |
| `choice_1` | Small Text | Yes | First answer choice |
| `choice_2` | Small Text | Yes | Second answer choice |
| `choice_3` | Small Text | No | Third answer choice |
| `choice_4` | Small Text | No | Fourth answer choice |
| `correct_choice` | Int | Yes | 1-based index (1–4) of correct answer |

**Validation:**
- `correct_choice` must be between 1 and the number of non-empty choices.
- `choice_1` and `choice_2` are mandatory (minimum 2 choices).

### 7.3 CDN Structure

```
sites/{site}/public/files/cdn/exams/{subject_id}/_index.json
sites/{site}/public/files/cdn/exams/{subject_id}/{exam_id}.json
```

**`_index.json`** — exam listing (public, no answers):
```json
{
  "subject_id": "SUBJ-MATH-G5",
  "exams": [
    {
      "exam_id": "OEXAM-00001",
      "exam_title": "Math Exam 1",
      "question_count": 40
    },
    {
      "exam_id": "OEXAM-00002",
      "exam_title": "Math Exam 2",
      "question_count": 35
    }
  ]
}
```

**`{exam_id}.json`** — full exam (gated, includes correct answers):
```json
{
  "exam_id": "OEXAM-00001",
  "exam_title": "Math Exam 1",
  "subject_id": "SUBJ-MATH-G5",
  "question_count": 40,
  "questions": [
    {
      "idx": 1,
      "question_text": "...",
      "choice_1": "...",
      "choice_2": "...",
      "choice_3": "...",
      "choice_4": "...",
      "correct_choice": 2
    }
  ]
}
```

**Build trigger:** Frappe `on_update` hook on `Memora Official Exam`.
- Rebuilds `{exam_id}.json` for the saved exam.
- Rebuilds `_index.json` for the exam's subject (queries all published exams for that subject).
- Triggers CDN cache purge for affected paths.

**On delete/unpublish:** Remove `{exam_id}.json`, rebuild `_index.json`.

### 7.4 Raw SQL Tables

**`tabMemora Exam Attempt`** — one row per (player, exam):

```sql
CREATE TABLE `tabMemora Exam Attempt` (
  `player_id`       VARCHAR(140) NOT NULL,
  `exam_id`         VARCHAR(140) NOT NULL,
  `attempt_count`   INT UNSIGNED NOT NULL DEFAULT 1,
  `best_score`      INT UNSIGNED NOT NULL DEFAULT 0,
  `best_total`      INT UNSIGNED NOT NULL DEFAULT 0,
  `last_attempt_at` DATETIME NOT NULL,

  PRIMARY KEY (`player_id`, `exam_id`),
  KEY `idx_exam_id` (`exam_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**`tabMemora Exam Attempt Detail`** — one row per (player, exam, question):

```sql
CREATE TABLE `tabMemora Exam Attempt Detail` (
  `player_id`     VARCHAR(140) NOT NULL,
  `exam_id`       VARCHAR(140) NOT NULL,
  `question_idx`  SMALLINT UNSIGNED NOT NULL,
  `is_correct`    TINYINT(1) NOT NULL DEFAULT 0,

  PRIMARY KEY (`player_id`, `exam_id`, `question_idx`),
  KEY `idx_exam_id` (`exam_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**On submit:**
1. UPSERT `tabMemora Exam Attempt`: increment `attempt_count`, `best_score = GREATEST(best_score, new_score)`, update `last_attempt_at`.
2. DELETE all detail rows for this (player_id, exam_id).
3. INSERT new detail rows (20–60 rows).
4. All in one transaction.

Tables are created in `memora_admin/setup.py` (`before_migrate` hook), same pattern as
`tabMemora Practice Log`.

### 7.5 `Memora Grant Component` — Exam Grant

**No schema changes needed.** The existing `key_type` field (added by practice-standalone-subscription)
already supports custom key types. Add `exam` as a new option:

| `key_type` | `target_doctype` | Generated key |
|------------|-------------------|---------------|
| `full` | Memora Subject | `SUB-{subject_id}` |
| `full` | Memora Track | `TRK-{track_id}` |
| `practice` | Memora Subject | `PRAC-SUB-{subject_id}` |
| **`exam`** | **Memora Subject** | **`EXAM-SUB-{subject_id}`** |

**Validation:** If `key_type = exam`, then `target_doctype` must be `Memora Subject`.

### 7.6 Redis Keys

No new Redis key types for access control. `EXAM-SUB-*` strings live inside the existing
`memora:access:{player_id}` SET.

Add to `fastapi_app/core/redis_keys.py`:

```python
# Exam session (optional — for preventing concurrent starts)
def exam_session_key(player_id: str) -> str:
    """Active exam session. TTL: 3600s (1 hour)."""
    return f"memora:exam:session:{player_id}"

EXAM_SESSION_TTL = 3600  # 1 hour
```

Document `EXAM-SUB-{subject_id}` as an access key convention in the existing comments.

## 8. API Endpoints

All endpoints are under `fastapi_app/api/v1/endpoints/exam.py`.

### 8.1 `GET /api/v1/exams/{subject_id}`

**Auth:** Authenticated player (JWT). No exam grant required.

**Purpose:** List published exams for a subject, with player's attempt stats.

**Response:**
```json
{
  "subject_id": "SUBJ-MATH-G5",
  "exams": [
    {
      "exam_id": "OEXAM-00001",
      "exam_title": "Math Exam 1",
      "question_count": 40,
      "attempt_count": 3,
      "best_score": 35,
      "best_total": 40
    },
    {
      "exam_id": "OEXAM-00002",
      "exam_title": "Math Exam 2",
      "question_count": 35,
      "attempt_count": 0,
      "best_score": null,
      "best_total": null
    }
  ],
  "has_access": true
}
```

**Flow:**
1. Read `_index.json` from CDN/cache (exam listing).
2. Batch query `tabMemora Exam Attempt` for player's stats on all listed exams.
3. Check `EXAM-SUB-{subject_id}` access grant → populate `has_access`.
4. Merge and return.

### 8.2 `POST /api/v1/exams/{exam_id}/start`

**Auth:** Authenticated player + `EXAM-SUB-{subject_id}` grant.

**Purpose:** Return all exam questions with correct answers.

**Request:** (no body needed — exam_id in path)

**Response:**
```json
{
  "exam_id": "OEXAM-00001",
  "exam_title": "Math Exam 1",
  "question_count": 40,
  "questions": [
    {
      "idx": 1,
      "question_text": "...",
      "choice_1": "...",
      "choice_2": "...",
      "choice_3": "...",
      "choice_4": "...",
      "correct_choice": 2
    }
  ],
  "last_attempt_details": [
    {"question_idx": 3, "is_correct": false},
    {"question_idx": 7, "is_correct": false}
  ]
}
```

**Flow:**
1. Check `EXAM-SUB-{subject_id}` access → 403 if denied.
2. Read `{exam_id}.json` from CDN/cache.
3. Shuffle question order (randomize per attempt to reduce memorization).
4. Query last attempt's detail rows (so client can highlight previously wrong questions).
5. Return full exam content.

**Note:** Questions are shuffled server-side. The `idx` in the response is the **shuffled position**
(1-based), not the original authoring order. The submit endpoint uses this shuffled `idx`.

### 8.3 `POST /api/v1/exams/{exam_id}/submit`

**Auth:** Authenticated player + `EXAM-SUB-{subject_id}` grant.

**Purpose:** Record attempt results.

**Request:**
```json
{
  "results": [
    {"question_idx": 1, "is_correct": true},
    {"question_idx": 2, "is_correct": false},
    {"question_idx": 3, "is_correct": true}
  ],
  "score": 35,
  "total": 40
}
```

**Validation:**
- `len(results)` must equal the exam's `question_count`.
- `score` must equal the count of `is_correct=true` in results.
- `total` must equal the exam's `question_count`.

**Response:**
```json
{
  "accepted": true,
  "attempt_count": 4,
  "best_score": 37,
  "best_total": 40,
  "is_new_best": true
}
```

**Flow:**
1. Check `EXAM-SUB-{subject_id}` access → 403 if denied.
2. Validate result count matches exam question count (read from CDN/cache).
3. Validate `score` matches `is_correct` count.
4. Begin transaction:
   a. UPSERT `tabMemora Exam Attempt` (increment count, GREATEST for best_score).
   b. DELETE existing detail rows for (player_id, exam_id).
   c. INSERT new detail rows.
5. Commit.
6. Return updated stats.

## 9. Excel/CSV Import

### Import format

| Column | Required | Notes |
|--------|----------|-------|
| `question_text` | Yes | The question stem |
| `choice_1` | Yes | First choice |
| `choice_2` | Yes | Second choice |
| `choice_3` | No | Third choice |
| `choice_4` | No | Fourth choice |
| `correct_choice` | Yes | Integer 1–4 |

### Implementation

Add `import_questions_from_excel()` method to `Memora Official Exam` DocType, following the
existing pattern in `Memora Live Challenge Event`:

```python
@frappe.whitelist()
def import_questions_from_excel(exam_name, file_url):
    """Import questions from Excel/CSV into exam's child table."""
    # 1. Read file (xlsx or csv)
    # 2. Validate columns and values
    # 3. Append rows to exam.questions child table
    # 4. Save exam (triggers on_update → JSON rebuild)
    # 5. Return count of imported questions
```

Admin can either replace all questions or append to existing ones.

## 10. Build Pipeline (JSON Generation)

### Hook registration

**File:** `memora_admin/hooks.py`

```python
doc_events = {
    "Memora Official Exam": {
        "on_update": "memora_admin.memora_admin.events.exam_build.on_exam_updated",
        "on_trash": "memora_admin.memora_admin.events.exam_build.on_exam_deleted",
    },
}
```

### Build logic

**File:** `memora_admin/memora_admin/events/exam_build.py`

```python
def on_exam_updated(doc, method):
    """Rebuild exam JSON and subject index on save."""
    _build_exam_json(doc)
    _build_subject_index(doc.subject)
    _purge_cdn_cache(doc)

def on_exam_deleted(doc, method):
    """Remove exam JSON and rebuild subject index on delete."""
    _delete_exam_json(doc)
    _build_subject_index(doc.subject)
    _purge_cdn_cache(doc)
```

**`_build_exam_json(doc)`:**
1. Serialize exam + questions to JSON.
2. Write to `cdn/exams/{subject_id}/{exam_id}.json`.
3. Only include published exams.

**`_build_subject_index(subject_id)`:**
1. Query all published `Memora Official Exam` for this subject.
2. Build `_index.json` with exam_id, title, question_count.
3. Write to `cdn/exams/{subject_id}/_index.json`.

**`_purge_cdn_cache(doc)`:**
1. Use existing CDN purge infrastructure (`memora_admin/services/cdn_purge.py`).
2. Purge paths: `exams/{subject_id}/{exam_id}.json`, `exams/{subject_id}/_index.json`.

## 11. Data Model Summary

| Entity | Type | Purpose |
|--------|------|---------|
| `Memora Official Exam` | Frappe DocType | Exam definition + admin UI |
| `Memora Official Exam Question` | Frappe Child Table | Questions within an exam |
| `tabMemora Exam Attempt` | Raw SQL table | Player attempt aggregates (PK: player+exam) |
| `tabMemora Exam Attempt Detail` | Raw SQL table | Last attempt per-question results |
| `cdn/exams/{subject}/_index.json` | CDN file | Public exam listing |
| `cdn/exams/{subject}/{exam}.json` | CDN file | Full exam content (gated) |

## 12. What This Feature Does NOT Touch

- **Review Items** — exam questions are a separate bank, not extracted from lessons.
- **FSRS / Memory State** — no spaced repetition for exam questions.
- **Practice Arena / Practice Log** — separate system, separate question pool.
- **Live Challenges** — separate event-based system.
- **Content hierarchy below Subject** — exams are flat, no track/unit/topic mapping.
- **Leaderboards** — no ranking for exam performance.
- **XP / Wallet** — no XP awarded for exam attempts.
- **Streaks** — exam attempts do not affect streaks.

## 13. Backward Compatibility

- No existing DocTypes modified (except adding `exam` option to `Memora Grant Component.key_type`).
- No existing API endpoints affected.
- No existing Redis keys affected.
- New tables created via `setup.py` `before_migrate` — no migration risk.

## 14. Performance Considerations

- **Hot path is CDN reads** — no DB queries to serve questions.
- **Submit is a single transaction** — 1 UPSERT + 1 DELETE + 1 batch INSERT (≤60 rows).
- **List endpoint** — 1 file read + 1 small query (attempt stats).
- **No Redis session needed** — exam state is client-side (all questions sent at once).
  Optional session key for preventing concurrent starts, but not required for MVP.

## 15. Implementation Tasks

| # | Task | File(s) | Effort |
|---|------|---------|--------|
| T1 | Create `Memora Official Exam` DocType (JSON + py) | `doctype/memora_official_exam/` | Medium |
| T2 | Create `Memora Official Exam Question` child table DocType | `doctype/memora_official_exam_question/` | Small |
| T3 | Add `exam` option to `Memora Grant Component.key_type` | `memora_grant_component.json`, `.py` | Small |
| T4 | Update `get_grant_keys()` to emit `EXAM-SUB-*` | `api/products.py` | Small |
| T5 | Create raw SQL tables in `setup.py` | `setup.py` | Small |
| T6 | Build exam JSON pipeline (`exam_build.py`) | `events/exam_build.py` | Medium |
| T7 | Register hooks for `on_update` / `on_trash` | `hooks.py` | Trivial |
| T8 | Add Redis key builder + TTL constant | `fastapi_app/core/redis_keys.py` | Trivial |
| T9 | Create `GET /exams/{subject_id}` endpoint | `fastapi_app/api/v1/endpoints/exam.py` | Medium |
| T10 | Create `POST /exams/{exam_id}/start` endpoint | `fastapi_app/api/v1/endpoints/exam.py` | Medium |
| T11 | Create `POST /exams/{exam_id}/submit` endpoint | `fastapi_app/api/v1/endpoints/exam.py` | Medium |
| T12 | Add Pydantic request/response models | `fastapi_app/models/exam.py` | Small |
| T13 | Implement Excel/CSV import method | `memora_official_exam.py` | Medium |
| T14 | Register exam router in FastAPI app | `fastapi_app/main.py` | Trivial |
| T15 | Document `EXAM-SUB-*` in `redis_keys.py` comments | `redis_keys.py` | Trivial |
| T16 | Write tests: DocType validation | `test_memora_official_exam.py` | Small |
| T17 | Write tests: JSON build pipeline | `test_exam_build.py` | Medium |
| T18 | Write tests: API endpoints (list, start, submit) | `fastapi_app/tests/test_exam.py` | Medium |
| T19 | Write tests: access control (grant/no-grant) | `fastapi_app/tests/test_exam.py` | Small |
| T20 | Write tests: Excel/CSV import | `test_memora_official_exam.py` | Small |

**Total estimated effort: 5–7 days.**

## 16. Decisions Log

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Separate question bank from Review Items | Exam questions are authored independently, not extracted from lesson stages |
| D2 | No FSRS integration | No topic mapping → no actionable review path; mixing pools would confuse students |
| D3 | Flat hierarchy (Subject only) | Exams are "Math Exam 1" style, not mapped to tracks/units/topics |
| D4 | Child table for questions | 20–60 questions fits well; inline editing; drag-to-reorder; copy exam copies questions |
| D5 | Client-side scoring | Correct answers sent with questions; server trusts client results (same as Practice Arena) |
| D6 | Last attempt results only | Stores per-question breakdown for most recent attempt; retake overwrites; keeps storage bounded |
| D7 | Aggregate stats (attempt count + best score) | Single row per player-exam; shows improvement without full history |
| D8 | CDN-based serving | JSON pre-built on admin save; no DB on hot path; same pattern as Practice Maps |
| D9 | Separate access grant (`EXAM-SUB-*`) | Exams are independently monetizable; not bundled with lesson or practice subscriptions |
| D10 | Unlimited retakes | Mock exam purpose is practice and improvement, not assessment |
| D11 | Shuffled question order | Randomized per start to reduce answer-pattern memorization |
| D12 | No XP/streak/leaderboard impact | Exams are a separate practice tool, not part of the gamification loop |

## 17. Future Considerations (Out of Scope)

- **Timed exam mode**: Add optional `duration_minutes` field — client enforces timer, server validates submit timestamp.
- **Topic mapping**: Add optional track/unit/topic links per question for weakness breakdown.
- **Exam-specific review**: Feed wrong answers into a dedicated FSRS partition for exam-focused spaced repetition.
- **Difficulty tagging**: Add difficulty metadata per question for adaptive exam generation.
- **Randomized subsets**: For large question banks, randomly select N questions from a pool per attempt.
- **Full attempt history**: Store every attempt (not just latest) for detailed progress analytics.
- **Plan-level exam entitlement**: Some plans include exam access implicitly.
- **Bulk exam generation**: Auto-generate exams from a question pool with configurable rules.
