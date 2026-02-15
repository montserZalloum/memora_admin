# Feature Specification: Voucher Crypto & Generator Unit Tests

**Feature Branch**: `003-crypto-generator-tests`
**Created**: 2026-02-15
**Status**: Draft
**Input**: User description: "Phase 3: Unit Tests — Crypto & Generator (~18 tests)"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - PIN Generation Correctness (Priority: P1)

As a developer maintaining the voucher system, I need confidence that PIN generation produces secure, correctly formatted, and unique PINs so that voucher cards are cryptographically sound and free of ambiguous characters that could confuse end users.

**Why this priority**: PIN generation is the foundation of the voucher system. If PINs are predictable, duplicated, or contain ambiguous characters, the entire system's security and usability is compromised.

**Independent Test**: Can be fully tested by running the PIN generation function with various parameters and validating output format, character set, length, and uniqueness across a large batch.

**Acceptance Scenarios**:

1. **Given** a call to generate a PIN with default settings, **When** the PIN is returned, **Then** it is exactly 12 characters long
2. **Given** a call to generate a PIN with a custom length (e.g., 14 or 16), **When** the PIN is returned, **Then** it matches the requested length
3. **Given** a generated PIN, **When** each character is inspected, **Then** none are ambiguous characters (0, O, 1, I, L)
4. **Given** 1000 PINs generated in sequence, **When** they are collected into a set, **Then** all 1000 are unique (no collisions)

---

### User Story 2 - HMAC Verification Integrity (Priority: P1)

As a developer maintaining the voucher system, I need confidence that the HMAC computation is deterministic, collision-resistant, and produces correctly formatted output so that PIN verification during redemption works reliably and securely.

**Why this priority**: HMAC is the mechanism used to verify PINs at redemption time without storing plaintext PINs. If HMAC is non-deterministic or produces incorrect formats, redemption will silently fail.

**Independent Test**: Can be fully tested by computing HMACs with known inputs and verifying determinism, uniqueness across different inputs, and output format compliance.

**Acceptance Scenarios**:

1. **Given** the same PIN and secret, **When** HMAC is computed twice, **Then** both results are identical
2. **Given** two different PINs with the same secret, **When** HMACs are computed, **Then** the results differ
3. **Given** the same PIN with two different secrets, **When** HMACs are computed, **Then** the results differ
4. **Given** any PIN and secret, **When** HMAC is computed, **Then** the output is a 64-character hexadecimal string (SHA-256)

---

### User Story 3 - Serial Number Reservation Correctness (Priority: P1)

As a developer maintaining the voucher system, I need confidence that serial number reservation produces contiguous, correctly formatted blocks without gaps or collisions so that voucher cards have unique, traceable identifiers.

**Why this priority**: Serial numbers serve as the primary identifier for voucher cards. Gaps, collisions, or format errors would cause database integrity issues and break card lookup workflows.

**Independent Test**: Can be fully tested by calling the serial reservation function and validating the returned serial format, count, starting offset, and contiguity across consecutive calls.

**Acceptance Scenarios**:

1. **Given** a fresh series (no prior reservations), **When** a block is reserved, **Then** serials start at VCH-000001
2. **Given** a prior reservation, **When** a second block is reserved, **Then** the new block starts immediately after the last serial of the prior block (contiguous)
3. **Given** a reservation of N serials, **When** the results are inspected, **Then** each serial matches the `VCH-NNNNNN` zero-padded 6-digit format
4. **Given** a reservation request for N serials, **When** the result is returned, **Then** exactly N serials are in the list

---

### User Story 4 - CSV Export Integrity (Priority: P2)

As a developer maintaining the voucher system, I need confidence that CSV export construction produces correctly structured output with proper headers and accurate card data so that exported files can be reliably consumed by downstream processes (print shops, distribution partners).

**Why this priority**: CSV exports are the delivery mechanism for voucher cards to external partners. Incorrect headers or mismatched data would cause operational failures.

**Independent Test**: Can be fully tested by building a CSV from known card data and validating headers, row count, and content accuracy.

**Acceptance Scenarios**:

1. **Given** a call to build a CSV export, **When** the output is parsed, **Then** the first row contains headers: `serial_no,pin,product_names,face_value`
2. **Given** N cards of data, **When** the CSV is built, **Then** the output contains exactly N+1 rows (1 header + N data rows)
3. **Given** known serial numbers and PINs, **When** the CSV is built and parsed, **Then** each row's serial and PIN match the original input data

---

### User Story 5 - Export Encryption Roundtrip (Priority: P2)

As a developer maintaining the voucher system, I need confidence that the Fernet-based encryption/decryption roundtrip preserves data integrity and that encrypted output cannot be decrypted with the wrong secret, so that exported CSV files containing plaintext PINs are stored securely at rest.

**Why this priority**: Encrypted exports protect plaintext PINs on disk. If encryption is broken or decryption with wrong keys silently succeeds, PIN confidentiality is compromised.

**Independent Test**: Can be fully tested by encrypting known data, decrypting it, and verifying the roundtrip produces identical output, while also verifying wrong-key decryption fails.

**Acceptance Scenarios**:

1. **Given** plaintext data and a secret, **When** encrypted and then decrypted with the same secret, **Then** the result is identical to the original plaintext
2. **Given** plaintext data, **When** encrypted, **Then** the encrypted bytes differ from the original plaintext
3. **Given** data encrypted with one secret, **When** decryption is attempted with a different secret, **Then** an error is raised

---

### Edge Cases

- What happens when PIN length is set to the minimum (1 character)? The function should still produce a valid single-character PIN from the safe alphabet.
- What happens if `reserve_serial_block(0)` is called? The function should return an empty list.
- What happens if the HMAC secret is an empty string? The function should still compute a valid HMAC (empty strings are valid HMAC keys).
- What happens if CSV card data list is empty? The function should produce a CSV with only the header row.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Test suite MUST verify that default PIN generation produces exactly 12 characters
- **FR-002**: Test suite MUST verify that custom PIN lengths (14, 16) are respected
- **FR-003**: Test suite MUST verify that generated PINs contain only characters from the safe alphabet (no 0, O, 1, I, L)
- **FR-004**: Test suite MUST verify that 1000 consecutively generated PINs are all unique
- **FR-005**: Test suite MUST verify that HMAC computation is deterministic (same input produces same output)
- **FR-006**: Test suite MUST verify that different PINs produce different HMAC values
- **FR-007**: Test suite MUST verify that different secrets produce different HMAC values for the same PIN
- **FR-008**: Test suite MUST verify that HMAC output is a 64-character hexadecimal string
- **FR-009**: Test suite MUST verify that the first serial block starts at VCH-000001 (or the next available number)
- **FR-010**: Test suite MUST verify that consecutive serial reservations produce contiguous blocks
- **FR-011**: Test suite MUST verify that serials follow the `VCH-NNNNNN` zero-padded 6-digit format
- **FR-012**: Test suite MUST verify that serial reservation returns exactly the requested count
- **FR-013**: Test suite MUST verify that CSV output starts with the correct header row (`serial_no,pin,product_names,face_value`)
- **FR-014**: Test suite MUST verify that CSV contains N+1 rows for N cards
- **FR-015**: Test suite MUST verify that CSV row content matches the input card data
- **FR-016**: Test suite MUST verify that encrypt-then-decrypt roundtrip preserves original data
- **FR-017**: Test suite MUST verify that encrypted output differs from plaintext input
- **FR-018**: Test suite MUST verify that decryption with a wrong secret raises an error

### Key Entities

- **PIN**: A randomly generated string of characters from a safe alphabet (excludes ambiguous characters), used as the voucher redemption code. Attributes: length (configurable, default 12), character set (30 safe alphanumeric characters).
- **HMAC Digest**: A deterministic SHA-256 hash computed from a PIN and a server-side secret. Used for secure PIN verification without storing plaintext. Attributes: 64-character hex string.
- **Serial Number**: A unique, contiguous identifier for each voucher card in `VCH-NNNNNN` format. Reserved atomically in blocks from a central counter. Attributes: prefix "VCH-", 6-digit zero-padded number.
- **CSV Export**: A structured text file containing card data (serial, PIN, product names, face value) with a fixed header row. Used for delivery to external partners.
- **Encrypted Export**: A Fernet-encrypted blob of a CSV export, using an HKDF-derived key from the HMAC secret. Stored at rest to protect plaintext PINs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 18 unit tests pass when executed via the project's test runner
- **SC-002**: Tests for PIN generation, HMAC, serial reservation, CSV export, and encryption/decryption each run independently without requiring database setup or external services (except serial reservation which uses the database)
- **SC-003**: The full test suite for Phase 3 completes in under 30 seconds
- **SC-004**: Every test validates exactly one behavior (single assertion focus) and has a descriptive name that communicates the expected behavior
- **SC-005**: Test coverage addresses all public functions in the generator module (`generate_pin`, `compute_hmac`, `reserve_serial_block`, `build_export_csv`) and the crypto module (`encrypt_data`, `decrypt_data`)

### Assumptions

- The test infrastructure from Phase 2 (fixtures, helpers, base test class) is already in place and available for import
- The `voucher_hmac_secret` is configured in the test site's `site_config.json`
- The `tabSeries` table is available in the test database for serial reservation tests
- The `cryptography` Python package (providing Fernet) is installed in the test environment
- Tests will use the Frappe test framework (`FrappeTestCase` or `unittest.TestCase` as appropriate)
- PIN generation tests do not require database access; HMAC tests do not require database access; serial reservation tests require database access; CSV tests do not require database access; crypto tests do not require database access
