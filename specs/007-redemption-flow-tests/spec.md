# Feature Specification: Integration Tests — Redemption Flow

**Feature Branch**: `007-redemption-flow-tests`
**Created**: 2026-02-15
**Status**: Draft
**Input**: Phase 7 of VOUCHER_TEST_SUITE_PLAN.md — 22 integration tests covering voucher preview, redemption success, all error paths, audit logging, security, and batch auto-close

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Verify Successful Redemption (Priority: P1)

A developer needs confidence that the core redemption flow works correctly end-to-end: a valid PIN against an allocated card creates the right subscription transaction, updates all card fields, increments batch counters, and logs the success.

**Why this priority**: This is the primary happy path. If the core redemption doesn't work, nothing else matters. These tests catch regressions in the most business-critical flow.

**Independent Test**: Can be fully tested by creating a batch, generating cards, allocating to a library, then redeeming with a valid PIN. Delivers confidence that the core revenue-generating flow works.

**Acceptance Scenarios**:

1. **Given** an allocated card with a valid PIN, **When** `redeem_voucher()` is called with correct HMAC and player/grant, **Then** the card status becomes "Redeemed" and a subscription transaction is created with "Completed" status
2. **Given** a successful redemption, **When** the card is inspected, **Then** `redeemed_by`, `redeemed_at`, `redeemed_grant`, and `subscription_transaction` fields are all populated correctly
3. **Given** a successful redemption, **When** the parent batch is inspected, **Then** `redeemed_count` is incremented by 1
4. **Given** a successful redemption, **When** the redemption log is queried, **Then** a log entry exists with status "Success", the correct player, masked PIN, and IP address

---

### User Story 2 - Verify All Error Paths Return Correct Codes (Priority: P1)

A developer needs assurance that every invalid redemption attempt is rejected with the correct error code and does NOT consume the card. Each error scenario must be independently tested and produce an audit log entry.

**Why this priority**: Error handling is equally critical to the happy path — incorrect error handling could allow unauthorized redemptions or consume cards without granting access.

**Independent Test**: Can be tested by setting up cards in various states (Available, Redeemed, Void, Expired) and attempting redemption against each. Each test verifies both the error code returned and the card's unchanged state.

**Acceptance Scenarios**:

1. **Given** a wrong PIN HMAC, **When** `redeem_voucher()` is called, **Then** error code `INVALID_PIN` is returned and the card state is unchanged
2. **Given** a card with status "Available" (not allocated), **When** redemption is attempted, **Then** error code `NOT_ALLOCATED` is returned
3. **Given** a card with status "Redeemed", **When** redemption is attempted, **Then** error code `ALREADY_REDEEMED` is returned
4. **Given** a card with status "Expired", **When** redemption is attempted, **Then** error code `EXPIRED` is returned
5. **Given** a card with status "Void", **When** redemption is attempted, **Then** error code `VOID` is returned
6. **Given** a card whose batch is not "Active", **When** redemption is attempted, **Then** error code `BATCH_INACTIVE` is returned
7. **Given** a card whose season has ended, **When** redemption is attempted, **Then** error code `SEASON_INACTIVE` is returned
8. **Given** a grant ID not present in the batch, **When** redemption is attempted, **Then** error code `GRANT_NOT_IN_BATCH` is returned
9. **Given** a player who already owns the requested grant, **When** redemption is attempted, **Then** error code `ALREADY_OWNED` is returned and the card remains "Allocated" (not consumed)

---

### User Story 3 - Verify Preview Returns Correct Information (Priority: P2)

A developer needs to verify that the preview endpoint correctly shows what a card unlocks, filters out already-owned grants, and returns appropriate errors for invalid cards — all without modifying any state.

**Why this priority**: Preview is the read-only companion to redemption. It's the user's decision-making step before committing to redeem. Incorrect preview could mislead users.

**Independent Test**: Can be tested by calling `preview_voucher()` with various card/player combinations and verifying returned grants and face value without any state mutation.

**Acceptance Scenarios**:

1. **Given** a valid allocated card with 2 grants, **When** `preview_voucher()` is called, **Then** the response includes the face value and both available grants
2. **Given** a player who already owns 1 of 2 grants on a card, **When** `preview_voucher()` is called, **Then** only the unowned grant is returned
3. **Given** a player who owns ALL grants on a card, **When** `preview_voucher()` is called, **Then** error code `ALL_GRANTS_OWNED` is returned

---

### User Story 4 - Verify Audit Logging and Security (Priority: P2)

A developer needs to confirm that every redemption attempt (success or failure) produces an immutable audit log with masked PINs, IP addresses, and correct status mapping. Additionally, the system must use timing-safe comparison for HMAC validation.

**Why this priority**: Audit trails are critical for compliance, dispute resolution, and security forensics. Timing-safe comparison prevents side-channel attacks.

**Independent Test**: Can be tested by attempting several redemptions (valid and invalid) and verifying log entries. The timing-safe comparison is verified via code inspection.

**Acceptance Scenarios**:

1. **Given** any redemption attempt (success or failure), **When** the redemption log is queried, **Then** an entry exists with the correct status, masked PIN (last 4 chars prefixed with ****), and client IP address
2. **Given** each distinct error code, **When** a redemption fails with that code, **Then** the log entry's status matches the expected human-readable label
3. **Given** the redemption code, **When** inspected, **Then** HMAC comparison uses `hmac.compare_digest` (timing-safe)

---

### User Story 5 - Verify Batch Auto-Close on Last Redemption (Priority: P3)

A developer needs to verify that when the last non-terminal card in a batch is redeemed, the batch automatically transitions from "Active" to "Closed".

**Why this priority**: Auto-close is an operational convenience feature. While important for batch lifecycle management, it's lower priority than core redemption correctness.

**Independent Test**: Can be tested by creating a minimal batch (1-2 cards), redeeming all cards, and verifying the batch transitions to "Closed".

**Acceptance Scenarios**:

1. **Given** a batch with 1 allocated card, **When** that card is redeemed, **Then** the batch status transitions from "Active" to "Closed"

---

### Edge Cases

- What happens when redemption is attempted with an empty or malformed PIN HMAC?
- How does the system behave when a card's batch exists but has been deleted or is in an unexpected state?
- What happens when two simultaneous redemption attempts target the same card? (Row-level locking should prevent double-redemption)
- What happens when the subscription transaction creation fails mid-redemption? (Atomicity via SQL transaction)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Test suite MUST verify that successful redemption transitions card status to "Redeemed" and creates a "Completed" subscription transaction
- **FR-002**: Test suite MUST verify that all 4 card fields (`redeemed_by`, `redeemed_at`, `redeemed_grant`, `subscription_transaction`) are correctly populated after redemption
- **FR-003**: Test suite MUST verify that batch `redeemed_count` is incremented after successful redemption
- **FR-004**: Test suite MUST verify that `preview_voucher()` returns correct face value and available grants without modifying state
- **FR-005**: Test suite MUST verify that preview filters out grants the player already owns
- **FR-006**: Test suite MUST verify that preview returns `ALL_GRANTS_OWNED` when player owns every grant on the card
- **FR-007**: Test suite MUST verify each error code is returned for its corresponding invalid state: `INVALID_PIN`, `NOT_ALLOCATED`, `ALREADY_REDEEMED`, `EXPIRED`, `VOID`, `BATCH_INACTIVE`, `SEASON_INACTIVE`, `GRANT_NOT_IN_BATCH`, `ALREADY_OWNED`
- **FR-008**: Test suite MUST verify that every redemption attempt (success and each failure type) creates a Redemption Log entry with correct status
- **FR-009**: Test suite MUST verify that logged PINs are masked (only last 4 characters of HMAC, prefixed with ****)
- **FR-010**: Test suite MUST verify that client IP address is captured in the redemption log
- **FR-011**: Test suite MUST verify (via code inspection) that HMAC comparison uses `hmac.compare_digest` for timing-safe comparison
- **FR-012**: Test suite MUST verify that redeeming the last non-terminal card in a batch triggers auto-close (batch status becomes "Closed")
- **FR-013**: All tests MUST use the existing test infrastructure (fixtures, helpers) from Phase 2 and build on the batch/allocation setup from Phases 5-6
- **FR-014**: Test file MUST be the existing stub at the Voucher Card DocType test location

### Key Entities

- **Voucher Card**: The central entity — transitions through Available → Allocated → Redeemed. Holds PIN HMAC, batch reference, library assignment, and redemption metadata
- **Voucher Batch**: Parent container for cards. Tracks counters (generated, allocated, redeemed, voided, expired). Auto-closes when all cards reach terminal states
- **Redemption Log**: Immutable audit trail. One entry per redemption attempt. Stores masked PIN, player, IP, status, timestamp
- **Product Grant**: Defines what access a card unlocks. A batch can contain multiple grants. Player selects one grant during redemption
- **Subscription Transaction**: Created on successful redemption. Triggers downstream pipeline (Player Subscription creation, Redis access sync)
- **Player Profile**: The end user redeeming the card. Used to check existing ownership of grants

## Assumptions

- Tests will reuse the existing season `SEAS-00027` to avoid MySQL partitioning constraints
- The `voucher_hmac_secret` is configured in the test site's `site_config.json`
- Tests will use `generate_batch_sync()` helper to bypass the job queue for card generation
- The `MEMORA-VOUCHER-CARD` item exists in the test database (created by `setup.py`)
- Tests for `SEASON_INACTIVE` will modify the season's end date to simulate expiration rather than creating a new expired season
- The timing-safe comparison test (FR-011) is a code-level assertion (inspecting source) rather than a runtime timing attack test

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 22 tests pass when run via `bench run-tests` against the voucher card test module
- **SC-002**: 100% of documented error codes (`INVALID_PIN`, `NOT_ALLOCATED`, `ALREADY_REDEEMED`, `EXPIRED`, `VOID`, `BATCH_INACTIVE`, `SEASON_INACTIVE`, `GRANT_NOT_IN_BATCH`, `ALREADY_OWNED`) have dedicated test coverage
- **SC-003**: Every test that triggers a redemption attempt (success or failure) verifies a corresponding Redemption Log entry exists with correct status
- **SC-004**: The test suite completes within 60 seconds total execution time
- **SC-005**: No test leaves orphaned or dirty state that causes other tests to fail (each test is independent and idempotent)
