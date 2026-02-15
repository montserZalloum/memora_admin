# Feature Specification: Voucher Test Infrastructure

**Feature Branch**: `002-voucher-test-infra`
**Created**: 2026-02-15
**Status**: Draft
**Input**: User description: "Phase 2: Test Infrastructure — Create shared fixture factories and test helpers for the voucher system test suite"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create Test Data Quickly with Fixture Factories (Priority: P1)

As a developer writing voucher tests, I need a set of fixture factory functions that create valid test data (batches, product grants, seasons, customers, players, allocations) with sensible defaults, so I can set up test scenarios in a few lines instead of manually constructing complex DocType hierarchies each time.

**Why this priority**: Without fixture factories, every test file must duplicate 20+ lines of DocType setup code. This is the foundational building block that all subsequent test phases (P3-P10, ~145 tests) depend on.

**Independent Test**: Can be validated by importing the fixture module and calling each factory function — each must return a valid, saved document with correct default field values and relationships.

**Acceptance Scenarios**:

1. **Given** no prior test data exists, **When** a developer calls `make_batch()` with no arguments, **Then** a valid Draft batch document is created with default quantity (10), pin length (12), face value (5), and no grants.
2. **Given** a developer needs a batch with custom grants, **When** they call `make_batch(grants=[...])`, **Then** the batch is created with Batch Grant child rows matching the provided grant references.
3. **Given** a developer needs a product grant with an associated plan, **When** they call `make_product_grant(item_code, plan="some-plan")`, **Then** the grant is created along with the required Academic Plan and Season dependencies.
4. **Given** a developer needs interconnected test data, **When** they call `make_allocation(batch, customer)`, **Then** the allocation document is created with correct references to the batch and customer.

---

### User Story 2 - Execute Common Test Operations with Helpers (Priority: P1)

As a developer writing voucher tests, I need shared helper functions that encapsulate common multi-step test operations (generating a batch synchronously, redeeming a card with proper HMAC, checking batch counters), so tests remain concise and focused on the behavior being verified rather than boilerplate mechanics.

**Why this priority**: Test helpers reduce duplication across ~145 tests and ensure consistent test operation patterns. Without them, each test would need to reimplement HMAC computation, batch generation calls, and counter assertion logic.

**Independent Test**: Can be validated by calling each helper function in a test context — e.g., `generate_batch_sync()` must produce cards, `get_card_statuses()` must return accurate status counts, and `assert_batch_counters()` must correctly pass/fail assertions.

**Acceptance Scenarios**:

1. **Given** a Draft batch exists, **When** `generate_batch_sync(batch_name)` is called, **Then** cards are generated synchronously (bypassing the background queue) and the batch transitions to Generated status.
2. **Given** a batch with cards in various states, **When** `get_card_statuses(batch_name)` is called, **Then** it returns an accurate dictionary of status counts (e.g., `{"Available": 5, "Allocated": 3}`).
3. **Given** a valid card PIN and HMAC secret, **When** `redeem_card_by_pin(pin, secret, player_id, grant_id)` is called, **Then** it correctly computes the HMAC and invokes the redeem endpoint, returning the result.
4. **Given** a batch with known counter values, **When** `assert_batch_counters(test_case, batch_name, generated_count=10, allocated_count=3)` is called, **Then** it asserts that each specified counter matches the actual value in the database and produces clear failure messages for mismatches.

---

### User Story 3 - Verify Test Prerequisites Before Running Tests (Priority: P2)

As a developer running the test suite, I need the test infrastructure to verify that essential prerequisites (HMAC secret configuration, required Item records) are in place before tests execute, so that test failures clearly indicate missing prerequisites rather than producing cryptic errors deep in test logic.

**Why this priority**: Missing prerequisites cause confusing cascading failures across the entire test suite. Early validation saves debugging time, but this is lower priority than the factories and helpers that all tests directly depend on.

**Independent Test**: Can be validated by running a prerequisite check — it should pass on a correctly configured test site and fail with clear messages when prerequisites are missing.

**Acceptance Scenarios**:

1. **Given** a test site with `voucher_hmac_secret` set in site config, **When** the prerequisite check runs, **Then** it confirms the HMAC secret is available.
2. **Given** a test site without `voucher_hmac_secret` in site config, **When** the prerequisite check runs, **Then** it produces a clear, descriptive error indicating the missing configuration.
3. **Given** a test site with the `MEMORA-VOUCHER-CARD` Item created by setup, **When** the prerequisite check runs, **Then** it confirms the Item exists.
4. **Given** a test site missing the `MEMORA-VOUCHER-CARD` Item, **When** the prerequisite check runs, **Then** it produces a clear error indicating the missing Item record.

---

### Edge Cases

- What happens when a fixture factory is called with conflicting parameters (e.g., `make_batch(status="Generated")` without generating cards)? The factory should create the document in the requested state; it is the caller's responsibility to ensure logical consistency for their test scenario.
- What happens when fixture factories are called multiple times in the same test? Each call must produce unique, non-colliding documents (unique names, serial ranges, etc.) to prevent conflicts.
- What happens when helpers are called against a non-existent batch or card? Helpers should propagate the underlying framework error rather than silently returning empty results.
- What happens when `generate_batch_sync()` is called on an already-generated batch? The helper should propagate the validation error from the generation logic.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The fixture module MUST provide a `make_batch()` factory that creates a Voucher Batch with configurable quantity, pin length, face value, grants, and status, using sensible defaults when arguments are omitted.
- **FR-002**: The fixture module MUST provide a `make_product_grant()` factory that creates a Product Grant document, and optionally creates associated Academic Plan and Season dependencies when a plan name is provided.
- **FR-003**: The fixture module MUST provide a `make_season()` factory that creates a Season document with configurable start date, end date, and publication status.
- **FR-004**: The fixture module MUST provide a `make_customer()` factory that creates a Customer document with voucher-specific custom fields (requires_approval, commission_type, commission_value).
- **FR-005**: The fixture module MUST provide a `make_player()` factory that creates a Player Profile document.
- **FR-006**: The fixture module MUST provide a `make_allocation()` factory that creates an Allocation document linked to a given batch and customer, with configurable allocation type and sale model.
- **FR-007**: Each fixture factory MUST return the created document object after saving it to the database.
- **FR-008**: Each fixture factory MUST produce unique, non-colliding documents when called multiple times within the same test session.
- **FR-009**: The helper module MUST provide a `generate_batch_sync()` function that triggers batch card generation synchronously (without using the background job queue).
- **FR-010**: The helper module MUST provide a `get_card_statuses()` function that returns a dictionary of card status counts for a given batch.
- **FR-011**: The helper module MUST provide a `fill_and_complete_allocation()` function that drives an allocation through its complete workflow.
- **FR-012**: The helper module MUST provide a `redeem_card_by_pin()` function that computes the correct HMAC for a PIN and invokes the redemption logic.
- **FR-013**: The helper module MUST provide an `assert_batch_counters()` function that asserts batch counter fields match expected values with clear failure messages.
- **FR-014**: The test infrastructure MUST include a prerequisite check that verifies `voucher_hmac_secret` is configured in the test site.
- **FR-015**: The test infrastructure MUST include a prerequisite check that verifies the `MEMORA-VOUCHER-CARD` Item record exists.
- **FR-016**: Prerequisite check failures MUST produce descriptive error messages identifying exactly what is missing and how to fix it.

### Key Entities

- **Voucher Batch**: Container for a set of voucher cards with counters (generated, allocated, redeemed, voided, expired) and lifecycle status (Draft, Generated, Active, Closed).
- **Voucher Card**: Individual voucher with PIN, HMAC, status (Available, Allocated, Redeemed, Void, Expired), and links to batch, customer, and player.
- **Product Grant**: Defines what a voucher grants to the redeemer (e.g., subscription access). Links to an item code and optionally an Academic Plan.
- **Voucher Allocation**: Assignment of cards from a batch to a customer/library, with approval workflow and sale model (Prepaid/Consignment).
- **Season**: Time-bounded period that controls card validity and expiration.
- **Customer (Library)**: Business entity receiving allocated cards, with commission and approval settings.
- **Player Profile**: End user who redeems voucher cards.

## Assumptions

- The test infrastructure is built for Frappe's standard testing framework (`FrappeTestCase` + `bench run-tests`). All fixtures and helpers follow Frappe testing conventions.
- Factory functions use `frappe.get_doc({...}).insert()` pattern to create documents, consistent with standard Frappe test patterns.
- The `generate_batch_sync()` helper calls the card generation function directly rather than enqueuing a background job, since test environments should execute synchronously.
- Fixture factories generate unique names using suffixes (e.g., timestamps or random strings) to avoid naming collisions across tests.
- The prerequisite check is implemented as a base test class or setup method that runs before the test suite, not as a standalone script.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new voucher test (in any subsequent phase P3-P10) can set up its required test data in 5 lines or fewer using fixture factories, rather than 20+ lines of manual DocType construction.
- **SC-002**: All 6 fixture factory functions (`make_batch`, `make_product_grant`, `make_season`, `make_customer`, `make_player`, `make_allocation`) produce valid, saved documents that pass framework validation.
- **SC-003**: All 5 helper functions (`generate_batch_sync`, `get_card_statuses`, `fill_and_complete_allocation`, `redeem_card_by_pin`, `assert_batch_counters`) execute successfully against a properly configured test site.
- **SC-004**: Prerequisite checks detect missing configuration (HMAC secret, Item record) and report clear, actionable error messages within the first seconds of a test run.
- **SC-005**: The fixture and helper modules can be imported without errors from any test file within the project.
- **SC-006**: Calling any fixture factory 10 times in sequence produces 10 distinct, non-conflicting documents.
