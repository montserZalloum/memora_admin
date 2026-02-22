# Implementation Plan: Review Item Table

**Branch**: `024-review-item-table` | **Date**: 2026-02-22 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/024-review-item-table/spec.md`

## Summary

Create a `Memora Review Item` DocType to store denormalized review question data (question text, choices, correct answer) extracted from lesson stage `config_json`. This replaces N file lookups with a single SQL JOIN during review sessions. Items auto-populate on lesson save via a doc_event hook and cascade-delete when content is removed. The existing `GET /reviews/{subject}` endpoint is enriched with question data via a LEFT JOIN to the new table.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: Frappe Framework (ORM, DocType, hooks), FastAPI, Pydantic v2, redis.asyncio
**Storage**: MariaDB via Frappe ORM (standard DocType — NOT partitioned)
**Testing**: pytest + pytest-asyncio (FastAPI), FrappeTestCase (Frappe side)
**Target Platform**: Linux server (x.conanacademy.com)
**Project Type**: Dual architecture (Frappe admin + FastAPI sidecar)
**Performance Goals**: <5ms batch retrieval for 10 items (SC-001), 40M rows without degradation (SC-002)
**Constraints**: 100k concurrent students, sub-20ms game API, no Frappe ORM on Memory State table
**Scale/Scope**: ~40M review items (one per item_id across all lessons), 4 stage types

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applies? | Status | Notes |
|-----------|----------|--------|-------|
| I. Self-Healing Cache | No | PASS | Review Item table is in MariaDB (source of truth), not Redis. No cache layer needed — data is queried directly via SQL JOIN. |
| II. Sub-20ms Game API | Yes | PASS | The SQL JOIN adds ~2ms to the existing Memory State query. Total retrieval stays under 5ms (SC-001). No Frappe ORM in hot path — the `get_due_items` query uses raw SQL with the new LEFT JOIN. |
| III. Content Hierarchy Integrity | Yes | PASS | Review Items store denormalized hierarchy refs (subject, track, unit, topic, lesson) populated from the lesson's parent chain. Deletion cascades clean up Review Items and Memory State together. No bitmap impact. |
| IV. Double-Gate Access Control | No | PASS | Review Items are content data, not access grants. Access is still checked via the existing review endpoint before returning items. |
| V. Cryptographic Voucher Security | No | N/A | No voucher interaction. |
| VI. Financial Precision | No | N/A | No monetary calculations. |
| VII. Auditable State Machines | No | PASS | Review Item has no state machine — it's a denormalization cache with create/update/delete lifecycle only. |
| VIII. Test-First Coverage | Yes | PASS | Tests planned for: extraction logic (unit), sync on save (integration), batch retrieval (integration), cascade deletion (integration), FastAPI endpoint enrichment (endpoint). |

**Pre-design gate**: PASS — no violations.

### Post-Design Re-Check

| Principle | Status | Design Notes |
|-----------|--------|-------------|
| I. Self-Healing Cache | PASS | No Redis caching introduced. Review Item data lives in MariaDB only. |
| II. Sub-20ms Game API | PASS | LEFT JOIN to `tabMemora Review Item` on `name = BIN_TO_UUID(item_id)` — PK lookup, O(1). The existing raw SQL in `get_due_items()` is extended, not replaced. |
| III. Content Hierarchy Integrity | PASS | Hierarchy refs populated from `lesson.subject`, `lesson.track`, `lesson.unit`, `lesson.topic` (read-only computed fields on Memora Lesson). |
| VIII. Test-First Coverage | PASS | See contracts for test plan. |

**Post-design gate**: PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/024-review-item-table/
├── plan.md              # This file
├── research.md          # Phase 0: Research findings
├── data-model.md        # Phase 1: Entity schema and relationships
├── quickstart.md        # Phase 1: Setup and verification guide
├── contracts/           # Phase 1: API contracts
│   └── review-items-api.md
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
memora_admin/
├── memora_admin/memora_admin/
│   ├── doctype/
│   │   ├── memora_review_item/           # NEW DocType
│   │   │   ├── memora_review_item.json   # Schema (UUID PK, hierarchy refs, MCQ fields, content_json)
│   │   │   ├── memora_review_item.py     # Document class (minimal — validation only)
│   │   │   └── test_memora_review_item.py # Frappe-side tests (sync, deletion, extraction)
│   │   └── memora_settings/
│   │       └── memora_settings.json      # MODIFIED: add review_session_size field
│   ├── api/
│   │   ├── reviews.py                    # MODIFIED: get_due_items() adds LEFT JOIN
│   │   └── review_items.py              # NEW: sync_review_items(), delete_review_items_for_lesson()
│   └── events/
│       └── review_item_sync.py          # NEW: on_lesson_save(), on_lesson_trash() hooks
├── hooks.py                              # MODIFIED: add Memora Lesson doc_events for review sync
├── fastapi_app/
│   ├── models/
│   │   └── review.py                    # MODIFIED: DueItem adds question fields
│   ├── api/v1/endpoints/
│   │   └── reviews.py                   # MODIFIED: pass enriched data through
│   └── tests/
│       └── test_review_items.py         # NEW: FastAPI endpoint tests for enriched responses
```

**Structure Decision**: Follows existing dual-architecture pattern. Frappe side handles the DocType, extraction logic, and sync hooks. FastAPI side updates models and passes enriched data from the Frappe API.

## Complexity Tracking

No constitution violations — section not applicable.
