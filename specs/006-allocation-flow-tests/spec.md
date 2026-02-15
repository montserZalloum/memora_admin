# Feature Specification: Integration Tests — Allocation Flow

**Feature Branch**: `006-allocation-flow-tests`
**Created**: 2026-02-15
**Status**: Draft
**Input**: User description: "Phase 6: Integration Tests — Allocation Flow (~22 tests) from VOUCHER_TEST_SUITE_PLAN.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fill Cards into Allocation (Priority: P1)

An administrator creates a voucher allocation to assign cards from a generated batch to a library (Customer). The system fills Available cards into the allocation's child table (for Allocate type) or Allocated cards belonging to the library (for Return type), respecting a quantity limit.

**Why this priority**: Card filling is the entry point of the allocation workflow. Without it, no allocation can proceed. This validates the data selection logic that underpins all downstream operations.

**Independent Test**: Can be fully tested by creating a generated batch, calling fill_cards(), and verifying the allocation child table is populated with the correct cards. Delivers confidence that the right cards are selected by type and quantity.

**Acceptance Scenarios**:

1. **Given** a generated batch with 10 Available cards, **When** fill_cards() is called for an Allocate-type allocation, **Then** the allocation child table contains all 10 Available card references.
2. **Given** a batch with 5 Allocated cards belonging to Library A, **When** fill_cards() is called for a Return-type allocation targeting Library A, **Then** the allocation child table contains those 5 Allocated cards.
3. **Given** a generated batch with 10 Available cards, **When** fill_cards() is called with quantity=5, **Then** exactly 5 cards are filled.
4. **Given** an allocation in non-Draft status, **When** fill_cards() is called, **Then** the system raises a ValidationError.

---

### User Story 2 - Submit and Approval Workflow (Priority: P1)

An administrator submits a filled allocation. The system checks whether the library requires approval. If no approval is needed, the allocation transitions directly to Completed. If approval is required, it transitions to Pending Approval and waits for an explicit approve or reject action.

**Why this priority**: The approval workflow is the core state machine governing allocation. It determines whether cards get allocated or returned, and whether invoices get created. This is the most business-critical path.

**Independent Test**: Can be fully tested by creating libraries with and without the approval flag, submitting allocations, and verifying the resulting status. Delivers confidence that the approval gate works correctly.

**Acceptance Scenarios**:

1. **Given** a filled allocation for a library that does NOT require approval, **When** submit_allocation() is called, **Then** the allocation status becomes Completed.
2. **Given** a filled allocation for a library that DOES require approval, **When** submit_allocation() is called, **Then** the allocation status becomes Pending Approval.
3. **Given** an allocation with no cards, **When** submit_allocation() is called, **Then** the system raises a ValidationError ("No cards").
4. **Given** an allocation containing cards from a different batch, **When** submit_allocation() is called, **Then** the system raises a ValidationError ("Cards do not belong to batch").
5. **Given** a Pending Approval allocation, **When** approve_allocation() is called, **Then** the allocation status becomes Completed.
6. **Given** a Pending Approval allocation, **When** reject_allocation() is called, **Then** the allocation status becomes Rejected.
7. **Given** a Draft allocation, **When** approve_allocation() is called, **Then** the system raises a ValidationError (can only approve Pending Approval).

---

### User Story 3 - Card State Updates on Completion (Priority: P1)

When an allocation completes, the system bulk-updates the allocated cards. For Allocate type, cards transition from Available to Allocated with library, allocation, and sale_model fields set. For Return type, cards transition from Allocated back to Available with those fields cleared and return_allocation set.

**Why this priority**: Card state integrity is the fundamental data contract. If cards aren't updated correctly, downstream operations (redemption, invoicing, returns) will fail or produce incorrect results.

**Independent Test**: Can be fully tested by completing an allocation and querying card records to verify status, library, allocation, and sale_model fields. Delivers confidence that the allocation engine correctly mutates card state.

**Acceptance Scenarios**:

1. **Given** a completed Allocate-type allocation for Library A, **When** cards are queried, **Then** each card has status=Allocated, library=Library A, sale_model matching the allocation.
2. **Given** a completed Return-type allocation, **When** cards are queried, **Then** each card has status=Available, library=NULL, allocation=NULL, sale_model=NULL, and return_allocation set to the return allocation name.

---

### User Story 4 - Batch Counter and Status Updates (Priority: P2)

When an allocation completes, the batch's allocated_count counter is recounted from actual card data. On the first allocation against a Generated batch, the batch status transitions to Active.

**Why this priority**: Accurate counters are important for reporting and batch lifecycle management. However, they are derived data (recounted from cards), so incorrect counters don't corrupt the primary data.

**Independent Test**: Can be fully tested by completing an allocation and checking batch counter values and batch status. Delivers confidence that batch metadata stays synchronized with card reality.

**Acceptance Scenarios**:

1. **Given** a Generated batch with 10 cards, **When** 5 cards are allocated, **Then** batch.allocated_count equals 5.
2. **Given** a Generated batch, **When** the first allocation completes, **Then** the batch status transitions from Generated to Active.

---

### User Story 5 - Prepaid Invoice Creation (Priority: P2)

When a Prepaid allocation completes, the system automatically creates a submitted Sales Invoice linked to the allocation. The invoice reflects the correct quantity and net amount after commission calculations.

**Why this priority**: Financial document creation is critical for business operations but is secondary to the core allocation mechanics. Invoice creation failure is designed to be non-blocking (logged but doesn't roll back allocation).

**Independent Test**: Can be fully tested by completing a Prepaid allocation and querying the Sales Invoice. Delivers confidence that financial documents are generated correctly.

**Acceptance Scenarios**:

1. **Given** a completed Prepaid allocation of 5 cards at face_value=5, **When** the allocation completes, **Then** a Sales Invoice is created with the correct customer, item, quantity, and amount.
2. **Given** a library with 10% commission, **When** a Prepaid allocation of 10 cards at face_value=5 completes, **Then** the invoice amount reflects the net after commission (4.50 per card).

---

### User Story 6 - State Machine Enforcement (Priority: P2)

The allocation document enforces a strict state machine. Invalid transitions (e.g., Draft to Completed, Completed to Draft) are rejected with a ValidationError. Terminal states (Completed, Rejected, Cancelled) cannot transition to any other state.

**Why this priority**: State machine integrity prevents data corruption from programming errors or API misuse, but the happy-path transitions (covered in Story 2) are more critical to validate first.

**Independent Test**: Can be fully tested by attempting invalid status transitions and verifying they raise ValidationError. Delivers confidence that the state machine is correctly enforced.

**Acceptance Scenarios**:

1. **Given** a Draft allocation, **When** status is set directly to Completed (skipping Approved), **Then** the system raises a ValidationError.
2. **Given** a Completed allocation, **When** status is set to Draft, **Then** the system raises a ValidationError ("terminal state").

---

### Edge Cases

- What happens when fill_cards() is called on an allocation that already has cards? Existing child rows are cleared and replaced with fresh query results.
- What happens when a Return-type fill targets a library with zero Allocated cards? Zero cards are filled, the allocation child table is empty.
- What happens when a prepaid invoice creation fails? The allocation still completes successfully; the error is logged but does not roll back the allocation.
- What happens when the same batch is allocated to multiple libraries? Each allocation takes from remaining Available cards; sequential allocations reduce the available pool.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST fill Available cards from the batch into Allocate-type allocations
- **FR-002**: System MUST fill Allocated cards belonging to the library into Return-type allocations
- **FR-003**: System MUST respect the quantity parameter when filling cards (0 means all available)
- **FR-004**: System MUST reject fill_cards() on non-Draft allocations with a ValidationError
- **FR-005**: System MUST auto-approve allocations for libraries without the approval requirement (Draft to Completed)
- **FR-006**: System MUST route allocations to Pending Approval for libraries with the approval requirement
- **FR-007**: System MUST reject submit_allocation() when the allocation has no cards
- **FR-008**: System MUST reject submit_allocation() when cards belong to a different batch
- **FR-009**: System MUST transition Pending Approval allocations to Completed upon approve_allocation()
- **FR-010**: System MUST transition Pending Approval allocations to Rejected upon reject_allocation()
- **FR-011**: System MUST reject approve_allocation() for non-Pending Approval allocations
- **FR-012**: System MUST update card status to Allocated with library, allocation, and sale_model fields upon Allocate completion
- **FR-013**: System MUST update card status to Available with cleared fields and return_allocation set upon Return completion
- **FR-014**: System MUST update batch allocated_count after allocation completion
- **FR-015**: System MUST transition batch from Generated to Active on first allocation
- **FR-016**: System MUST create a submitted Sales Invoice for completed Prepaid allocations
- **FR-017**: System MUST calculate invoice amount using commission rules (net after commission)
- **FR-018**: System MUST reject invalid state transitions with ValidationError
- **FR-019**: System MUST enforce terminal states (Completed, Rejected, Cancelled) as non-transitionable
- **FR-020**: System MUST return Allocated cards to Available status upon Return-type completion

### Key Entities

- **Memora Voucher Allocation**: Tracks card assignment/return between batches and libraries. Has allocation_type (Allocate/Return), sale_model (Prepaid/Consignment), status state machine, and child table of allocation cards.
- **Memora Voucher Card**: Individual voucher with status lifecycle (Available, Allocated, Redeemed, Expired, Void). Links to batch, library, allocation, and sale_model.
- **Memora Voucher Batch**: Container for generated cards with status (Draft, Generated, Active, Closed) and counter fields (generated_count, allocated_count, redeemed_count, voided_count, expired_count).
- **Customer (Library)**: Distribution partner with voucher_requires_approval flag and commission settings (voucher_commission_type, voucher_commission_value).
- **Sales Invoice**: Financial document created for Prepaid allocations, linked back to the allocation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 22 allocation flow tests pass consistently when run via `bench run-tests`
- **SC-002**: Tests cover all 6 user stories: fill logic, approval workflow, card state updates, batch counters, invoice creation, and state machine enforcement
- **SC-003**: Each test is independently runnable and does not depend on execution order of other tests
- **SC-004**: Tests use the existing shared fixture factories and helper functions without duplicating test infrastructure
- **SC-005**: Test execution completes within 60 seconds for the full allocation test suite
- **SC-006**: Tests validate both happy paths and error/rejection scenarios for every workflow step

## Assumptions

- The existing test infrastructure (voucher_fixtures.py, voucher_helpers.py, voucher_test_base.py) from Phase 2 is complete and functional
- Phase 5 batch lifecycle tests are passing, meaning batch generation works correctly as a precondition
- The test site has `voucher_hmac_secret` configured and the `MEMORA-VOUCHER-CARD` Item exists
- Season `SEAS-00027` is used for all tests to avoid MySQL partitioning constraints
- The allocation API functions (fill_cards, submit_allocation, approve_allocation, reject_allocation) are the primary interfaces under test
- The Sales Invoice creation for Prepaid allocations relies on the existing `create_prepaid_allocation_invoice()` service function
- Commission calculation is handled by the existing voucher commission service and is tested in Phase 4; allocation tests verify the end-to-end invoice amount but do not re-test commission math in isolation

## Dependencies

- **Phase 2** (Test Infrastructure): Fixture factories and helper functions
- **Phase 5** (Batch Lifecycle Tests): Batch generation must work correctly as a precondition for allocation tests
- **Phase 4** (Commission & Invoice Unit Tests): Commission calculation and invoice creation services
