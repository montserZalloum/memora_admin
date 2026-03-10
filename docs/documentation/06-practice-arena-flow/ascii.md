# Practice Arena Full Flow (ASCII)

This document shows the current end-to-end practice flow in code, ending at the creation or update of a `tabMemora Practice Log` row.

Important:

- `GET /api/v1/practice/hierarchy` does not write Practice Log.
- `POST /api/v1/practice/start` does not write Practice Log.
- The Practice Log row is created or updated only on `POST /api/v1/practice/submit` or `POST /api/v1/practice/submit-continue`.

## 1. Content Hierarchy -> Review Item -> Practice Arena

```text
Memora content source
`-- Subject
    `-- Track
        `-- Unit
            `-- Topic
                `-- Lesson
                    `-- Stage
                        |-- QUESTION
                        |-- FILL_BLANK
                        |-- MATCHING
                        `-- INFORMATION / other stage types
                            |
                            `-- Review Item extraction
                                `-- tabMemora Review Item
                                    `-- Practice Arena reads questions from here
```

## 2. Full Runtime Flow Until Practice Log Record Exists

```text
Player opens Practice Arena
|
+-- 1) Browse selectable hierarchy
|   |
|   `-- GET /api/v1/practice/hierarchy
|       |
|       +-- fastapi_app/api/v1/endpoints/practice.py
|       |   `-- get_practice_hierarchy()
|       |
|       `-- PracticeService.get_practice_hierarchy()
|           |
|           +-- HierarchyService.get_hierarchy(subject_id)
|           |   `-- loads structural tree: track -> unit -> topic -> lesson
|           |
|           +-- _load_hierarchy_meta(subject_id)
|           |   |
|           |   +-- Redis cache
|           |   |   `-- memora:practice:hierarchy_meta:{subject_id}
|           |   |
|           |   `-- on cache miss -> Frappe RPC
|           |       `-- memora_admin.api.practice.get_practice_hierarchy_meta()
|           |           |
|           |           +-- reads Memora Subject / Track / Unit / Topic
|           |           `-- counts tabMemora Review Item rows per topic
|           |
|           +-- AccessService checks subject / track access
|           +-- optional completed filter via ProgressService bitmap
|           `-- returns hierarchy tree with:
|               |-- track_id / unit_id / topic_id
|               |-- item_count
|               `-- has_access
|
+-- 2) Start a practice session
|   |
|   `-- POST /api/v1/practice/start
|       |
|       +-- fastapi_app/api/v1/endpoints/practice.py
|       |   `-- start_practice()
|       |       |
|       |       +-- validates request shape
|       |       |   |-- tracks must be non-empty
|       |       |   |-- multi-track cannot also send units/topics
|       |       |   `-- multi-unit cannot also send topics
|       |       |
|       |       `-- calls PracticeService.start_session()
|       |
|       `-- PracticeService.start_session()
|           |
|           +-- load hierarchy
|           +-- _get_accessible_lessons()
|           |   |
|           |   +-- AccessService checks subject-level / track-level access
|           |   +-- free-content fallback for units/topics
|           |   `-- optional "completed" filter via ProgressService
|           |
|           +-- _get_topic_ids_for_lessons()
|           |
|           +-- select first batch
|           |   |
|           |   +-- optional Redis scope cache
|           |   |   `-- memora:practice:{player_id}:scope:{scope_token}
|           |   |
|           |   +-- Frappe RPC: prepare_practice_batch()
|           |   |   or count_practice_items_per_topic() + select_practice_candidates()
|           |   |
|           |   `-- Frappe SQL reads:
|           |       +-- tabMemora Review Item
|           |       `-- LEFT JOIN tabMemora Practice Log
|           |           |
|           |           +-- priority 0 = no Practice Log row for this player/item
|           |           `-- priority 1 = Practice Log row exists
|           |
|           +-- compute per-topic quotas
|           +-- build batch_0 questions
|           |
|           +-- create Redis session hash
|           |   `-- memora:practice:{player_id}
|           |       |
|           |       +-- subject_id
|           |       +-- filter
|           |       +-- tracks / units / topics
|           |       +-- schema_version = 4
|           |       +-- batch_seq = 0
|           |       +-- batch_0_item_ids
|           |       +-- accessible_lessons
|           |       +-- selected_topics
|           |       +-- session_started_at
|           |       +-- topic_counts / total_available
|           |       `-- submitted_{n} markers later
|           |
|           +-- create Redis served-items set
|           |   `-- memora:practice:{player_id}:served
|           |
|           `-- return first batch to mobile/web client
|               |
|               +-- batch_seq = 0
|               +-- questions[]
|               +-- total_available
|               `-- all_seen_warning
|                   |
|                   `-- still NO Practice Log write yet
|
+-- 3) Player answers batch_0
|   |
|   `-- client builds:
|       |-- batch_seq
|       `-- results[]
|           `-- {item_id, is_correct}
|
`-- 4) Submit results and write Practice Log
    |
    +-- POST /api/v1/practice/submit
    |   or POST /api/v1/practice/submit-continue
    |
    +-- fastapi_app/api/v1/endpoints/practice.py
    |   `-- submit_practice() or submit_and_continue_practice()
    |
    `-- PracticeService.submit_batch()
        |
        +-- load Redis session hash
        |   `-- memora:practice:{player_id}
        |
        +-- validate submit
        |   |
        |   +-- session must exist
        |   +-- batch_seq cannot skip ahead
        |   +-- duplicate item_ids in payload are rejected
        |   +-- item_ids must belong to batch_{n}_item_ids
        |   `-- if submitted_{n} already exists:
        |       `-- return cached response, do not write again
        |
        +-- call Frappe RPC
        |   `-- memora_admin.api.practice.upsert_practice_results()
        |       |
        |       +-- loop over submitted results
        |       +-- map is_correct -> last_result / correct_count delta
        |       `-- execute SQL UPSERT into tabMemora Practice Log
        |
        +-- cache submitted_{n} result in Redis
        +-- optionally prefetch next batch
        |
        `-- if endpoint was /submit-continue
            `-- continue_session()
                `-- activates next batch after successful submit
```

## 3. Exact Practice Log Write Path

```text
PracticeService.submit_batch()
`-- frappe.call("memora_admin.api.practice.upsert_practice_results", ...)
    `-- memora_admin/api/practice.py
        `-- upsert_practice_results(player_id, results, seen_at)
            |
            +-- build VALUES tuple per submitted result
            |   `-- (player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
            |
            `-- SQL
                |
                +-- INSERT INTO `tabMemora Practice Log`
                |   |
                |   +-- player_id
                |   +-- item_id
                |   +-- first_seen_at = seen_at
                |   +-- last_seen_at = seen_at
                |   +-- last_result = Correct / Incorrect
                |   +-- attempt_count = 1
                |   `-- correct_count = 1 or 0
                |
                `-- ON DUPLICATE KEY UPDATE
                    |
                    +-- last_seen_at = VALUES(last_seen_at)
                    +-- last_result = VALUES(last_result)
                    +-- attempt_count = attempt_count + 1
                    `-- correct_count = correct_count + VALUES(correct_count)
```

## 4. Final Record Shape

```text
tabMemora Practice Log
`-- PRIMARY KEY (player_id, item_id)
    |
    +-- player_id
    +-- item_id
    +-- first_seen_at
    +-- last_seen_at
    +-- last_result
    +-- attempt_count
    `-- correct_count
```

Meaning:

- first submit for `(player_id, item_id)` -> row is created
- later submit for the same `(player_id, item_id)` -> same row is updated

## 5. Main Files Behind This Flow

```text
fastapi_app/api/v1/endpoints/practice.py
`-- HTTP entrypoints for hierarchy, start, submit, submit-continue, continue

fastapi_app/services/practice.py
`-- main business flow:
    |-- get_practice_hierarchy()
    |-- start_session()
    |-- submit_batch()
    `-- continue_session()

memora_admin/api/practice.py
`-- Frappe-side SQL helpers:
    |-- get_practice_hierarchy_meta()
    |-- prepare_practice_batch()
    |-- select_practice_candidates()
    `-- upsert_practice_results()

memora_admin/memora_admin/setup.py
`-- creates tabMemora Practice Log table
```
