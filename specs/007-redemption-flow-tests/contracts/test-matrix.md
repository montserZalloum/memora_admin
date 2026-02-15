# Test Matrix: Redemption Flow Tests

**Feature**: 007-redemption-flow-tests | **Date**: 2026-02-15

## Test → Requirement Traceability

### User Story 1: Successful Redemption (P1) — 4 tests

| Test ID | Test Name | FR | Acceptance Scenario | Asserts |
|---------|-----------|-----|---------------------|---------|
| TC-01 | `test_redeem_success_card_status_and_transaction` | FR-001 | US1-AC1 | Card status → "Redeemed"; Subscription Transaction exists with status "Completed" |
| TC-02 | `test_redeem_success_card_fields_populated` | FR-002 | US1-AC2 | `redeemed_by`, `redeemed_at`, `redeemed_grant`, `subscription_transaction` all non-null and correct |
| TC-03 | `test_redeem_success_batch_counter_incremented` | FR-003 | US1-AC3 | `batch.redeemed_count` incremented by 1 |
| TC-04 | `test_redeem_success_log_entry_created` | FR-008, FR-009, FR-010 | US1-AC4 | Redemption Log entry with status "Success", masked PIN `****XXXX`, IP address |

### User Story 2: Error Paths (P1) — 9 tests

| Test ID | Test Name | FR | Error Code | Setup |
|---------|-----------|-----|-----------|-------|
| TC-05 | `test_error_invalid_pin` | FR-007 | `INVALID_PIN` | Wrong HMAC value |
| TC-06 | `test_error_not_allocated` | FR-007 | `NOT_ALLOCATED` | Card status set to "Available" via DB |
| TC-07 | `test_error_already_redeemed` | FR-007 | `ALREADY_REDEEMED` | Redeem card first, then retry |
| TC-08 | `test_error_expired` | FR-007 | `EXPIRED` | Card status set to "Expired" via DB |
| TC-09 | `test_error_void` | FR-007 | `VOID` | Card status set to "Void" via DB |
| TC-10 | `test_error_batch_inactive` | FR-007 | `BATCH_INACTIVE` | Batch status set to "Closed" via DB |
| TC-11 | `test_error_season_inactive` | FR-007 | `SEASON_INACTIVE` | Season end_date set to past date |
| TC-12 | `test_error_grant_not_in_batch` | FR-007 | `GRANT_NOT_IN_BATCH` | Use grant not in batch.batch_grants |
| TC-13 | `test_error_already_owned` | FR-007 | `ALREADY_OWNED` | Create Player Subscription with matching access_key |

**All error tests additionally assert** (FR-008):
- Card state is unchanged after failed attempt
- Redemption Log entry created with correct status mapping

### User Story 3: Preview (P2) — 3 tests

| Test ID | Test Name | FR | Acceptance Scenario | Asserts |
|---------|-----------|-----|---------------------|---------|
| TC-14 | `test_preview_returns_grants_and_face_value` | FR-004 | US3-AC1 | Response includes `face_value` and `grants` list |
| TC-15 | `test_preview_filters_owned_grants` | FR-005 | US3-AC2 | Only unowned grant returned when player owns 1 of 2 |
| TC-16 | `test_preview_all_grants_owned_error` | FR-006 | US3-AC3 | Returns `{"error": "ALL_GRANTS_OWNED"}` |

### User Story 4: Audit Logging & Security (P2) — 5 tests

| Test ID | Test Name | FR | Asserts |
|---------|-----------|-----|---------|
| TC-17 | `test_log_success_entry` | FR-008 | Success log entry has correct status, card, batch, grant fields |
| TC-18 | `test_log_failure_entries_all_codes` | FR-008 | Each error code maps to correct human-readable log status |
| TC-19 | `test_log_pin_masked` | FR-009 | `pin_masked` starts with `****` and contains last 4 chars of HMAC |
| TC-20 | `test_log_ip_address_captured` | FR-010 | `ip_address` field matches the value passed to `redeem_voucher()` |
| TC-21 | `test_hmac_uses_compare_digest` | FR-011 | Source of `redeem_voucher` contains `compare_digest` |

### User Story 5: Batch Auto-Close (P3) — 1 test

| Test ID | Test Name | FR | Asserts |
|---------|-----------|-----|---------|
| TC-22 | `test_batch_auto_close_on_last_redemption` | FR-012 | 1-card batch → redeem → batch.status = "Closed" |

## Total: 22 tests

## Helper Functions Required

| Helper | File | Purpose |
|--------|------|---------|
| `get_pins_from_export(batch_name)` | `voucher_helpers.py` | Extract serial_no → PIN dict from encrypted export |
| `preview_card_by_pin(pin, player_id)` | `voucher_helpers.py` | Compute HMAC and call `preview_voucher()` |

## Fixture Enhancement Required

| Fixture | File | Change |
|---------|------|--------|
| `make_product_grant()` | `voucher_fixtures.py` | Add optional `grant_components` parameter for child rows |

## Test Class Structure

```python
class TestMemoraVoucherCard(VoucherTestCase):
    """Integration tests for voucher redemption flow (Phase 7)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Shared setup: create subject, grant, batch, generate, allocate
        # ~30 cards, all allocated to a single library
        # Extract PINs from export

    # US1: Successful Redemption (TC-01 through TC-04)
    # US2: Error Paths (TC-05 through TC-13)
    # US3: Preview (TC-14 through TC-16)
    # US4: Audit Logging & Security (TC-17 through TC-21)
    # US5: Batch Auto-Close (TC-22)
```
