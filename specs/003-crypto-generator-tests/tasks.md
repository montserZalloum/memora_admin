# Tasks: Voucher Crypto & Generator Unit Tests

**Input**: Design documents from `/specs/003-crypto-generator-tests/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/test-matrix.md, quickstart.md

**Tests**: This entire feature IS tests. All tasks produce test code.

**Organization**: Tasks are grouped by user story (one test class per story) to enable independent implementation and validation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Test files**: `memora_admin/memora_admin/tests/`
- **Source under test**: `memora_admin/memora_admin/services/voucher/generator.py`, `memora_admin/memora_admin/services/voucher/crypto.py`
- **Existing infra**: `voucher_test_base.py`, `voucher_fixtures.py`, `voucher_helpers.py` in test directory

---

## Phase 1: Setup

**Purpose**: Verify all prerequisites are in place before writing tests

- [x] T001 Verify prerequisites: confirm `cryptography` package is installed (`pip show cryptography`), `voucher_hmac_secret` is set in site config, existing Phase 2 test infrastructure files exist (`voucher_test_base.py`, `voucher_fixtures.py`, `voucher_helpers.py`), and source files under test exist (`services/voucher/generator.py`, `services/voucher/crypto.py`). Document any missing prerequisites before proceeding.

---

## Phase 2: Foundational (Test File Scaffolding)

**Purpose**: Create both test files with imports, constants, and empty class stubs so that user story phases can fill in test methods independently

**CRITICAL**: No user story work can begin until both files are created with correct imports and class declarations.

- [x] T002 Create `memora_admin/memora_admin/tests/test_generator.py` with: module docstring, imports (`unittest`, `re`, `csv`, `io`, `frappe`, `FrappeTestCase`), import of SUT functions (`generate_pin`, `compute_hmac`, `reserve_serial_block`, `build_export_csv` from `memora_admin.memora_admin.services.voucher.generator`), import of `PIN_ALPHABET` constant, and 4 empty test class declarations: `TestGeneratePin(unittest.TestCase)`, `TestComputeHmac(unittest.TestCase)`, `TestBuildExportCsv(unittest.TestCase)`, `TestReserveSerialBlock(FrappeTestCase)`. Each class should have a `pass` body placeholder.
- [x] T003 [P] Create `memora_admin/memora_admin/tests/test_crypto.py` with: module docstring, imports (`unittest`, `cryptography.fernet.InvalidToken`), import of SUT functions (`encrypt_data`, `decrypt_data` from `memora_admin.memora_admin.services.voucher.crypto`), and empty `TestCrypto(unittest.TestCase)` class declaration with `pass` body.

**Checkpoint**: Both test files exist, are importable, and contain correct class stubs. Running `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_generator` should succeed with 0 tests found.

---

## Phase 3: User Story 1 — PIN Generation Correctness (Priority: P1)

**Goal**: Verify that `generate_pin()` produces secure, correctly formatted, and unique PINs (FR-001 through FR-004 + EC-1)

**Independent Test**: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_generator --case TestGeneratePin`

### Implementation

- [x] T004 [US1] Implement 5 test methods in `TestGeneratePin` class in `memora_admin/memora_admin/tests/test_generator.py`:
  1. `test_default_pin_length_is_12` (FR-001): Call `generate_pin()` with no args, assert `len(pin) == 12`
  2. `test_custom_pin_length` (FR-002): Call `generate_pin(14)` and `generate_pin(16)`, assert lengths match requested values
  3. `test_pin_contains_only_safe_characters` (FR-003): Generate a PIN, assert every character is in `PIN_ALPHABET`, assert none are in `{'0', 'O', '1', 'I', 'L'}`
  4. `test_1000_pins_are_unique` (FR-004): Generate 1000 PINs in a list, convert to set, assert `len(set) == 1000`
  5. `test_minimum_length_pin` (FR-002/EC-1): Call `generate_pin(1)`, assert length is 1 and character is in `PIN_ALPHABET`

**Checkpoint**: All 5 TestGeneratePin tests pass. PIN generation is validated.

---

## Phase 4: User Story 2 — HMAC Verification Integrity (Priority: P1)

**Goal**: Verify that `compute_hmac()` is deterministic, collision-resistant, and produces correct SHA-256 hex output (FR-005 through FR-008 + EC-3)

**Independent Test**: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_generator --case TestComputeHmac`

### Implementation

- [ ] T005 [US2] Implement 5 test methods in `TestComputeHmac` class in `memora_admin/memora_admin/tests/test_generator.py`:
  1. `test_hmac_is_deterministic` (FR-005): Compute HMAC of `"ABCDEF123456"` with secret `"test-secret"` twice, assert both results are identical
  2. `test_different_pins_produce_different_hmacs` (FR-006): Compute HMACs of `"ABCDEF123456"` and `"ZYXWVU987654"` with same secret `"test-secret"`, assert results differ
  3. `test_different_secrets_produce_different_hmacs` (FR-007): Compute HMACs of same PIN `"ABCDEF123456"` with secrets `"secret-a"` and `"secret-b"`, assert results differ
  4. `test_hmac_output_format` (FR-008): Compute HMAC, assert `len(result) == 64`, assert result matches regex `^[0-9a-f]{64}$`
  5. `test_hmac_with_empty_secret` (FR-005/EC-3): Compute HMAC with `secret=""`, assert result is valid 64-char hex string (empty strings are valid HMAC keys)

**Checkpoint**: All 5 TestComputeHmac tests pass. HMAC computation is validated.

---

## Phase 5: User Story 3 — Serial Number Reservation Correctness (Priority: P1)

**Goal**: Verify that `reserve_serial_block()` produces contiguous, correctly formatted serial blocks via atomic `tabSeries` operations (FR-009 through FR-012 + EC-2)

**Independent Test**: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_generator --case TestReserveSerialBlock`

### Implementation

- [ ] T006 [US3] Implement 5 test methods in `TestReserveSerialBlock(FrappeTestCase)` class in `memora_admin/memora_admin/tests/test_generator.py`. This class uses `FrappeTestCase` (not `unittest.TestCase`) because it requires DB access to `tabSeries`. Add a `setUp` method that deletes any existing `VCH-SERIAL` row from `tabSeries` (via `frappe.db.sql("DELETE FROM tabSeries WHERE name = 'VCH-SERIAL'")`) to ensure a clean series state for each test. Tests:
  1. `test_first_block_starts_at_one` (FR-009): Call `reserve_serial_block(3)`, assert first serial is `"VCH-000001"`
  2. `test_consecutive_blocks_are_contiguous` (FR-010): Reserve block of 3, then block of 2; assert second block starts at `"VCH-000004"` (immediately after first block's last serial `"VCH-000003"`)
  3. `test_serial_format` (FR-011): Reserve a block, assert every serial matches regex `^VCH-\d{6}$`
  4. `test_exact_count_returned` (FR-012): Call `reserve_serial_block(5)`, assert `len(result) == 5`
  5. `test_zero_count_returns_empty_list` (FR-012/EC-2): Call `reserve_serial_block(0)`, assert result is `[]`

**Checkpoint**: All 5 TestReserveSerialBlock tests pass. Serial reservation is validated.

---

## Phase 6: User Story 4 — CSV Export Integrity (Priority: P2)

**Goal**: Verify that `build_export_csv()` produces correctly structured CSV output with proper headers and data (FR-013 through FR-015 + EC-4)

**Independent Test**: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_generator --case TestBuildExportCsv`

### Implementation

- [ ] T007 [US4] Implement 4 test methods in `TestBuildExportCsv` class in `memora_admin/memora_admin/tests/test_generator.py`. Use `csv.reader` on `io.StringIO` to parse the UTF-8 decoded output of `build_export_csv()`. Test data: 2 cards with `[{"serial_no": "VCH-000001", "pin": "ABCDEF123456"}, {"serial_no": "VCH-000002", "pin": "GHJKMN234567"}]`, product_names `"Test Product"`, face_value `"10.00"`. Tests:
  1. `test_csv_header_row` (FR-013): Parse CSV output, assert first row equals `["serial_no", "pin", "product_names", "face_value"]`
  2. `test_csv_row_count` (FR-014): Build CSV from 2 cards, parse all rows, assert total row count is 3 (1 header + 2 data)
  3. `test_csv_content_matches_input` (FR-015): Parse CSV data rows, assert row[0] serial and PIN match input card data, assert product_names and face_value columns match
  4. `test_empty_cards_produces_header_only` (FR-014/EC-4): Build CSV with empty cards list `[]`, parse rows, assert exactly 1 row (header only)

**Checkpoint**: All 4 TestBuildExportCsv tests pass. CSV export is validated.

---

## Phase 7: User Story 5 — Export Encryption Roundtrip (Priority: P2)

**Goal**: Verify that `encrypt_data()`/`decrypt_data()` roundtrip preserves data and rejects wrong keys (FR-016 through FR-018)

**Independent Test**: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_crypto`

### Implementation

- [ ] T008 [P] [US5] Implement 3 test methods in `TestCrypto` class in `memora_admin/memora_admin/tests/test_crypto.py`. Test data: `plaintext = b"serial_no,pin\nVCH-000001,ABCDEF123456"`, `secret = "test-secret"`, `wrong_secret = "wrong-secret"`. Tests:
  1. `test_encrypt_decrypt_roundtrip` (FR-016): Encrypt plaintext with secret, decrypt result with same secret, assert decrypted bytes equal original plaintext
  2. `test_ciphertext_differs_from_plaintext` (FR-017): Encrypt plaintext, assert encrypted bytes `!=` plaintext bytes
  3. `test_wrong_secret_raises_error` (FR-018): Encrypt with `secret`, then `assertRaises(InvalidToken)` when decrypting with `wrong_secret`

**Checkpoint**: All 3 TestCrypto tests pass. Encryption roundtrip is validated.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Full suite validation, performance check, and final cleanup

- [ ] T009 Run full Phase 3 test suite: execute both `test_generator` and `test_crypto` modules via `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_generator` and `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_crypto`. Verify all 22 tests pass (SC-001). Verify execution completes in under 30 seconds (SC-003). Fix any failures.
- [ ] T010 Run quickstart.md validation: follow the exact commands in `specs/003-crypto-generator-tests/quickstart.md` to verify all documented run commands work correctly (full suite, single class, single test). Ensure the troubleshooting scenarios in quickstart.md are accurate.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Stories (Phases 3–7)**: All depend on Foundational phase completion
  - US1–US4 (Phases 3–6) share `test_generator.py` — execute sequentially within that file
  - US5 (Phase 7) uses `test_crypto.py` — **can run in parallel** with US1–US4
- **Polish (Phase 8)**: Depends on all user story phases being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2. No dependencies on other stories.
- **US2 (P1)**: Can start after Phase 2. No dependencies on other stories. Same file as US1 — execute after T004.
- **US3 (P1)**: Can start after Phase 2. No dependencies on other stories. Same file as US1/US2 — execute after T005.
- **US4 (P2)**: Can start after Phase 2. No dependencies on other stories. Same file as US1–US3 — execute after T006.
- **US5 (P2)**: Can start after Phase 2. **Different file** — can run in parallel with US1–US4.

### Within Each User Story

- Each story = one test class with all its methods
- Single task per story (class is small enough to implement atomically)
- Run class-specific tests immediately after implementation to validate

### Parallel Opportunities

- **T002 ∥ T003**: File scaffolding for `test_generator.py` and `test_crypto.py` (different files)
- **T004–T007 ∥ T008**: US1–US4 (generator tests) can run in parallel with US5 (crypto tests) since they target different files
- Within `test_generator.py` (T004–T007): Sequential — same file, but each class is independent

---

## Parallel Example: Foundational Phase

```
# These two file creation tasks can run in parallel:
Task T002: "Create test_generator.py scaffolding"
Task T003: "Create test_crypto.py scaffolding"
```

## Parallel Example: User Story Implementation

```
# Generator tests (sequential within same file) run in parallel with crypto tests:
Stream A (test_generator.py): T004 → T005 → T006 → T007
Stream B (test_crypto.py):    T008

# Stream B can start as soon as T003 completes (no dependency on T002 or T004–T007)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Verify prerequisites
2. Complete Phase 2: Create test file scaffolding
3. Complete Phase 3: Implement TestGeneratePin (US1)
4. **STOP and VALIDATE**: Run `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_generator --case TestGeneratePin`
5. 5 tests passing = MVP achieved

### Incremental Delivery

1. Setup + Foundational → Both test files exist with stubs
2. US1 (TestGeneratePin) → 5 tests passing → PIN generation validated
3. US2 (TestComputeHmac) → 10 tests passing → HMAC validated
4. US3 (TestReserveSerialBlock) → 15 tests passing → Serial reservation validated
5. US4 (TestBuildExportCsv) → 19 tests passing → CSV export validated
6. US5 (TestCrypto) → 22 tests passing → Encryption validated
7. Polish → Full suite green, performance verified

### FR-to-Task Traceability

| FR | Task | Test Method |
|----|------|-------------|
| FR-001 | T004 | `test_default_pin_length_is_12` |
| FR-002 | T004 | `test_custom_pin_length`, `test_minimum_length_pin` |
| FR-003 | T004 | `test_pin_contains_only_safe_characters` |
| FR-004 | T004 | `test_1000_pins_are_unique` |
| FR-005 | T005 | `test_hmac_is_deterministic`, `test_hmac_with_empty_secret` |
| FR-006 | T005 | `test_different_pins_produce_different_hmacs` |
| FR-007 | T005 | `test_different_secrets_produce_different_hmacs` |
| FR-008 | T005 | `test_hmac_output_format` |
| FR-009 | T006 | `test_first_block_starts_at_one` |
| FR-010 | T006 | `test_consecutive_blocks_are_contiguous` |
| FR-011 | T006 | `test_serial_format` |
| FR-012 | T006 | `test_exact_count_returned`, `test_zero_count_returns_empty_list` |
| FR-013 | T007 | `test_csv_header_row` |
| FR-014 | T007 | `test_csv_row_count`, `test_empty_cards_produces_header_only` |
| FR-015 | T007 | `test_csv_content_matches_input` |
| FR-016 | T008 | `test_encrypt_decrypt_roundtrip` |
| FR-017 | T008 | `test_ciphertext_differs_from_plaintext` |
| FR-018 | T008 | `test_wrong_secret_raises_error` |

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story = one test class, independently runnable via `--case` flag
- All tests except `TestReserveSerialBlock` use `unittest.TestCase` (no DB needed)
- `TestReserveSerialBlock` uses `FrappeTestCase` (needs `tabSeries` in MariaDB)
- Edge cases are embedded as separate test methods within their respective classes
- Total: 22 test methods across 5 classes in 2 files
- Commit after each phase checkpoint
