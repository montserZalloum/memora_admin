# Implementation Plan: Voucher Redemption Log Cleanup

**Branch**: `044-voucher-log-cleanup` | **Date**: 2026-03-11 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/044-voucher-log-cleanup/spec.md`

## Summary

Implement a daily scheduled cleanup task that permanently deletes `Memora Voucher Redemption Log` rows older than 100 days, using batched deletion (1000 rows per batch) with commit-per-batch for restart safety. Follows the established `task_log_archive_batch_cleanup.py` pattern exactly.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: Frappe framework
**Storage**: MariaDB (existing `tabMemora Voucher Redemption Log` table)
**Testing**: Frappe `FrappeTestCase` integration tests
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Single (Frappe app module)
**Performance Goals**: N/A — off-peak batch cleanup
**Constraints**: Batch size 1000, commit per batch to keep lock duration short
**Scale/Scope**: Single table cleanup, ~daily accumulation of rows

## Constitution Check

*No constitution defined (template placeholders only). No gates to evaluate.*

## Project Structure

### Documentation (this feature)

```text
specs/044-voucher-log-cleanup/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 research findings
├── data-model.md        # Data model documentation
├── quickstart.md        # Implementation quickstart
├── contracts/           # Function contracts
│   └── cleanup-task.md
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code

```text
memora_admin/
├── tasks/
│   └── voucher_log_cleanup.py    # NEW — cleanup task
├── hooks.py                       # MODIFY — add scheduler entry
└── tests/
    └── test_voucher_log_cleanup.py # NEW — integration tests
```

**Structure Decision**: Single module in `tasks/` following existing cleanup task conventions. No new directories needed.

## Complexity Tracking

No violations — straightforward single-file task following established patterns.
