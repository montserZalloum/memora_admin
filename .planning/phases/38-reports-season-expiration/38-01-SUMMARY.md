---
phase: 38-reports-season-expiration
plan: 01
subsystem: reporting
tags: [frappe-script-report, sql-aggregation, commission, voucher-analytics]

# Dependency graph
requires:
  - phase: 37-financial-integration
    provides: "Commission service (resolve_commission, calculate_commission), sales_invoice custom field on Voucher Card"
provides:
  - "Sales by Library script report with commission/revenue breakdown per library"
  - "Batch Performance script report with card status distribution and season countdown"
  - "report/ module directory structure for future reports"
affects: [38-02-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Frappe Script Report 4-file pattern (py/js/json/__init__.py)", "SQL CASE/SUM aggregation for card status distribution", "MIN subquery for multi-grant season resolution"]

key-files:
  created:
    - memora_admin/memora_admin/report/__init__.py
    - memora_admin/memora_admin/report/sales_by_library/sales_by_library.py
    - memora_admin/memora_admin/report/sales_by_library/sales_by_library.js
    - memora_admin/memora_admin/report/sales_by_library/sales_by_library.json
    - memora_admin/memora_admin/report/sales_by_library/__init__.py
    - memora_admin/memora_admin/report/batch_performance/batch_performance.py
    - memora_admin/memora_admin/report/batch_performance/batch_performance.js
    - memora_admin/memora_admin/report/batch_performance/batch_performance.json
    - memora_admin/memora_admin/report/batch_performance/__init__.py
  modified: []

key-decisions:
  - "Sales by Library groups by library+batch+sale_model+invoice_status for granular commission calculation"
  - "Batch Performance uses MIN subquery for season end to handle multi-grant batches conservatively"
  - "add_total_row=0 for Batch Performance since totaling percentages and days_until_end is meaningless"

patterns-established:
  - "Script Report file structure: report/{name}/{name}.py + .js + .json + __init__.py"
  - "Post-processing commission in Python (not SQL) using existing service functions"
  - "MIN subquery pattern for resolving earliest season across batch grants"

# Metrics
duration: 2min
completed: 2026-02-14
---

# Phase 38 Plan 01: Script Reports Summary

**Two Frappe Script Reports (Sales by Library + Batch Performance) with SQL aggregation, commission service integration, and report_summary indicators**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-14T14:08:21Z
- **Completed:** 2026-02-14T14:10:47Z
- **Tasks:** 2
- **Files created:** 9

## Accomplishments
- Sales by Library report queries redeemed cards grouped by library/batch/sale_model with invoice status, calculates commission via existing service, and displays report_summary with total redeemed/revenue/commission
- Batch Performance report shows card status distribution (Available/Allocated/Redeemed/Void/Expired) per batch with SQL-computed redemption rate and season countdown via MIN subquery through batch grant chain
- Established the report/ module directory structure for the remaining two reports in plan 02

## Task Commits

Each task was committed atomically:

1. **Task 1: Sales by Library Script Report (RPT-01)** - `a6750e3` (feat)
2. **Task 2: Batch Performance Script Report (RPT-02)** - `d34860a` (feat)

## Files Created/Modified
- `memora_admin/memora_admin/report/__init__.py` - Report module package init
- `memora_admin/memora_admin/report/sales_by_library/sales_by_library.py` - RPT-01 execute() with SQL JOIN, commission post-processing, report_summary
- `memora_admin/memora_admin/report/sales_by_library/sales_by_library.js` - RPT-01 client filters (date range, library, sale_model)
- `memora_admin/memora_admin/report/sales_by_library/sales_by_library.json` - RPT-01 Script Report metadata (add_total_row=1)
- `memora_admin/memora_admin/report/sales_by_library/__init__.py` - Package init
- `memora_admin/memora_admin/report/batch_performance/batch_performance.py` - RPT-02 execute() with CASE/SUM aggregation, MIN season subquery
- `memora_admin/memora_admin/report/batch_performance/batch_performance.js` - RPT-02 client filters (batch, status)
- `memora_admin/memora_admin/report/batch_performance/batch_performance.json` - RPT-02 Script Report metadata (add_total_row=0)
- `memora_admin/memora_admin/report/batch_performance/__init__.py` - Package init

## Decisions Made
- Sales by Library groups by library+batch+sale_model+invoice_status for granular per-batch commission calculation (each batch may have different face_value and commission terms)
- Batch Performance uses add_total_row=0 because totaling percentages and days_until_end across batches is meaningless
- Season end resolved via MIN subquery to handle batches with multiple grants pointing to different seasons (conservative: earliest end date wins)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- report/ directory structure established, ready for plan 02 (Consignment Reconciliation + Security Audit reports)
- Both reports verified via bench execute (no import errors, correct 5-tuple return)

## Self-Check: PASSED

- All 9 created files verified on disk
- Both task commits (a6750e3, d34860a) verified in git log
- SUMMARY.md exists at expected path

---
*Phase: 38-reports-season-expiration*
*Completed: 2026-02-14*
