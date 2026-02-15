# Data Model: Voucher Test Infrastructure

**Feature**: 002-voucher-test-infra | **Date**: 2026-02-15

## Overview

This feature does NOT create new DocTypes or database tables. It creates factory functions that produce instances of existing DocTypes and helper functions that orchestrate existing API operations. This document maps the existing entities that factories produce.

## Entity Map (Existing DocTypes → Factory Functions)

### Memora Voucher Batch → `make_batch()`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `batch_name` | Data (reqd) | `f"Test Batch {random_string(8)}"` | Auto-generated unique name |
| `quantity` | Int (reqd) | `10` | Small default for fast tests |
| `pin_length` | Select (12/14/16) | `12` | Shortest option |
| `face_value` | Currency (reqd) | `5` | Non-zero for invoice tests |
| `status` | Select | `"Draft"` | Valid initial state |
| `batch_grants` | Table | `[]` | Empty by default; caller passes grants |

**Autoname**: `VBATCH-.#####.`

**Child table: Memora Voucher Batch Grant**

| Field | Type | Notes |
|-------|------|-------|
| `product_grant` | Link (reqd) | Reference to `Memora Product Grant` |
| `commission_type` | Select | Optional: Percentage / Fixed Amount |
| `commission_value` | Data | Optional: commission amount |

### Memora Product Grant → `make_product_grant()`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `plan` | Link (reqd) | Auto-created via `make_plan()` | `Memora Academic Plan` |
| `item_code` | Link (reqd) | `"MEMORA-VOUCHER-CARD"` | Must exist as Item |
| `is_published` | Check | `1` | Published by default for tests |
| `grant_components` | Table | `[]` | Optional; empty = no access keys |

**Autoname**: `GRNT-.#####.`

### Memora Season → `make_season()`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `season_title` | Data (reqd) | `f"Test Season {random_string(8)}"` | Unique name |
| `season_seq` | Int (reqd) | `1` | Sequence number |
| `start_date` | Date (reqd) | `frappe.utils.today()` | Current date |
| `end_date` | Date (reqd) | `frappe.utils.add_days(today(), 365)` | 1 year out |
| `is_published` | Check | `1` | Published for active season |

**Autoname**: `SEAS-.#####.`

### Customer (Frappe core) → `make_customer()`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `customer_name` | Data (reqd) | `f"Test Library {random_string(8)}"` | Unique name |
| `customer_type` | Select | `"Company"` | Standard ERPNext |
| `voucher_requires_approval` | Check (custom) | `0` | No approval = auto-complete |
| `voucher_commission_type` | Select (custom) | `None` | Optional commission |
| `voucher_commission_value` | Data (custom) | `None` | Optional commission value |

**Autoname**: ERPNext default (Customer name-based)

### Memora Player Profile → `make_player()`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `display_name` | Data (reqd) | `f"Test Player {random_string(8)}"` | Unique name |
| `plan` | Link (reqd) | Auto-created | `Memora Academic Plan` |
| `grade` | Link (reqd) | Auto-created | `Memora Grade` |
| `major` | Link (reqd) | Auto-created | `Memora Major` |
| `season` | Link (reqd) | Auto-created | `Memora Season` |
| `avatar` | Select (reqd) | `"pre"` | First available option |

**Autoname**: `PLAYER-.#####.`

**Transitive dependencies** (auto-created when not provided):
- `Memora Grade` — `grade_title` = `f"Test Grade {random_string(8)}"`
- `Memora Major` — `major_title` = `f"Test Major {random_string(8)}"`
- `Memora Academic Plan` — requires `grade` + `season`

### Memora Voucher Allocation → `make_allocation()`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `allocation_type` | Select (reqd) | `"Allocate"` | Standard forward allocation |
| `batch` | Link (reqd) | *caller must provide* | `Memora Voucher Batch` |
| `customer` | Link (reqd) | *caller must provide* | Customer (library) |
| `status` | Select (reqd) | `"Draft"` | Initial state |
| `sale_model` | Select (reqd) | `"Prepaid"` | Standard sale model |

**Autoname**: `VALLOC-.#####.`

## Relationship Diagram

```
make_season() ──────────────────────────┐
                                        │
make_grade() ───────────────┐           │
                            ▼           ▼
make_major() ───────> make_plan() [Academic Plan]
                            │
                            ▼
make_product_grant() ──────[plan]──────> [Product Grant]
                                              │
make_batch(grants=[...]) ─────[batch_grants]──┘
       │
       ▼
make_customer() ──────────┐
                          ▼
make_allocation(batch, customer)
       │
       ▼
make_player(plan, grade, major, season)
```

## State Machines (relevant to helpers)

### Batch Lifecycle (via `generate_batch_sync`)
```
Draft ──[generate_cards_job()]──> Generated
```

### Allocation Lifecycle (via `fill_and_complete_allocation`)
```
Draft ──[fill_cards()]──> Draft (with cards)
      ──[submit_allocation()]──> Completed (auto-approve)
                              or Pending Approval (requires approval)
      ──[approve_allocation()]──> Completed
```

### Card Lifecycle (via `redeem_card_by_pin`)
```
Available ──[allocation complete]──> Allocated ──[redeem_voucher()]──> Redeemed
```
