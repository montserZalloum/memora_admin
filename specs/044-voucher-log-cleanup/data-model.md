# Data Model: Voucher Redemption Log Cleanup

**Feature**: 044-voucher-log-cleanup | **Date**: 2026-03-11

## Entities

### Memora Voucher Redemption Log (existing — no changes)

Standard Frappe DocType. Insert-only audit table.

| Field | Type | Notes |
|-------|------|-------|
| `name` | VARCHAR | Frappe PK (auto-generated) |
| `creation` | DATETIME | Frappe insert timestamp — **cleanup eligibility field** |
| `timestamp` | DATETIME | Business timestamp — NOT used for cleanup |
| `...` | ... | Other business fields (untouched by this feature) |

### Cleanup Query

```sql
-- SELECT candidates (per batch)
SELECT name
FROM `tabMemora Voucher Redemption Log`
WHERE creation < NOW() - INTERVAL 100 DAY
ORDER BY creation ASC, name ASC
LIMIT 1000

-- DELETE batch
DELETE FROM `tabMemora Voucher Redemption Log`
WHERE name IN (<batch_names>)

-- COMMIT after each batch
```

## State Transitions

None. This feature only deletes rows — no status changes.

## Relationships

No new relationships. The cleanup task is standalone and does not affect any other DocType.
