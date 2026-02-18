# Tasks: Fix Export For Print Includes Redeemed Cards

**Input**: Design documents from `/specs/020-fix-export-redeemed-cards/`
**Prerequisites**: plan.md (required), spec.md (required), research.md

**Tests**: Included — Constitution Principle VIII (Test-First Coverage) requires tests for all production code changes.

**Organization**: Tasks grouped by user story. All stories modify the same function (`export_for_print()` in `voucher.py`), so US2 and US3 depend on US1 completing first.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Test Infrastructure)

**Purpose**: Create the test file and base class for export filtering tests

- [X] T001 Create test file skeleton with imports and base class in memora_admin/memora_admin/tests/test_export_filtering.py — extend VoucherTestCase, add setUp that generates a 5-card batch via generate_batch_sync(), add helper methods: _set_card_status(serial_no, status) using direct SQL UPDATE, _export_and_parse() that calls export_for_print() and returns parsed CSV rows, _get_serial_nos() that returns list of serial_nos from batch

**Checkpoint**: Test file exists, base class setUp generates batch successfully, helper methods work

---

## Phase 2: User Story 1 — Export Excludes Non-Available Cards (Priority: P1)

**Goal**: The CSV download from "Export for Print" includes only cards with status = Available. Cards that are Redeemed, Void, Expired, or Allocated are excluded.

**Independent Test**: Generate batch, change some card statuses, export → verify CSV row count matches Available card count.

### Tests for User Story 1

> **Write these tests FIRST, ensure they FAIL before implementing T006**

- [X] T002 [P] [US1] Write test_export_excludes_redeemed_cards in memora_admin/memora_admin/tests/test_export_filtering.py — generate 5 cards, set 2 to Redeemed via _set_card_status(), call _export_and_parse(), assert len(rows) == 3, assert all returned serial_nos have status Available in DB
- [X] T003 [P] [US1] Write test_export_excludes_void_and_expired_cards in memora_admin/memora_admin/tests/test_export_filtering.py — generate 5 cards, set 1 to Void and 1 to Expired, export, assert len(rows) == 3
- [X] T004 [P] [US1] Write test_export_excludes_allocated_cards in memora_admin/memora_admin/tests/test_export_filtering.py — generate 5 cards, set 2 to Allocated, export, assert len(rows) == 3 (validates FR-002)
- [X] T005 [P] [US1] Write test_export_all_available_no_regression in memora_admin/memora_admin/tests/test_export_filtering.py — generate 5 cards, do NOT change any status, export, assert len(rows) == 5 (all cards present — regression guard)

### Implementation for User Story 1

- [X] T006 [US1] Modify export_for_print() in memora_admin/memora_admin/api/voucher.py — add `import csv` and `import io` at top; after decrypting csv_bytes (line 257), add: (1) parse CSV with csv.DictReader, (2) query `SELECT serial_no FROM tabMemora Voucher Card WHERE batch = %s AND status = 'Available'` to get available set, (3) filter rows where serial_no is in available set, (4) rebuild CSV bytes with csv.writer preserving header [serial_no, pin, product_names, face_value], (5) replace csv_bytes with filtered bytes before serving. Do NOT yet change the export_log card_count or add empty-batch guard (those are US2/US3).

**Checkpoint**: Tests T002-T005 pass. Existing tests `test_export_decrypts_correctly` and `test_export_audit_logged` still pass (regression).

---

## Phase 3: User Story 2 — Export Log Reflects Actual Exported Count (Priority: P2)

**Goal**: The export_log child table entry records the actual number of cards in the filtered CSV, not batch.generated_count.

**Independent Test**: Export a partially-redeemed batch, check export_log.card_count matches actual CSV row count.

### Tests for User Story 2

- [X] T007 [US2] Write test_export_log_count_matches_filtered in memora_admin/memora_admin/tests/test_export_filtering.py — generate 5 cards, set 2 to Redeemed, export, reload batch, assert last export_log entry card_count == 3

### Implementation for User Story 2

- [X] T008 [US2] Update export_for_print() in memora_admin/memora_admin/api/voucher.py — change the export_log append (around line 263) to use the actual filtered row count instead of batch.generated_count. Replace `"card_count": batch.generated_count` with `"card_count": len(filtered_rows)` (using the filtered row count variable from T006).

**Checkpoint**: Test T007 passes. All previous tests still pass.

---

## Phase 4: User Story 3 — Export Blocked When No Available Cards (Priority: P2)

**Goal**: When all cards in a batch are non-Available, the export returns an error instead of an empty file.

**Independent Test**: Set all cards to Redeemed/Void, click export → get error message.

### Tests for User Story 3

- [X] T009 [US3] Write test_export_no_available_cards_throws in memora_admin/memora_admin/tests/test_export_filtering.py — generate 5 cards, set ALL to Redeemed, call export_for_print(), assert frappe.throw raises with message containing "No available cards"

### Implementation for User Story 3

- [X] T010 [US3] Update export_for_print() in memora_admin/memora_admin/api/voucher.py — after filtering rows (from T006), before rebuilding CSV, add guard: if filtered_rows is empty, call frappe.throw("No available cards to export for this batch."). This must come BEFORE the CSV rebuild and export_log append so no log entry is created for a failed export.

**Checkpoint**: Test T009 passes. All previous tests still pass.

---

## Phase 5: Polish & Validation

**Purpose**: Edge case coverage, CSV format validation, and full regression check

- [X] T011 [P] Write test_export_mixed_statuses in memora_admin/memora_admin/tests/test_export_filtering.py — generate 5 cards, set one each to Allocated/Redeemed/Void/Expired (leaving 1 Available), export, assert len(rows) == 1 and the serial_no matches the Available card
- [X] T012 [P] Write test_export_csv_format_preserved in memora_admin/memora_admin/tests/test_export_filtering.py — export a batch, verify CSV header columns are exactly [serial_no, pin, product_names, face_value], verify each row has non-empty values for all 4 columns (validates FR-006)
- [X] T013 Run full regression suite: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_batch.test_memora_voucher_batch` and `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_export_filtering` — all tests must pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **US1 (Phase 2)**: Depends on Phase 1 — CORE FIX, blocks US2 and US3
- **US2 (Phase 3)**: Depends on Phase 2 (needs filtered_rows variable from T006)
- **US3 (Phase 4)**: Depends on Phase 2 (needs filtered_rows variable from T006)
- **US2 and US3 are independent of each other** — can run in parallel after US1
- **Polish (Phase 5)**: Depends on all user stories complete

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Implementation task references exact lines and variables
- Checkpoint validates both new and existing tests pass

### Parallel Opportunities

- T002, T003, T004, T005 can all run in parallel (different test methods, same file but no dependencies)
- T007 and T009 can run in parallel (after T006 completes)
- T011 and T012 can run in parallel (independent test methods)

---

## Parallel Example: User Story 1 Tests

```bash
# All US1 tests can be written simultaneously (they test the same fix from different angles):
Task: "Write test_export_excludes_redeemed_cards"
Task: "Write test_export_excludes_void_and_expired_cards"
Task: "Write test_export_excludes_allocated_cards"
Task: "Write test_export_all_available_no_regression"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: US1 tests (T002-T005) → implementation (T006)
3. **STOP and VALIDATE**: Run all tests — the core bug is fixed
4. This alone resolves the user's reported issue

### Incremental Delivery

1. US1 → Core fix deployed (CSV filtering works)
2. US2 → Audit accuracy (export_log card_count is correct)
3. US3 → Error guard (empty batch handled gracefully)
4. Polish → Edge cases and format validation confirmed

---

## Notes

- All tasks modify only 2 files: `voucher.py` (production) and `test_export_filtering.py` (tests)
- No schema changes, no new dependencies, no architectural changes
- Max batch size is 1,000 cards — filtering overhead is negligible (~4ms)
- The encrypted export file is NOT modified — it remains the master source of plaintext PINs
- `get_pins_from_export()` helper in voucher_helpers.py will automatically return only Available PINs after the fix — this is correct behavior for downstream test usage
