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

### Memora Live Challenge Event (add hook behavior)

**No new fields.** New behavior on `before_save`:

| Trigger | Condition | Action |
|---------|-----------|--------|
| `before_save` | `is_paid == 1` AND `erpnext_item_code` is empty | Create ERPNext Item, set `erpnext_item_code` |
| `before_save` | `is_paid == 1` AND `erpnext_item_code` is set AND item exists | No-op (idempotent) |
| `before_save` | `is_paid == 0` | No-op (FR-014: never delete items) |

### ERPNext Item (auto-created for paid events)

| Field | Value | Notes |
|-------|-------|-------|
| `item_code` | `LIVE-EVENT-{event.name}` | e.g., `LIVE-EVENT-LC-00042` |
| `item_name` | `Live Event Ticket: {event.event_title}` | Human-readable |
| `item_group` | `Services` | Service item, not stock |
| `stock_uom` | `Nos` | Standard unit |
| `is_stock_item` | `0` | No inventory tracking |
| `is_sales_item` | `1` | Can appear on Sales Invoices |
| `include_item_in_manufacturing` | `0` | Not manufactured |
| `description` | `Ticket for live event {event.name}` | For invoice line items |

**Idempotency**: Before creating, check `frappe.db.exists("Item", item_code)`. If exists, just set `doc.erpnext_item_code = item_code` without creating.

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
       Create Credit Note (Sales Invoice with is_return=1)
         - return_against = purchase.erpnext_invoice
         - customer = _get_player_customer(purchase.player)
         - items[0].item_code = purchase.erpnext_item_code
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
│ Event                   │
│─────────────────────────│
│ is_paid                 │
│ price                   │
│ currency                │
│ erpnext_item_code ──────│──→ ERPNext Item (auto-created on save)
│ eligible_plans[]        │
└─────────┬───────────────┘
          │ 1:N
          ▼
┌─────────────────────────┐         ┌──────────────────────┐
│ Memora Live Event       │         │ ERPNext Sales Invoice │
│ Purchase                │         │                      │
│─────────────────────────│    1:1  │                      │
│ status                  │────────→│ (created on payment) │
│ expires_at ★ (NEW)      │         └──────────┬───────────┘
│ amount / currency       │                    │
│ erpnext_item_code       │                    │ return_against
│ event_access_ref ───────│──┐                 ▼
└─────────────────────────┘  │    ┌──────────────────────┐
                             │    │ Credit Note ★ (NEW)  │
                             │    │ (is_return=1)        │
                             │    │ Created on refund    │
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
