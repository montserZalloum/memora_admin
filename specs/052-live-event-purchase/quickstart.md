# Quickstart: Single Live Event Purchase (052)

## What's New

Three gaps from the 051 monetized-access foundation:

1. **Purchase Expiry** (FR-001, FR-010): Pending purchases auto-cancel after 30 minutes via a scheduled job
2. **Refund Credit Note** (FR-011): Refunds now atomically create an accounting Credit Note linked to the original invoice
3. **Shared Item** (FR-013): All paid events use a single shared `LIVE-EVENT-ACCESS` ERPNext Item on invoices; event-specific details in the line description

## Prerequisites

- 051 monetized-access fully deployed (commit 378d022)
- ERPNext available with Item, Sales Invoice, Credit Note DocTypes
- Frappe bench with scheduler enabled
- Redis running (for existing cache invalidation — no new keys in 052)

## Key Files

| File | Purpose | Change Type |
|------|---------|-------------|
| `tasks/purchase_expiry.py` | Scheduled job: cancel expired purchases | NEW |
| `events/item_sync.py` | Shared `LIVE-EVENT-ACCESS` item constant + ensure function | NEW |
| `services/premium/refund.py` | Refund flow: add credit note creation | MODIFY |
| `services/premium/event_purchase.py` | Purchase creation: set `expires_at` | MODIFY |
| `doctype/memora_live_event_purchase/` | DocType: add `expires_at` field | MODIFY |
| `setup.py` | Ensure shared item at after_migrate | MODIFY |
| `hooks.py` | Register scheduler job (removed before_save doc_event) | MODIFY |

## Testing

### Unit Tests

```bash
# Purchase expiry query logic (mock datetime, verify correct filtering)
bench run-tests --app memora_admin --module memora_admin.tests.test_purchase_expiry

# Shared item creation (LIVE-EVENT-ACCESS)
bench run-tests --app memora_admin --module memora_admin.tests.test_item_sync

# Credit note parameter construction
bench run-tests --app memora_admin --module memora_admin.tests.test_refund_credit_note
```

### Integration Tests

```bash
# Full lifecycle: create purchase → wait → auto-cancel after expiry
# Full lifecycle: purchase → pay → refund → verify credit note exists
# Create paid event → verify item → save again → verify no duplicate
bench run-tests --app memora_admin --module memora_admin.tests.test_purchase_lifecycle
```

### Manual Smoke Test

1. **Expiry**: Create a paid event. Create a purchase via API. Wait 30+ minutes (or patch `expires_at` to past). Trigger `cancel_expired_purchases` from bench console. Verify purchase status is `cancelled`.

2. **Credit Note**: Create and pay for a paid event. Call `refund_event_purchase(purchase_id)`. Verify: purchase is `refunded`, access is `refunded`, Credit Note exists and links to original invoice.

3. **Shared Item**: Run `bench migrate`. Verify ERPNext Item `LIVE-EVENT-ACCESS` exists. Run migrate again. Verify no duplicate. Create a paid event, purchase, and confirm payment. Verify the Sales Invoice uses `LIVE-EVENT-ACCESS` with event-specific description.

## Scheduler Configuration

The auto-cancel job runs every 5 minutes via cron:

```python
# hooks.py scheduler_events
"*/5 * * * *": [
    "memora_admin.memora_admin.tasks.purchase_expiry.cancel_expired_purchases",
]
```

This means expired purchases may persist for up to 5 minutes past their `expires_at` time. This is acceptable because:
- The 30-minute window is the user-facing guarantee
- The duplicate-purchase check (FR-003) already prevents new purchases while a pending one exists
- The 5-minute lag only affects cleanup, not user-facing behavior
