---
phase: 35-allocation-distribution
plan: 01
subsystem: api
tags: [frappe-whitelist, allocation, approval-workflow, direct-sql, batch-counters]

# Dependency graph
requires:
  - phase: 33-voucher-schema
    provides: "Voucher Allocation DocType with status transitions, Voucher Card with library/allocation/sale_model fields, Customer voucher_requires_approval field"
  - phase: 34-batch-generation
    provides: "Generated voucher cards in Available status, batch with Generated status"
provides:
  - "fill_cards whitelisted method for auto-populating allocation child table"
  - "submit_allocation with conditional approval workflow branching"
  - "approve_allocation and reject_allocation for admin approval flow"
  - "on_update hook applying bulk card status changes on allocation completion"
  - "Batch allocated_count recount and Generated->Active transition"
affects: [35-02-allocation-distribution, 36-redemption, 37-invoicing]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Whitelisted API + on_update controller hook for status-driven side effects"
    - "Two-step save (Approved then Completed) to follow VALID_TRANSITIONS state machine"
    - "Direct SQL UPDATE with parameterized IN clause for bulk card operations"

key-files:
  created:
    - "memora_admin/memora_admin/api/allocation.py"
  modified:
    - "memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.py"

key-decisions:
  - "Two-step save for auto-approve (Draft->Approved->Completed) to respect VALID_TRANSITIONS map"
  - "status IN ('Available', 'Allocated') in _apply_allocation SQL for re-allocation support"
  - "frappe.db.count for batch allocated_count (recount, not increment/decrement) to avoid drift"
  - "Card-batch validation in both API layer (submit_allocation) and controller (validate) for defense-in-depth"

patterns-established:
  - "Allocation API pattern: whitelisted method modifies status, controller on_update applies side effects"
  - "Batch counter recount pattern: frappe.db.count after each allocation/return completion"

# Metrics
duration: 2min
completed: 2026-02-14
---

# Phase 35 Plan 01: Allocation API & Controller Summary

**Allocation API with fill_cards, submit/approve/reject workflow, and on_update hook applying bulk card status changes via direct SQL with batch counter management**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-14T10:12:07Z
- **Completed:** 2026-02-14T10:14:15Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Created 4 whitelisted API methods (fill_cards, submit_allocation, approve_allocation, reject_allocation) with conditional approval workflow
- Added on_update hook to allocation controller that bulk-updates card statuses via direct SQL when allocation reaches Completed
- Implemented batch counter recounting and Generated->Active status transition on first allocation
- Added card-batch validation in both API submit and controller validate for defense-in-depth

## Task Commits

Each task was committed atomically:

1. **Task 1: Create allocation API with fill_cards, submit, approve, reject methods** - `80030ec` (feat)
2. **Task 2: Add on_update hook to allocation controller for card status updates and batch counter management** - `c7d2b3b` (feat)

## Files Created/Modified

- `memora_admin/memora_admin/api/allocation.py` - 4 whitelisted methods: fill_cards (auto-fill child table), submit_allocation (approval branching), approve_allocation, reject_allocation
- `memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.py` - Added on_update with _apply_allocation, _apply_return, _update_batch_counters, _activate_batch_if_needed, _validate_cards_belong_to_batch

## Decisions Made

- **Two-step save for auto-approve:** Draft->Approved->Completed in separate saves to follow VALID_TRANSITIONS (Draft->Approved, Approved->Completed). The on_update hook fires on the Completed save.
- **Re-allocation via status IN ('Available', 'Allocated'):** The _apply_allocation SQL targets both Available and Allocated cards, allowing cards to be moved between libraries without returning to Available first (per research decision #3).
- **Recount, not increment:** Using frappe.db.count for batch allocated_count after each completion avoids counter drift from missed increments/decrements.
- **Defense-in-depth validation:** Card-batch validation exists in both api/allocation.py (submit_allocation) and the controller validate() method. API validation catches it with a clear error message; controller validation catches direct saves.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All 4 API methods ready for JS form buttons in Plan 02 (35-02)
- Controller hooks will fire automatically when API methods transition allocation to Completed
- Batch counter and status management is automatic on allocation completion
- STATE.md blocker "_handle_approval() commit behavior needs integration test in Phase 36" remains relevant -- the on_update hook pattern follows the established MemoraSubscriptionTransaction pattern

## Self-Check: PASSED

- FOUND: `memora_admin/memora_admin/api/allocation.py` (197 lines, above 80-line minimum)
- FOUND: `memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.py` (128 lines)
- FOUND: `.planning/phases/35-allocation-distribution/35-01-SUMMARY.md`
- FOUND: commit `80030ec` (Task 1)
- FOUND: commit `c7d2b3b` (Task 2)
- All 8 plan verifications passed via bench console

---
*Phase: 35-allocation-distribution*
*Completed: 2026-02-14*
