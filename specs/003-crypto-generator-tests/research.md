# Research: Voucher Crypto & Generator Unit Tests

**Phase 0 Output** | **Date**: 2026-02-15

## R1: Test File Organization — DB vs Pure Tests

**Decision**: Split into two test files by DB dependency.

**Rationale**: Functions in `generator.py` and `crypto.py` have fundamentally different dependency profiles:
- `generate_pin`, `compute_hmac`, `build_export_csv`, `create_encrypted_export` — pure functions, no DB needed
- `encrypt_data`, `decrypt_data` — pure functions, no DB needed
- `reserve_serial_block` — requires MariaDB (`tabSeries` with `FOR UPDATE`)

Mixing these in one `FrappeTestCase` class forces all tests through Frappe's heavier test setup even when unnecessary.

**Alternatives considered**:
1. **Single file, single class** — Simpler but slower; pure tests pay DB setup cost (~0.5s overhead)
2. **Three files (pin, serial, crypto)** — Too granular for ~18 tests; increases maintenance burden
3. **Two files by module** — Natural split, but `test_generator.py` would still mix DB and non-DB tests

**Final approach**: Two files:
- `test_generator.py` — Contains two test classes:
  - `TestGeneratePin(unittest.TestCase)` — PIN generation tests (no DB)
  - `TestComputeHmac(unittest.TestCase)` — HMAC tests (no DB)
  - `TestBuildExportCsv(unittest.TestCase)` — CSV export tests (no DB)
  - `TestReserveSerialBlock(FrappeTestCase)` — Serial tests (needs DB)
- `test_crypto.py` — Contains one test class:
  - `TestCrypto(unittest.TestCase)` — Encrypt/decrypt roundtrip tests (no DB)

## R2: Serial Reservation Test Isolation

**Decision**: Save and restore `tabSeries` state before/after each test.

**Rationale**: `reserve_serial_block` mutates `tabSeries` by incrementing the `VCH-SERIAL` counter. Tests must not leave side effects that affect other tests or production data.

**Approach**:
- In `setUp()`: Read current `VCH-SERIAL` value (or note its absence)
- In `tearDown()`: Restore original value (or delete the row if it didn't exist)
- This is safe because tests run within a Frappe test transaction that rolls back

**Alternatives considered**:
1. **Rely on FrappeTestCase rollback** — Frappe's `FrappeTestCase` wraps each test in a savepoint that auto-rolls back. This should handle cleanup automatically without manual save/restore.
2. **Use a separate series name** — Would require modifying the function under test, violating "test the real code" principle.

**Final approach**: Rely on `FrappeTestCase`'s automatic savepoint rollback. The `tabSeries` mutation happens within the test transaction and is automatically reverted. No manual save/restore needed.

## R3: PIN Uniqueness Test — Statistical Confidence

**Decision**: Test 1000 PINs for uniqueness (per FR-004).

**Rationale**: With a 12-char PIN from a 30-char alphabet, the keyspace is 30^12 ≈ 5.3 × 10^17. The probability of any collision in 1000 PINs is astronomically low (~10^-12 by birthday paradox). Testing 1000 PINs provides high confidence that the CSPRNG is working correctly without being slow.

**Alternatives considered**:
1. **100 PINs** — Too small to catch subtle seeding issues
2. **10,000 PINs** — Slower execution, negligible additional confidence
3. **1000 PINs** — Good balance: catches broken RNG, fast execution (~10ms)

## R4: HMAC Test Vectors

**Decision**: Use known input pairs rather than external test vectors.

**Rationale**: The function under test (`compute_hmac`) is a thin wrapper around `hmac.new(..., hashlib.sha256).hexdigest()`. Testing determinism (same input → same output) and sensitivity (different input → different output) is sufficient. We don't need NIST test vectors because we're testing the wrapper, not the cryptographic primitive.

**Test data**:
- PIN: `"ABCDEF123456"`, Secret: `"test-secret"` → assert deterministic
- PIN: `"ABCDEF123456"` vs `"ZYXWVU987654"` with same secret → assert different
- Same PIN with `"secret-a"` vs `"secret-b"` → assert different
- Any result → assert 64-char hex string (SHA-256 output)

## R5: Fernet Encryption Test Strategy

**Decision**: Test encrypt/decrypt roundtrip, ciphertext differs from plaintext, and wrong-key rejection.

**Rationale**: `encrypt_data`/`decrypt_data` wrap `Fernet.encrypt`/`Fernet.decrypt` with HKDF key derivation. Key behaviors to validate:
1. Roundtrip integrity (encrypt then decrypt returns original)
2. Ciphertext is not plaintext (encryption actually happened)
3. Wrong key raises `InvalidToken` (Fernet's built-in MAC check)

**Alternatives considered**:
1. **Test HKDF derivation separately** — Useful but `get_fernet_key` is an internal detail; testing through the public API (`encrypt_data`/`decrypt_data`) is more meaningful
2. **Test with empty data** — Edge case, but Fernet handles empty bytes fine; not high-value
3. **Test key determinism** — `get_fernet_key` with same secret produces same key; implicitly tested by roundtrip

## R6: CSV Export Validation Strategy

**Decision**: Parse CSV output back and validate structure + content.

**Rationale**: `build_export_csv` returns UTF-8 bytes. Tests should:
1. Decode bytes and parse with `csv.reader` to validate structure
2. Check header row matches expected columns exactly
3. Check row count = N+1 (header + data)
4. Check data content matches input

**Edge case**: Empty cards list should produce header-only CSV (1 row).

## R7: Edge Case Coverage

**Decision**: Include 4 edge case tests (from spec) distributed across test classes.

| Edge Case | Test Location | Expected Behavior |
|-----------|--------------|-------------------|
| `generate_pin(1)` — minimum length | `TestGeneratePin` | Single char from PIN_ALPHABET |
| `reserve_serial_block(0)` — zero count | `TestReserveSerialBlock` | Empty list returned |
| `compute_hmac(pin, "")` — empty secret | `TestComputeHmac` | Valid 64-char hex string |
| Empty cards list for CSV | `TestBuildExportCsv` | Header-only CSV (1 row) |

## R8: Test Base Class Usage

**Decision**: `VoucherTestCase` only for tests that need both DB AND voucher prerequisites (HMAC secret + Item). Serial reservation tests need DB but not necessarily the Item check — however, using `VoucherTestCase` ensures the HMAC secret is configured, which is required by `create_encrypted_export` tests. Use `FrappeTestCase` directly for serial tests.

**Final mapping**:
- `unittest.TestCase`: PIN generation, HMAC computation, CSV export, crypto encrypt/decrypt
- `FrappeTestCase`: Serial reservation (needs `tabSeries`)
- `VoucherTestCase`: Not needed for Phase 3 (no tests require MEMORA-VOUCHER-CARD Item)
