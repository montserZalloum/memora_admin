# Quickstart: Monetized Access

**Branch**: `051-monetized-access`

## Prerequisites

- Frappe v15 bench environment running
- Redis available on default port
- MariaDB with existing Memora Admin schema
- ERPNext installed (for Sales Invoice integration)
- `voucher_hmac_secret` set in `site_config.json`

## New DocTypes (6)

| DocType | Autoname | Purpose |
|---|---|---|
| Memora Plan Premium | `PP-.#####.` | Entitlement: player has premium for a plan |
| Memora Plan Premium Purchase | `PPP-.#####.` | Financial: payment record for premium |
| Memora Live Event Access | `LEA-.#####.` | Entitlement: player can join paid event |
| Memora Live Event Purchase | `LEP-.#####.` | Financial: payment record for event ticket |
| Memora Access Voucher | `AV-.#####.` | Promotional code that grants entitlements |
| Memora Access Voucher Redemption | `AVR-.#####.` | Audit: record of voucher redemption |

## Extended DocType (1)

| DocType | New Fields |
|---|---|
| Memora Live Challenge Event | `price` (Currency), `currency` (Link→Currency), `erpnext_item_code` (Link→Item) |

## Source Code Layout

### Frappe Side

```
memora_admin/memora_admin/
├── doctype/
│   ├── memora_plan_premium/                    # NEW
│   │   ├── memora_plan_premium.json
│   │   └── memora_plan_premium.py
│   ├── memora_plan_premium_purchase/           # NEW
│   │   ├── memora_plan_premium_purchase.json
│   │   └── memora_plan_premium_purchase.py
│   ├── memora_live_event_access/               # NEW
│   │   ├── memora_live_event_access.json
│   │   └── memora_live_event_access.py
│   ├── memora_live_event_purchase/             # NEW
│   │   ├── memora_live_event_purchase.json
│   │   └── memora_live_event_purchase.py
│   ├── memora_access_voucher/                  # NEW
│   │   ├── memora_access_voucher.json
│   │   └── memora_access_voucher.py
│   ├── memora_access_voucher_redemption/       # NEW
│   │   ├── memora_access_voucher_redemption.json
│   │   └── memora_access_voucher_redemption.py
│   └── memora_live_challenge_event/            # MODIFIED
│       └── memora_live_challenge_event.json     # Add price, currency, item_code
├── api/
│   ├── premium.py                              # NEW — admin grant/revoke/refund
│   └── access_voucher.py                       # NEW — admin voucher management
├── services/
│   └── premium/                                # NEW service module
│       ├── __init__.py
│       ├── access_check.py                     # FR-003: centralized usability check
│       ├── purchase.py                         # Purchase creation + invoice
│       ├── voucher.py                          # Voucher redemption logic
│       └── refund.py                           # Atomic refund processing
└── events/
    ├── premium_sync.py                         # NEW — Redis sync on premium changes
    └── event_access_sync.py                    # NEW — Redis sync on event access changes
```

### FastAPI Side

```
fastapi_app/
├── services/
│   ├── premium.py                              # NEW — PremiumService
│   └── event_access.py                         # NEW — EventAccessService
├── api/v1/endpoints/
│   ├── premium.py                              # NEW — player-facing premium endpoints
│   └── event_access.py                         # NEW — player-facing event access endpoints
└── core/
    └── redis_keys.py                           # MODIFIED — add new key builders
```

## Key Architectural Patterns

### 1. Computed Validity (No Stored Expiry)

Premium has NO `expires_at` field. Usability is computed at runtime:

```python
def is_plan_premium_usable(premium, player_plan, season) -> dict:
    if premium.status != "active":
        return {"usable": False, "reason": "revoked"}
    if premium.plan != player_plan:
        return {"usable": False, "reason": "plan_mismatch"}
    if now() > season.end_date:
        return {"usable": False, "reason": "season_ended"}
    return {"usable": True, "reason": "none", "season_end": season.end_date}
```

### 2. Two-Layer Concurrency Control

```
Layer 1: Redis SET NX EX 10 (primary)
  memora:lock:premium:{player}:{plan}
  memora:lock:event_access:{player}:{event}

Layer 2: MariaDB virtual column unique index (safety net)
  IF(status='active', plan, NULL) → UNIQUE(player, _unique_active_plan)
```

### 3. Self-Healing Cache (Principle I)

```python
class PremiumService:
    async def is_plan_premium_usable(self, player_id, plan_id):
        # 1. Process-local cache (60s TTL)
        cached = self._local_cache.get(f"{player_id}:{plan_id}")
        if cached:
            return cached
        # 2. Redis hash
        state = await self.redis.hgetall(f"memora:premium:{player_id}:{plan_id}")
        if state:
            self._local_cache.set(f"{player_id}:{plan_id}", state, ttl=60)
            return state
        # 3. Hydrate from MariaDB
        state = await self._hydrate_premium_state(player_id, plan_id)
        await self.redis.hset(f"memora:premium:{player_id}:{plan_id}", mapping=state)
        self._local_cache.set(f"{player_id}:{plan_id}", state, ttl=60)
        return state
```

### 4. Access Control Extension

Premium check inserts between Gate 1 (season) and Gate 2 (grants):

```
Gate 1: Season active?  ──NO──▶ DENY
  │ YES
  ▼
Gate 1.5 (NEW): Usable plan premium?  ──YES──▶ ALLOW
  │ NO
  ▼
Gate 2: Explicit grant OR plan membership?  ──YES──▶ ALLOW
  │ NO
  ▼
DENY
```

## Redis Keys (New)

```python
# Add to fastapi_app/core/redis_keys.py

def premium_key(player_id: str, plan_id: str) -> str:
    return f"memora:premium:{player_id}:{plan_id}"

def event_access_key(player_id: str, event_id: str) -> str:
    return f"memora:event_access:{player_id}:{event_id}"

def premium_lock_key(player_id: str, plan_id: str) -> str:
    return f"memora:lock:premium:{player_id}:{plan_id}"

def event_access_lock_key(player_id: str, event_id: str) -> str:
    return f"memora:lock:event_access:{player_id}:{event_id}"

def monetized_webhook_idempotency_key(idempotency_key: str) -> str:
    return f"memora:webhook:monetized:{idempotency_key}"
```

## Hooks Registration (New)

```python
# Add to hooks.py doc_events:

"Memora Plan Premium": {
    "after_insert": "memora_admin.events.premium_sync.on_premium_created",
    "on_update": "memora_admin.events.premium_sync.on_premium_updated",
},
"Memora Live Event Access": {
    "after_insert": "memora_admin.events.event_access_sync.on_event_access_created",
    "on_update": "memora_admin.events.event_access_sync.on_event_access_updated",
},
```

## DB Migrations (Virtual Columns + Indexes)

Run after `bench migrate` creates the tables:

```sql
-- Plan Premium: one active per (player, plan)
ALTER TABLE `tabMemora Plan Premium`
  ADD COLUMN `_unique_active_plan` VARCHAR(140) AS (
    IF(status = 'active', plan, NULL)
  ) VIRTUAL;
CREATE UNIQUE INDEX idx_one_active_premium
  ON `tabMemora Plan Premium` (player, `_unique_active_plan`);

-- Live Event Access: one active per (player, event)
ALTER TABLE `tabMemora Live Event Access`
  ADD COLUMN `_unique_active_event` VARCHAR(140) AS (
    IF(status = 'active', event, NULL)
  ) VIRTUAL;
CREATE UNIQUE INDEX idx_one_active_event_access
  ON `tabMemora Live Event Access` (player, `_unique_active_event`);

-- Access Voucher: unique code hash
CREATE UNIQUE INDEX idx_voucher_code_hash
  ON `tabMemora Access Voucher` (code_hash);

-- Access Voucher Redemption: one success per (voucher, player)
ALTER TABLE `tabMemora Access Voucher Redemption`
  ADD COLUMN `_unique_success` VARCHAR(140) AS (
    IF(status = 'success', voucher, NULL)
  ) VIRTUAL;
CREATE UNIQUE INDEX idx_redemption_unique
  ON `tabMemora Access Voucher Redemption` (player, `_unique_success`);
```

These should be applied via `setup.py:before_migrate()` or a post-migrate hook, following the existing pattern for `idx_task_log_archive`.

## Testing Strategy

| Layer | Framework | What to Test |
|---|---|---|
| Unit | pytest | Computed validity logic, voucher code generation, HMAC verification |
| Integration (Frappe) | FrappeTestCase | Purchase → payment → entitlement flow, refund atomicity, voucher redemption |
| Integration (FastAPI) | pytest + httpx | Access state endpoints, premium bypass in access checks |
| Concurrency | pytest threads | Duplicate prevention under concurrent requests |
| E2E | pytest | Full flow: purchase → webhook → access check → event join |
