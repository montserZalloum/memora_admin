# Data Model: Voucher System Audit & Comprehensive Tests

**Phase 1 output** | **Date**: 2026-02-16

This feature is test-only (no new production code), so "data model" here means the **test file organization and complete test method inventory**.

## Test File 1: `test_redemption_edge.py`

Tests the `redeem_voucher()` and `preview_voucher()` API functions for edge cases and error codes not covered by existing tests.

**Source under test**: `memora_admin/api/voucher.py:462-691`

### Class: `TestRedemptionErrorCodes` (extends VoucherTestCase)

| # | Method | Acceptance Scenario | FR | Setup |
|---|--------|--------------------|----|-------|
| 1 | `test_invalid_pin_returns_error` | Invalid PIN → INVALID_PIN | FR-003 | Random bogus HMAC |
| 2 | `test_already_redeemed_returns_error` | Card status=Redeemed → ALREADY_REDEEMED (simulated concurrent) | FR-001 | Manually set card to Redeemed |
| 3 | `test_not_allocated_card_returns_error` | Card status=Available → NOT_ALLOCATED | FR-001 | Generated but not allocated card |
| 4 | `test_void_card_returns_error` | Card status=Void → VOID | FR-001 | Manually void card |
| 5 | `test_expired_card_returns_error` | Card status=Expired → EXPIRED | FR-001 | Manually expire card |
| 6 | `test_batch_inactive_returns_error` | Batch status≠Active → BATCH_INACTIVE | FR-001 | Set batch to Closed |
| 7 | `test_grant_not_in_batch_returns_error` | product_grant_id not in batch → GRANT_NOT_IN_BATCH | FR-005 | Use invalid grant ID |
| 8 | `test_empty_grant_id_returns_error` | Empty product_grant_id → validation error or GRANT_NOT_IN_BATCH | FR-005 | Pass empty string |
| 9 | `test_all_grants_owned_returns_error` | Player owns all grant keys → ALL_GRANTS_OWNED | FR-004 | Create player subscriptions for all keys |
| 10 | `test_partial_grant_ownership_allows_redemption` | Player owns some (not all) keys → redemption proceeds | FR-004 | Create subscription for 1 of N keys |

### Class: `TestRedemptionAtomicity` (extends VoucherTestCase)

| # | Method | Acceptance Scenario | FR | Setup |
|---|--------|--------------------|----|-------|
| 11 | `test_successful_redemption_creates_transaction` | Success → card Redeemed + SubscriptionTransaction created | FR-001 | Normal redemption |
| 12 | `test_redemption_log_created_on_success` | Success → Redemption Log entry with status "Success" | FR-001 | Normal redemption |
| 13 | `test_redemption_log_created_on_failure` | ALREADY_REDEEMED → Redemption Log entry with correct status | FR-001 | Simulated concurrent |
| 14 | `test_redemption_updates_batch_counters` | After redemption → batch redeemed_count incremented | FR-012 | Normal redemption + counter check |

### Class: `TestPreviewVoucher` (extends VoucherTestCase)

| # | Method | Acceptance Scenario | FR | Setup |
|---|--------|--------------------|----|-------|
| 15 | `test_preview_returns_grants_for_allocated_card` | Allocated card → grants list with face_value | — | Normal allocated card |
| 16 | `test_preview_filters_owned_grants` | Player owns all grants → ALL_GRANTS_OWNED | FR-004 | Create subscriptions |
| 17 | `test_preview_invalid_pin` | Invalid PIN → INVALID_PIN | FR-003 | Bogus HMAC |

---

## Test File 2: `test_voiding.py`

Tests `void_batch()` and `void_card()` API functions plus auto-close behavior.

**Source under test**: `memora_admin/api/voucher.py:274-359`, `services/voucher/batch_utils.py`

### Class: `TestVoidBatch` (extends VoucherTestCase)

| # | Method | Acceptance Scenario | FR | Setup |
|---|--------|--------------------|----|-------|
| 1 | `test_void_batch_with_mixed_states` | Available+Allocated→Void, Redeemed untouched, batch→Closed | FR-009 | Batch with mixed card states |
| 2 | `test_void_batch_requires_reason` | Empty void_reason → ValidationError | FR-009 | Pass empty string |
| 3 | `test_void_draft_batch_raises_error` | Draft batch → ValidationError | FR-009 | Batch in Draft status |
| 4 | `test_void_closed_batch_raises_error` | Closed batch → ValidationError | FR-009 | Batch in Closed status |
| 5 | `test_void_batch_deletes_encrypted_file` | File doc + disk file removed after void | FR-018 | Generate batch with export |

### Class: `TestVoidCard` (extends VoucherTestCase)

| # | Method | Acceptance Scenario | FR | Setup |
|---|--------|--------------------|----|-------|
| 6 | `test_void_available_card` | Available card → Void, void_reason set, counters updated | FR-010 | Single Available card |
| 7 | `test_void_allocated_card` | Allocated card → Void, counters updated | FR-010 | Single Allocated card |
| 8 | `test_void_redeemed_card_raises_error` | Redeemed card → ValidationError | FR-010 | Redeemed card |
| 9 | `test_void_card_triggers_auto_close` | Last non-terminal card voided → batch auto-closes | FR-010 | Batch with 1 remaining Available card |

---

## Test File 3: `test_security_audit.py`

Documents known security gaps as passing tests with `# TODO: SECURITY-FIX` markers.

**Source under test**: `memora_admin/api/voucher.py` (various functions)

### Class: `TestSecurityGaps` (extends VoucherTestCase)

| # | Method | Acceptance Scenario | FR | Marker |
|---|--------|--------------------|----|--------|
| 1 | `test_no_rate_limiting_on_redemption` | Multiple rapid redemption attempts succeed without rate limit | FR-016 | `# TODO: SECURITY-FIX` |
| 2 | `test_any_user_can_redeem_for_other_player` | Redemption with another player's ID succeeds | FR-016 | `# TODO: SECURITY-FIX` |
| 3 | `test_season_check_fails_open_on_exception` | Database error in season check → redemption allowed | FR-016 | `# TODO: SECURITY-FIX` |
| 4 | `test_hmac_uses_timing_safe_comparison` | Verify `hmac.compare_digest` is used (code inspection test) | FR-017 | — |
| 5 | `test_hmac_secret_absent_redemption_behavior` | Missing HMAC secret during redemption → graceful error | FR-017 | `# TODO: FIX` |

### Class: `TestAllocationSecurityGaps` (extends VoucherTestCase)

| # | Method | Acceptance Scenario | FR | Marker |
|---|--------|--------------------|----|--------|
| 6 | `test_reallocation_steals_cards_from_other_library` | Cards allocated to Library A can be re-allocated to Library B without return | FR-016 | `# TODO: SECURITY-FIX` |
| 7 | `test_stale_cards_in_allocation_accepted` | Cards voided between fill and submit are still accepted | FR-016 | `# TODO: FIX` |

---

## Test File 4: `test_counter_integrity.py`

Tests batch counter accuracy across operations and `recount_and_maybe_close()` behavior.

**Source under test**: `services/voucher/batch_utils.py`, `api/voucher.py`

### Class: `TestCounterIntegrity` (extends VoucherTestCase)

| # | Method | Acceptance Scenario | FR | Setup |
|---|--------|--------------------|----|-------|
| 1 | `test_full_lifecycle_counter_accuracy` | generate→allocate→redeem 3→void 2→return 2→recount → all counters correct | FR-012 | Full lifecycle |
| 2 | `test_recount_idempotency` | Two consecutive recount calls return identical results | FR-013 | Any batch state |
| 3 | `test_auto_close_only_active_batches` | Generated batch with all terminal cards → does NOT auto-close | FR-013 | Generated batch |
| 4 | `test_auto_close_on_all_terminal_cards` | Active batch with all Redeemed/Void/Expired → auto-closes | FR-013 | Active batch with terminal cards |
| 5 | `test_counters_after_void_batch` | void_batch() → voided_count accurate, redeemed_count unchanged | FR-012 | Mixed-state batch |

---

## Fixture Requirements

All new tests reuse existing factory functions from `voucher_fixtures.py`:

| Factory | Parameters Used | Notes |
|---------|----------------|-------|
| `make_product_grant()` | `season="SEAS-00027"` | Creates grant + plan + grade |
| `make_batch()` | `quantity=10`, `grants=[grant.name]` | Small batches for speed |
| `make_customer()` | `requires_approval=0` | Default no-approval for simplicity |
| `make_player()` | `season="SEAS-00027"` | Creates player profile |
| `make_allocation()` | `allocation_type="Allocate"` | For allocation tests |

Helpers from `voucher_helpers.py`:

| Helper | Used By |
|--------|---------|
| `generate_batch_sync()` | All test classes (generates cards) |
| `fill_and_complete_allocation()` | Redemption & voiding tests (allocates cards) |
| `get_pins_from_export()` | Redemption tests (gets plaintext PINs) |
| `redeem_card_by_pin()` | Redemption tests (redeems via PIN) |
| `assert_batch_counters()` | Counter integrity tests |
