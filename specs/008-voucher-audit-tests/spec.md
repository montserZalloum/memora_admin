# Feature Specification: Voucher System Audit & Comprehensive Tests

**Feature Branch**: `008-voucher-audit-tests`
**Created**: 2026-02-15
**Status**: Draft
**Input**: Analyze the Coupons & Voucher Library system for edge cases, abuse scenarios, logical flaws, missing validations. Write complete unit + integration tests covering expiry, multiple redemptions, partial usage, concurrency, fraud, admin misuse, and refund/rollback flows.

## Clarifications

### Session 2026-02-16

- Q: Should the 3 critical logical flaws be fixed or only documented with tests? → A: Tests only — document current behavior with clear TODO comments; fixes will be a separate feature branch.
- Q: How should overlapping test coverage be handled (43 existing tests cover allocation, commission, invoice)? → A: Skip already-covered scenarios; only add tests for uncovered edge cases in those areas.
- Q: What approach should concurrent redemption tests use? → A: Simulated state (manually set card to Redeemed, then verify second call returns ALREADY_REDEEMED) — no real threading.
- Q: How should security gap tests assert? → A: Tests PASS asserting current insecure behavior with `# TODO: SECURITY-FIX` comments explaining correct behavior. Grep-able markers for future fix branch.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Redemption Edge Case Coverage (Priority: P1)

A QA lead needs to verify that all edge cases in the voucher redemption flow are tested, including concurrent redemption attempts, PIN brute-force resilience, player spoofing, partial grant ownership, and rollback behavior when subscription creation fails mid-redemption.

**Why this priority**: Redemption is the most critical path -- it involves money, access grants, and player-facing interactions. Untested edge cases here represent real revenue and security risk.

**Independent Test**: Can be fully tested by running the redemption edge-case test suite against a live bench instance, delivering confidence that no redemption-related bug can reach production undetected.

**Acceptance Scenarios**:

1. **Given** a card already marked as Redeemed (simulating a concurrent winner), **When** a second `redeem_voucher` call is made for the same card, **Then** it returns ALREADY_REDEEMED (verifies the status-check guard without real threading)
2. **Given** a card is marked Redeemed (step 8) but subscription creation (step 11) fails, **When** the error is caught, **Then** the card status is rolled back to Allocated (or the entire transaction rolls back)
3. **Given** a player_id that does not exist in the database, **When** redeem_voucher is called, **Then** an appropriate error is returned (not a 500 crash)
4. **Given** a player partially owns grant keys (e.g., owns SUB-X but not TRK-Y from same grant), **When** redeem_voucher checks ALREADY_OWNED, **Then** redemption proceeds because not ALL keys are owned
5. **Given** a valid PIN, **When** redeem_voucher is called with an empty product_grant_id, **Then** a validation error is returned (not a crash)
6. **Given** a card whose batch has 0 grants (empty batch_grants), **When** redemption is attempted, **Then** GRANT_NOT_IN_BATCH is returned

---

### User Story 2 - Allocation Flow Testing (Priority: P1)

A QA lead needs to verify the full allocation lifecycle including approval workflows, return flows, re-allocation between libraries, prepaid vs consignment financial paths, and invoice/credit-note creation.

**Why this priority**: Allocation directly controls which libraries can sell cards and drives financial operations (invoicing). The allocation test file is currently empty (stub). This is a critical testing gap.

**Independent Test**: Can be tested by exercising the allocation API endpoints (fill_cards, submit_allocation, approve_allocation, reject_allocation) and verifying card state transitions, batch counter updates, and financial document creation.

**Acceptance Scenarios**:

1. **Given** a Generated batch with 10 Available cards and a library, **When** fill_cards is called with quantity=5, **Then** exactly 5 cards are added to the allocation
2. **Given** a Draft allocation with cards, **When** submit_allocation is called for a library that does NOT require approval, **Then** status transitions Draft->Approved->Completed and cards become Allocated
3. **Given** a Draft allocation with cards, **When** submit_allocation is called for a library that requires approval, **Then** status transitions to Pending Approval only
4. **Given** a Pending Approval allocation, **When** approve_allocation is called, **Then** status transitions Pending Approval->Approved->Completed and cards become Allocated
5. **Given** a Pending Approval allocation, **When** reject_allocation is called with a reason, **Then** status transitions to Rejected and cards remain unchanged
6. **Given** 5 Allocated cards belonging to Library A, **When** a Return allocation is completed, **Then** cards return to Available status with library/allocation/sale_model cleared and return_allocation set
7. **Given** a completed Prepaid allocation, **When** the on_update hook fires, **Then** a Sales Invoice is created with correct amounts reflecting commission
8. **Given** a completed Prepaid return, **When** the on_update hook fires, **Then** a Credit Note is created against the original invoice
9. **Given** the first allocation for a Generated batch, **When** completed, **Then** batch status transitions from Generated to Active
10. **Given** a batch with cards allocated to Library A, **When** a new allocation assigns those same cards to Library B, **Then** the cards are re-allocated (status IN ('Available', 'Allocated') clause)

---

### User Story 3 - Voiding & Expiry Flow Testing (Priority: P2)

A QA lead needs to verify that batch voiding, single card voiding, and expiry scenarios work correctly, including financial implications, counter accuracy, auto-close behavior, and admin audit trails.

**Why this priority**: Voiding and expiry are admin-critical operations that must handle edge cases cleanly (e.g., voiding a batch with mixed card states, voiding the last non-terminal card triggering auto-close).

**Independent Test**: Can be tested by creating batches in various states, voiding them, and verifying card states, counter accuracy, encrypted file deletion, and auto-close behavior.

**Acceptance Scenarios**:

1. **Given** an Active batch with 10 cards (5 Allocated, 3 Redeemed, 2 Available), **When** void_batch is called, **Then** only Available and Allocated cards become Void (Redeemed cards untouched), counters update correctly, batch transitions to Closed
2. **Given** a single Allocated card, **When** void_card is called, **Then** card status is Void, void_reason is set, batch counters update, and auto-close is checked
3. **Given** a batch with an encrypted export file, **When** void_batch is called, **Then** the encrypted file is deleted from disk and the File document is removed
4. **Given** void_batch called with empty void_reason, **When** validated, **Then** a ValidationError is raised
5. **Given** a batch where all remaining non-terminal cards are voided, **When** recount_and_maybe_close runs, **Then** batch auto-closes (status -> Closed, no void_reason)
6. **Given** a Draft batch (no cards exist), **When** void_batch is called, **Then** a ValidationError is raised ("Cannot void a Draft batch")
7. **Given** an already-Closed batch, **When** void_batch is called, **Then** a ValidationError is raised ("Batch is already Closed")
8. **Given** a Redeemed card, **When** void_card is called, **Then** a ValidationError is raised (only Available/Allocated can be voided)

---

### User Story 4 - Fraud & Security Scenario Testing (Priority: P2)

A security engineer needs to verify that the voucher system is resilient to fraud scenarios including PIN brute-force, timing attacks, player spoofing, grant injection, admin export abuse, and concurrent manipulation.

**Why this priority**: Security vulnerabilities in a financial system (vouchers have monetary value) represent direct business risk. These tests serve as regression guards against future code changes.

**Independent Test**: Can be tested by simulating attack patterns (rapid invalid PIN attempts, timing measurements, spoofed player IDs, injected grant IDs) and verifying the system responds safely.

**Acceptance Scenarios**:

1. **Given** the Redemption Log DocType has a "Rate Limited" status option, **When** examining the codebase, **Then** rate limiting logic should exist (currently it does NOT - this is a gap to document and optionally fix)
2. **Given** any authenticated user, **When** they call redeem_voucher with another player's ID, **Then** this should be validated/restricted (currently it is NOT - this is a gap)
3. **Given** a product_grant_id not in the batch, **When** redeem_voucher is called, **Then** GRANT_NOT_IN_BATCH is returned (this works correctly)
4. **Given** multiple rapid export_for_print calls, **When** examining the audit log, **Then** each export is logged with timestamp and user
5. **Given** the HMAC secret is rotated (changed in site_config), **When** existing cards are presented for redemption, **Then** they fail with INVALID_PIN (expected behavior, but should be documented)
6. **Given** timing-safe HMAC comparison, **When** verified via code inspection, **Then** hmac.compare_digest is used in both preview_voucher and redeem_voucher

---

### User Story 5 - Financial Accuracy Testing (Priority: P3)

A finance team member needs confidence that commission calculations, invoice amounts, credit notes, and net values are mathematically accurate with Decimal precision across all sale models.

**Why this priority**: Financial inaccuracy, even by fractions, compounds over thousands of transactions. Decimal arithmetic must be verified for correctness.

**Independent Test**: Can be tested by computing commissions for various inputs (percentage, fixed, zero, edge values) and verifying Sales Invoice and Credit Note line items.

**Acceptance Scenarios**:

1. **Given** a 15% commission on a 100 EGP face value batch of 50 cards, **When** calculated, **Then** per_card_commission=15.00, total_commission=750.00, net_per_card=85.00, net_total=4250.00
2. **Given** a fixed 10 EGP commission on a 50 EGP face value batch of 20 cards, **When** calculated, **Then** per_card_commission=10.00, total_commission=200.00, net_per_card=40.00, net_total=800.00
3. **Given** a commission value that produces repeating decimals (e.g., 33.33% of 100), **When** calculated, **Then** Decimal ROUND_HALF_UP produces correct result without floating-point drift
4. **Given** a prepaid allocation with commission, **When** the invoice is created, **Then** line item rates reflect net amounts (face_value minus commission)
5. **Given** a prepaid return against an invoice, **When** the credit note is created, **Then** it references the original invoice and has matching line items

---

### User Story 6 - Batch Lifecycle & Counter Integrity Testing (Priority: P3)

A system administrator needs confidence that batch counters (generated, allocated, redeemed, voided, expired) remain accurate across all operations and that recount_and_maybe_close is idempotent.

**Why this priority**: Counter drift causes incorrect reporting and can mask operational issues. Idempotent recount is the safety net.

**Independent Test**: Can be tested by performing a sequence of operations (generate, allocate, redeem some, void some, return some) and verifying counters at each step.

**Acceptance Scenarios**:

1. **Given** a batch with 10 cards through a full lifecycle (generate->allocate->redeem 3->void 2->return 2->recount), **When** counters are checked, **Then** generated_count=10, allocated_count=3, redeemed_count=3, voided_count=2, expired_count=0
2. **Given** recount_and_maybe_close called twice on the same batch, **When** results compared, **Then** both calls return identical values (idempotent)
3. **Given** a batch with all cards in terminal states (mix of Redeemed/Void/Expired), **When** recount_and_maybe_close runs, **Then** batch auto-closes
4. **Given** a Generated batch (not Active), **When** all cards reach terminal states and recount_and_maybe_close runs, **Then** batch does NOT auto-close (only Active batches auto-close)

---

### Edge Cases

- What happens when a batch has 0 grants in batch_grants? (Generation succeeds but redemption has nothing to offer)
- How does the system handle PIN collisions? (Astronomically unlikely with CSPRNG, but no uniqueness check exists)
- What happens if the encrypted export file is corrupted or missing from disk? (export_for_print will raise IOError)
- What happens when fill_cards is called on a depleted batch? (Returns 0 filled cards but no error)
- What happens when allocation cards reference cards that have been voided between fill and submit? (Cards in child table may no longer be Available)
- What happens when two allocations for the same batch submit simultaneously? (Race condition on card status updates)
- What happens when a player's plan has no season? (_check_season_active returns True - skips check)
- What happens when void_batch is called on a batch with Redeemed cards? (Only Available/Allocated are voided; Redeemed cards remain)
- What happens when a return allocation includes cards that were redeemed between fill and complete? (Return SQL only targets status='Allocated', so redeemed cards are skipped silently)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST validate that concurrent redemption of the same card results in exactly one success via database-level locking (SELECT FOR UPDATE)
- **FR-002**: System MUST roll back card status if subscription creation fails during redemption (atomic operation)
- **FR-003**: System MUST validate player_id existence before processing redemption or preview
- **FR-004**: System MUST correctly handle partial grant key ownership (only block if ALL keys are owned)
- **FR-005**: System MUST prevent redemption with empty or null product_grant_id
- **FR-006**: System MUST have test coverage for the full allocation lifecycle (fill, submit, approve, reject, return)
- **FR-007**: System MUST have test coverage for prepaid allocation invoice creation
- **FR-008**: System MUST have test coverage for prepaid return credit note creation
- **FR-009**: System MUST have test coverage for batch voiding with mixed card states
- **FR-010**: System MUST have test coverage for single card voiding with auto-close check
- **FR-011**: System MUST have test coverage for commission calculation accuracy with Decimal precision
- **FR-012**: System MUST have test coverage for batch counter accuracy across all operations
- **FR-013**: System MUST have test coverage for recount_and_maybe_close idempotency
- **FR-014**: System MUST have test coverage for allocation state machine transitions (valid and invalid)
- **FR-015**: System MUST have test coverage for card state machine transitions (valid and invalid)
- **FR-016**: System MUST document known security gaps via passing tests that assert current insecure behavior, each annotated with `# TODO: SECURITY-FIX` comment explaining the correct behavior (gaps: missing rate limiting, missing player ownership validation on redemption API, season check fails open)
- **FR-017**: System MUST have test coverage for HMAC secret absence during redemption (not just generation)
- **FR-018**: System MUST have test coverage verifying that voiding a batch deletes the encrypted export file
- **FR-019**: System MUST have test coverage for the batch auto-activation on first allocation (Generated -> Active)
- **FR-020**: System MUST have test coverage for return allocation clearing card fields (library, allocation, sale_model)

### Key Entities

- **Voucher Batch**: Container for a set of voucher cards. Tracks lifecycle (Draft->Generated->Active->Closed) and aggregate counters.
- **Voucher Card**: Individual redeemable unit. Tracks lifecycle (Available->Allocated->Redeemed/Void/Expired), PIN (as HMAC), library assignment, and redemption details.
- **Voucher Allocation**: Workflow document for assigning/returning cards between batches and libraries. Supports approval workflows.
- **Redemption Log**: Immutable audit trail for all redemption attempts (success and failure).
- **Product Grant**: Defines what content access a voucher card unlocks (maps to subjects/tracks).
- **Commission**: Financial calculation for library markup (percentage or fixed amount).
- **Sales Invoice / Credit Note**: Financial documents auto-created for prepaid allocations/returns.

## Detected Logical Flaws & Security Gaps

### Critical

1. **No Redemption Atomicity**: In `redeem_voucher()` (voucher.py:644-677), card is marked Redeemed at step 8, but subscription is created at step 11. If step 11 fails, the card is consumed but the player gets nothing. The `frappe.db.commit()` at line 689 commits everything, but there's no try/except around steps 8-12 to roll back the card status on failure.

2. **No Player Ownership Validation**: `redeem_voucher()` accepts any `player_id` from any authenticated user. A malicious user could redeem cards for other players. The `allow_guest=False` only ensures the caller is logged in, not that they own the player profile.

3. **Season Check Fails Open**: `_check_season_active()` (voucher.py:457-459) catches ALL exceptions and returns `True`, meaning any database error during season validation silently allows redemption.

### High

4. **No Rate Limiting (Unused Status)**: The Redemption Log DocType has a "Rate Limited" status option, but no rate-limiting logic exists in the codebase. PIN brute-force is theoretically possible (though 30^12 keyspace makes it impractical).

5. **Re-Allocation Can Steal Cards**: `_apply_allocation()` targets cards with `status IN ('Available', 'Allocated')`, meaning a new allocation can reassign cards already allocated to another library without explicit return.

6. **Stale Cards in Allocation**: Between `fill_cards()` and `submit_allocation()`, cards could be voided, redeemed, or allocated to another library. The `submit_allocation` only validates cards belong to the correct batch, not that they're still in the correct status.

### Medium

7. **Invoice Failure Silent**: `_create_prepaid_invoice()` catches all exceptions and only logs them. A failed invoice for a completed allocation could go unnoticed indefinitely.

8. **Missing Input Validation on Redemption**: No validation for empty/null `pin_hmac`, `player_id`, or `product_grant_id` parameters before database queries.

9. **No Duplicate PIN Detection**: While CSPRNG makes collisions astronomically unlikely, no uniqueness constraint exists on `pin_hmac` in the database or in the generation code.

10. **Export File Path Traversal**: `export_for_print` constructs file path from `batch.encrypted_file_url` without sanitization (mitigated by Frappe's file handling, but worth noting).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All identified edge cases not already covered by existing tests (65 tests in phases 003-007) have new test cases that pass (target: 25+ new tests focused on redemption, voiding, fraud/security, and counter integrity gaps)
- **SC-002**: Allocation test suite covers the full lifecycle: fill, submit (with/without approval), approve, reject, return -- minimum 12 tests
- **SC-003**: Voiding test suite covers batch void, single card void, auto-close, and mixed-state scenarios -- minimum 8 tests
- **SC-004**: Security/fraud test suite documents known gaps and verifies existing protections -- minimum 6 tests
- **SC-005**: Financial accuracy tests verify Decimal precision for commission calculations across percentage, fixed, zero, and edge-value scenarios -- minimum 5 tests
- **SC-006**: Counter integrity tests verify accuracy after each operation type and idempotency of recount -- minimum 4 tests
- **SC-007**: All new tests execute within 30 seconds total on the test bench
- **SC-008**: Zero test pollution: each test cleans up after itself and can run in any order
- **SC-009**: Critical logical flaws (items 1-3) and security gaps (items 4, 5, 6) have passing regression tests that assert current (flawed) behavior, each with `# TODO: SECURITY-FIX` or `# TODO: FIX` comments explaining expected correct behavior

## Assumptions

- Tests will use the existing test infrastructure (VoucherTestCase, voucher_fixtures, voucher_helpers)
- Tests will use the existing season `SEAS-00027` to avoid MySQL partitioning issues
- Tests run on `bench --site x.conanacademy.com run-tests`
- The existing `voucher_hmac_secret` is configured in site_config.json
- The `MEMORA-VOUCHER-CARD` Item exists in the database
- ERPNext Sales Invoice DocType is available for financial tests
- Commission and invoice service tests from Phase 004 are already passing (11 + 9 tests)
- Allocation flow tests from Phase 006 are already passing (23 tests) — this branch adds only uncovered edge cases, not duplicate coverage
- User Stories 2 and 5 are largely satisfied by existing tests; new tests target only gaps (e.g., re-allocation stealing, stale cards in allocation, credit note on return)
- Critical logical flaws (items 1-3) will NOT be fixed in this branch; tests will document current behavior with TODO comments for a future fix branch
