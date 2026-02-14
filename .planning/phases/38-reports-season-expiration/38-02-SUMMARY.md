---
phase: 38-reports-season-expiration
plan: 02
subsystem: reporting
tags: [frappe-script-report, sql-aggregation, commission, security-audit, scheduled-job, season-expiration]

# Dependency graph
requires:
  - phase: 38-01
    provides: "Report module directory structure, Script Report 4-file pattern"
  - phase: 37-financial-integration
    provides: "Commission service (resolve_commission, calculate_commission), sales_invoice custom field"
provides:
  - "Consignment Reconciliation script report with commission-adjusted amount due per library"
  - "Security Audit script report with failed redemption attempt grouping by player/IP/failure_type"
  - "Season expiration scheduled job expiring cards linked to ended/unpublished seasons"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns: ["Consignment-specific SQL with uninvoiced card counting", "Security audit grouping by player/IP/status", "5-table JOIN chain for batch-to-season resolution in scheduled job"]

key-files:
  created:
    - memora_admin/memora_admin/report/consignment_reconciliation/consignment_reconciliation.py
    - memora_admin/memora_admin/report/consignment_reconciliation/consignment_reconciliation.js
    - memora_admin/memora_admin/report/consignment_reconciliation/consignment_reconciliation.json
    - memora_admin/memora_admin/report/consignment_reconciliation/__init__.py
    - memora_admin/memora_admin/report/security_audit/security_audit.py
    - memora_admin/memora_admin/report/security_audit/security_audit.js
    - memora_admin/memora_admin/report/security_audit/security_audit.json
    - memora_admin/memora_admin/report/security_audit/__init__.py
    - memora_admin/tasks/season_expiration.py
  modified:
    - memora_admin/hooks.py

key-decisions:
  - "Consignment Reconciliation date filter uses c.modified (allocation date) not c.redeemed_at for full lifecycle visibility"
  - "Uninvoiced consignment cards displayed as normal informational data, not errors (monthly billing runs on 1st)"
  - "Season expiration expires cards if ANY batch grant links to ended/unpublished season (conservative approach)"

patterns-established:
  - "Security audit report pattern: GROUP BY player+IP+status with attempt_count DESC ordering"
  - "Batch-to-season 5-table JOIN chain: Batch -> Batch Grant -> Product Grant -> Academic Plan -> Season"

# Metrics
duration: 2min
completed: 2026-02-14
---

# Phase 38 Plan 02: Consignment Reconciliation, Security Audit Reports, and Season Expiration Summary

**Consignment reconciliation report with commission-adjusted amount due, security audit report with failed redemption grouping, and daily season expiration job via 5-table batch-to-season JOIN chain**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-14T14:14:40Z
- **Completed:** 2026-02-14T14:16:54Z
- **Tasks:** 2
- **Files created:** 9
- **Files modified:** 1

## Accomplishments
- Consignment Reconciliation report (RPT-03) shows allocated/redeemed/uninvoiced card counts per consignment library, calculates commission via existing service, and displays amount due as net revenue after commission
- Security Audit report (RPT-04) shows failed redemption attempts grouped by player/IP/failure_type, ordered by most attempts first, with report_summary showing total failures, unique players, and unique IPs
- Season expiration scheduled job (SCHED-01) finds batches via 5-table JOIN chain to seasons, bulk-updates only Available/Allocated cards to Expired with void_reason="Season Ended", registered at daily 01:05

## Task Commits

Each task was committed atomically:

1. **Task 1: Consignment Reconciliation and Security Audit Reports (RPT-03, RPT-04)** - `e5cacb1` (feat)
2. **Task 2: Season Expiration Scheduled Job (SCHED-01)** - `3ef34db` (feat)

## Files Created/Modified
- `memora_admin/memora_admin/report/consignment_reconciliation/consignment_reconciliation.py` - RPT-03 execute() with consignment SQL, commission post-processing, uninvoiced counting
- `memora_admin/memora_admin/report/consignment_reconciliation/consignment_reconciliation.js` - RPT-03 client filters (from_date, to_date, library)
- `memora_admin/memora_admin/report/consignment_reconciliation/consignment_reconciliation.json` - RPT-03 Script Report metadata (add_total_row=1, ref_doctype=Memora Voucher Card)
- `memora_admin/memora_admin/report/consignment_reconciliation/__init__.py` - Package init
- `memora_admin/memora_admin/report/security_audit/security_audit.py` - RPT-04 execute() with failed redemption grouping by player/IP/status
- `memora_admin/memora_admin/report/security_audit/security_audit.js` - RPT-04 client filters (from_date, to_date, player, failure_type)
- `memora_admin/memora_admin/report/security_audit/security_audit.json` - RPT-04 Script Report metadata (add_total_row=0, ref_doctype=Memora Voucher Redemption Log)
- `memora_admin/memora_admin/report/security_audit/__init__.py` - Package init
- `memora_admin/tasks/season_expiration.py` - SCHED-01 expire_season_cards() with 5-table JOIN, batch-level try/except, status guard
- `memora_admin/hooks.py` - Added cron entry "5 1 * * *" for season expiration

## Decisions Made
- Consignment Reconciliation date filter uses `c.modified` (covering allocation date) rather than `c.redeemed_at` to give full lifecycle visibility of all consignment cards in the date range
- Uninvoiced consignment cards are displayed as normal informational data, not flagged as errors -- the monthly billing job runs on the 1st, so mid-month uninvoiced cards are expected
- Season expiration uses ANY-grant-ended logic (conservative) -- if any batch grant links to an ended/unpublished season, the batch's non-terminal cards are expired

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All four Script Reports complete (Sales by Library, Batch Performance, Consignment Reconciliation, Security Audit)
- Season expiration job registered and ready for production scheduling
- Phase 38 (Reports & Season Expiration) fully complete -- this is the final phase of v3.0 Voucher Management System

## Self-Check: PASSED

- All 9 created files verified on disk
- Both task commits (e5cacb1, 3ef34db) verified in git log
- SUMMARY.md exists at expected path

---
*Phase: 38-reports-season-expiration*
*Completed: 2026-02-14*
