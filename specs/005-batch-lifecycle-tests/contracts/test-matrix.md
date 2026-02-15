# Test Contract: Batch Lifecycle Integration Tests

**Feature**: 005-batch-lifecycle-tests
**Date**: 2026-02-15

> No new API endpoints are introduced. This document maps existing API functions
> to the integration tests that exercise them.

## Functions Under Test

### `generate_batch(batch_name)` — Validation Layer

| Test Method                          | Input                          | Expected Behavior                    | FR    |
|--------------------------------------|--------------------------------|--------------------------------------|-------|
| `test_generate_non_draft_fails`      | Batch status = "Generated"     | Raises `ValidationError`             | FR-007 |
| `test_generate_already_generated_fails` | Batch status = "Generated"  | Raises `ValidationError`             | FR-013 |
| `test_generate_zero_quantity_fails`  | quantity = 0                   | Raises `ValidationError`             | FR-008 |
| `test_generate_exceeds_max_fails`    | quantity = 1001                | Raises `ValidationError`             | FR-009 |
| `test_generate_no_hmac_secret_fails` | No `voucher_hmac_secret`       | Raises `ValidationError`             | FR-010 |

### `generate_cards_job(batch_name)` — Generation Layer

| Test Method                          | Input                          | Expected Behavior                    | FR    |
|--------------------------------------|--------------------------------|--------------------------------------|-------|
| `test_generate_creates_cards`        | Draft batch, qty=10, 1 grant   | 10 cards, status=Available           | FR-001 |
| `test_generate_status_transition`    | Draft batch                    | Batch status → Generated             | FR-002 |
| `test_generate_counters`             | Draft batch, qty=10            | generated=10, others=0               | FR-003 |
| `test_generate_encrypted_file`       | Draft batch                    | encrypted_file_url set, file exists  | FR-004 |
| `test_generate_serial_format`        | Draft batch                    | All serials match VCH-NNNNNN         | FR-005 |
| `test_generate_hmac_stored`          | Draft batch                    | pin_hmac set, no pin column          | FR-006 |
| `test_generate_rollback_on_failure`  | Draft batch + patched failure  | 0 cards, status=Draft                | FR-014 |

### `export_for_print(batch_name)` — Export Layer

| Test Method                          | Input                          | Expected Behavior                    | FR    |
|--------------------------------------|--------------------------------|--------------------------------------|-------|
| `test_export_decrypts_correctly`     | Generated batch                | CSV content matches cards            | FR-011 |
| `test_export_audit_logged`           | Generated batch                | export_log child row added           | FR-012 |

## State Transition Coverage

```
Batch Status:
  Draft → Generated  ✅ test_generate_status_transition
  Draft → ERROR      ✅ 5 guard rail tests
  Generated → ERROR  ✅ test_generate_non_draft_fails, test_generate_already_generated_fails

Card Status:
  (none) → Available ✅ test_generate_creates_cards
```

## Traceability Matrix

| FR     | Test Method                          | User Story |
|--------|--------------------------------------|------------|
| FR-001 | test_generate_creates_cards          | US1        |
| FR-002 | test_generate_status_transition      | US1        |
| FR-003 | test_generate_counters               | US1        |
| FR-004 | test_generate_encrypted_file         | US1        |
| FR-005 | test_generate_serial_format          | US1        |
| FR-006 | test_generate_hmac_stored            | US1        |
| FR-007 | test_generate_non_draft_fails        | US2        |
| FR-008 | test_generate_zero_quantity_fails    | US2        |
| FR-009 | test_generate_exceeds_max_fails      | US2        |
| FR-010 | test_generate_no_hmac_secret_fails   | US2        |
| FR-011 | test_export_decrypts_correctly       | US3        |
| FR-012 | test_export_audit_logged             | US3        |
| FR-013 | test_generate_already_generated_fails | US2       |
| FR-014 | test_generate_rollback_on_failure    | US4        |
