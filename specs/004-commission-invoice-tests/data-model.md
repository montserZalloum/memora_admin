# Data Model: Commission & Invoice Unit Tests

**Feature**: 004-commission-invoice-tests
**Date**: 2026-02-15

## Overview

This feature creates unit and integration tests — no new data models are introduced. This document catalogs the **existing entities** exercised by the tests, their fields relevant to commission/invoice logic, and the relationships between them.

## Entities Under Test

### 1. Commission Calculation (Pure Value Object)

Not a DocType — a dict returned by `calculate_commission()`.

| Field | Type | Description |
|-------|------|-------------|
| `per_card_commission` | `Decimal` | Commission deducted from a single card's face value |
| `total_commission` | `Decimal` | `per_card_commission * quantity` |
| `net_per_card` | `Decimal` | `face_value - per_card_commission` |
| `net_total` | `Decimal` | `net_per_card * quantity` |

**Inputs**: `face_value: str`, `quantity: int`, `commission_type: str|None`, `commission_value: str|None`

**Validation Rules**:
- All outputs quantized to 2 decimal places with `ROUND_HALF_UP`
- Unknown `commission_type` → zero commission
- Missing `commission_type` or `commission_value` → zero commission

---

### 2. Customer (Library) — Frappe Core DocType with Custom Fields

| Custom Field | Type | Options | Used By |
|-------------|------|---------|---------|
| `voucher_commission_type` | Select | `\nPercentage\nFixed Amount` | `resolve_commission()` tier 2 |
| `voucher_commission_value` | Data | (string, parsed as Decimal) | `resolve_commission()` tier 2 |
| `voucher_requires_approval` | Check | 0/1 | Allocation workflow |

---

### 3. Memora Voucher Batch Grant (Child Table)

**Parent**: `Memora Voucher Batch`

| Field | Type | Options | Used By |
|-------|------|---------|---------|
| `product_grant` | Link | Memora Product Grant | Grant identification |
| `commission_type` | Select | `\nPercentage\nFixed Amount` | `resolve_commission()` tier 1 |
| `commission_value` | Data | (string) | `resolve_commission()` tier 1 |

---

### 4. Sales Invoice (ERPNext Core)

Created by `create_voucher_invoice()` and `create_prepaid_allocation_invoice()`.

| Field | Value | Notes |
|-------|-------|-------|
| `customer` | Library name | Matches allocation customer |
| `posting_date` | `nowdate()` or override | |
| `remarks` | Allocation/batch reference | |
| `docstatus` | 1 (Submitted) | Always submitted |
| `items[0].item_code` | `MEMORA-VOUCHER-CARD` | Hardcoded constant |
| `items[0].qty` | Card count | From `len(allocation.allocation_cards)` |
| `items[0].rate` | `float(net_per_card)` | Decimal→float at ORM boundary |

---

### 5. Credit Note (ERPNext Core — Sales Invoice with return flags)

Created by `create_credit_note()` and `create_prepaid_return_credit_note()`.

| Field | Value | Notes |
|-------|-------|-------|
| `is_return` | 1 | Marks as Credit Note |
| `return_against` | Original SI name | Required (validated) |
| `items[0].qty` | `-abs(qty)` | Negative for returns |
| `docstatus` | 1 (Submitted) | Always submitted |

---

### 6. Memora Voucher Allocation

| Field | Type | Relevance |
|-------|------|-----------|
| `batch` | Link | Source batch for commission resolution |
| `customer` | Link | Library for commission resolution + invoice customer |
| `allocation_type` | Select | `Allocate` or `Return` |
| `sale_model` | Select | `Prepaid` triggers financial docs |
| `status` | Select | Must be `Completed` for invoice creation |
| `allocation_cards` | Table | Card count determines invoice quantity |
| `sales_invoice` | Link | Set after invoice/credit note creation |

## Entity Relationship Diagram

```
Customer (Library)
  ├── voucher_commission_type/value  ──── resolve_commission() tier 2
  └── allocation.customer  ──────────── invoice.customer

Memora Voucher Batch
  └── batch_grants[] ──── Memora Voucher Batch Grant
        ├── commission_type/value  ──── resolve_commission() tier 1
        └── product_grant  ──── Memora Product Grant

Memora Voucher Allocation
  ├── batch ──── Memora Voucher Batch (face_value)
  ├── customer ──── Customer (library)
  ├── allocation_cards[] ──── card count → invoice qty
  └── sales_invoice ──── Sales Invoice (linked after creation)

Sales Invoice
  ├── customer ←── allocation.customer
  ├── items[].rate ←── calculate_commission().net_per_card
  └── items[].qty ←── len(allocation.allocation_cards)

Credit Note (Sales Invoice)
  ├── is_return = 1
  ├── return_against ←── original Sales Invoice
  └── items[].qty ←── -abs(card_count)
```

## State Transitions Exercised

```
Commission Resolution:
  batch_grant.commission_type SET  →  return (grant.type, grant.value)
  batch_grant not set, customer.voucher_commission_type SET  →  return (customer.type, customer.value)
  neither set  →  return (None, None)

Invoice Creation:
  Allocation.status == "Completed" + sale_model == "Prepaid"
    → create_prepaid_allocation_invoice()
    → Sales Invoice (docstatus=1) + allocation.sales_invoice linked

Credit Note Creation:
  Return Allocation.status == "Completed" + sale_model == "Prepaid"
    → create_prepaid_return_credit_note()
    → Credit Note (docstatus=1, is_return=1) + allocation.sales_invoice linked
```
