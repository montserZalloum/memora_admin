# Data Model: Monetized Access

**Feature Branch**: `051-monetized-access`
**Date**: 2026-03-18

## Entity Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ENTITLEMENT LAYER                            │
│                                                                     │
│  ┌──────────────────────┐       ┌───────────────────────────┐      │
│  │ Memora Plan Premium  │       │ Memora Live Event Access  │      │
│  │ (player, plan)       │       │ (player, event)           │      │
│  │ status: active|rev   │       │ status: active|rev|ref    │      │
│  │ source: pur|vou|adm  │       │ access_type: pur|vou|adm  │      │
│  └──────┬───────┬───────┘       └──────┬───────┬────────────┘      │
│         │       │                      │       │                    │
│    purchase  voucher              purchase  voucher                 │
│    _ref      _ref                 _ref      _ref                   │
└─────┬───────────┬──────────────────┬───────────┬───────────────────┘
      │           │                  │           │
┌─────┴───────────┴──────────────────┴───────────┴───────────────────┐
│                        SOURCE LAYER                                 │
│                                                                     │
│  ┌──────────────────────────┐  ┌────────────────────────────────┐  │
│  │ Memora Plan Premium      │  │ Memora Live Event Purchase     │  │
│  │ Purchase                 │  │                                │  │
│  │ status: pend|paid|ref    │  │ status: pend|paid|ref          │  │
│  │ → erpnext_invoice        │  │ → erpnext_invoice              │  │
│  └──────────────────────────┘  └────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────┐  ┌────────────────────────────────┐  │
│  │ Memora Access Voucher    │  │ Memora Access Voucher          │  │
│  │                          │  │ Redemption                     │  │
│  │ code_hash (HMAC-SHA256)  │◄─┤ voucher → Access Voucher      │  │
│  │ voucher_type             │  │ player, premium_ref|access_ref │  │
│  │ max_redemptions          │  └────────────────────────────────┘  │
│  └──────────────────────────┘                                      │
└────────────────────────────────────────────────────────────────────┘
      │
      │ extends
      ▼
┌────────────────────────────────────┐
│ Memora Live Challenge Event        │
│ (existing — add: price, currency,  │
│  erpnext_item_code)                │
└────────────────────────────────────┘
```

## Entities

### 1. Memora Plan Premium (NEW)

The entitlement record proving a player has premium access to a specific plan. Validity is computed at runtime, not stored.

| Field | Type | Required | Notes |
|---|---|---|---|
| name | Data (autoname) | auto | `PP-.#####.` |
| player | Link → Memora Player Profile | yes | |
| plan | Link → Memora Academic Plan | yes | |
| season | Link → Memora Season | yes | Season when granted |
| status | Select: `active`, `revoked` | yes | Default: `active` |
| source_type | Select: `purchase`, `voucher`, `admin` | yes | |
| purchase_ref | Link → Memora Plan Premium Purchase | no | Required if source_type = purchase |
| voucher_ref | Link → Memora Voucher Card | no | Required if source_type = voucher (B2B voucher batch card) |
| granted_by | Link → User | no | Required if source_type = admin |
| revoked_at | Datetime | no | Set when status → revoked |
| revoked_by | Link → User | no | Set when status → revoked |

**Constraints**:
- At most one `active` premium per (player, plan) — enforced via virtual column unique index (see R-001)
- `_unique_active_plan` virtual column: `IF(status = 'active', plan, NULL)`
- `UNIQUE INDEX idx_one_active_premium ON (player, _unique_active_plan)`

**Voucher Batch Integration**: The B2B `Memora Voucher Batch` system supports `grant_type = plan_premium` with a child table of eligible plans (`Memora Voucher Batch Eligible Plan`). On redemption, the system checks if the player's current plan is in the batch's eligible list, then creates a `Memora Plan Premium` with `source_type = voucher` and `voucher_ref` pointing to the `Memora Voucher Card`.

**State Machine**:
```
active ──revoke/refund──▶ revoked (terminal)
```

**Computed Validity** (NOT stored — evaluated at query time):
```
is_usable = (
    premium.status == 'active'
    AND premium.plan == player.current_plan
    AND NOW() <= season.end_date
)

reason:
  - none         → usable
  - plan_mismatch → player changed to different plan
  - season_ended  → season.end_date < NOW()
  - revoked       → status == 'revoked'
```

---

### 2. Memora Plan Premium Purchase (NEW)

Financial record of a player's payment for plan premium. Separated from the entitlement per FR-018.

| Field | Type | Required | Notes |
|---|---|---|---|
| name | Data (autoname) | auto | `PPP-.#####.` |
| player | Link → Memora Player Profile | yes | |
| plan | Link → Memora Academic Plan | yes | |
| season | Link → Memora Season | yes | |
| status | Select: `pending`, `paid`, `failed`, `cancelled`, `refunded` | yes | Default: `pending` |
| amount | Currency | yes | |
| currency | Link → Currency | yes | Default: `JOD` |
| erpnext_item_code | Link → Item | yes | |
| erpnext_invoice | Link → Sales Invoice | no | Created on payment confirmation |
| payment_gateway | Data | no | Gateway identifier |
| payment_reference | Data | no | Gateway's transaction ID |
| paid_at | Datetime | no | Set when status → paid |
| refunded_at | Datetime | no | Set when status → refunded |
| premium_ref | Link → Memora Plan Premium | no | Back-reference to created entitlement |

**State Machine**:
```
         ┌──▶ paid ──refund──▶ refunded (terminal)
pending ─┤
         ├──▶ failed (terminal)
         └──▶ cancelled (terminal)
```

**Validation Rules**:
- Reject creation if player already has usable premium for the plan
- Reject creation if player already has a `pending` purchase for the plan

---

### 3. Memora Live Event Access (NEW)

Entitlement proving a player can join a specific paid event.

| Field | Type | Required | Notes |
|---|---|---|---|
| name | Data (autoname) | auto | `LEA-.#####.` |
| player | Link → Memora Player Profile | yes | |
| event | Link → Memora Live Challenge Event | yes | |
| status | Select: `active`, `revoked`, `refunded` | yes | Default: `active` |
| access_type | Select: `purchase`, `voucher`, `admin` | yes | |
| purchase_ref | Link → Memora Live Event Purchase | no | Required if access_type = purchase |
| voucher_ref | Link → Memora Access Voucher | no | Required if access_type = voucher |
| granted_by | Link → User | no | Required if access_type = admin |
| revoked_at | Datetime | no | Set when status → revoked/refunded |
| revoked_by | Link → User | no | Set when status → revoked |

**Constraints**:
- At most one `active` access per (player, event) — virtual column unique index (see R-001)
- `_unique_active_event` virtual column: `IF(status = 'active', event, NULL)`
- `UNIQUE INDEX idx_one_active_event_access ON (player, _unique_active_event)`

**State Machine**:
```
         ┌──revoke──▶ revoked (terminal)
active ──┤
         └──refund──▶ refunded (terminal)
```

---

### 4. Memora Live Event Purchase (NEW)

Financial record of a player's ticket payment for a live event.

| Field | Type | Required | Notes |
|---|---|---|---|
| name | Data (autoname) | auto | `LEP-.#####.` |
| player | Link → Memora Player Profile | yes | |
| event | Link → Memora Live Challenge Event | yes | |
| plan_snapshot | Link → Memora Academic Plan | no | Player's plan at purchase time |
| season | Link → Memora Season | yes | |
| status | Select: `pending`, `paid`, `failed`, `cancelled`, `refunded` | yes | Default: `pending` |
| amount | Currency | yes | |
| currency | Link → Currency | yes | Default: `JOD` |
| erpnext_item_code | Link → Item | yes | |
| erpnext_invoice | Link → Sales Invoice | no | Created on payment confirmation |
| payment_gateway | Data | no | Gateway identifier |
| payment_reference | Data | no | Gateway's transaction ID |
| paid_at | Datetime | no | Set when status → paid |
| refunded_at | Datetime | no | Set when status → refunded |
| event_access_ref | Link → Memora Live Event Access | no | Back-reference to created entitlement |

**State Machine**: Same as Plan Premium Purchase.

**Validation Rules**:
- Reject creation if player already has active event access
- Reject creation if player has usable plan premium covering this event (prevents double-charging per FR-007)
- Reject creation if player already has a `pending` purchase for this event

---

### 5. Memora Access Voucher (NEW)

A redeemable promotional code that grants either Plan Premium or Live Event Access. Distinct from the existing B2B `Memora Voucher Card` system (see R-002).

| Field | Type | Required | Notes |
|---|---|---|---|
| name | Data (autoname) | auto | `AV-.#####.` |
| code_hash | Data | yes | HMAC-SHA256 of the voucher code. Plaintext NEVER stored. |
| voucher_type | Select: `plan_premium`, `live_event_access` | yes | |
| target_plan | Link → Memora Academic Plan | no | Required if voucher_type = plan_premium |
| target_event | Link → Memora Live Challenge Event | no | Required if voucher_type = live_event_access |
| max_redemptions | Int | yes | Default: 1 |
| total_redemptions | Int | no | Default: 0. Denormalized counter. |
| valid_until | Date | no | NULL = no expiry |
| is_active | Check | yes | Default: 1 |
| created_by_admin | Link → User | yes | Admin who created this voucher |
| notes | Small Text | no | Admin-only notes |

**Security Requirements** (Constitution Principle V):
- Code generation: `secrets.choice()` from 30-char unambiguous alphabet
- Code storage: HMAC-SHA256 hash only
- Code verification: `hmac.compare_digest()` (timing-safe)
- HMAC secret: `voucher_hmac_secret` from `site_config.json`

**Validation Rules**:
- `target_plan` required when `voucher_type = plan_premium`
- `target_event` required when `voucher_type = live_event_access`

---

### 6. Memora Access Voucher Redemption (NEW)

Immutable event record of a player redeeming an Access Voucher.

| Field | Type | Required | Notes |
|---|---|---|---|
| name | Data (autoname) | auto | `AVR-.#####.` |
| voucher | Link → Memora Access Voucher | yes | |
| player | Link → Memora Player Profile | yes | |
| status | Select: `success`, `reversed` | yes | Default: `success` |
| redeemed_at | Datetime | yes | Server timestamp |
| redeemed_plan | Link → Memora Academic Plan | no | Player's plan at redemption time |
| premium_ref | Link → Memora Plan Premium | no | Set for plan_premium vouchers |
| event_access_ref | Link → Memora Live Event Access | no | Set for live_event_access vouchers |

**Constraints**:
- One successful redemption per (voucher, player) combination
- `UNIQUE INDEX idx_voucher_player_success ON (voucher, player)` with virtual column to scope to `status = 'success'`

---

### 7. Memora Live Challenge Event (EXTENDED)

Existing DocType. Add monetization fields.

| Field | Type | Required | Notes |
|---|---|---|---|
| is_paid | Check | no | **Already exists**, default: 0 |
| price | Currency | no | **NEW**. Required if is_paid = 1 |
| currency | Link → Currency | no | **NEW**. Default: JOD |
| erpnext_item_code | Link → Item | no | **NEW**. Required if is_paid = 1 |

**Validation**: When `is_paid = 1`, `price > 0` and `erpnext_item_code` must be set.

---

## Redis Key Map (New Keys)

| Redis Key Pattern | Type | Source of Truth | TTL | Invalidation |
|---|---|---|---|---|
| `memora:premium:{player}:{plan}` | Hash | `tabMemora Plan Premium` + Season | None | Event-driven: premium create/revoke, plan change, season update |
| `memora:event_access:{player}:{event}` | Hash | `tabMemora Live Event Access` | None | Event-driven: access create/revoke |
| `memora:lock:premium:{player}:{plan}` | String | N/A (ephemeral) | 10s | Auto-expire |
| `memora:lock:event_access:{player}:{event}` | String | N/A (ephemeral) | 10s | Auto-expire |
| `memora:webhook:monetized:{idempotency_key}` | String | N/A (ephemeral) | 24h | Auto-expire |

**Premium Hash Fields**:
```
usable:     "1" | "0"
reason:     "none" | "plan_mismatch" | "season_ended" | "revoked"
season_end: "2026-06-30"
source_type: "purchase" | "voucher" | "admin"
premium_id: "PP-00001"
```

**Event Access Hash Fields**:
```
has_access:  "1" | "0"
access_type: "purchase" | "voucher" | "admin"
access_id:   "LEA-00001"
```

---

## Index Strategy

| Table | Index | Columns | Purpose |
|---|---|---|---|
| `tabMemora Plan Premium` | `idx_one_active_premium` | `(player, _unique_active_plan)` UNIQUE | One active per (player, plan) |
| `tabMemora Plan Premium` | `idx_premium_player` | `(player, status)` | Player's premiums lookup |
| `tabMemora Plan Premium Purchase` | `idx_purchase_player_plan` | `(player, plan, status)` | Duplicate pending check |
| `tabMemora Live Event Access` | `idx_one_active_event_access` | `(player, _unique_active_event)` UNIQUE | One active per (player, event) |
| `tabMemora Live Event Access` | `idx_event_access_player` | `(player, status)` | Player's event access lookup |
| `tabMemora Live Event Purchase` | `idx_event_purchase_player` | `(player, event, status)` | Duplicate pending check |
| `tabMemora Access Voucher` | `idx_voucher_code_hash` | `(code_hash)` UNIQUE | Code lookup |
| `tabMemora Access Voucher Redemption` | `idx_redemption_unique` | `(voucher, player, _unique_success)` UNIQUE | One success per (voucher, player) |
| `tabMemora Access Voucher Redemption` | `idx_redemption_voucher` | `(voucher)` | Count redemptions |
