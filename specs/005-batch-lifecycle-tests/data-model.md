# Data Model: Batch Lifecycle Integration Tests

**Feature**: 005-batch-lifecycle-tests
**Date**: 2026-02-15

> This feature is test-only. No new entities or schema changes are introduced.
> This document describes the existing entities under test.

## Entities Under Test

### Memora Voucher Batch

The primary entity being tested. Represents a generation order for voucher cards.

**Key fields exercised by tests**:

| Field              | Type     | Test Coverage                              |
|--------------------|----------|--------------------------------------------|
| status             | Select   | Draft → Generated transition (FR-002)      |
| quantity           | Int      | Card count validation (FR-001, FR-008, FR-009) |
| pin_length         | Select   | Passed to generator (FR-001)               |
| face_value         | Currency | Included in export CSV (FR-011)            |
| batch_grants       | Table    | Product grant linkage (FR-001)             |
| generated_count    | Int      | Counter accuracy (FR-003)                  |
| allocated_count    | Int      | Zero after generation (FR-003)             |
| redeemed_count     | Int      | Zero after generation (FR-003)             |
| voided_count       | Int      | Zero after generation (FR-003)             |
| expired_count      | Int      | Zero after generation (FR-003)             |
| encrypted_file_url | Data     | File existence (FR-004)                    |
| export_log         | Table    | Audit trail (FR-012)                       |

**State transitions tested**:
```
Draft → Generated  (via generate_cards_job)
Draft → ERROR      (via generate_batch guard rails)
```

### Memora Voucher Card

Individual card records created during generation.

**Key fields exercised by tests**:

| Field     | Type   | Test Coverage                         |
|-----------|--------|---------------------------------------|
| serial_no | Data   | VCH-NNNNNN format (FR-005)           |
| pin_hmac  | Data   | HMAC presence, no plaintext (FR-006) |
| batch     | Link   | Batch linkage (FR-001)               |
| status    | Select | "Available" after generation (FR-001) |

### Memora Product Grant

Required dependency for batch configuration. Created via `make_product_grant()` fixture.

### Memora Voucher Batch Export Log (child table)

Audit trail entry created on export.

**Fields exercised by tests**:

| Field       | Type     | Test Coverage              |
|-------------|----------|----------------------------|
| exported_by | Data     | User recorded (FR-012)     |
| exported_at | Datetime | Timestamp recorded (FR-012)|
| card_count  | Int      | Count recorded (FR-012)    |

## Relationships

```
Memora Voucher Batch
  ├── 1:N → Memora Voucher Card (via card.batch)
  ├── 1:N → Memora Voucher Batch Grant (child table)
  └── 1:N → Memora Voucher Batch Export Log (child table)

Memora Voucher Batch Grant
  └── N:1 → Memora Product Grant (via batch_grant.product_grant)
```

## No Schema Changes

This phase introduces zero modifications to any DocType JSON schema. All entities and fields already exist from prior phases.
