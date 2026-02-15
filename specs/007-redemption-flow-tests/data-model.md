# Data Model: Redemption Flow Tests

**Feature**: 007-redemption-flow-tests | **Date**: 2026-02-15

## Entities Under Test

These are EXISTING DocTypes — no schema changes required. This document catalogs the fields and relationships relevant to the 22 redemption tests.

### Memora Voucher Card

| Field | Type | Relevance to Tests |
|-------|------|-------------------|
| `name` / `serial_no` | Data (PK) | Card identifier for queries and assertions |
| `pin_hmac` | Data (indexed) | HMAC-SHA256 hash — used for card lookup and timing-safe verification |
| `batch` | Link → Voucher Batch | Parent batch — checked for Active status |
| `status` | Select | States: Available, Allocated, Redeemed, Void, Expired — **primary assertion target** |
| `library` | Link → Customer | Populated on allocation, used in redemption log |
| `allocation` | Link → Voucher Allocation | Allocation reference |
| `sale_model` | Select | Prepaid or Consignment |
| `redeemed_by` | Link → Player Profile | **Set on redemption** — FR-002 |
| `redeemed_at` | Datetime | **Set on redemption** — FR-002 |
| `redeemed_grant` | Link → Product Grant | **Set on redemption** — FR-002 |
| `subscription_transaction` | Link → Subscription Transaction | **Set on redemption** — FR-002 |

**State Machine** (tests verify transitions):
```
Available ─── Allocated ─── Redeemed (terminal)
    │              │
    └── Void       ├── Void (terminal)
    └── Expired    ├── Expired (terminal)
                   └── Available (return)
```

### Memora Voucher Batch

| Field | Type | Relevance to Tests |
|-------|------|-------------------|
| `name` | Data (PK) | Batch identifier |
| `status` | Select | Draft, Generated, Active, Closed — tested for auto-close (FR-012) |
| `face_value` | Currency | Passed to subscription transaction; returned by preview |
| `batch_grants` | Table → Batch Grant | List of Product Grants — used for GRANT_NOT_IN_BATCH check |
| `redeemed_count` | Int | **Incremented on redemption** — FR-003 |
| `allocated_count` | Int | Decremented by recount after redemption |
| `generated_count` | Int | Set once during generation, never changes |

### Memora Voucher Redemption Log

| Field | Type | Relevance to Tests |
|-------|------|-------------------|
| `name` | Data (PK) | Auto-named VRLOG-.#####. |
| `player` | Link → Player Profile | Required — always set |
| `pin_masked` | Data | Format: `****{last_4_of_hmac}` — FR-009 |
| `card` | Link → Voucher Card | Nullable (NULL on INVALID_PIN when card not found) |
| `library` | Link → Customer | Nullable |
| `batch` | Link → Voucher Batch | Nullable |
| `requested_grant` | Link → Product Grant | Nullable |
| `status` | Select | "Success", "Invalid PIN", "Already Redeemed", etc. — FR-008 |
| `failure_reason` | Data | Error code string or empty for success |
| `ip_address` | Data | Client IP — FR-010 |
| `timestamp` | Datetime | When attempt occurred |

**Immutable**: No update hooks. Insert-only audit trail.

### Memora Subscription Transaction

| Field | Type | Relevance to Tests |
|-------|------|-------------------|
| `name` | Data (PK) | Auto-named TRX-.#####. |
| `player` | Link → Player Profile | Must match redeeming player |
| `payment_method` | Select | Must be "Voucher" for voucher redemptions |
| `status` | Select | Must be "Completed" after redemption — FR-001 |
| `related_grant` | Link → Product Grant | Must match selected grant |
| `amount_paid` | Currency | Must match batch face_value |
| `transaction_id` | Data | Must be card.name |

### Memora Product Grant

| Field | Type | Relevance to Tests |
|-------|------|-------------------|
| `name` | Data (PK) | Auto-named GRNT-.#####. |
| `item_code` | Link → Item | Display name in preview |
| `plan` | Link → Academic Plan | Plan reference |
| `grant_components` | Table → Grant Component | List of target_doctype + target_name pairs |

### Memora Grant Component (child table)

| Field | Type | Relevance to Tests |
|-------|------|-------------------|
| `target_doctype` | Link → DocType | "Memora Subject" or "Memora Track" |
| `target_name` | Dynamic Link | Subject/Track name |

**Key relationship**: `get_grant_keys(grant_id)` → `["SUB-{target_name}"]` for subjects

### Memora Player Subscription

| Field | Type | Relevance to Tests |
|-------|------|-------------------|
| `player` | Link → Player Profile | Must match player |
| `access_key` | Data | E.g., "SUB-MATH" — used for ALREADY_OWNED check |
| `expires_at` | Date | Expiration date |

## Error Code → Log Status Mapping

| Error Code | Log Status | Card State Trigger |
|-----------|------------|-------------------|
| `INVALID_PIN` | "Invalid PIN" | PIN not found or HMAC mismatch |
| `NOT_ALLOCATED` | "Not Allocated" | Card status = "Available" |
| `ALREADY_REDEEMED` | "Already Redeemed" | Card status = "Redeemed" |
| `EXPIRED` | "Expired" | Card status = "Expired" |
| `VOID` | "Void" | Card status = "Void" |
| `BATCH_INACTIVE` | "Batch Inactive" | Batch status ≠ "Active" |
| `SEASON_INACTIVE` | "Season Inactive" | Season ended or unpublished |
| `GRANT_NOT_IN_BATCH` | "Grant Not In Batch" | Grant not in batch.batch_grants |
| `ALREADY_OWNED` | "Already Owned" | Player owns all access keys for grant |

## Test Data Relationships

```
Season (SEAS-00027)
 └── Academic Plan
      ├── Product Grant (with grant_components → Subject)
      │    └── Voucher Batch (with batch_grants → Product Grant)
      │         └── Voucher Cards (10-30 cards)
      │              └── Allocated to Library (Customer)
      └── Player Profile
           └── Player Subscription (for ALREADY_OWNED tests)
```
