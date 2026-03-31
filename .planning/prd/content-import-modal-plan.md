# Content Import Modal for Memora Topics

## Context

Admins currently create lessons, stages, and review items manually through Frappe forms. An AI agent (via n8n) generates lesson content as JSON, but there's no way to import it. This feature adds a full-screen modal on the Memora Topic form that guides admins through a 4-step import workflow: Upload → Review Questions → Split Lessons → Confirm & Import.

**Architectural change**: Review Items are now first-class entities — created directly from the JSON, not extracted from lesson stages. The existing extraction pipeline (`sync_review_items`, `_extract_question`, content_hash debounce, dirty set consumer) is removed entirely. Stages are teaching content only; Review Items are review content only. The link is `item_id`.

## Input Format

The JSON file contains a top-level array of two objects:
- **Questions object**: Has `sub_lessons[].questions` — MCQ items with integer `id`, `question`, `options[]`, `correct_answer` (0-based index)
- **Stages object**: Has `sub_lessons[].stages` — lesson stages (INFORMATION, FILL_BLANK, REVEAL, MATCHING) with `config` containing `item_id` references (integers matching question `id`s)

Detection: check for `questions` vs `stages` key in `sub_lessons[0]`.

---

## Part A: Remove the Review Item Extraction Pipeline

This is a prerequisite cleanup before the import feature. Removes ~1,650 lines of code + ~1,200 lines of tests.

### A1. Delete `memora_admin/api/review_items.py` (entire file)

Contains: `sync_review_items()`, `_extract_question()`, `_compute_lesson_content_hash()`, `resync_all_review_items()`, `delete_review_items_for_lesson()`, `_delete_review_items_and_memory_state()`

**Before deleting**: Relocate `delete_review_items_for_lesson()` and `_delete_review_items_and_memory_state()` to a new home (see A5).

### A2. Delete `memora_admin/events/review_item_sync.py` (entire file)

Contains: `on_lesson_save()` (dirty set producer), `on_lesson_trash()` (cleanup on delete)

### A3. Edit `memora_admin/hooks.py`

Remove from `Memora Lesson` doc_events:
- `on_update`: remove `"memora_admin.events.review_item_sync.on_lesson_save"`
- `on_trash`: remove `"memora_admin.events.review_item_sync.on_lesson_trash"` → replace with new handler (see A5)

Remove from `scheduler_events` (`*/2 * * * *`):
- `"memora_admin.tasks.sync.sync_dirty_review_items"`

Add to `Memora Review Item` doc_events (existing hooks stay, add new one):
- `after_insert`: add `"memora_admin.events.build_trigger.on_review_item_changed"`
- `on_update`: add `"memora_admin.events.build_trigger.on_review_item_changed"`
- `on_trash`: add `"memora_admin.events.build_trigger.on_review_item_changed"`

### A4. Edit `memora_admin/tasks/sync.py`

Remove the `sync_dirty_review_items()` function (~47 lines) and its `DIRTY_REVIEW_ITEMS_KEY` import.

### A5. Create `memora_admin/events/lesson_cleanup.py` (new, minimal)

Relocate the lesson trash cleanup logic — when a lesson is deleted, its Review Items and their associated Memory State / Practice Log records must be cleaned up.

```python
def on_lesson_trash(doc, method):
    """Delete Review Items (and cascade to Memory State + Practice Log) when a lesson is trashed."""
    items = frappe.get_all("Memora Review Item", filters={"lesson": doc.name}, pluck="name")
    if not items:
        return
    _delete_review_items_and_memory_state(items)

def _delete_review_items_and_memory_state(item_ids):
    # Moved from review_items.py — partition-aware Memory State cleanup + Practice Log cleanup
    ...
```

Register in hooks.py: `"on_trash": ["memora_admin.events.build_trigger.on_content_updated", "memora_admin.events.lesson_cleanup.on_lesson_trash"]`

### A6. Rewire challenge question rebuild to Review Item hooks

`rebuild_challenge_questions_for_lesson()` in `build_trigger.py:716` currently has **one caller**: `sync_dirty_review_items()` (being removed in A4). This function rebuilds per-topic challenge question JSON files after Review Items change.

**What to do**: Add a new handler `on_review_item_changed(doc, method)` to `build_trigger.py` that calls `rebuild_topic_question_file(doc.topic)` with debounce. Register it on `Memora Review Item` `after_insert`, `on_update`, and `on_trash` (see A3).

This is separate from the existing `practice_content_trigger.on_review_item_changed` which regenerates *practice arena* content per subject — different concern, different output files.

```python
# In build_trigger.py — new function
def on_review_item_changed(doc, method):
    """Rebuild challenge question file when a Review Item changes."""
    if not doc.topic:
        return
    try:
        from memora_admin.memora_admin.services.build.challenge_questions import (
            rebuild_topic_question_file,
        )
        rebuild_topic_question_file(doc.topic)
    except Exception as e:
        frappe.log_error(
            f"Failed to rebuild challenge questions for topic {doc.topic}: {e}",
            "Challenge Question Rebuild Error",
        )
```

After this, `rebuild_challenge_questions_for_lesson()` becomes dead code — delete it.

### A7. Remove `content_hash` field from `memora_lesson.json`

Remove from `field_order` and `fields` arrays. The column will be dropped automatically by Frappe on next `bench migrate`.

**Confirmed safe**: This field is used ONLY by the extraction pipeline debounce. The `content_hash` references in FastAPI (`stats.py`, `progress.py`) are about `SubjectHierarchy.content_hash` — a completely separate concept.

### A8. Delete `memora_admin/memora_admin/doctype/memora_review_item/test_memora_review_item.py`

All ~1,200 lines test the extraction pipeline. Entire file can be deleted.

### A9. Edit `fastapi_app/core/redis_keys.py` and `fastapi_app/core/constants.py`

- `redis_keys.py`: Remove `dirty_review_items_key()` function
- `constants.py`: Remove `dirty_review_items_key` import and `DIRTY_REVIEW_ITEMS_KEY` constant

**Confirmed safe**: `DIRTY_REVIEW_ITEMS_KEY` is only imported by `tasks/sync.py` (being cleaned in A4). No FastAPI endpoint or service reads from this set.

---

## Part B: Content Import Feature

### File Structure

**New files:**
```
memora_admin/api/content_import.py           # Backend: validate, preview, execute
memora_admin/public/js/content_import.js     # Frontend: full modal (single file, like game_lesson.js)
memora_admin/public/css/content_import.css   # Styles for the import modal
```

**Modified files:**
```
memora_admin/memora_admin/doctype/memora_topic/memora_topic.js   # Add "Import Content" button
memora_admin/hooks.py                                             # Add doctype_js + app_include_css
```

---

### B1. Backend API (`memora_admin/api/content_import.py`)

#### `validate_import_json(topic_name, json_data) → dict`

- Parse JSON, detect questions vs stages arrays by checking `sub_lessons[0]` keys
- Match sub_lessons by `title` between the two arrays
- Validate: JSON structure, question fields, stage_type existence (query `Memora Lesson Stage Settings` once), `item_id` cross-references
- Return merged lessons: `[{title, questions: [...], stages: [...]}]` with warnings/errors

#### `preview_import(topic_name, lessons_json) → dict`

- Dry-run summary: counts of lessons, stages, review items per lesson
- No DB writes

#### `execute_import(topic_name, lessons_json, id_to_uuid, mode) → dict`

1. **Permission check** — require System Manager or Memora Admin role
2. **Load topic hierarchy** — get `unit`, `track`, `subject` from topic doc
3. **Replace mode** — delete existing lessons via `frappe.delete_doc()` (triggers on_trash hooks → `build_trigger.on_content_updated` + `lesson_cleanup.on_lesson_trash`)
4. **UUID mapping** — receive `id_to_uuid` dict from frontend (UUIDs generated client-side)
5. **For each lesson**:
   a. Create `Memora Lesson` doc (`is_published=0`, `is_reviewable=1`, hierarchy from topic)
   b. Append stages from the stages array only (with integer `item_id`s replaced by mapped UUIDs in configs)
   c. `lesson_doc.insert(ignore_permissions=True)` → triggers `before_insert` (bit_index) + `on_update` (build trigger)
6. **Create Review Items directly** — for each question, create `Memora Review Item` with:
   - `item_id` = UUID from `id_to_uuid` mapping
   - `lesson` = created lesson's name
   - `subject`, `track`, `unit`, `topic` = from topic hierarchy
   - `question_text`, `choice_1..4` from options, `correct_choice` = `correct_answer + 1` (0-based → 1-based)
   - Each `.insert()` fires existing hooks: `practice_content_trigger.on_review_item_changed` (practice arena) + new `build_trigger.on_review_item_changed` (challenge questions). Both have debounce, so bulk inserts are efficient.
7. **Return** `{lessons_created, stages_created, review_items_created, lesson_names}`

**Item ID rewriting in stage configs**: Walk each stage's `config` dict recursively. Any string value that matches a key in `id_to_uuid` (comparing as strings) gets replaced with the UUID. This handles `highlights[].item_id`, matching pair references, etc.

---

### B2. Frontend — Modal Shell (`content_import.js`)

Loaded via `doctype_js` in `hooks.py` for Memora Topic. Single file following the `game_lesson.js` pattern.

#### Modal Structure

Uses `frappe.ui.Dialog` with CSS overrides for near-full-screen:

```css
.content-import-modal .modal-dialog { width: 95vw; max-width: 1200px; height: 90vh; }
.content-import-modal .modal-body { height: calc(90vh - 130px); overflow-y: auto; direction: rtl; }
```

#### State Object (single source of truth)

```javascript
state = {
    mode: "add",
    lessons: [],              // [{title, questions: [{id, question, options, correct_answer}], stages: [{stage_type, config}]}]
    id_to_uuid: {},           // {integer_id: "uuid-string"}
    current_lesson: 0,
    current_question: 0,
    reviewed_lessons: new Set()
}
```

#### Step Navigation

- Progress bar at top: 4 numbered steps with Arabic labels
- Previous/Next buttons in dialog footer
- Each step: `render(container)`, `validate() → bool`, `on_enter()`, `on_leave()`

---

### B3. Frontend Steps

#### Step 1 — Upload

- File drop zone (`<input type="file" accept=".json">`)
- Radio buttons: Add (إضافة) / Replace (استبدال)
- On file select: `FileReader.readAsText()` → `JSON.parse()` → call `validate_import_json` API
- On success: generate UUIDs via `crypto.randomUUID()` (with `generateItemUUID()` fallback from game_lesson.js), populate `state.lessons` and `state.id_to_uuid`
- Show topic name mismatch notice if applicable
- Show validation errors inline

#### Step 2 — Review Questions

- **Top**: Lesson tabs as buttons (title + question count badge). Reviewed = green highlight.
- **Center**: Single question editor:
  - Question number: "3 / 18"
  - Editable textarea for question text
  - 4 editable text inputs for options
  - Correct answer: radio buttons or clickable option highlight
  - Delete question button (with confirmation — also removes item_id references from stages)
- **Bottom**: Prev/Next question buttons
- **Keyboard**: → or Enter = next, ← = previous, navigates across lesson boundaries
- Lesson marked "reviewed" after admin visits at least one question
- All edits mutate `state.lessons[i].questions[j]` directly

#### Step 3 — Split Lessons

- **Left sidebar**: Lesson list with question counts. >15 questions = warning badge.
- **Main area**: Table for selected lesson:
  - Rows: question number, truncated question text
  - Drag-and-drop reordering via SortableJS (available in Frappe)
  - "Split here" icon between rows on hover → inserts divider, creates new lesson
  - Each group gets distinct background color + editable lesson name
  - Click divider to remove split
- **Stage auto-distribution**: Each stage follows its `item_id`s. Multi-`item_id` stages (e.g., MATCHING) go to the lesson with the majority of their `item_id`s. Tie → first lesson.
- Reordering questions reorders associated stages accordingly.

#### Step 4 — Confirm & Import

- Summary: topic name, lesson count, per-lesson stats
- Mode indicator with warning for Replace
- Calls `preview_import` API for server-validated summary
- "Import" button → calls `execute_import` with loading indicator
- On success: show result message, close modal, `frm.reload_doc()`
- On error: show error, allow Back to fix

---

### B4. Integration & Hooks

#### `memora_topic.js` — Add button

```javascript
if (!frm.is_new()) {
    frm.add_custom_button(__("استيراد محتوى"), () => {
        new ContentImportModal(frm);
    }, __("Actions"));
}
```

#### `hooks.py` — Register assets

```python
doctype_js = {
    ...existing...,
    "Memora Topic": "public/js/content_import.js",   # NEW
}

app_include_css = "/assets/memora_admin/css/content_import.css"  # NEW
```

---

## Build Pipeline (Automatic)

No new trigger code needed for lessons. When `lesson_doc.insert()` is called:
1. `before_insert` → assigns `bit_index` (existing `memora_lesson.py`)
2. `on_update` → `build_trigger.on_content_updated()` invalidates hierarchy cache + queues plan builds (2-min debounce)

For replace mode, `frappe.delete_doc()` fires `on_trash` → `build_trigger.on_content_updated` deletes lesson JSON from CDN + queues plan rebuilds.

For Review Items, the new `build_trigger.on_review_item_changed` hook rebuilds challenge question files per topic. The existing `practice_content_trigger.on_review_item_changed` hook regenerates practice arena content per subject. Both have debounce (Redis SET NX EX).

---

## Implementation Order

1. **Part A** (pipeline removal) — do first, it's a prerequisite
   - A1-A2: Delete `review_items.py`, `review_item_sync.py`
   - A3: Edit hooks.py (remove old hooks, add new Review Item hooks)
   - A4: Remove `sync_dirty_review_items()` from `tasks/sync.py`
   - A5: Create `lesson_cleanup.py` with relocated trash handler
   - A6: Add `on_review_item_changed()` to `build_trigger.py`, delete dead `rebuild_challenge_questions_for_lesson()`
   - A7: Remove `content_hash` field from `memora_lesson.json`
   - A8: Delete extraction pipeline tests
   - A9: Remove dirty key from `redis_keys.py` + `constants.py`
2. **Part B** (import feature)
   - B1: Backend API
   - B2: Frontend modal shell + CSS
   - B3: Frontend steps (Upload → Review → Split → Confirm)
   - B4: Integration (button, hooks)

---

## Verification

1. **After Part A**:
   - `bench migrate` succeeds (content_hash column dropped)
   - Lesson save no longer enqueues to dirty set
   - Lesson delete still cleans up Review Items + Memory State + Practice Log
   - Build pipeline still triggers on lesson save/delete
   - Existing Review Items remain intact in DB
   - Manually creating/editing a Review Item triggers challenge question rebuild + practice content regeneration

2. **After Part B**:
   - Upload valid JSON → navigate all 4 steps → import → verify lessons + review items in Frappe
   - Upload invalid JSON → verify error messages in Step 1
   - Edit questions in Step 2 → verify changes persist to Step 4
   - Split a lesson in Step 3 → verify stage distribution + question count updates
   - Replace mode → verify old lessons deleted, new ones created
   - Keyboard shortcuts in Step 2 (arrow keys)
   - RTL layout with Arabic content
   - Large import (30+ questions) performance
   - Build pipeline fires after import (check Memora Build Queue)
   - Challenge question files rebuilt after import (check topic question JSON)
