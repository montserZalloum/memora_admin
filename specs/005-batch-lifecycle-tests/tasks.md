# Tasks: Batch Lifecycle Integration Tests

**Input**: Design documents from `/specs/005-batch-lifecycle-tests/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/test-matrix.md, quickstart.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

**Target file**: All tasks modify a single file — `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py` (existing empty stub)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- All tasks target the same file, so no [P] markers apply

## Path Conventions

- **Test file**: `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- **Functions under test** (NOT modified):
  - `memora_admin/memora_admin/api/voucher.py` — `generate_batch()`, `generate_cards_job()`, `export_for_print()`
  - `memora_admin/memora_admin/services/voucher/crypto.py` — `decrypt_data()`
  - `memora_admin/memora_admin/services/voucher/generator.py` — `compute_hmac()`
- **Test infrastructure** (NOT modified):
  - `memora_admin/memora_admin/tests/voucher_test_base.py` — `VoucherTestCase`
  - `memora_admin/memora_admin/tests/voucher_fixtures.py` — `make_product_grant()`, `make_batch()`
  - `memora_admin/memora_admin/tests/voucher_helpers.py` — `generate_batch_sync()`, `get_card_statuses()`, `assert_batch_counters()`

---

## Phase 1: Setup

**Purpose**: Replace the empty test stub with imports, class declaration, and shared constants

- [X] T001 Populate test file with imports (`frappe`, `os`, `re`, `unittest.mock.patch`, `csv`, `io`), import test infrastructure (`VoucherTestCase`, fixture factories, helpers), import functions under test (`generate_batch`, `generate_cards_job`, `export_for_print`, `MAX_BATCH_QUANTITY`), and declare `TestMemoraVoucherBatch(VoucherTestCase)` class in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared setUp method that creates common fixtures needed by most tests

**CRITICAL**: No user story tests can work without this phase complete

- [X] T002 Implement `setUp()` method that creates a product grant via `make_product_grant(season="SEAS-00027")` and a Draft batch via `make_batch(grants=[grant.name], quantity=10)`, storing both as `self.grant` and `self.batch` in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`

**Checkpoint**: Test class structure ready — individual test methods can now be added

---

## Phase 3: User Story 1 — Happy Path Generation Validation (Priority: P1) MVP

**Goal**: Verify that the core card generation workflow produces correct output — card count, status transition, counters, encrypted file, serial format, and HMAC storage

**Independent Test**: Run with `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_batch.test_memora_voucher_batch --test test_generate_creates_cards` (and similarly for each test below)

### Implementation for User Story 1

- [X] T003 [US1] Implement `test_generate_creates_cards` — call `generate_batch_sync(self.batch.name)`, then assert `get_card_statuses(self.batch.name)` returns `{"Available": 10}` (FR-001) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T004 [US1] Implement `test_generate_status_transition` — call `generate_batch_sync(self.batch.name)`, reload batch, assert `self.batch.status == "Generated"` (FR-002) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T005 [US1] Implement `test_generate_counters` — call `generate_batch_sync(self.batch.name)`, then call `assert_batch_counters(self, self.batch.name, generated_count=10, allocated_count=0, redeemed_count=0, voided_count=0, expired_count=0)` (FR-003) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T006 [US1] Implement `test_generate_encrypted_file` — call `generate_batch_sync(self.batch.name)`, reload batch, assert `self.batch.encrypted_file_url` is truthy and `os.path.exists(frappe.get_site_path(url.lstrip("/")))` (FR-004) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T007 [US1] Implement `test_generate_serial_format` — call `generate_batch_sync(self.batch.name)`, query all `Memora Voucher Card` records for the batch, assert every `serial_no` matches `r'^VCH-\d{6}$'` regex (FR-005) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T008 [US1] Implement `test_generate_hmac_stored` — call `generate_batch_sync(self.batch.name)`, query all cards, assert every card has non-empty `pin_hmac`; also verify the Memora Voucher Card DocType meta has no field named `pin` (FR-006) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`

**Checkpoint**: 6 happy path tests should pass — batch generation core is validated

---

## Phase 4: User Story 2 — Generation Guard Rails (Priority: P2)

**Goal**: Verify that the generation function rejects invalid inputs and prevents double-generation

**Independent Test**: Each guard rail test creates its own invalid scenario and asserts `frappe.ValidationError` is raised

### Implementation for User Story 2

- [X] T009 [US2] Implement `test_generate_non_draft_fails` — create a batch, generate it via `generate_batch_sync()`, then call `generate_batch(self.batch.name)` on the now-Generated batch and assert `frappe.ValidationError` is raised (FR-007) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T010 [US2] Implement `test_generate_zero_quantity_fails` — create a batch with `quantity=0` via `make_batch(grants=[self.grant.name], quantity=0)`, call `generate_batch(batch.name)`, assert `frappe.ValidationError` (FR-008) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T011 [US2] Implement `test_generate_exceeds_max_fails` — create a batch with `quantity=1001` via `make_batch(grants=[self.grant.name], quantity=1001)`, call `generate_batch(batch.name)`, assert `frappe.ValidationError` (FR-009) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T012 [US2] Implement `test_generate_no_hmac_secret_fails` — store `frappe.conf.voucher_hmac_secret`, set it to `""`, call `generate_batch(self.batch.name)` in a try block, assert `frappe.ValidationError`, restore original secret in `finally` (FR-010) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T013 [US2] Implement `test_generate_already_generated_fails` — call `generate_batch_sync(self.batch.name)` to generate cards, then call `generate_batch(self.batch.name)` again and assert `frappe.ValidationError` (FR-013) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`

**Checkpoint**: 11 tests should pass — generation is validated for both valid and invalid inputs

---

## Phase 5: User Story 3 — Export and Audit Trail (Priority: P3)

**Goal**: Verify that encrypted export decrypts to valid CSV matching generated cards, and that export actions are audit-logged

**Independent Test**: Generate a batch first, then call `export_for_print()` and verify CSV content and audit log entries

### Implementation for User Story 3

- [X] T014 [US3] Implement `test_export_decrypts_correctly` — call `generate_batch_sync(self.batch.name)`, set `frappe.session.user` to Administrator (System Manager role), call `export_for_print(self.batch.name)`, read CSV from `frappe.local.response.filecontent`, parse with `csv.reader`, verify serial numbers exist in DB and PIN column is present (FR-011) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`
- [X] T015 [US3] Implement `test_export_audit_logged` — call `generate_batch_sync(self.batch.name)`, count initial `export_log` rows, call `export_for_print(self.batch.name)`, reload batch, assert `len(self.batch.export_log)` increased by 1 (FR-012) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`

**Checkpoint**: 13 tests should pass — export integrity and audit trail confirmed

---

## Phase 6: User Story 4 — Rollback on Failure (Priority: P3)

**Goal**: Verify that a failed generation leaves no partial data — the operation is atomic

**Independent Test**: Monkeypatch `frappe.db.bulk_insert` to raise an exception, attempt generation, verify zero cards and Draft status

### Implementation for User Story 4

- [X] T016 [US4] Implement `test_generate_rollback_on_failure` — use `unittest.mock.patch("frappe.db.bulk_insert", side_effect=Exception("simulated failure"))`, call `generate_cards_job(self.batch.name)` wrapped in try/except, assert zero cards exist for the batch via `get_card_statuses()` returning empty dict, reload batch and assert `status == "Draft"` (FR-014) in `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py`

**Checkpoint**: All 14 tests should pass — complete batch lifecycle coverage achieved

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Validate the complete test suite and confirm quality criteria

- [X] T017 Run full batch lifecycle test suite via `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_batch.test_memora_voucher_batch` and verify all 14 tests pass
- [X] T018 Validate test execution completes within 30 seconds (SC-003) and confirm test isolation by running any single test independently via `--test` flag

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user story phases
- **User Story 1 (Phase 3)**: Depends on Phase 2 — each test needs the setUp fixtures
- **User Story 2 (Phase 4)**: Depends on Phase 2 — guard rail tests are independent of happy path tests
- **User Story 3 (Phase 5)**: Depends on Phase 2 — export tests are independent (they call `generate_batch_sync()` themselves)
- **User Story 4 (Phase 6)**: Depends on Phase 2 — rollback test is independent
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Independent — can start after Phase 2
- **US2 (P2)**: Independent — can start after Phase 2 (no dependency on US1)
- **US3 (P3)**: Independent — can start after Phase 2 (generates its own batch internally)
- **US4 (P3)**: Independent — can start after Phase 2 (generates its own batch internally)

### Within Each User Story

All tasks within a story are sequential (same file — no parallel opportunities)

### Parallel Opportunities

Since ALL tasks modify the same file (`test_memora_voucher_batch.py`), there are **no file-level parallel opportunities**. However, user stories are logically independent and can be tested independently once written.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (imports, class)
2. Complete Phase 2: Foundational (setUp method)
3. Complete Phase 3: User Story 1 (6 happy path tests)
4. **STOP and VALIDATE**: Run the 6 tests, confirm all pass
5. This alone covers FR-001 through FR-006

### Incremental Delivery

1. Phase 1 + 2 → Test class structure ready
2. Add US1 (Phase 3) → 6 tests → Validate independently (MVP!)
3. Add US2 (Phase 4) → 5 more tests → Validate (11 total)
4. Add US3 (Phase 5) → 2 more tests → Validate (13 total)
5. Add US4 (Phase 6) → 1 more test → Validate (14 total)
6. Phase 7 → Full validation and timing check

---

## Requirement Traceability

| FR     | Task | Test Method                          | User Story |
|--------|------|--------------------------------------|------------|
| FR-001 | T003 | test_generate_creates_cards          | US1        |
| FR-002 | T004 | test_generate_status_transition      | US1        |
| FR-003 | T005 | test_generate_counters               | US1        |
| FR-004 | T006 | test_generate_encrypted_file         | US1        |
| FR-005 | T007 | test_generate_serial_format          | US1        |
| FR-006 | T008 | test_generate_hmac_stored            | US1        |
| FR-007 | T009 | test_generate_non_draft_fails        | US2        |
| FR-008 | T010 | test_generate_zero_quantity_fails    | US2        |
| FR-009 | T011 | test_generate_exceeds_max_fails      | US2        |
| FR-010 | T012 | test_generate_no_hmac_secret_fails   | US2        |
| FR-011 | T014 | test_export_decrypts_correctly       | US3        |
| FR-012 | T015 | test_export_audit_logged             | US3        |
| FR-013 | T013 | test_generate_already_generated_fails | US2       |
| FR-014 | T016 | test_generate_rollback_on_failure    | US4        |

---

## Notes

- All 18 tasks target the same file — no [P] markers apply
- [Story] labels map tasks to user stories for traceability (US1–US4)
- Each user story is independently testable via `--test` flag
- Existing test infrastructure (Phase 2) is used as-is — no modifications needed
- Use existing season `SEAS-00027` for all fixture creation
- Commit after each phase completion for clean git history
