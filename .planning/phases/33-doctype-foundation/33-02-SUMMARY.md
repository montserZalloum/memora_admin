---
phase: 33-doctype-foundation
plan: 02
subsystem: database
tags: [frappe, doctype, voucher, state-machine, hmac, child-table]

# Dependency graph
requires:
  - phase: 33-doctype-foundation-01
    provides: Memora Voucher Batch DocType (batch link target for Card)
provides:
  - Memora Voucher Card DocType with 5-state lifecycle and pin_hmac security
  - Memora Voucher Allocation DocType with Allocate/Return type support
  - Memora Voucher Allocation Card child table linking allocations to individual cards
  - Card state machine enforcement (terminal states: Redeemed, Void, Expired)
  - Allocation state machine enforcement (terminal states: Rejected, Completed, Cancelled)
affects: [33-doctype-foundation-03, 34-card-generation, 35-allocation-workflow, 36-redemption-engine]

# Tech tracking
tech-stack:
  added: []
  patterns: [state-machine-validation, hidden-security-field, child-table-fetch-from, defense-in-depth-js]

key-files:
  created:
    - memora_admin/memora_admin/doctype/memora_voucher_card/memora_voucher_card.json
    - memora_admin/memora_admin/doctype/memora_voucher_card/memora_voucher_card.py
    - memora_admin/memora_admin/doctype/memora_voucher_card/memora_voucher_card.js
    - memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.json
    - memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.py
    - memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.js
    - memora_admin/memora_admin/doctype/memora_voucher_allocation_card/memora_voucher_allocation_card.json
  modified: []

key-decisions:
  - "Data fieldtype for pin_hmac (not Password) to enable WHERE clause queries for O(1) redemption lookup"
  - "index_web_pages_for_search=0 on Voucher Card to prevent 10K+ cards from polluting global search"
  - "Terminal states (Redeemed/Void/Expired) enforce immutability at the ORM level"

patterns-established:
  - "Hidden security field: hidden=1 + report_hide=1 + print_hide=1 in JSON, plus JS defense-in-depth frm.set_df_property"
  - "State machine pattern: module-level VALID_TRANSITIONS dict with set-based allowed transitions"
  - "Child table fetch_from pattern: read-only display fields that auto-populate from linked record"

# Metrics
duration: 2min
completed: 2026-02-14
---

# Phase 33 Plan 02: Voucher Card & Allocation DocTypes Summary

**Voucher Card with hidden pin_hmac, 5-state lifecycle machine, and Allocation DocType with Allocate/Return child table**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-14T08:13:44Z
- **Completed:** 2026-02-14T08:16:10Z
- **Tasks:** 2
- **Files modified:** 14

## Accomplishments
- Voucher Card DocType with hidden pin_hmac field (Data type, indexed for O(1) lookup, invisible in all Desk views)
- 5-state Card lifecycle (Available, Allocated, Redeemed, Void, Expired) with Python state machine enforcing terminal states
- Voucher Allocation DocType supporting both Allocate and Return workflows with 6-state approval flow
- Allocation Card child table linking individual cards to allocations with auto-populated serial_no and status

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Voucher Card DocType with state machine and security constraints** - `4bee9f0` (feat)
2. **Task 2: Create Voucher Allocation and Allocation Card DocTypes** - `6bcc774` (feat)

**Plan metadata:** `f3a831f` (docs: complete plan)

## Files Created/Modified
- `memora_admin/memora_admin/doctype/memora_voucher_card/__init__.py` - Package init
- `memora_admin/memora_admin/doctype/memora_voucher_card/memora_voucher_card.json` - Card schema with hidden pin_hmac, unique serial_no, 5-state status, indexed batch
- `memora_admin/memora_admin/doctype/memora_voucher_card/memora_voucher_card.py` - Card state machine: VALID_TRANSITIONS enforcing terminal states
- `memora_admin/memora_admin/doctype/memora_voucher_card/memora_voucher_card.js` - Defense-in-depth: JS hides pin_hmac, makes status read-only
- `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py` - Test stub
- `memora_admin/memora_admin/doctype/memora_voucher_allocation_card/__init__.py` - Package init
- `memora_admin/memora_admin/doctype/memora_voucher_allocation_card/memora_voucher_allocation_card.json` - Child table: istable=1, voucher_card link with fetch_from fields
- `memora_admin/memora_admin/doctype/memora_voucher_allocation_card/memora_voucher_allocation_card.py` - Document stub
- `memora_admin/memora_admin/doctype/memora_voucher_allocation_card/test_memora_voucher_allocation_card.py` - Test stub
- `memora_admin/memora_admin/doctype/memora_voucher_allocation/__init__.py` - Package init
- `memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.json` - Allocation schema: VALLOC autoname, Allocate/Return type, 6-state approval flow
- `memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.py` - Allocation state machine with auto-computed quantity
- `memora_admin/memora_admin/doctype/memora_voucher_allocation/memora_voucher_allocation.js` - Locks fields after Draft status
- `memora_admin/memora_admin/doctype/memora_voucher_allocation/test_memora_voucher_allocation.py` - Test stub

## Decisions Made
- **Data fieldtype for pin_hmac** -- Password fieldtype stores values in `__Auth` table which breaks WHERE queries; Data allows indexed lookup for O(1) redemption
- **index_web_pages_for_search=0 on Card** -- prevents 10K+ card records from being indexed in Frappe global search, critical for performance
- **Terminal states enforced at ORM level** -- Redeemed/Void/Expired cards and Rejected/Completed/Cancelled allocations cannot be modified, ensuring data integrity

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Voucher Card, Allocation, and Allocation Card DocTypes are ready for Plan 03 (Redemption Code & Subscription Transaction)
- Card state machine is ready for Phase 34 (Card Generation) which will create cards in Available state
- Allocation DocType is ready for Phase 35 (Allocation Workflow) which will implement the allocate/return logic
- pin_hmac field is ready for Phase 36 (Redemption Engine) which will write HMAC hashes and look them up

## Self-Check: PASSED

All 14 files verified present. Both task commits (4bee9f0, 6bcc774) verified in git log.

---
*Phase: 33-doctype-foundation*
*Completed: 2026-02-14*
