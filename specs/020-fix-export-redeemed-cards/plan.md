# Implementation Plan: Fix Export For Print Includes Redeemed Cards

**Branch**: `020-fix-export-redeemed-cards` | **Date**: 2026-02-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/020-fix-export-redeemed-cards/spec.md`

## Summary

The "Export for Print" function in `voucher.py:export_for_print()` serves the entire encrypted CSV as-is, including cards that have been redeemed, voided, allocated, or expired. The fix filters the decrypted CSV at export time, retaining only rows whose `serial_no` corresponds to cards with `status = 'Available'` in the database.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (ORM, whitelist API), `csv` (stdlib), `io` (stdlib)
**Storage**: MariaDB via Frappe ORM (card status lookup), encrypted file on disk (PIN source)
**Testing**: `FrappeTestCase` (bench run-tests)
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Web (Frappe admin panel)
**Performance Goals**: Export completes under 5 seconds for 1,000-card batch
**Constraints**: Max batch size = 1,000 cards; PIN plaintext only in encrypted file
**Scale/Scope**: Bug fix — 1 function modified, 1 test file created

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevant? | Status | Notes |
|-----------|-----------|--------|-------|
| I. Self-Healing Cache | No | N/A | No Redis involvement in export path |
| II. Sub-20ms Game API | No | N/A | This is a Frappe admin endpoint, not FastAPI |
| III. Content Hierarchy | No | N/A | Voucher domain, not content |
| IV. Double-Gate Access | No | N/A | Export uses System Manager role check, not player access gates |
| V. Cryptographic Voucher Security | **Yes** | **PASS** | PIN plaintext remains only in encrypted file; no DB storage of plaintext; decryption uses existing HKDF-derived Fernet key |
| VI. Financial Precision | No | N/A | No monetary calculations in export path |
| VII. Auditable State Machines | **Yes** | **PASS** | Export log `card_count` will now accurately reflect exported count; card state machine not modified |
| VIII. Test-First Coverage | **Yes** | **PASS** | New test file covers filtering behavior; existing tests unaffected |

**Post-Phase 1 Re-check**: Same — no design changes affect constitution compliance.

## Project Structure

### Documentation (this feature)

```text
specs/020-fix-export-redeemed-cards/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Root cause analysis and solution decision
├── quickstart.md        # Manual and automated testing guide
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # (Phase 2 - created by /speckit.tasks)
```

### Source Code (files modified)

```text
memora_admin/memora_admin/
├── api/
│   └── voucher.py                          # MODIFIED: export_for_print() — add filtering logic
└── tests/
    └── test_export_filtering.py            # NEW: tests for filtered export behavior
```

**Structure Decision**: This is a targeted bug fix within the existing Frappe module. Only one production file is modified (`voucher.py`) and one test file is added. No new modules, services, or architectural changes.

## Implementation Design

### Modified Function: `export_for_print()` (voucher.py:230)

**Current behavior**: Decrypt → serve as-is
**New behavior**: Decrypt → parse CSV → filter by Available serial_nos → rebuild CSV → serve

```
export_for_print(batch_name)
├── [existing] Role check (System Manager)
├── [existing] Load batch, read encrypted file
├── [existing] Decrypt to csv_bytes
├── [NEW] Parse CSV with csv.DictReader
├── [NEW] Query: SELECT serial_no FROM tabMemora Voucher Card
│         WHERE batch = %s AND status = 'Available'
├── [NEW] Build set of available serial numbers
├── [NEW] Filter CSV rows (keep only available)
├── [NEW] If zero rows → frappe.throw("No available cards to export")
├── [NEW] Rebuild CSV with csv.writer (same column format)
├── [MODIFIED] Log actual filtered count (not batch.generated_count)
└── [existing] Serve as file download
```

### New Imports (voucher.py)

```python
import csv
import io
```

### DB Query

```sql
SELECT serial_no
FROM `tabMemora Voucher Card`
WHERE batch = %s AND status = 'Available'
```

This uses the existing index on `batch` field. The `status` filter is cheap for ≤1,000 rows.

### Test Plan

New file: `memora_admin/memora_admin/tests/test_export_filtering.py`

| Test | Description | Validates |
|------|-------------|-----------|
| `test_export_excludes_redeemed_cards` | Generate 5 cards, set 2 to Redeemed, export → 3 rows | FR-001, SC-001 |
| `test_export_excludes_void_cards` | Generate 5 cards, set 1 to Void, export → 4 rows | FR-001, SC-001 |
| `test_export_excludes_allocated_cards` | Generate 5 cards, set 2 to Allocated, export → 3 rows | FR-002, SC-001 |
| `test_export_excludes_expired_cards` | Generate 5 cards, set 1 to Expired, export → 4 rows | FR-001, SC-001 |
| `test_export_mixed_statuses` | All 5 statuses present, export → only Available rows | FR-001, FR-002 |
| `test_export_all_available_no_regression` | All cards Available → all rows exported | FR-003 regression |
| `test_export_no_available_cards_throws` | All cards non-Available → error raised | FR-005, SC-003 |
| `test_export_log_count_matches_filtered` | 5 cards, 2 redeemed → export_log.card_count = 3 | FR-004, SC-002 |
| `test_export_csv_format_preserved` | Verify columns: serial_no, pin, product_names, face_value | FR-006 |

### Regression Impact

- `test_export_decrypts_correctly` (existing): Fresh batch, all Available → **no change** (all 10 rows exported)
- `test_export_audit_logged` (existing): Fresh batch → **no change** (card_count still = generated_count when all Available)
- `get_pins_from_export()` helper: Returns only Available PINs after fix → **correct behavior** for redemption test flows (you can only redeem Available/Allocated cards)

## Complexity Tracking

> No constitution violations. No complexity tracking needed.
