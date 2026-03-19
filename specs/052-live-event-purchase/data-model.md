# Data Model: Single Live Event Purchase (Delta from 051)

This document describes only the **changes** to the data model introduced by 052. The base data model (6 new DocTypes, Redis cache keys, state machines) was established in 051 (commit 378d022).

## Modified Entities

### Memora Live Event Purchase (add field)

**New field**:

| Field | Type | Label | Description | Default | Required | Read Only |
|-------|------|-------|-------------|---------|----------|-----------|
| `expires_at` | Datetime | Expires At | Auto-cancel deadline; set to `now + 30 min` at creation | None | No | Yes |

**Placement**: After the `status` field in the DocType layout.

**Behavior**:
- Set automatically by `create_event_purchase()` — never editable by admin or API
- Only meaningful when `status = "pending"` — ignored for all other statuses
- The auto-cancel job reads this field to find expired purchases

**Query for auto-cancel job**:
```sql
UPDATE `tabMemora Live Event Purchase`
SET status = 'cancelled', modified = NOW(), modified_by = 'Administrator'
WHERE status = 'pending' AND expires_at < NOW()
```

### Memora Live Challenge Event (field removal)

**Removed field**: `erpnext_item_code` — no longer needed. Per-event items are replaced by a shared `LIVE-EVENT-ACCESS` item.

**Removed behavior**: The `before_save` doc_event hook (`ensure_paid_event_item`) has been removed from `hooks.py`. The `erpnext_item_code` field has been removed from both the DocType JSON and the Redis meta hash.

### ERPNext Item (shared for all paid events)

A single shared item `LIVE-EVENT-ACCESS` is used for all paid event invoices:

| Field | Value | Notes |
|-------|-------|-------|
| `item_code` | `LIVE-EVENT-ACCESS` | Shared across all events |
| `item_name` | `Live Event Access` | Generic name |
| `item_group` | `Services` | Service item, not stock |
| `stock_uom` | `Nos` | Standard unit |
| `is_stock_item` | `0` | No inventory tracking |
| `is_sales_item` | `1` | Can appear on Sales Invoices |
| `include_item_in_manufacturing` | `0` | Not manufactured |
| `description` | `Access ticket for Memora live challenge events` | Generic description |

**Event-specific identification**: The invoice line item `description` field carries event details:
`"Live Event Ticket: {event_name} ({event_id}) — {scheduled_start}"`

**Idempotency**: `ensure_shared_live_event_item()` checks `frappe.db.exists("Item", "LIVE-EVENT-ACCESS")` before creating.

**Creation paths**: (1) `setup.py:after_migrate` (eager), (2) `event_purchase.py:_create_purchase_invoice` (lazy guard).

**Migration note**: Old per-event items (e.g., `LIVE-EVENT-LC-00042`) remain in ERPNext for existing invoice references. They are simply no longer created.

## State Machines (unchanged from 051)

### Live Event Purchase

```
                 ┌──→ paid ──→ refunded
                 │
pending ─────────┤
                 ├──→ failed
                 │
                 └──→ cancelled  ← (NEW: auto-cancel after 30 min via scheduled job)
```

- `pending → cancelled`: Triggered by auto-cancel job when `expires_at < NOW()`, OR by manual cancellation
- `paid → refunded`: Triggered by admin refund. **052 addition**: now also creates a Credit Note
- All other transitions unchanged from 051

### Live Event Access

```
active ──→ revoked   (admin revoke)
   │
   └──→ refunded    (purchase refund cascade)
```

No changes from 051.

## Refund Flow (extended in 052)

### Before (051)

```
refund_event_purchase(purchase_id):
  1. purchase.status → "refunded", set refunded_at
  2. access.status → "refunded", set revoked_at
  3. Invalidate Redis cache
  Return: {purchase_id, access_id, status}
```

### After (052)

```
refund_event_purchase(purchase_id):
  1. purchase.status → "refunded", set refunded_at
  2. access.status → "refunded", set revoked_at
  3. IF purchase.erpnext_invoice exists:
       Fetch original invoice: frappe.get_doc("Sales Invoice", purchase.erpnext_invoice)
       Read item_code and description from original_inv.items[0]
       Create Credit Note (Sales Invoice with is_return=1)
         - return_against = purchase.erpnext_invoice
         - customer = _get_player_customer(purchase.player)
         - items[0].item_code = original_inv.items[0].item_code  (backward compatible)
         - items[0].description = original_inv.items[0].description
         - items[0].qty = -1
         - items[0].rate = purchase.amount
       Submit Credit Note
  4. Invalidate Redis cache
  Return: {purchase_id, access_id, credit_note_id, status}
```

**Atomicity**: Steps 1-3 execute within a single Frappe transaction. If Credit Note creation fails (step 3), the entire transaction rolls back — purchase stays `paid`, access stays `active`. This satisfies FR-011.

## Entity Relationship Diagram (052 additions highlighted)

```
┌─────────────────────────┐
│ Memora Live Challenge   │
│ Event                   │       ┌──────────────────────────┐
│─────────────────────────│       │ ERPNext Item             │
│ is_paid                 │       │ LIVE-EVENT-ACCESS        │
│ price                   │       │ (shared, created once)   │
│ currency                │       └──────────┬───────────────┘
│ eligible_plans[]        │                  │ used by all invoices
└─────────┬───────────────┘                  │
          │ 1:N                              │
          ▼                                  │
┌─────────────────────────┐         ┌────────┴─────────────┐
│ Memora Live Event       │         │ ERPNext Sales Invoice │
│ Purchase                │         │                      │
│─────────────────────────│    1:1  │ item: LIVE-EVENT-    │
│ status                  │────────→│   ACCESS             │
│ expires_at ★ (NEW)      │         │ desc: event-specific │
│ amount / currency       │         └──────────┬───────────┘
│ event_access_ref ───────│──┐                 │
└─────────────────────────┘  │                 │ return_against
                             │                 ▼
                             │    ┌──────────────────────┐
                             │    │ Credit Note ★ (NEW)  │
                             │    │ (is_return=1)        │
                             │    │ reads item from      │
                             │    │ original invoice     │
                             │    └──────────────────────┘
                             │
                             ▼
                   ┌─────────────────────────┐
                   │ Memora Live Event       │
                   │ Access                  │
                   │─────────────────────────│
                   │ status (active/refunded)│
                   │ access_type             │
                   └─────────────────────────┘
```

★ = New in 052
