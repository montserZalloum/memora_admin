# Implementation Plan: Voucher Batch Counter Fixes & Auto-Close

**Branch**: `001-voucher-batch-fixes` | **Date**: 2026-02-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-voucher-batch-fixes/spec.md`

## Summary

Add `expired_count` tracking to voucher batches and implement automatic batch closure when all cards reach terminal states. The expiration job (`season_expiration.py`) currently expires cards but never updates batch counters. Additionally, no auto-close mechanism exists — batches stay Active forever even when all cards are consumed. This plan introduces a shared `_recount_and_maybe_close()` helper that recounts all batch counters from actual card states (idempotent) and transitions to Closed when zero non-terminal cards remain.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (ORM, background jobs, hooks), MariaDB
**Storage**: MariaDB via Frappe ORM, direct SQL for bulk updates
**Testing**: `frappe.tests.utils.FrappeTestCase` with pytest runner
**Target Platform**: Linux server (x.conanacademy.com)
**Project Type**: Single (Frappe app)
**Performance Goals**: Counter updates < 5ms (single COUNT query per status), auto-close check < 2ms
**Constraints**: Max 1000 cards per batch (bounded COUNT queries), no Redis involvement (pure MariaDB counters)
**Scale/Scope**: Voucher subsystem only — 6 files modified, 1 new helper module, 1 schema change

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevant? | Status | Notes |
|-----------|-----------|--------|-------|
| I. Cryptographic Security | No | PASS | No PIN generation, storage, or verification changes |
| II. Auditable Lifecycle | **Yes** | PASS | Adding `expired_count` counter, enforcing batch state machine (Active → Closed), no new state transitions — just triggering existing ones automatically |
| III. Financial Precision | No | PASS | No monetary calculations affected |
| IV. Self-Healing Architecture | No | PASS | No Redis changes; counters are MariaDB-only |
| V. Test-First Coverage | **Yes** | PASS | Tests will be created via `/speckit.tasks` phase (test infrastructure is GAP-07, but individual tests for this feature will be defined) |

**Constitution Gaps Addressed**: GAP-03 (batch auto-close), GAP-09 (season expiration counters)

**Post-Design Re-Check**: Counter recount approach uses `frappe.db.count()` (existing pattern from `void_batch`, `redeem_voucher`, `_update_batch_counters`). Auto-close uses `frappe.db.count()` for non-terminal cards. No new patterns introduced — consistent with Constitution Principle II (Auditable Lifecycle: "Batch counters MUST stay consistent with actual card states at all times").

## Project Structure

### Documentation (this feature)

```text
specs/001-voucher-batch-fixes/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── batch-counters.md
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── doctype/
│   └── memora_voucher_batch/
│       ├── memora_voucher_batch.json     # MODIFY: add expired_count field
│       └── memora_voucher_batch.py       # NO CHANGE (validation only)
├── api/
│   └── voucher.py                        # MODIFY: add auto-close after redeem + void_card; add allocated_count recount after redeem
├── services/
│   └── voucher/
│       └── batch_utils.py                # NEW: shared recount_and_maybe_close() helper
├── tasks/
│   └── season_expiration.py              # MODIFY: add counter recount + auto-close after expiring cards
└── doctype/
    └── memora_voucher_allocation/
        └── memora_voucher_allocation.py  # NO CHANGE (already recounts allocated_count correctly)
```

**Structure Decision**: All changes are within the existing `memora_admin/memora_admin/` Frappe module. One new file (`services/voucher/batch_utils.py`) provides a shared helper to avoid duplicating recount + auto-close logic across 3 call sites.

## Complexity Tracking

No constitution violations. No complexity justifications needed.
