# Test-to-Requirement Traceability Matrix

**Phase 1 Output** | **Date**: 2026-02-15

## Test Files

| File | Test Class | Base Class | DB Required |
|------|-----------|------------|-------------|
| `test_generator.py` | `TestGeneratePin` | `unittest.TestCase` | No |
| `test_generator.py` | `TestComputeHmac` | `unittest.TestCase` | No |
| `test_generator.py` | `TestBuildExportCsv` | `unittest.TestCase` | No |
| `test_generator.py` | `TestReserveSerialBlock` | `FrappeTestCase` | Yes |
| `test_crypto.py` | `TestCrypto` | `unittest.TestCase` | No |

## Test Matrix: test_generator.py

### TestGeneratePin (4 tests)

| Test Name | FR | SC | Acceptance Scenario | Description |
|-----------|----|----|-------------------|-------------|
| `test_default_pin_length_is_12` | FR-001 | SC-001, SC-004 | US1-1 | `generate_pin()` returns exactly 12 characters |
| `test_custom_pin_length` | FR-002 | SC-001, SC-004 | US1-2 | `generate_pin(14)` and `generate_pin(16)` return correct lengths |
| `test_pin_contains_only_safe_characters` | FR-003 | SC-001, SC-004 | US1-3 | Every character in generated PIN is in `PIN_ALPHABET`; none are in `{0, O, 1, I, L}` |
| `test_1000_pins_are_unique` | FR-004 | SC-001, SC-004 | US1-4 | Set of 1000 generated PINs has cardinality 1000 |

**Edge case** (included in TestGeneratePin):

| Test Name | FR | Edge Case | Description |
|-----------|----|-----------| ------------|
| `test_minimum_length_pin` | FR-002 | EC-1 | `generate_pin(1)` returns single valid character |

### TestComputeHmac (4 tests)

| Test Name | FR | SC | Acceptance Scenario | Description |
|-----------|----|----|-------------------|-------------|
| `test_hmac_is_deterministic` | FR-005 | SC-001, SC-004 | US2-1 | Same PIN + secret → identical HMAC twice |
| `test_different_pins_produce_different_hmacs` | FR-006 | SC-001, SC-004 | US2-2 | Two distinct PINs with same secret → different HMACs |
| `test_different_secrets_produce_different_hmacs` | FR-007 | SC-001, SC-004 | US2-3 | Same PIN with two secrets → different HMACs |
| `test_hmac_output_format` | FR-008 | SC-001, SC-004 | US2-4 | Output is 64-char hex string matching `^[0-9a-f]{64}$` |

**Edge case** (included in TestComputeHmac):

| Test Name | FR | Edge Case | Description |
|-----------|----|-----------| ------------|
| `test_hmac_with_empty_secret` | FR-005 | EC-3 | Empty string secret still produces valid 64-char hex HMAC |

### TestReserveSerialBlock (4 tests)

| Test Name | FR | SC | Acceptance Scenario | Description |
|-----------|----|----|-------------------|-------------|
| `test_first_block_starts_at_one` | FR-009 | SC-001, SC-004 | US3-1 | Fresh series → first serial is `VCH-000001` |
| `test_consecutive_blocks_are_contiguous` | FR-010 | SC-001, SC-004 | US3-2 | Second block starts immediately after first block ends |
| `test_serial_format` | FR-011 | SC-001, SC-004 | US3-3 | All serials match `^VCH-\d{6}$` pattern |
| `test_exact_count_returned` | FR-012 | SC-001, SC-004 | US3-4 | `reserve_serial_block(N)` returns exactly N serials |

**Edge case** (included in TestReserveSerialBlock):

| Test Name | FR | Edge Case | Description |
|-----------|----|-----------| ------------|
| `test_zero_count_returns_empty_list` | FR-012 | EC-2 | `reserve_serial_block(0)` returns `[]` |

### TestBuildExportCsv (3 tests)

| Test Name | FR | SC | Acceptance Scenario | Description |
|-----------|----|----|-------------------|-------------|
| `test_csv_header_row` | FR-013 | SC-001, SC-004 | US4-1 | First row is `serial_no,pin,product_names,face_value` |
| `test_csv_row_count` | FR-014 | SC-001, SC-004 | US4-2 | N cards → N+1 rows total |
| `test_csv_content_matches_input` | FR-015 | SC-001, SC-004 | US4-3 | Parsed row data matches original input dicts |

**Edge case** (included in TestBuildExportCsv):

| Test Name | FR | Edge Case | Description |
|-----------|----|-----------| ------------|
| `test_empty_cards_produces_header_only` | FR-014 | EC-4 | Empty cards list → 1 row (header only) |

## Test Matrix: test_crypto.py

### TestCrypto (3 tests)

| Test Name | FR | SC | Acceptance Scenario | Description |
|-----------|----|----|-------------------|-------------|
| `test_encrypt_decrypt_roundtrip` | FR-016 | SC-001, SC-004 | US5-1 | `decrypt(encrypt(data, secret), secret) == data` |
| `test_ciphertext_differs_from_plaintext` | FR-017 | SC-001, SC-004 | US5-2 | `encrypt(data, secret) != data` |
| `test_wrong_secret_raises_error` | FR-018 | SC-001, SC-004 | US5-3 | `decrypt(encrypted, wrong_secret)` raises `InvalidToken` |

## Summary

| Metric | Value |
|--------|-------|
| Total tests | 18 (14 generator + 4 crypto, including edge cases embedded in base tests) |
| Tests requiring DB | 4-5 (serial reservation class) |
| Tests DB-free | 13-14 (PIN, HMAC, CSV, crypto) |
| FRs covered | FR-001 through FR-018 (100%) |
| SCs covered | SC-001, SC-004, SC-005 (SC-002 by design, SC-003 by execution) |
| Edge cases | 4 (all from spec) |

## Requirement Coverage Verification

| FR | Covered By | Status |
|----|-----------|--------|
| FR-001 | `test_default_pin_length_is_12` | Covered |
| FR-002 | `test_custom_pin_length`, `test_minimum_length_pin` | Covered |
| FR-003 | `test_pin_contains_only_safe_characters` | Covered |
| FR-004 | `test_1000_pins_are_unique` | Covered |
| FR-005 | `test_hmac_is_deterministic`, `test_hmac_with_empty_secret` | Covered |
| FR-006 | `test_different_pins_produce_different_hmacs` | Covered |
| FR-007 | `test_different_secrets_produce_different_hmacs` | Covered |
| FR-008 | `test_hmac_output_format` | Covered |
| FR-009 | `test_first_block_starts_at_one` | Covered |
| FR-010 | `test_consecutive_blocks_are_contiguous` | Covered |
| FR-011 | `test_serial_format` | Covered |
| FR-012 | `test_exact_count_returned`, `test_zero_count_returns_empty_list` | Covered |
| FR-013 | `test_csv_header_row` | Covered |
| FR-014 | `test_csv_row_count`, `test_empty_cards_produces_header_only` | Covered |
| FR-015 | `test_csv_content_matches_input` | Covered |
| FR-016 | `test_encrypt_decrypt_roundtrip` | Covered |
| FR-017 | `test_ciphertext_differs_from_plaintext` | Covered |
| FR-018 | `test_wrong_secret_raises_error` | Covered |
