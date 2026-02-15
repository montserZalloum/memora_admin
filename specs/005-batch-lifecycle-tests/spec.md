# Feature Specification: Batch Lifecycle Integration Tests

**Feature Branch**: `005-batch-lifecycle-tests`
**Created**: 2026-02-15
**Status**: Draft
**Input**: User description: "Phase 5: Integration Tests — Batch Lifecycle (~14 tests) from VOUCHER_TEST_SUITE_PLAN.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Happy Path Generation Validation (Priority: P1)

A developer runs the batch lifecycle test suite to confirm that the core card generation workflow produces correct output. When a Draft batch with valid configuration is generated, it must create the expected number of cards, transition statuses correctly, produce serials in the correct format, store HMAC hashes (never plaintext PINs), update batch counters, and create an encrypted export file.

**Why this priority**: Card generation is the foundational batch operation. Every downstream feature (allocation, redemption, void, export) depends on cards being generated correctly. Without this confidence, no other voucher test can be trusted.

**Independent Test**: Can be fully tested by creating a Draft batch with a valid grant, calling the synchronous generation function, and asserting output card records, batch status, counters, serial format, HMAC storage, and encrypted file existence.

**Acceptance Scenarios**:

1. **Given** a Draft batch with quantity=10 and one valid product grant, **When** generation is executed, **Then** exactly 10 cards exist with status "Available" linked to that batch
2. **Given** a Draft batch, **When** generation completes, **Then** batch status transitions to "Generated"
3. **Given** a Draft batch with quantity=N, **When** generation completes, **Then** `generated_count` equals N and all other counters (allocated, redeemed, voided, expired) equal 0
4. **Given** a Draft batch, **When** generation completes, **Then** `encrypted_file_url` is set and points to an existing file
5. **Given** a Draft batch, **When** generation completes, **Then** all cards have serial numbers matching the `VCH-NNNNNN` zero-padded format
6. **Given** a Draft batch, **When** generation completes, **Then** all cards have `pin_hmac` populated and no plaintext PIN column exists in the card record

---

### User Story 2 - Generation Guard Rails (Priority: P2)

A developer runs tests to confirm that the generation function rejects invalid inputs and prevents re-generation. The system must refuse to generate cards for non-Draft batches, zero-quantity batches, batches exceeding the maximum limit, and batches when the HMAC secret is not configured.

**Why this priority**: Guard rails prevent data corruption and operational mistakes. Ensuring these validations work correctly prevents accidental double-generation (duplicate serials) and invalid batches from polluting the system.

**Independent Test**: Can be fully tested by creating batches in various invalid states and asserting that generation raises the appropriate validation error for each case, with no cards created as a side effect.

**Acceptance Scenarios**:

1. **Given** a batch in "Generated" status, **When** generation is attempted, **Then** a validation error is raised and no new cards are created
2. **Given** a batch already in "Generated" status (same scenario, explicit re-generation guard), **When** generation is attempted again, **Then** a validation error is raised
3. **Given** a Draft batch with quantity=0, **When** generation is attempted, **Then** a validation error is raised
4. **Given** a Draft batch with quantity exceeding the maximum (1001+), **When** generation is attempted, **Then** a validation error is raised
5. **Given** a Draft batch but no `voucher_hmac_secret` in site configuration, **When** generation is attempted, **Then** a validation error is raised

---

### User Story 3 - Export and Audit Trail (Priority: P3)

A developer runs tests to verify that the encrypted export can be decrypted back to valid CSV data matching the generated cards, and that every export action is logged in the batch's audit trail.

**Why this priority**: Export is the bridge between digital generation and physical card printing. Ensuring decryption integrity and audit logging gives confidence that card data is correct and every access is traceable.

**Independent Test**: Can be fully tested by generating a batch, calling the export function, verifying the decrypted CSV content matches expected card data, and checking that the export_log child table contains a new entry.

**Acceptance Scenarios**:

1. **Given** a generated batch with an encrypted export file, **When** the export is decrypted, **Then** the CSV content matches the generated cards' serial numbers and PINs
2. **Given** a generated batch, **When** the export function is called, **Then** a new row is appended to the `export_log` child table recording who exported, when, and how many cards

---

### User Story 4 - Rollback on Failure (Priority: P3)

A developer runs a test to confirm that if card generation fails mid-process, no partial data persists — the operation is atomic.

**Why this priority**: Partial generation would leave the system in an inconsistent state with orphaned cards and wrong counters. Atomicity ensures all-or-nothing behavior.

**Independent Test**: Can be tested by simulating a failure during the generation process and verifying that zero cards exist for the batch and the batch remains in Draft status.

**Acceptance Scenarios**:

1. **Given** a Draft batch where generation encounters a failure mid-process, **When** the error is raised, **Then** no cards exist for that batch and the batch status remains "Draft"

---

### Edge Cases

- What happens when generation is called on a batch that has already been through the full lifecycle (Closed status)?
- How does the system handle concurrent generation requests for the same batch?
- What happens when the encrypted file storage location is inaccessible?
- What happens when the serial number sequence has gaps from previous failed generations?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Test suite MUST verify that card generation creates exactly the configured quantity of cards, all with "Available" status
- **FR-002**: Test suite MUST verify that batch status transitions from "Draft" to "Generated" after successful generation
- **FR-003**: Test suite MUST verify that `generated_count` is set to the batch quantity and all other counters remain at zero after generation
- **FR-004**: Test suite MUST verify that `encrypted_file_url` is populated and the referenced file exists on disk
- **FR-005**: Test suite MUST verify that all generated card serial numbers conform to the `VCH-NNNNNN` zero-padded format
- **FR-006**: Test suite MUST verify that all cards store an HMAC hash (`pin_hmac`) and no plaintext PIN is persisted
- **FR-007**: Test suite MUST verify that generation fails with a validation error when the batch is not in "Draft" status
- **FR-008**: Test suite MUST verify that generation fails when batch quantity is zero
- **FR-009**: Test suite MUST verify that generation fails when batch quantity exceeds the maximum allowed (1000)
- **FR-010**: Test suite MUST verify that generation fails when `voucher_hmac_secret` is missing from site configuration
- **FR-011**: Test suite MUST verify that the decrypted export CSV matches the generated cards
- **FR-012**: Test suite MUST verify that each export action creates an audit log entry in the `export_log` child table
- **FR-013**: Test suite MUST verify that a second generation attempt on an already-generated batch is rejected
- **FR-014**: Test suite MUST verify that a failed generation leaves no partial cards and the batch remains in "Draft" status

### Key Entities

- **Memora Voucher Batch**: Central entity under test. Contains batch configuration (quantity, PIN length, face value), product grant children, generation counters, encrypted export file reference, export audit log, and lifecycle status (Draft → Generated → Active → Closed)
- **Memora Voucher Card**: Individual voucher card created during generation. Contains serial number (`VCH-NNNNNN`), HMAC-hashed PIN, batch reference, and lifecycle status (Available → Allocated → Redeemed/Void/Expired)
- **Memora Product Grant**: Product entitlement linked to a batch via the Batch Grant child table. Required for batch configuration
- **Encrypted Export File**: Binary file containing AES-encrypted CSV of card serial numbers and plaintext PINs, attached to the batch for secure print fulfillment

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 14 batch lifecycle tests pass successfully when run via the test framework
- **SC-002**: Test suite achieves 100% coverage of the batch generation function's success and error paths
- **SC-003**: Tests execute within 30 seconds total for the complete batch lifecycle suite
- **SC-004**: Zero false positives — tests fail only when actual generation behavior is broken, not due to test infrastructure issues
- **SC-005**: Tests are fully isolated — each test can run independently without depending on state from other tests

## Assumptions

- The existing test infrastructure (fixture factories, helpers, base test class) from Phase 2 is available and working
- The `voucher_hmac_secret` is configured in the test site's `site_config.json`
- The existing season `SEAS-00027` is available in the test database for creating product grants
- The `generate_cards_job()` function can be called synchronously (bypassing the queue) as established by the `generate_batch_sync()` helper
- The `generate_batch()` validation function (which enqueues the job) is the appropriate target for testing guard rail validations
- The `export_for_print()` function handles decryption and audit logging and can be called directly in tests
- The maximum batch quantity limit is 1000 cards as defined by `MAX_BATCH_QUANTITY`

## Scope Boundaries

**In scope**:
- 14 integration tests covering batch generation happy path, guard rails, export/audit, and rollback
- All tests in the existing stub file `test_memora_voucher_batch.py`
- Tests use existing fixture factories and helpers from Phase 2

**Out of scope**:
- Allocation flow tests (Phase 6)
- Redemption flow tests (Phase 7)
- Return/void/expiration tests (Phase 8)
- Batch auto-close tests (Phase 9)
- Any modifications to production code — this phase is test-only
