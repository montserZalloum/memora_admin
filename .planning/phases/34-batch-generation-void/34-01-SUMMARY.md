---
phase: 34-batch-generation-void
plan: 01
subsystem: api
tags: [fernet, hkdf, hmac-sha256, cryptography, voucher, csv-export]

requires:
  - phase: 33-doctype-foundation
    provides: "Voucher Batch and Voucher Card DocTypes with base schemas"
provides:
  - "Export Log child table DocType (Memora Voucher Batch Export Log)"
  - "Updated Voucher Batch schema with encrypted_file_url and export_log fields"
  - "services/voucher/crypto.py with HKDF-SHA256 key derivation and Fernet encrypt/decrypt"
  - "services/voucher/generator.py with PIN generation, HMAC, serial reservation, CSV export"
affects: [34-02-PLAN, 34-03-PLAN]

tech-stack:
  added: [cryptography (Fernet, HKDF)]
  patterns: [HKDF key derivation from HMAC secret, atomic serial block reservation via tabSeries FOR UPDATE]

key-files:
  created:
    - memora_admin/memora_admin/doctype/memora_voucher_batch_export_log/memora_voucher_batch_export_log.json
    - memora_admin/memora_admin/doctype/memora_voucher_batch_export_log/memora_voucher_batch_export_log.py
    - memora_admin/memora_admin/services/voucher/crypto.py
    - memora_admin/memora_admin/services/voucher/generator.py
  modified:
    - memora_admin/memora_admin/doctype/memora_voucher_batch/memora_voucher_batch.json

key-decisions:
  - "HKDF with fixed salt/info for Fernet key derivation (not PBKDF2) -- deterministic, versioned"
  - "PIN alphabet is 30 chars excluding ambiguous 0/O/1/I/L for print readability"
  - "Serial block reservation uses single FOR UPDATE lock for entire block, not per-card"

patterns-established:
  - "services/voucher/ module pattern: crypto.py for encryption, generator.py for card logic"
  - "HKDF_SALT versioning (memora-voucher-export-v1) enables future key rotation"

duration: 2min
completed: 2026-02-14
---

# Phase 34 Plan 01: Schema & Service Foundation Summary

**Export Log child table, Voucher Batch schema additions, and voucher service module with HKDF-derived Fernet encryption and CSPRNG PIN generation**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-14T09:21:13Z
- **Completed:** 2026-02-14T09:23:46Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Created Memora Voucher Batch Export Log child table DocType with exported_by, exported_at, card_count fields
- Extended Voucher Batch schema with encrypted_file_url (hidden) and Export History section with export_log table
- Built services/voucher/crypto.py with HKDF-SHA256 key derivation producing Fernet-compatible keys
- Built services/voucher/generator.py with PIN generation (secrets.choice), HMAC-SHA256, atomic serial block reservation, CSV builder, and encrypted export creation

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Export Log child table and update Voucher Batch schema** - `82676af` (feat)
2. **Task 2: Create voucher service module with crypto and generator utilities** - `7c747cf` (feat)

## Files Created/Modified
- `memora_admin/memora_admin/doctype/memora_voucher_batch_export_log/__init__.py` - Empty init for child table DocType
- `memora_admin/memora_admin/doctype/memora_voucher_batch_export_log/memora_voucher_batch_export_log.json` - Export Log child table schema (istable=1)
- `memora_admin/memora_admin/doctype/memora_voucher_batch_export_log/memora_voucher_batch_export_log.py` - Empty Document class
- `memora_admin/memora_admin/doctype/memora_voucher_batch/memora_voucher_batch.json` - Added encrypted_file_url, section_export_history, export_log fields
- `memora_admin/memora_admin/services/voucher/__init__.py` - Empty init for voucher service module
- `memora_admin/memora_admin/services/voucher/crypto.py` - HKDF key derivation, Fernet encrypt/decrypt
- `memora_admin/memora_admin/services/voucher/generator.py` - PIN generation, HMAC, serial reservation, CSV export

## Decisions Made
- HKDF with fixed versioned salt (`memora-voucher-export-v1`) for Fernet key derivation rather than PBKDF2 -- HKDF is designed for key derivation from high-entropy input (HMAC secret), while PBKDF2 is for low-entropy passwords
- PIN alphabet uses 30 characters excluding ambiguous glyphs (0/O, 1/I/L) for print readability on physical voucher cards
- Serial block reservation acquires a single FOR UPDATE lock for the entire block, not per-card, eliminating lock contention for large batches

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Export Log child table and Voucher Batch schema ready for Plan 02 (background generation job)
- crypto.py and generator.py functions ready for import by generation and export logic
- `voucher_hmac_secret` must be present in site_config.json (established in Phase 33)

## Self-Check: PASSED

- All 7 files verified present on disk
- Both task commits verified in git log (82676af, 7c747cf)
- Export Log child table has istable=1 and correct 3 fields
- Voucher Batch JSON contains encrypted_file_url and export_log fields
- Smoke tests passed: PIN generation, HMAC, Fernet roundtrip, CSV building
- bench migrate completed without errors

---
*Phase: 34-batch-generation-void*
*Completed: 2026-02-14*
