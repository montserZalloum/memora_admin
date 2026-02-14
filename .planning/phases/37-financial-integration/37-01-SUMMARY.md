---
phase: 37-financial-integration
plan: 01
subsystem: payments
tags: [decimal, sales-invoice, credit-note, erpnext, commission]

# Dependency graph
requires:
  - phase: 33-voucher-schema
    provides: "Voucher Batch, Voucher Card, Voucher Allocation DocTypes with commission fields"
  - phase: 35-allocation-workflow
    provides: "Allocation completion flow and card status management"
provides:
  - "Commission calculation service with Decimal precision (calculate_commission, resolve_commission)"
  - "Sales Invoice and Credit Note creation via ERPNext ORM (create_voucher_invoice, create_credit_note)"
  - "Prepaid allocation invoice orchestration (create_prepaid_allocation_invoice)"
  - "Prepaid return credit note orchestration (create_prepaid_return_credit_note)"
  - "sales_invoice Link field on Voucher Card and Voucher Allocation DocTypes"
  - "MEMORA-VOUCHER-CARD service Item in ERPNext"
affects: [37-02-PLAN, consignment-billing, allocation-workflow]

# Tech tracking
tech-stack:
  added: [decimal.Decimal, ERPNext Sales Invoice ORM]
  patterns: [Decimal-only financial arithmetic, ERPNext insert+submit pattern, priority chain resolution]

key-files:
  created:
    - memora_admin/memora_admin/services/voucher/commission.py
    - memora_admin/memora_admin/services/voucher/invoice.py
    - memora_admin/memora_admin/custom/invoice_fields.py
  modified:
    - memora_admin/memora_admin/setup.py

key-decisions:
  - "First batch grant with commission set wins (not per-grant resolution)"
  - "Decimal-to-float conversion only at ERPNext rate assignment point"
  - "Credit notes grouped by original invoice for returns spanning multiple invoices"

patterns-established:
  - "Commission priority chain: batch grant override > library Customer fields > zero"
  - "ERPNext invoice pattern: new_doc -> set fields -> insert(ignore_permissions=True) -> submit()"
  - "Bulk SQL UPDATE for linking invoice to cards (no per-card ORM save)"

# Metrics
duration: 4min
completed: 2026-02-14
---

# Phase 37 Plan 01: Financial Services Summary

**Commission calculation with Decimal precision, ERPNext Sales Invoice/Credit Note creation, and MEMORA-VOUCHER-CARD service Item for voucher billing**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-14T13:29:12Z
- **Completed:** 2026-02-14T13:33:38Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Commission calculation module using only decimal.Decimal with ROUND_HALF_UP for Percentage, Fixed Amount, and no-commission cases
- Priority chain resolver for commission: batch grant override > library Customer default > zero
- Sales Invoice creation and submission with MEMORA-VOUCHER-CARD line items via ERPNext ORM
- Credit Note creation with is_return=1 and return_against linking to original invoice
- Orchestration functions for prepaid allocation invoicing and prepaid return credit notes
- sales_invoice custom Link fields on Voucher Card and Voucher Allocation DocTypes
- MEMORA-VOUCHER-CARD service Item (Services group, non-stock, sales-only)

## Task Commits

Each task was committed atomically:

1. **Task 1: Commission calculation service and invoice custom fields** - `c861219` (feat)
2. **Task 2: Invoice and Credit Note creation service** - `39b6af5` (feat)

## Files Created/Modified
- `memora_admin/memora_admin/services/voucher/commission.py` - Commission calculation (calculate_commission) and priority chain resolution (resolve_commission) using decimal.Decimal
- `memora_admin/memora_admin/services/voucher/invoice.py` - Sales Invoice creation, Credit Note creation, prepaid allocation invoice orchestration, prepaid return credit note orchestration
- `memora_admin/memora_admin/custom/invoice_fields.py` - Custom sales_invoice Link fields on Voucher Card and Allocation DocTypes
- `memora_admin/memora_admin/setup.py` - Wires invoice fields and service item creation into _setup_voucher_schema()

## Decisions Made
- First batch grant with commission_type set wins the priority chain (not per-grant resolution) -- a single card has one face value, splitting commission by grant adds complexity with no business value
- Decimal-to-float conversion happens only at the ERPNext rate assignment point (float(item["rate"])) -- all prior arithmetic stays in Decimal
- For returns spanning multiple original invoices, credit notes are grouped per original invoice with correct return_against links

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Commission and invoice services ready for Plan 02 to wire into allocation completion flow
- MEMORA-VOUCHER-CARD Item exists for all invoice line items
- Custom fields in place for invoice tracking on cards and allocations
- Plan 02 will add consignment billing cron and hook invoice creation into allocation on_update

---
*Phase: 37-financial-integration*
*Completed: 2026-02-14*
