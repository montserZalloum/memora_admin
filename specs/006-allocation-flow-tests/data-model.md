# Data Model: Integration Tests — Allocation Flow

**Feature**: 006-allocation-flow-tests | **Date**: 2026-02-15

## Entities Under Test

### Memora Voucher Allocation (Primary)

The central entity being tested. Tracks card assignment/return between batches and libraries.

| Field | Type | Tested? | Test Assertions |
|-------|------|---------|-----------------|
| `allocation_type` | Select (Allocate/Return) | Yes | Determines fill logic (Available vs Allocated cards) |
| `batch` | Link → Memora Voucher Batch | Yes | Cards must belong to this batch |
| `customer` | Link → Customer | Yes | Library receiving/returning cards |
| `status` | Select | **Yes (primary)** | State machine transitions tested exhaustively |
| `sale_model` | Select (Prepaid/Consignment) | Yes | Determines invoice creation |
| `quantity` | Int (read-only) | Yes | Auto-calculated from allocation_cards length |
| `allocation_cards` | Table → Allocation Card | Yes | Filled by fill_cards(), validated on submit |
| `notes` | Small Text | Yes | Stores rejection reason |
| `sales_invoice` | Link → Sales Invoice | Yes | Set after prepaid invoice creation |

**Status State Machine**:
```
Draft ──→ Pending Approval ──→ Approved ──→ Completed (terminal)
  │                              ↗
  └──→ Approved ────────────────┘
  └──→ Cancelled (terminal)
         Pending Approval ──→ Rejected (terminal)
```

### Memora Voucher Allocation Card (Child Table)

| Field | Type | Tested? | Test Assertions |
|-------|------|---------|-----------------|
| `voucher_card` | Link → Memora Voucher Card | Yes | Card references populated by fill_cards() |
| `serial_no` | Data (fetched) | No | Read-only display field |
| `card_status` | Data (fetched) | No | Read-only display field |

### Memora Voucher Card (Mutated)

Cards are mutated by allocation completion hooks.

| Field | Type | Tested? | Allocate Sets | Return Sets |
|-------|------|---------|--------------|-------------|
| `status` | Select | **Yes** | "Allocated" | "Available" |
| `library` | Link → Customer | **Yes** | customer name | NULL |
| `allocation` | Link → Allocation | **Yes** | allocation name | NULL |
| `sale_model` | Select | **Yes** | "Prepaid"/"Consignment" | NULL |
| `return_allocation` | Link → Allocation | **Yes** | (unchanged) | allocation name |

### Memora Voucher Batch (Updated)

Batch metadata updated after allocation completion.

| Field | Type | Tested? | Test Assertions |
|-------|------|---------|-----------------|
| `status` | Select | **Yes** | Generated→Active on first allocation |
| `allocated_count` | Int | **Yes** | Recounted from actual card status="Allocated" |
| `generated_count` | Int | Yes | Unchanged (set during generation) |

### Customer (Library) — Input Only

| Field | Type | Tested? | Test Purpose |
|-------|------|---------|-------------|
| `voucher_requires_approval` | Check | **Yes** | Controls approval routing |
| `voucher_commission_type` | Select | Yes | Commission calculation for invoices |
| `voucher_commission_value` | Data | Yes | Commission amount/percentage |

### Sales Invoice (Created)

| Field | Type | Tested? | Test Assertions |
|-------|------|---------|-----------------|
| `docstatus` | Int | Yes | Must be 1 (submitted) |
| `customer` | Link | Yes | Matches allocation customer |
| `items[0].item_code` | Data | Yes | "MEMORA-VOUCHER-CARD" |
| `items[0].qty` | Int | Yes | Matches allocation card count |
| `items[0].rate` | Currency | Yes | Net per card after commission |

## Entity Relationships (Test Flow)

```
Test Setup:
  make_product_grant(season="SEAS-00027")
    └──→ Memora Product Grant
  make_batch(grants=[grant.name])
    └──→ Memora Voucher Batch (Draft)
  generate_batch_sync(batch.name)
    └──→ Memora Voucher Card × N (Available)
    └──→ Batch → status="Generated"
  make_customer(requires_approval=T/F, commission_type=..., commission_value=...)
    └──→ Customer

Test Execution:
  make_allocation(batch, customer, type, sale_model)
    └──→ Memora Voucher Allocation (Draft)
  fill_cards(allocation, quantity)
    └──→ Allocation Card child rows populated
  submit_allocation(allocation)
    ├── [no approval] → Approved → Completed
    │   ├──→ Cards: Available → Allocated (with library, allocation, sale_model)
    │   ├──→ Batch: Generated → Active, allocated_count updated
    │   └──→ Sales Invoice (if Prepaid)
    └── [requires approval] → Pending Approval
        ├── approve_allocation() → Approved → Completed (same as above)
        └── reject_allocation() → Rejected (no card/batch changes)
```

## Test Data Matrix

| Test Class | Batch Qty | Allocate Qty | Library Approval | Sale Model | Commission |
|------------|----------|-------------|-----------------|------------|------------|
| TestFillCards | 10 | varies | No | Prepaid | None |
| TestSubmitAndApproval | 10 | 10 | Both | Prepaid | None |
| TestCardStateOnAllocate | 10 | 5 | No | Prepaid | None |
| TestCardStateOnReturn | 10 | 5→return | No | Prepaid | None |
| TestBatchCountersAndStatus | 10 | 5 | No | Prepaid | None |
| TestPrepaidInvoiceOnAllocation | 10 | 5 | No | Prepaid | 10% |
| TestStateMachineEnforcement | 10 | 10 | Yes | Prepaid | None |
