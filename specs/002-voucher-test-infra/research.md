# Research: Voucher Test Infrastructure

**Feature**: 002-voucher-test-infra | **Date**: 2026-02-15

## Research Questions & Findings

### R1: How does Frappe's test runner work and what patterns should fixtures follow?

**Decision**: Use `frappe.get_doc({...}).insert(ignore_permissions=True)` for all factory functions. Frappe's test runner (`bench run-tests`) wraps each test class in a savepoint that rolls back after completion, so cleanup is automatic.

**Rationale**: This is the standard Frappe testing pattern. Using `.insert()` ensures all validation hooks fire and the document is saved to the database. Using `ignore_permissions=True` avoids requiring specific roles in the test environment.

**Alternatives considered**:
- `frappe.get_doc({...}).db_insert()` — Bypasses validation hooks; would create potentially invalid documents
- Manual SQL inserts — Bypasses ORM entirely; would miss child table handling, autoname, and hooks

### R2: How to generate unique document names in factory functions?

**Decision**: Use `frappe.utils.random_string(8)` as a suffix for user-visible fields (batch_name, season_title, etc.). Let autoname patterns (e.g., `VBATCH-.#####.`) handle the document `name` field automatically.

**Rationale**: Frappe's autoname generates unique document names (PK). User-visible fields like `batch_name` need uniqueness across test runs. `random_string(8)` provides 36^8 combinations — more than enough for test isolation.

**Alternatives considered**:
- UUID suffixes — Too long for human-readable test output
- Timestamp suffixes — Not unique enough under parallel execution
- Sequential counters — Would require global state management

### R3: How to run card generation synchronously (bypassing background queue)?

**Decision**: Call `generate_cards_job(batch_name)` directly instead of `generate_batch()`. The job function is a plain Python function that can be invoked synchronously. The only difference is `frappe.publish_progress()` and `frappe.publish_realtime()` calls, which are no-ops in test context (no websocket connection).

**Rationale**: `generate_batch()` validates the batch and enqueues `generate_cards_job` via `frappe.enqueue()`. In tests, we want synchronous execution. Calling the job function directly skips the queue while preserving all the actual generation logic (serial reservation, HMAC computation, bulk insert, encrypted export).

**Alternatives considered**:
- Mocking `frappe.enqueue` to run synchronously — Adds complexity, fragile if enqueue signature changes
- Using `frappe.flags.in_test = True` flag — Frappe doesn't natively support sync-on-test for enqueue
- Reimplementing generation logic — Duplication, would drift from production code

### R4: How to compute HMAC for `redeem_card_by_pin()` helper?

**Decision**: Use `compute_hmac(pin, secret)` from `services/voucher/generator.py` with `frappe.conf.get("voucher_hmac_secret")`. The test must have access to the plaintext PIN, which is only available during generation (stored as HMAC in DB). The helper will need the PIN as input parameter.

**Rationale**: The `generate_cards_job()` generates PINs and immediately HMACs them. Plaintext is only in the encrypted export file. For test helpers, we have two approaches: (1) decrypt the export to get PINs, or (2) require the test to capture PINs during generation. Since `generate_batch_sync()` calls the job directly, we can capture PINs by reading the cards + decrypting the export, OR we can generate cards manually in fixtures. The simplest approach: `redeem_card_by_pin()` accepts a plaintext PIN, computes HMAC, and calls the redeem function.

**Alternatives considered**:
- Decrypting export file in helper — Adds complexity, couples to file storage
- Storing plaintext PINs in test database — Violates Constitution Principle I
- Mocking HMAC verification — Would not test the real code path

### R5: How does `fill_and_complete_allocation()` work end-to-end?

**Decision**: The helper will orchestrate: (1) create Draft allocation via `make_allocation()`, (2) call `fill_cards()` API to populate child rows, (3) call `submit_allocation()` API to drive through approval workflow. For libraries that don't require approval, this auto-completes. For libraries that require approval, the helper will also call `approve_allocation()`.

**Rationale**: This mirrors the real UI workflow (Draft → Fill → Submit → [Approve] → Completed). Using the API functions ensures all hooks fire correctly (card status updates, batch counter updates, invoice creation for prepaid).

**Alternatives considered**:
- Direct status manipulation — Would bypass hooks and produce invalid state
- Separate fill/submit helpers — Adds API surface without clear benefit; the combined operation is the most common test need

### R6: What prerequisite checks are needed and how to implement them?

**Decision**: Create a `VoucherTestCase` base class extending `FrappeTestCase` that checks in `setUpClass()`:
1. `voucher_hmac_secret` exists in `frappe.conf`
2. `MEMORA-VOUCHER-CARD` Item exists in the database

If either is missing, `skipTest()` is called with a descriptive message rather than raising an error, so other non-voucher tests still run.

**Rationale**: `setUpClass()` runs once per test class, so the check cost is minimal. Using `skipTest()` rather than `fail()` prevents cascading failures when prerequisites are genuinely missing (e.g., on a CI environment without voucher setup).

**Alternatives considered**:
- conftest.py with pytest — Project uses `bench run-tests` (unittest), not pytest
- Module-level check — Would prevent import and hide the actual error
- Decorator-based skip — More boilerplate per test class

### R7: What dependencies does `make_player()` require?

**Decision**: `make_player()` must create or accept: `Memora Academic Plan` (requires `Memora Season` + `Memora Grade`), `Memora Grade`, `Memora Major`, and `Memora Season`. The factory will accept optional `plan`, `grade`, `major`, `season` parameters and create defaults when not provided. Default chain: `make_season()` → `make_grade()` + `make_major()` → `make_plan()` → `make_player()`.

**Rationale**: Player Profile has 4 required Link fields (`plan`, `grade`, `major`, `season`). Academic Plan has 2 required Links (`grade`, `season`). Creating these transitively keeps the API simple — callers just call `make_player()` and get a fully wired-up player.

**Alternatives considered**:
- Requiring callers to create all dependencies manually — Violates SC-001 (5 lines or fewer)
- Using a single "make everything" function — Too monolithic, not composable

### R8: What about `make_customer()` — does Customer already exist in Frappe?

**Decision**: Customer is a core Frappe/ERPNext DocType. The `make_customer()` factory will create a Customer document and set voucher-specific custom fields (`voucher_requires_approval`, `voucher_commission_type`, `voucher_commission_value`). Since these are custom fields added by the memora_admin app, they may or may not exist depending on installation. The factory will set them using `frappe.db.set_value()` after insert to avoid validation errors if fields don't exist.

**Rationale**: Customer is ERPNext core with many required fields. The factory should set the minimum required fields (`customer_name`, `customer_type`) plus the voucher-specific custom fields. Using `db.set_value()` for custom fields is more robust than including them in the initial doc creation.

**Alternatives considered**:
- Including custom fields in initial `.insert()` — May fail if custom fields aren't installed
- Creating a separate "Memora Library" DocType — Would diverge from existing architecture that uses Customer
