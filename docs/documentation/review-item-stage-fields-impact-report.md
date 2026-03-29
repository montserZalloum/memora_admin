# Impact Report: Removing `stage_id`, `stage_type`, and `content_json` from Memora Review Item

This report covers every usage of these three fields across the codebase (excluding docs/markdown), explains **what** each usage does and **why** it exists, and outlines the consequences of removal.

---

## Table of Contents

1. [Field Overview](#1-field-overview)
2. [stage_id — All Usages](#2-stage_id--all-usages)
3. [stage_type — All Usages](#3-stage_type--all-usages)
4. [content_json — All Usages](#4-content_json--all-usages)
5. [Removal Impact Summary](#5-removal-impact-summary)
6. [Migration Checklist](#6-migration-checklist)

---

## 1. Field Overview

| Field | Type | Required | Purpose |
|---|---|---|---|
| `stage_id` | Data | Yes (reqd=1) | Links to a Lesson Stage child-table row (`tabMemora Lesson Stage.name`) |
| `stage_type` | Link → Memora Lesson Stage Settings | Yes (reqd=1) | Identifies the kind of stage (QUESTION, FILL_BLANK, MATCHING, MINDMAP, etc.) |
| `content_json` | Code (JSON) | No | Stores non-MCQ content that doesn't fit in `choice_1..4`/`correct_choice` columns |

---

## 2. `stage_id` — All Usages

### 2.1 Review Item Extraction (writes `stage_id` into Review Item)

**File:** `memora_admin/api/review_items.py:359`
```python
item["stage_id"] = stage.name  # child table row name
```
- **What:** When a lesson is saved, `sync_review_items()` extracts items from each stage and stamps the child-table row name as `stage_id`.
- **Why:** Creates the link between "this review item came from stage X in lesson Y."

**File:** `memora_admin/api/review_items.py:392, 407, 429`
```python
ex.stage_id != item_data["stage_id"]          # change detection
"stage_id": item_data["stage_id"],            # upsert (update)
doc.stage_id = item_data["stage_id"]          # upsert (insert)
```
- **What:** Used in the upsert logic — checks if stage_id changed, writes it on create/update.
- **Why:** Keeps the Review Item's stage_id in sync if a lesson's stages are re-ordered.

### 2.2 Review Session Delivery (reads `stage_id` to join with Lesson Stage)

**File:** `memora_admin/api/reviews.py:86-98`
```sql
SELECT ms.stage_id, ms.lesson,
       ls.stage_type,
       ri.question_text, ri.choice_1, ...
FROM `tabMemora Memory State` ms
INNER JOIN `tabMemora Lesson Stage` ls
    ON ls.name = ms.stage_id AND ls.parent = ms.lesson
LEFT JOIN `tabMemora Review Item` ri
    ON ri.name = BIN_TO_UUID(ms.item_id)
```
- **What:** `get_due_items()` joins Memory State → Lesson Stage via `stage_id` to fetch `stage_type` at query time.
- **Why:** The API needs `stage_type` so the mobile app knows how to render each due item (MCQ vs fill-blank vs matching). `stage_id` is the join key.
- **Impact of removal:** This JOIN would break. The API could still get `stage_type` from the Review Item itself (it's stored there too), but the query would need restructuring.

**File:** `memora_admin/api/reviews.py:138`
```python
"stage_id": row.stage_id,
```
- **What:** `stage_id` is included in the API response for each due item.
- **Why:** The mobile client or FSRS processor uses it to write interaction logs back with the correct `stage_id`.

### 2.3 Memory State Table (stores `stage_id` as a column)

**File:** `memora_admin/memora_admin/setup.py` (schema comment, line ~443)
```sql
stage_id      VARCHAR(140)             -- Lesson stage identifier
```
- **What:** `tabMemora Memory State` has its own `stage_id` column.
- **Why:** When the FSRS processor creates a new Memory State row, it needs to know which stage the item came from.
- **Note:** This is a column on `tabMemora Memory State`, not on Review Item. Removing it from Review Item doesn't remove it from Memory State — but Review Item is the authoritative source that populates it.

### 2.4 FSRS Processor (reads `stage_id` from interactions, writes to Memory State)

**File:** `memora_admin/tasks/fsrs_processor.py:275, 303-311, 361, 374, 381, 487, 530`
```python
interactions = frappe.get_all("Memora Interaction Log", fields=[..., "stage_id", ...])
# ...
stage_row = stage_map.get(stage_id)
if stage_row.stage_type in skippable_types:   # skip if globally skippable
# ...
_insert_memory_state(..., stage_id=stage_id, ...)
```
- **What:** The FSRS processor reads `stage_id` from Interaction Log records, uses it to look up stage metadata (is it skippable?), and stores it in Memory State.
- **Why:** `stage_id` is the key to determine if an interaction should be excluded from spaced repetition (skippable stage types).
- **Note:** This reads from `tabMemora Interaction Log.stage_id` and `tabMemora Lesson Stage`, not directly from Review Item. But the value originally traces back through the system from Review Item → API response → mobile client → interaction log.

### 2.5 Session Endpoint (writes `stage_id` into interaction buffer)

**File:** `fastapi_app/api/v1/endpoints/sessions.py:300, 314`
```python
interaction = {
    "player": user.sub,
    "lesson": session.lesson_id,
    "stage_id": stage.stage_id,
    ...
}
```
- **What:** When a game session is submitted, each stage's `stage_id` is included in the interaction that gets buffered to Redis.
- **Why:** The downstream FSRS processor needs `stage_id` to resolve stage metadata.

### 2.6 Challenge Service (reads `stage_id` for interaction push)

**File:** `fastapi_app/services/challenge.py:966, 990, 1046-1049, 1082`
```python
# question_lookup maps item_id → {lesson, stage_id}
item_meta["stage_id"]   # used when pushing FSRS interactions
```
- **What:** After grading a challenge attempt, the service pushes per-question FSRS interactions. It needs `stage_id` from the Review Item lookup.
- **Why:** Without `stage_id`, the challenge service cannot create interaction log entries that the FSRS processor can trace back to a stage.

### 2.7 Interaction Log Sync (writes `stage_id` to DB)

**File:** `memora_admin/tasks/sync.py:842, 875`
```python
str(item.get("stage_id", "")),
# ...
INSERT INTO `tabMemora Interaction Log`
(name, player, lesson, stage_id, item_id, ...)
```
- **What:** `flush_interaction_buffer()` batch-inserts interaction records from Redis into MariaDB, including `stage_id`.
- **Why:** Persists the stage_id so the FSRS processor and analytics can trace each interaction to its source stage.

### 2.8 Plan Generator (writes `stage_id` into lesson JSON for CDN)

**File:** `memora_admin/memora_admin/services/build/plan_generator.py:580-581, 648-650`
```python
stage_data = {
    "stage_id": stage.name,
    "stage_type": stage.stage_type,
    ...
}
```
- **What:** When generating static lesson JSON files for the mobile CDN, each stage entry includes `stage_id`.
- **Why:** The mobile app uses `stage_id` from the lesson JSON to report back interactions (which the FSRS processor then reads).

### 2.9 Analytics Pipeline

**Files:**
- `analytics_exporter/schemas/dim_review_item.yaml:16, 28` — exports `stage_id` to Parquet
- `analytics_exporter/schemas/dim_lesson_stage.yaml:9, 13, 26` — `stage_id` is the primary key
- `analytics_exporter/schemas/fact_interaction.yaml:16, 31` — exports interaction `stage_id`
- `analytics_cli/views/semantic.py:69, 167, 173` — DuckDB views include `stage_id` in memory_state schema
- `archive_schemas/archive_types/memory_state.v1.yaml:11, 26, 35, 59` — `stage_id` in archive export
- `archive_schemas/archive_types/interaction_log.v1.yaml:8, 20, 29, 57` — `stage_id` in archive export

- **What:** `stage_id` is exported to the analytics data warehouse and archive Parquet files as a dimension/fact column.
- **Why:** Analytics queries join on `stage_id` to group metrics by stage type, identify which stages have high error rates, etc.

### 2.10 Load Tests

**Files:** `load_tests/config.py`, `load_tests/locustfile.py`
- **What:** Load test fixtures include `stage_id` in simulated interaction payloads.

---

## 3. `stage_type` — All Usages

### 3.1 Review Item Extraction (dispatches extraction by stage_type)

**File:** `memora_admin/api/review_items.py:61-71`
```python
stage_type = stage.stage_type
if stage_type == "QUESTION":
    return _extract_question(config, stage_type)
elif stage_type == "FILL_BLANK":
    return _extract_fill_blank(config, stage_type)
elif stage_type == "MATCHING":
    return _extract_matching(config, stage_type)
elif stage_type == "MINDMAP":
    return _extract_mindmap(config, stage_type)
else:
    return _extract_generic(config, stage_type)
```
- **What:** The extraction strategy is entirely driven by `stage_type`. Each type has its own `_extract_*()` function that knows how to parse the stage's `config_json`.
- **Why:** QUESTION stages have MCQ fields (choice_1..4, correct_choice). FILL_BLANK has blank positions. MATCHING has left/right pairs. The extraction must vary by type.
- **Impact of removal from Review Item:** The extraction itself reads `stage_type` from the **Lesson Stage** child row, not the Review Item. But after extraction, `stage_type` is written INTO the Review Item (lines 111, 147, 185, 222, 250, 278). Removing it from Review Item means it can't be stored/queried later.

### 3.2 Skippable Stage Filtering

**File:** `memora_admin/api/review_items.py:345-352`
```python
skippable_types = _get_globally_skippable_types()
for stage in lesson_doc.stages:
    if stage.stage_type in skippable_types:
        continue
```
- **What:** Before extracting items, checks if the stage's type is globally marked as skippable (e.g., INFORMATION, REVEAL).
- **Why:** Some stage types are informational and should never generate review items.
- **Note:** This reads from the **Lesson Stage**, not from Review Item's `stage_type`. Unaffected by removal from Review Item.

**File:** `memora_admin/tasks/fsrs_processor.py:260, 381`
```python
skippable_types = _get_skippable_stage_types()
if stage_row.stage_type in skippable_types:
```
- **What:** FSRS processor also checks skippable types to skip interactions.
- **Note:** Also reads from **Lesson Stage**, not Review Item. Unaffected by removal from Review Item.

### 3.3 Review Session API (reads `stage_type` via JOIN)

**File:** `memora_admin/api/reviews.py:88, 140`
```sql
ls.stage_type,
```
```python
"stage_type": row.stage_type,
```
- **What:** `get_due_items()` gets `stage_type` by joining Memory State → Lesson Stage (via `stage_id`).
- **Why:** The mobile app needs `stage_type` to know how to render each review item.
- **Note:** Currently this reads from Lesson Stage, NOT from Review Item. So removing `stage_type` from Review Item wouldn't break this specific query — as long as `stage_id` still exists for the JOIN.

### 3.4 Practice API (reads `stage_type` from Review Item directly)

**File:** `memora_admin/api/practice.py:328, 332, 379`
```sql
ri.stage_type, ri.topic,
```
- **What:** Practice question selection queries read `stage_type` directly from `tabMemora Review Item`.
- **Why:** The practice content needs to know the item type for rendering.
- **Impact of removal:** These SQL queries would break. You'd need to JOIN to Lesson Stage to get `stage_type` from there instead.

### 3.5 Practice Content Builder (reads `stage_type` from Review Item)

**File:** `memora_admin/memora_admin/services/build/practice_content.py:216, 338`
```python
"stage_type",   # in frappe.get_all fields
"type": q["stage_type"] or "QUESTION",  # in chunk output
```
- **What:** When building practice content JSON for the CDN, reads `stage_type` from Review Item and puts it as `type` in the chunk file.
- **Why:** The mobile app's practice mode needs to know each question's type.
- **Impact of removal:** Build would fail. Need to JOIN to Lesson Stage via `stage_id` to get the type.

### 3.6 Plan Generator (reads from Lesson Stage, writes to CDN JSON)

**File:** `memora_admin/memora_admin/services/build/plan_generator.py:573, 581, 642, 650`
```python
stage.stage_type in tree.skippable_types
"stage_type": stage.stage_type,
```
- **What:** Reads `stage_type` from Lesson Stage (not Review Item) when building lesson JSONs.
- **Note:** Unaffected by removal from Review Item.

### 3.7 Content Hash (reads from Lesson Stage)

**File:** `memora_admin/api/review_items.py:309`
```python
f"{stage.name}:{stage.stage_type}:{stage.is_skippable}:{stage.config_json or ''}"
```
- **What:** Stage type is part of the content hash for debounce.
- **Note:** Reads from Lesson Stage. Unaffected.

### 3.8 Frontend Game Editor (reads from Lesson Stage child row)

**File:** `memora_admin/public/js/game_lesson.js:18, 23, 39, 56-71`
```javascript
if (row.stage_type === "MATCHING") { ... }
else if (row.stage_type === "QUESTION") { ... }
// etc.
```
- **What:** The admin-side lesson editor uses `stage_type` to dispatch to the correct dialog (matching editor, question editor, etc.).
- **Note:** This reads from the Lesson Stage child row in the form, NOT from Review Item. Unaffected.

### 3.9 FastAPI Models and Endpoints

**File:** `fastapi_app/models/review.py:25`
```python
stage_type: str
```
**File:** `fastapi_app/api/v1/endpoints/reviews.py:70`
```python
stage_type=i.get("stage_type", ""),
```
- **What:** The `DueItem` Pydantic model includes `stage_type`. The endpoint maps it from the Frappe API response.
- **Impact of removal from Review Item:** If the Frappe API still returns `stage_type` (via Lesson Stage JOIN), the FastAPI side is unaffected. The field stays in the model/response.

### 3.10 Doctype Validation (Review Item controller)

**File:** `memora_admin/memora_admin/doctype/memora_review_item/memora_review_item.py:28`
```python
if not self.choice_1 and not self.content_json:
    frappe.throw("At least one of Choice 1 or Content JSON must be provided")
```
- **What:** The validation doesn't check `stage_type` directly, but it relies on the concept — MCQ types populate `choice_1`, non-MCQ types populate `content_json`.
- **Note:** `stage_type` itself is not validated, but it being `reqd=1` means Frappe enforces its presence on save.

### 3.11 Analytics Pipeline

**Files:**
- `analytics_exporter/schemas/dim_review_item.yaml:17, 29` — exports `stage_type`
- `analytics_exporter/schemas/dim_content_hierarchy.yaml:30, 56` — `GROUP_CONCAT(DISTINCT ls.stage_type)` (from Lesson Stage)
- `archive_schemas/dimensions/review_item.v1.yaml:18` / `v2.yaml:26` — `ri.stage_type AS item_type`

- **What:** `stage_type` is exported from Review Item to analytics Parquet and archive dimension files.
- **Impact of removal:** Analytics schemas would need to JOIN to Lesson Stage to get the type, or the column would be missing from exports.

### 3.12 Search Fields

In `memora_review_item.json`:
```json
"search_fields": "lesson,stage_type,question_text"
```
- **What:** `stage_type` is a search field in the Frappe desk UI.
- **Impact of removal:** Minor — just update `search_fields`.

---

## 4. `content_json` — All Usages

### 4.1 Review Item Extraction (writes `content_json` for non-MCQ stages)

**File:** `memora_admin/api/review_items.py:118, 154, 192, 229, 260, 288`

| Stage Type | content_json Value |
|---|---|
| QUESTION | `None` (uses choice_1..4 + correct_choice) |
| FILL_BLANK | `{"blank_from": N, "blank_to": N, "correct_word": "...", "distractors": [...]}` |
| MATCHING | `{"left": "...", "right": "..."}` |
| MINDMAP | Full node dict from the tree |
| Generic | Full config dict or per-entry dict |

- **What:** For non-MCQ stages, the essential content data is serialized into `content_json` because it doesn't fit the MCQ columns.
- **Why:** Without `content_json`, there is no way to store fill-blank, matching, or mindmap item data in the Review Item.

### 4.2 Review Item Upsert (change detection + write)

**File:** `memora_admin/api/review_items.py:400, 415, 437`
```python
or (ex.content_json or "") != (item_data["content_json"] or "")
"content_json": item_data["content_json"],
doc.content_json = item_data["content_json"]
```
- **What:** Used in change detection and written on create/update.

### 4.3 Review Session API (returned to mobile app)

**File:** `memora_admin/api/reviews.py:95, 125-133, 144`
```sql
ri.content_json
```
```python
content_json = json.loads(row.content_json) if isinstance(row.content_json, str) else row.content_json
result.append({..., "content_json": content_json})
```
- **What:** When serving due review items, `content_json` is fetched from Review Item, parsed, and returned.
- **Why:** The mobile app needs this data to render fill-blank/matching/mindmap review screens. Without it, only MCQ reviews would work.
- **Impact of removal:** Non-MCQ review items would have no content data. The mobile app could not render them.

### 4.4 Practice API (returned in practice questions)

**File:** `memora_admin/api/practice.py:327, 331, 378`
```sql
ri.content_json,
```
- **What:** Practice question queries include `content_json` in the SELECT.
- **Why:** Practice mode also needs to render non-MCQ question types.
- **Impact of removal:** Practice mode would lose non-MCQ content.

### 4.5 Practice Content Builder (reads for CDN content)

**File:** `memora_admin/memora_admin/services/build/practice_content.py:223, 345-349`
```python
if q.get("content_json"):
    cj = json.loads(q["content_json"])
    if isinstance(cj, dict) and cj.get("explanation"):
        chunk_question["explanation"] = cj["explanation"]
```
- **What:** When building practice content for the CDN, checks `content_json` for explanations.
- **Why:** Some items have extra explanation data stored in content_json.
- **Impact of removal:** Explanations would be lost from practice content.

### 4.6 Doctype Validation

**File:** `memora_admin/memora_admin/doctype/memora_review_item/memora_review_item.py:27-29`
```python
def _validate_content(self):
    if not self.choice_1 and not self.content_json:
        frappe.throw("At least one of Choice 1 or Content JSON must be provided")
```
- **What:** Validation ensures every Review Item has EITHER MCQ choices OR content_json.
- **Why:** An item with neither has no content and is useless.
- **Impact of removal:** This validation would need to be removed or changed. Non-MCQ items would then pass validation with zero content.

### 4.7 FastAPI Models and Endpoints

**File:** `fastapi_app/models/review.py:29`
```python
content_json: dict | None = None
```
**File:** `fastapi_app/api/v1/endpoints/reviews.py:74`
```python
content_json=i.get("content_json"),
```
- **What:** The Pydantic model and endpoint mapping include `content_json`.
- **Impact of removal:** Would need to remove from model or always return `None`.

---

## 5. Removal Impact Summary

### 5.1 `stage_id` — HIGH IMPACT

`stage_id` is deeply embedded in the data flow:

```
Lesson Stage → Review Item.stage_id → API response → Mobile app → Interaction Log.stage_id → FSRS Processor → Memory State.stage_id → Analytics
```

Removing it from Review Item breaks:
- The `get_due_items()` API response (mobile needs it to report interactions)
- Challenge service's interaction push (needs `stage_id` per question)
- Analytics exports (dim_review_item schema)
- Archive schemas (review_item.v1/v2 dimensions)

**However:** `stage_id` also exists independently on `tabMemora Interaction Log` and `tabMemora Memory State`. The source of truth for the value is `tabMemora Lesson Stage.name`. You could potentially derive it at runtime via `item_id → lesson → stages`, but this would be expensive.

### 5.2 `stage_type` — MEDIUM IMPACT

`stage_type` is used in two distinct ways:
1. **From Lesson Stage** (extraction, skipping, plan generation, frontend editor) — **unaffected** by removal from Review Item
2. **From Review Item directly** (practice API, practice content builder, analytics exports) — **breaks**

The reviews API (`get_due_items`) already reads `stage_type` from Lesson Stage via JOIN, so it's unaffected. But the practice API and practice content builder read it directly from Review Item.

**Mitigation:** These could JOIN to Lesson Stage via `stage_id` to get the type — but that requires `stage_id` to still exist.

### 5.3 `content_json` — HIGH IMPACT (for non-MCQ content)

If you only have MCQ-type stages (QUESTION), removal is safe — `content_json` is always `NULL` for those.

If you have FILL_BLANK, MATCHING, MINDMAP, or any other non-MCQ stages:
- Review sessions cannot deliver non-MCQ content
- Practice sessions cannot deliver non-MCQ content
- Practice content CDN files lose non-MCQ questions and explanations
- The validation rule (`choice_1 OR content_json`) would reject non-MCQ items

### 5.4 What Stays Unaffected

These read `stage_id`/`stage_type` from **Lesson Stage** or **Interaction Log**, not from Review Item:
- `game_lesson.js` (frontend stage editor)
- `plan_generator.py` (CDN lesson JSON)
- `fsrs_processor.py` skippable-type filtering (reads from Lesson Stage)
- `sync.py` interaction flush (reads from Redis buffer, not Review Item)
- `sessions.py` (reads from session payload, not Review Item)

---

## 6. Migration Checklist

If you proceed with removal, here is every file that needs changes:

### Python — Must Change
| File | Lines | Change Needed |
|---|---|---|
| `memora_admin/api/review_items.py` | 111, 118, 147, 154, 185, 192, 222, 229, 250, 260, 278, 288, 359, 369-377, 392-393, 400, 407-408, 415, 429-430, 437 | Remove `stage_id`, `stage_type`, `content_json` from extraction dicts, upsert logic, change detection, field lists |
| `memora_admin/api/reviews.py` | 86, 88, 95, 125-133, 138-144 | Remove `content_json` from SELECT; restructure JOIN if `stage_id` removed; remove from response dict |
| `memora_admin/api/practice.py` | 327-328, 331-332, 378-379 | Remove `content_json`, `stage_type` from SQL SELECTs (or JOIN to Lesson Stage) |
| `memora_admin/memora_admin/doctype/memora_review_item/memora_review_item.py` | 27-29 | Remove `_validate_content()` or rewrite it |
| `memora_admin/memora_admin/services/build/practice_content.py` | 216, 223, 338, 345-349 | Remove `stage_type`, `content_json` from queries and chunk building |
| `fastapi_app/models/review.py` | 25, 29 | Remove `stage_type`, `content_json` from `DueItem` model |
| `fastapi_app/api/v1/endpoints/reviews.py` | 70, 74 | Remove from response mapping |
| `fastapi_app/services/challenge.py` | 990, 1046-1049, 1082 | Remove `stage_id` from question lookup (breaks interaction push) |
| `fastapi_app/api/v1/endpoints/sessions.py` | 300, 314 | `stage_id` comes from session payload, not Review Item — but the mobile app gets it from the lesson JSON which includes `stage_id` |

### YAML Schemas — Must Change
| File | Change Needed |
|---|---|
| `analytics_exporter/schemas/dim_review_item.yaml` | Remove `stage_id`, `stage_type` columns |
| `archive_schemas/dimensions/review_item.v1.yaml` | Remove `stage_type AS item_type` |
| `archive_schemas/dimensions/review_item.v2.yaml` | Remove `stage_type AS item_type` |

### JSON — Must Change
| File | Change Needed |
|---|---|
| `memora_admin/memora_admin/doctype/memora_review_item/memora_review_item.json` | Remove three field definitions, update `field_order`, update `search_fields` |

### Tests — Must Change
| File | Approximate Lines |
|---|---|
| `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py` | ~25 lines with `stage_type`/`content_json` assertions |
| `fastapi_app/tests/test_review_items.py` | ~40 lines with mock data and assertions |
| `fastapi_app/tests/test_review_endpoints.py` | ~4 lines |
| `fastapi_app/tests/test_challenge_service.py` | ~13 lines with `stage_id` in mock data |
| `analytics_exporter/tests/test_dim_review_item.py` | ~5 lines |
| `analytics_exporter/tests/test_dim_content_hierarchy.py` | ~4 lines (stage_types column) |
| `analytics_exporter/tests/test_fact_supplementary.py` | ~4 lines |
| `archive_executor/tests/` (multiple files) | ~15 lines |

### Analytics Views — Must Change
| File | Change Needed |
|---|---|
| `analytics_cli/views/semantic.py` | Remove `stage_id` from DuckDB view definitions (if removing from Memory State too) |

### Load Tests
| File | Change Needed |
|---|---|
| `load_tests/config.py` | Remove `stage_id` from fixture dicts |
| `load_tests/locustfile.py` | Remove `stage_id` from simulated payloads |

---

**Bottom line:** These three fields are not decorative metadata — they are load-bearing in the review/practice delivery pipeline and the analytics export chain. `stage_id` is the primary join key between Memory State and Lesson Stage. `stage_type` drives both extraction strategy and content rendering. `content_json` is the only storage for non-MCQ item content. Removing all three means the system can only support MCQ-type stages and would need significant restructuring of the review, practice, and analytics pipelines.
