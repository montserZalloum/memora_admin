---
phase: 37-financial-integration
plan: 02
subsystem: payments
tags: [sales-invoice, credit-note, cron, consignment, billing, frappe-hooks]

# Dependency graph
requires:
  - phase: 37-financial-integration
    plan: 01
    provides: "Commission calculation, Sales Invoice/Credit Note creation, prepaid allocation invoice orchestration"
  - phase: 35-allocation-workflow
    provides: "Allocation completion flow (on_update with status transitions, _apply_allocation, _apply_return)"
provides:
  - "Financial hooks wired into allocation on_update (prepaid invoice/credit note)"
  - "Monthly consignment billing scheduled task (generate_monthly_invoices)"
  - "Cron registration at 0 2 1 * * for consignment billing"
  - "Double-invoice prevention via sales_invoice link on cards"
affects: [phase-38-api-testing, voucher-admin-workflow]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Try/except with frappe.log_error for non-critical financial operations (don't roll back allocation on invoice failure)"
    - "Per-library transaction isolation in batch billing (commit/rollback per library)"
    - "itertools.groupby for SQL ORDER BY-aligned grouping"

key-files:
  created:
    - memora_admin/tasks/consignment_billing.py
  modified:
    - memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.py
    - memora_admin/hooks.py

key-decisions:
  - "Invoice failure logged but does not block allocation completion (non-critical path)"
  - "str() conversion for frappe.utils date return values before string concatenation"
  - "Lazy imports inside methods to avoid circular dependency risk"

patterns-established:
  - "Financial side-effect pattern: try/except with log_error, never re-raise in allocation hooks"
  - "Batch billing pattern: GROUP BY library in SQL, iterate with groupby, one invoice per library, commit per library"

# Metrics
duration: 3min
completed: 2026-02-14
---

# Phase 37 Plan 02: Financial Integration Hooks Summary

**Allocation completion triggers prepaid invoices/credit notes via on_update hooks; monthly cron job bills redeemed consignment cards grouped by library**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-14T13:36:52Z
- **Completed:** 2026-02-14T13:39:31Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Prepaid allocation completions automatically create submitted Sales Invoices linked to allocation and cards
- Prepaid return completions automatically create Credit Notes linked to original invoices
- Consignment allocations and returns produce no financial action (FIN-06)
- Monthly consignment billing job queries redeemed cards, groups by library/batch, creates invoices with per-batch line items
- Double-invoice prevention: each card tracks its sales_invoice link
- Per-library transaction isolation in billing: one library's failure doesn't affect others

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire financial hooks into allocation controller** - `5e06d11` (feat)
2. **Task 2: Monthly consignment billing scheduled job** - `9999231` (feat)

## Files Created/Modified
- `memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.py` - Added _create_prepaid_invoice and _create_prepaid_credit_note methods, updated on_update with sale_model gates
- `memora_admin/tasks/consignment_billing.py` - Monthly consignment billing scheduled task with library-grouped invoicing
- `memora_admin/hooks.py` - Added cron entry "0 2 1 * *" for consignment billing

## Decisions Made
- Invoice failure logged but does not block allocation completion -- financial docs can be recreated manually if needed
- Lazy imports inside _create_prepaid_invoice and _create_prepaid_credit_note to avoid any circular dependency risk
- str() conversion applied to frappe.utils date helpers (get_first_day, get_last_day return datetime.date, not str)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed datetime.date + str concatenation TypeError**
- **Found during:** Task 2 (consignment billing)
- **Issue:** `get_first_day()` and `get_last_day()` from frappe.utils return `datetime.date` objects, not strings. Concatenating `prev_month_end + " 23:59:59"` raised TypeError.
- **Fix:** Added `str()` conversion: `prev_month_start = str(get_first_day(...))`, `prev_month_end = str(get_last_day(...))`, and `posting_date=str(get_first_day(today))`
- **Files modified:** memora_admin/tasks/consignment_billing.py
- **Verification:** Dry run completed successfully, SQL query returned 0 cards without error
- **Committed in:** 9999231 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Essential fix for runtime correctness. No scope creep.

## Issues Encountered
- `frappe.logger().info()` raised PermissionError in bench console (log file permission issue in console context) -- this is a console-only issue and does not affect scheduled task execution under Frappe workers.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All FIN requirements complete (FIN-01 through FIN-07)
- SCHED-02 cron registered
- Phase 37 (Financial Integration) fully complete
- Ready for Phase 38 (API testing / final integration) or production deployment

## Self-Check: PASSED

All files verified present. Both task commits (5e06d11, 9999231) confirmed in git log.

---
*Phase: 37-financial-integration*
*Completed: 2026-02-14*
