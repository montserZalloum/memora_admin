# Research: Voucher System Audit & Comprehensive Tests

**Phase 0 output** | **Date**: 2026-02-16

## R1: Coverage Gap Analysis — What's Already Tested vs. What's Missing

### Decision: Focus new tests on 4 untested areas

**Rationale**: The 71 existing tests (phases 003-007) comprehensively cover generation, crypto, commission math, invoice creation, and the allocation happy path. The following gaps remain:

### Gap 1: Redemption Edge Cases (ZERO existing tests)
- No tests for `redeem_voucher()` or `preview_voucher()` at all
- No tests for error code paths: INVALID_PIN, NOT_ALLOCATED, ALREADY_REDEEMED, EXPIRED, VOID, BATCH_INACTIVE, SEASON_INACTIVE, ALL_GRANTS_OWNED, GRANT_NOT_IN_BATCH, ALREADY_OWNED
- No tests for the two-step Subscription Transaction save (steps 8-12 in redeem_voucher)
- No tests for redemption atomicity failure (card marked Redeemed but subscription fails)
- No tests for partial grant ownership (some keys owned, not all)
- No tests for empty/null parameter validation

### Gap 2: Voiding Flows (ZERO existing tests)
- No tests for `void_batch()` or `void_card()`
- No tests for mixed-state batch voiding (Available + Allocated voided, Redeemed untouched)
- No tests for encrypted file deletion on void
- No tests for auto-close via `recount_and_maybe_close()` after voiding
- No tests for void validation (Draft batch, Closed batch, Redeemed card)
- No tests for void_reason requirement

### Gap 3: Security/Fraud Gaps (ZERO existing tests)
- No tests documenting the missing rate limiting (GAP-01)
- No tests documenting the missing player ownership validation on redemption (Critical flaw #2)
- No tests documenting the season-check-fails-open behavior (Critical flaw #3)
- No tests for HMAC secret absence during redemption (`hmac.compare_digest` with empty string)
- No tests for grant injection (product_grant_id not in batch)
- `hmac.compare_digest` usage is verified only by code inspection, not by test

### Gap 4: Counter Integrity (PARTIALLY covered)
- `test_allocation_flow.py` tests `allocated_count` and batch activation
- NOT tested: full lifecycle counter progression (generate → allocate → redeem → void → recount)
- NOT tested: `recount_and_maybe_close()` idempotency
- NOT tested: auto-close only for Active batches (not Draft/Generated)
- NOT tested: counter accuracy after void operations

### Already Covered — SKIP
- Commission calculation (11 tests in `test_commission.py`) — covers percentage, fixed, zero, Decimal precision, priority chain
- Invoice creation (8 tests in `test_invoice.py`) — covers Sales Invoice, Credit Note, prepaid flow
- Allocation lifecycle (23 tests in `test_allocation_flow.py`) — covers fill, submit, approve, reject, return, card states, batch counters, state machine
- PIN generation & HMAC (19 tests in `test_generator.py`)
- Fernet crypto (3 tests in `test_crypto.py`)

**Alternatives considered**: Testing everything from scratch was rejected because existing tests are comprehensive and well-structured; duplicate coverage wastes time and creates maintenance burden.

---

## R2: Simulated Concurrency Approach

### Decision: Manual state manipulation (no threading)

**Rationale**: Per clarification, use simulated state — manually set card to Redeemed via `frappe.db.set_value`, then verify that a second `redeem_voucher()` call returns ALREADY_REDEEMED.

**Approach**:
1. Generate batch, allocate cards, export to get PINs
2. Manually set one card's status to Redeemed (simulating a concurrent winner)
3. Call `redeem_voucher()` with the same card's PIN
4. Assert response is `{"error": "ALREADY_REDEEMED"}`
5. Assert Redemption Log entry with status "Already Redeemed"

**Alternatives considered**:
- Real threading with `concurrent.futures` — rejected per user clarification; adds complexity, fragile in test environment
- Database-level `SELECT FOR UPDATE` testing — would require two database connections; overkill for documenting behavior

---

## R3: Security Gap Test Strategy

### Decision: Tests PASS asserting current (insecure) behavior, with `# TODO: SECURITY-FIX` markers

**Rationale**: Per clarification, security gaps are documented via passing tests, not fixed. Each test has a grep-able comment explaining what correct behavior should be.

**Pattern**:
```python
def test_any_user_can_redeem_for_another_player(self):
    """Document: No player ownership validation on redemption API.
    # TODO: SECURITY-FIX - redeem_voucher should verify that the
    # authenticated user owns the player_id being redeemed for.
    # Currently any logged-in user can redeem for any player.
    """
    result = redeem_voucher(pin, other_player_id, grant_id)
    # Passes because no ownership check exists
    self.assertEqual(result.get("status"), "success")
```

**Grep command**: `grep -rn "TODO: SECURITY-FIX\|TODO: FIX" memora_admin/memora_admin/tests/`

**Alternatives considered**:
- Tests that FAIL (asserting correct behavior) — rejected; failing tests block CI and obscure real failures
- Only documenting in comments without tests — rejected; tests serve as executable documentation and regression guards

---

## R4: Redemption Log Immutability Verification

### Decision: Verify log entries are created for every error code path

**Rationale**: The Redemption Log has 13 possible status values. Each error path in `redeem_voucher()` calls `_log_attempt()`. Tests verify that after each error condition, a Redemption Log entry exists with the correct status.

**Approach**: After each redemption attempt (success or failure), query `Memora Voucher Redemption Log` for the most recent entry and verify:
- `status` matches expected error
- `player` matches the caller
- `card` is set (or None for INVALID_PIN)
- `timestamp` is set

---

## R5: Void + File Deletion Testing

### Decision: Test file deletion by checking Frappe File doctype and disk path

**Rationale**: `void_batch()` deletes the encrypted export file via `frappe.delete_doc("File", ...)`. Testing requires:
1. Generate a batch (creates encrypted file)
2. Verify File doc and disk file exist
3. Call `void_batch()`
4. Verify File doc is deleted and `encrypted_file_url` is cleared

**Approach**: Use `frappe.get_all("File", ...)` to check for file existence, and `os.path.exists()` for disk verification.

---

## R6: Test Isolation Strategy

### Decision: Each test class creates its own batch via `setUp`/`setUpClass`

**Rationale**: Tests must not pollute each other. Since voucher operations are destructive (void, redeem), each test class needs fresh data.

**Pattern**:
- `setUpClass()`: Create batch + generate cards + allocate (shared setup for all tests in class)
- `tearDownClass()`: Delete all created documents (batch, cards, allocations, redemption logs)
- Individual tests operate on the pre-allocated cards
- Use existing `SEAS-00027` season for all tests

**Alternatives considered**:
- Per-test `setUp()` — rejected; too slow for integration tests that need full batch lifecycle
- Shared fixtures across files — rejected; creates coupling between test files
