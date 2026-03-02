# API Contracts: Scholarship & Gift Voucher System

**Feature**: 034-scholarship-gift-vouchers | **Date**: 2026-03-02

All endpoints are Frappe whitelisted methods (HTTP POST via `frappe.call`).

---

## New Endpoint: `direct_activate`

**Method**: `memora_admin.memora_admin.api.voucher.direct_activate`
**Decorator**: `@frappe.whitelist()`
**Permission**: System Manager (via Frappe session)

### Request

```python
frappe.call({
    method: "memora_admin.memora_admin.api.voucher.direct_activate",
    args: {
        batch_name: "VBATCH-00042"
    }
})
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `batch_name` | str | Yes | The Voucher Batch document name |

### Validation Guards

| # | Condition | Error |
|---|---|---|
| 1 | Batch does not exist | `frappe.DoesNotExistError` |
| 2 | `batch.status != 'Generated'` | `frappe.ValidationError`: "Batch must be in Generated status for Direct Activate. Current status: {status}" |
| 3 | `batch.batch_purpose == 'Sale'` | `frappe.ValidationError`: "Direct Activate is only available for non-sale batches (Scholarship, Gift, Promotion)." |

### Success Response

```json
{
    "status": "activated",
    "activated_count": 500
}
```

| Field | Type | Description |
|---|---|---|
| `status` | str | Always "activated" on success |
| `activated_count` | int | Number of cards transitioned from Available to Allocated |

### Behavior

1. Count cards with `status='Available'` in the batch
2. If 0: return `{"status": "activated", "activated_count": 0}` (idempotent)
3. Bulk SQL UPDATE:
   ```sql
   UPDATE `tabMemora Voucher Card`
   SET status = 'Allocated',
       library = 'Admin-Direct',
       modified = NOW(),
       modified_by = {current_user}
   WHERE batch = {batch_name}
     AND status = 'Available'
   ```
4. Transition batch: `status = 'Active'`, update `allocated_count`
5. `frappe.db.commit()`
6. Return count

### Idempotency

Calling twice on the same batch: second call finds 0 Available cards, returns `activated_count: 0`. No error.

---

## Modified Endpoint: `fill_cards`

**Method**: `memora_admin.memora_admin.api.allocation.fill_cards`

### New Guard (added before existing logic)

```python
batch = frappe.db.get_value("Memora Voucher Batch", alloc.batch, "batch_purpose")
if batch != "Sale":
    frappe.throw(
        "Cannot allocate cards from a non-sale batch. Use Direct Activate instead.",
        frappe.ValidationError,
    )
```

### Error Response (new)

| Condition | Error |
|---|---|
| `batch.batch_purpose != 'Sale'` | `frappe.ValidationError`: "Cannot allocate cards from a non-sale batch. Use Direct Activate instead." |

---

## Modified Endpoint: `submit_allocation`

**Method**: `memora_admin.memora_admin.api.allocation.submit_allocation`

### New Guard (added before existing logic)

Same guard as `fill_cards`:

```python
batch_purpose = frappe.db.get_value("Memora Voucher Batch", alloc.batch, "batch_purpose")
if batch_purpose != "Sale":
    frappe.throw(
        "Cannot allocate cards from a non-sale batch. Use Direct Activate instead.",
        frappe.ValidationError,
    )
```

---

## Modified Background Job: `generate_cards_job`

**Method**: `memora_admin.memora_admin.api.voucher.generate_cards_job`

### Change

Add `batch_purpose` to the bulk insert fields list:

**Before**: `fields = ["name", "serial_no", "pin_hmac", "batch", "status", ...]`
**After**: `fields = ["name", "serial_no", "pin_hmac", "batch", "status", "batch_purpose", ...]`

Each card row includes `batch.batch_purpose` value.

---

## Script Report: Scholarship & Gift Grants

**Path**: `memora_admin/memora_admin/report/scholarship_gift_grants/`
**DocType reference**: `Memora Voucher Batch`
**Roles**: System Manager

### Filters

| Filter | Fieldtype | Options | Default |
|---|---|---|---|
| `batch_purpose` | Select | Scholarship\nGift\nPromotion | (none — shows all non-Sale) |
| `from_date` | Date | — | (none) |
| `to_date` | Date | — | (none) |
| `product_grant` | Link | Memora Product Grant | (none) |

### Columns

| Column | Fieldtype | Width | Source |
|---|---|---|---|
| Batch | Link (Memora Voucher Batch) | 150 | `b.name` |
| Batch Name | Data | 150 | `b.batch_name` |
| Purpose | Data | 100 | `b.batch_purpose` |
| Product Grant | Data | 150 | GROUP_CONCAT of grant item_codes |
| Total Cards | Int | 90 | `b.quantity` |
| Activated | Int | 90 | COUNT(status IN ('Allocated', 'Redeemed', 'Void', 'Expired')) |
| Redeemed | Int | 90 | COUNT(status = 'Redeemed') |
| Voided | Int | 80 | COUNT(status = 'Void') |
| Remaining | Int | 90 | COUNT(status = 'Allocated') |
| Created | Date | 100 | `b.creation` |

**Note**: "Activated" = total cards that were directly activated (includes redeemed/voided/expired since they were activated first). "Remaining" = cards still in Allocated status awaiting distribution.

### Summary Row

| Metric | Calculation |
|---|---|
| Total Cards | SUM of all batch quantities |
| Total Redeemed | SUM of redeemed counts |
| Avg Redemption Rate | AVG(redeemed / total) across batches |

### SQL Query Pattern

```sql
SELECT
    b.name as batch,
    b.batch_name,
    b.batch_purpose as purpose,
    b.quantity as total_cards,
    SUM(CASE WHEN c.status != 'Available' THEN 1 ELSE 0 END) as activated,
    SUM(CASE WHEN c.status = 'Redeemed' THEN 1 ELSE 0 END) as redeemed,
    SUM(CASE WHEN c.status = 'Void' THEN 1 ELSE 0 END) as voided,
    SUM(CASE WHEN c.status = 'Allocated' THEN 1 ELSE 0 END) as remaining,
    b.creation as created
FROM `tabMemora Voucher Batch` b
LEFT JOIN `tabMemora Voucher Card` c ON c.batch = b.name
WHERE b.batch_purpose != 'Sale'
  {AND b.batch_purpose = %s}
  {AND b.creation >= %s}
  {AND b.creation <= %s}
  {AND EXISTS (SELECT 1 FROM `tabMemora Voucher Batch Grant` bg
               WHERE bg.parent = b.name AND bg.product_grant = %s)}
GROUP BY b.name
ORDER BY b.creation DESC
```
