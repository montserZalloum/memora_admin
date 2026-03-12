# Implementation Plan: Archive Job Cleanup

**Branch**: `045-archive-job-cleanup` | **Date**: 2026-03-12 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/045-archive-job-cleanup/spec.md`

## Summary

Implement a daily scheduled cleanup task that removes old terminal `Memora Archive Job` rows (`Purged` after 30 days, `Failed` after 90 days) from MariaDB. The task follows the established cleanup pattern used by `task_log_archive_batch_cleanup`, `sync_log_cleanup`, and `voucher_log_cleanup`: batched `DELETE` with per-batch commits, dependency safety check against `Memora Task Log Archive Batch`, and full observability integration (Prometheus metrics + Task Run Log).

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe ORM, `frappe.utils` (add_days, now_datetime)
**Storage**: MariaDB — `tabMemora Archive Job`, `tabMemora Task Log Archive Batch`
**Testing**: `FrappeTestCase` (Frappe's unittest-based integration test framework)
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Single Frappe app (`memora_admin`)
**Performance Goals**: Complete within 5 minutes for typical production volumes (SC-004)
**Constraints**: Batched deletes (500 rows), per-batch commit, no locks held across batches
**Scale/Scope**: Low volume — archive jobs accumulate slowly (one per doctype+scope+version per season)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevant? | Status | Notes |
|-----------|-----------|--------|-------|
| I. Self-Healing Cache | No | PASS | No Redis involvement — purely MariaDB cleanup |
| II. Sub-20ms Game API | No | PASS | Background task, not in request path |
| III. Content Hierarchy | No | PASS | No content structure changes |
| IV. Double-Gate Access | No | PASS | No access control changes |
| V. Cryptographic Voucher | No | PASS | No voucher operations |
| VI. Financial Precision | No | PASS | No monetary calculations |
| VII. Auditable State Machines | Yes | PASS | Only deletes rows already in terminal states (`Purged`, `Failed`). Does not transition states. Dependency check (FR-004) ensures active child batch rows are not orphaned. |
| VIII. Test-First Coverage | Yes | PASS | Full test suite required: zero-row, retention boundaries, status filtering, dependency safety, batching, idempotency, wrapper metrics. Follows existing `FrappeTestCase` pattern. |

**Pre-design gate: PASS** — No violations.

**Post-design re-check: PASS** — Design confirms:
- VII (State Machines): No state transitions introduced. Cleanup only removes terminal rows. Dependency subquery prevents orphaning active batch rows.
- VIII (Test-First): Test plan covers all acceptance scenarios from spec, plus batching mechanics, idempotency, and observability integration.

## Project Structure

### Documentation (this feature)

```text
specs/045-archive-job-cleanup/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/
├── tasks/
│   ├── task_utils.py                        # Existing — shared observability helpers
│   ├── task_log_archive_batch_cleanup.py     # Existing — reference pattern
│   └── archive_job_cleanup.py               # NEW — cleanup task implementation
├── tests/
│   ├── test_task_log_archive_batch_cleanup.py  # Existing — reference test suite
│   └── test_archive_job_cleanup.py             # NEW — cleanup task tests
└── hooks.py                                  # MODIFIED — add scheduler entry
```

**Structure Decision**: Follows the existing flat `tasks/` module layout. One new task file, one new test file, one hooks.py modification. No new directories or abstractions needed.

## Complexity Tracking

> No violations — section intentionally left empty.
