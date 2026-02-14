# Phase 36: Redemption API - Research

**Researched:** 2026-02-14
**Domain:** Voucher PIN redemption (Frappe transactional API + FastAPI proxy with rate limiting)
**Confidence:** HIGH (all components verified against existing codebase)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Preview response shape:**
- Claude's discretion on detail level (names, icons, etc.) -- pick what's practical given existing Product Grant data
- Already-owned grants are **hidden** from the preview (not shown with a label)
- Preview includes the card's **face value** (e.g., "50 SAR") so student confirms the right card
- If ALL grants are already owned, return **ALL_GRANTS_OWNED error** (not an empty success) -- card is preserved

**Multi-grant redemption flow:**
- **One grant per redemption, card consumed** -- student picks one grant from the card, card becomes Redeemed, remaining grants are not given
- No warning or grant count indicator needed -- in practice most cards have 1 grant
- ALREADY_OWNED error returns just the error code -- no available grants list (student can call preview again)
- **Fire-and-forget** -- no confirmation token required, POST /redeem with PIN + grant_id is final. App handles any confirmation UI before calling.

**Error experience:**
- **Machine-readable error codes only** (INVALID_PIN, ALREADY_REDEEMED, EXPIRED, VOID, BATCH_INACTIVE, SEASON_INACTIVE, ALL_GRANTS_OWNED, GRANT_NOT_IN_BATCH, ALREADY_OWNED, NOT_ALLOCATED, RATE_LIMITED)
- No Arabic messages in API responses -- app handles all human-readable copy
- **Specific per state** -- different error codes for each card state (not a single vague "invalid" message)
- Redeem success returns **minimal confirmation** only: status + grant reference. App already knows what was redeemed.

**Rate limiting:**
- Preview is **not rate limited** -- students are young, not tech-savvy, need forgiving UX
- Rate limiting applies to **failed attempts only** -- successful previews/redeems don't count against the limit
- 5 failed attempts/hour per player, 20 failed attempts/hour per IP (uniform, no auth tiers)
- RATE_LIMITED error **includes retry_after seconds** so the app can show a countdown
- No rate limit headers on non-error responses -- only the error code + retry_after on limit hit
- Redis TTL-based expiry, no cleanup job needed

### Claude's Discretion
- Preview response field names and structure
- HTTP status codes for each error type
- Redis key naming for rate limit counters
- Redemption Log field population details
- SELECT FOR UPDATE implementation specifics

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

## Summary

Phase 36 implements two FastAPI endpoints (`POST /voucher/preview` and `POST /voucher/redeem`) backed by Frappe whitelisted methods that perform the transactional database operations. The architecture splits cleanly: FastAPI handles JWT authentication, rate limiting (Redis), and response formatting; Frappe handles PIN lookup via HMAC, card state validation, `SELECT FOR UPDATE` locking, and Subscription Transaction creation that triggers the existing Phase 23 pipeline.

All DocTypes already exist (created in Phases 33-35): Voucher Card, Voucher Batch, Voucher Batch Grant, Voucher Allocation, Voucher Redemption Log, Product Grant. The Subscription Transaction DocType already supports `payment_method="Voucher"` and `status="Completed"`. The `_handle_approval()` method in `MemoraSubscriptionTransaction` creates Player Subscriptions and triggers Redis SADD via the `on_subscription_change` hook.

The key technical challenges are: (1) the two-step insert pattern for Subscription Transactions (insert with "Pending Approval" then save with "Completed" to trigger `on_update`), (2) rate limiting on failed attempts only (not counting successes), and (3) the "one grant per redemption" model where the student selects which grant to receive from a multi-grant batch.

**Primary recommendation:** Build two Frappe whitelisted methods (`preview_voucher` and `redeem_voucher`) in the existing `memora_admin/api/voucher.py`, create a `VoucherService` in FastAPI following the `PurchaseService` pattern, and adapt the existing `RateLimiter` for failed-attempt-only counting.

## Standard Stack

### Core (All Existing -- No New Dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Frappe v15 | 15.x | ORM, SELECT FOR UPDATE, Subscription Transaction pipeline | Source of truth for all card operations |
| FastAPI | 0.100+ | JWT auth, rate limit proxy, HTTP interface | Existing sidecar pattern |
| redis.asyncio | Existing | Rate limit counters with TTL | Existing Lua-script-based RateLimiter |
| hmac (stdlib) | Python 3.10+ | `compute_hmac()` for PIN lookup, `compare_digest()` for timing safety | Already used in generator.py |
| Pydantic v2 | Existing | Request/response models | Existing pattern |
| structlog | Existing | Structured logging | Existing pattern |
| httpx | Existing | FrappeClient HTTP bridge | Existing pattern |

### Supporting (Reused from Existing Code)

| Library | Purpose | When to Use |
|---------|---------|-------------|
| `FrappeClient` | Call Frappe whitelisted methods from FastAPI | Preview and redeem delegation |
| `RateLimiter` (adapted) | Failed-attempt-only counting | Redeem endpoint rate limiting |
| `CurrentUser` dependency | JWT authentication | Both endpoints |

### No New Dependencies Needed

This phase requires zero new pip packages or libraries. Everything is built on existing infrastructure.

## Architecture Patterns

### Recommended File Structure

```
fastapi_app/
  api/v1/endpoints/voucher.py      # NEW: POST /voucher/preview, POST /voucher/redeem
  services/voucher.py              # NEW: VoucherService (rate limit + FrappeClient)
  models/voucher.py                # NEW: Pydantic schemas
  api/deps.py                      # MODIFY: Add VoucherServiceDep
  api/v1/router.py                 # MODIFY: Include voucher router

memora_admin/memora_admin/api/voucher.py  # MODIFY: Add preview_voucher() and redeem_voucher()
```

### Pattern 1: FastAPI-as-Proxy (Matches PurchaseService)

**What:** FastAPI endpoint handles auth + rate limiting, delegates all business logic to Frappe via FrappeClient.
**When to use:** Any operation requiring MariaDB transactions (SELECT FOR UPDATE).
**Source:** Verified in `fastapi_app/services/purchase.py` and `fastapi_app/api/v1/endpoints/purchase.py`.

```python
# FastAPI service delegates to Frappe
class VoucherService:
    def __init__(self, redis_client, frappe_client):
        self.redis = redis_client
        self.frappe = frappe_client

    async def preview(self, pin: str, player_id: str) -> dict:
        pin_hmac = self._compute_hmac(pin)
        return await self.frappe.call(
            "memora_admin.api.voucher.preview_voucher",
            {"pin_hmac": pin_hmac, "player_id": player_id},
        )

    async def redeem(self, pin: str, player_id: str, grant_id: str, ip: str) -> dict:
        pin_hmac = self._compute_hmac(pin)
        return await self.frappe.call(
            "memora_admin.api.voucher.redeem_voucher",
            {"pin_hmac": pin_hmac, "player_id": player_id,
             "product_grant_id": grant_id, "ip_address": ip},
        )
```

### Pattern 2: Two-Step Save for Subscription Transaction

**What:** Insert transaction with default "Pending Approval", then save with "Completed" to trigger `on_update` -> `_handle_approval()`.
**Why:** `MemoraSubscriptionTransaction.on_update()` checks `has_value_changed("status")`. On initial insert, `on_update` is NOT fired -- only `after_insert`. The `after_insert` hook (`purchase_sync.on_purchase_request_created`) returns early if status != "Pending Approval". So a direct insert with "Completed" would NOT trigger `_handle_approval()`.
**Source:** Verified in `memora_subscription_transaction.py` lines 14-19 and `hooks.py` lines 171-173.

```python
# In redeem_voucher() Frappe whitelisted method:
trx = frappe.get_doc({
    "doctype": "Memora Subscription Transaction",
    "player": player_id,
    "payment_method": "Voucher",
    "status": "Pending Approval",  # Default -- MUST start here
    "related_grant": product_grant_id,
    "amount_paid": face_value,
    "transaction_id": card_name,
})
trx.insert(ignore_permissions=True)
# Now change status and save -- triggers on_update -> _handle_approval()
trx.status = "Completed"
trx.save(ignore_permissions=True)
```

**CRITICAL:** This is flagged in STATE.md as needing integration testing. The `purchase_sync.on_purchase_request_created` after_insert hook will fire on the insert (status="Pending Approval") and send an admin email. For voucher transactions, we should either:
- Accept the spurious email (it gets immediately completed anyway), OR
- Add a check in `purchase_sync.py` to skip if `payment_method == "Voucher"`, OR
- Use `flags.ignore_hooks = True` for the initial insert

**Recommendation:** Add `payment_method != "Voucher"` check to `purchase_sync.on_purchase_request_created` to suppress the notification for voucher-initiated transactions. The admin doesn't need to be notified about auto-approved voucher redemptions.

### Pattern 3: HMAC Computation in FastAPI (Not Frappe)

**What:** Compute HMAC-SHA256 of the plaintext PIN in the FastAPI service before sending to Frappe.
**Why:** The PIN never needs to travel to Frappe in plaintext. FastAPI computes the HMAC and sends only the hash. This reduces the attack surface (no plaintext PIN in HTTP body between FastAPI and Frappe).
**Source:** `compute_hmac()` exists in `memora_admin/memora_admin/services/voucher/generator.py` line 30-39.
**Requirement:** FastAPI needs access to `voucher_hmac_secret`. Add to `.env` or Settings.

```python
# FastAPI config needs:
voucher_hmac_secret: str  # Add to Settings class in config.py

# FastAPI service computes HMAC:
import hashlib
import hmac as hmac_module

def _compute_hmac(self, pin: str) -> str:
    return hmac_module.new(
        self.hmac_secret.encode("utf-8"),
        pin.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
```

### Pattern 4: Failed-Attempt-Only Rate Limiting

**What:** Adapt existing `RateLimiter` to only count failed attempts (not successes).
**Why:** User decision -- students are young and not tech-savvy, successful operations should never count toward limits.
**Approach:** Do NOT call rate limit check before the operation. Instead, call it AFTER a failed attempt to record the failure, and check remaining budget before proceeding. Alternatively, use a "check-then-increment-on-failure" pattern.

```python
# Check budget BEFORE operation (fails if already exceeded)
# Increment ONLY on failure (after Frappe returns error)

RATE_LIMIT_CHECK_SCRIPT = """
local count = redis.call("GET", KEYS[1])
if count and tonumber(count) >= tonumber(ARGV[1]) then
    local ttl = redis.call("TTL", KEYS[1])
    return ttl
end
return 0
"""

RATE_LIMIT_INCREMENT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
"""
```

### Pattern 5: Player Subscription Ownership Check via Frappe DB

**What:** Check if player already owns a grant's access keys by querying `Memora Player Subscription`.
**Why:** The preview must filter out already-owned grants, and the redeem must return ALREADY_OWNED if the chosen grant is already owned.
**How:** In the Frappe whitelisted methods, use `frappe.db.exists("Memora Player Subscription", {"player": player_id, "access_key": key})` for each grant component's access key.

```python
# In preview_voucher():
from memora_admin.api.products import get_grant_keys

for batch_grant in batch_grants:
    grant_keys = get_grant_keys(batch_grant.product_grant)
    owned = all(
        frappe.db.exists("Memora Player Subscription",
                         {"player": player_id, "access_key": key})
        for key in grant_keys
    )
    if not owned:
        available_grants.append(...)
```

### Anti-Patterns to Avoid

- **Sending plaintext PIN to Frappe:** Compute HMAC in FastAPI, send only hash. PIN should never cross the FastAPI->Frappe HTTP boundary.
- **Using `==` for HMAC comparison:** Always use `hmac.compare_digest()` per REDEEM-09. Even though we use HMAC for WHERE clause lookup (not direct comparison), the timing-safe comparison should be used for any post-query validation.
- **Creating Subscription Transaction directly with status="Completed":** Will NOT trigger `_handle_approval()`. Must use two-step save (insert with "Pending Approval", then save with "Completed").
- **Rate limiting successful attempts:** User decision explicitly says only failed attempts count.
- **Caching voucher card state in Redis:** Prior decision says no Redis for voucher state -- MariaDB provides atomicity.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Rate limiting | Custom counter logic | Existing `RateLimiter` Lua scripts (adapted) | Atomic INCR + TTL already battle-tested in auth |
| HMAC computation | Custom hash function | `hmac_module.new()` from generator.py | Already verified pattern in Phase 34 |
| JWT authentication | Custom auth | `CurrentUser` dependency from deps.py | Existing JWT + session validation |
| Access grant pipeline | Custom subscription creation | `Subscription Transaction` with two-step save | Phase 23 pipeline already handles everything |
| Audit logging | Custom log table | `Memora Voucher Redemption Log` DocType | Already created in Phase 33 |

## Common Pitfalls

### Pitfall 1: on_update Not Firing for New Documents

**What goes wrong:** Creating a Subscription Transaction with `status="Completed"` directly -- `_handle_approval()` never fires because `on_update` is not called on insert, only `after_insert`.
**Why it happens:** Frappe's `on_update` only fires on `.save()` after insert, and `has_value_changed("status")` requires the status to have actually changed from a previous value.
**How to avoid:** Always use two-step save: insert with "Pending Approval", then change to "Completed" and save.
**Warning signs:** Cards marked as Redeemed but player has no access in Redis.

### Pitfall 2: purchase_sync Notification Fires for Voucher Transactions

**What goes wrong:** The `after_insert` hook on Subscription Transaction sends admin email for "Pending Approval" transactions. The voucher two-step save (insert with "Pending Approval") triggers this notification unnecessarily.
**Why it happens:** `purchase_sync.on_purchase_request_created` checks `status == "Pending Approval"` but doesn't check `payment_method`.
**How to avoid:** Add `if doc.payment_method == "Voucher": return` early check in `purchase_sync.py`.
**Warning signs:** Admin inbox floods with "New Purchase Request" emails for every voucher redemption.

### Pitfall 3: HMAC Secret Not Available in FastAPI

**What goes wrong:** The `voucher_hmac_secret` is in Frappe's `site_config.json`, but FastAPI reads from `.env`. If HMAC is computed in FastAPI (as recommended), the secret must be in both places.
**Why it happens:** Different config sources for Frappe and FastAPI.
**How to avoid:** Add `VOUCHER_HMAC_SECRET` to `.env` and `Settings` class in `config.py`. Keep it in sync with `site_config.json`.
**Warning signs:** HMAC computed in FastAPI doesn't match HMAC stored in card records.

### Pitfall 4: Race Condition on Card Status Check

**What goes wrong:** Between checking card status and updating it, another request could redeem the same card.
**Why it happens:** Without `SELECT FOR UPDATE`, the status check and update are not atomic.
**How to avoid:** Use `SELECT ... FOR UPDATE` on the Voucher Card row. This acquires a row-level lock that blocks concurrent transactions. Already designed into the architecture.
**Warning signs:** Two redemptions succeed for the same card (duplicate Subscription Transactions).

### Pitfall 5: Batch Status vs Card Status Confusion

**What goes wrong:** Checking if the batch is "Active" when the card status is the primary gate.
**Why it happens:** Multiple status fields across Batch (Draft/Generated/Active/Closed) and Card (Available/Allocated/Redeemed/Void/Expired).
**How to avoid:** Card validation chain: (1) Card exists (HMAC match), (2) Card status == "Allocated" (not Available/Redeemed/Void/Expired), (3) Batch status == "Active" (not Draft/Generated/Closed), (4) Season is active.
**Warning signs:** Cards from inactive batches can still be redeemed, or valid allocated cards are rejected.

### Pitfall 6: grant_label Not a DB Field

**What goes wrong:** Trying to use `grant_label` from Product Grant for preview display. It appears in code (`voucher.py:103`) but is NOT in the `memora_product_grant.json` schema.
**Why it happens:** The field may have been added via Custom Field or may be derived. The code in `voucher.py:103` uses `frappe.db.get_value("Memora Product Grant", grant.product_grant, "grant_label")` with a fallback.
**How to avoid:** For preview display, use Product Grant's `item_code` link to get `item_name` from the Item DocType (same pattern as `purchase_sync.py:28-30` and `subscription_transaction.py:112-116`).
**Warning signs:** Empty product names in preview response.

## Code Examples

### Example 1: Frappe preview_voucher() Whitelisted Method

```python
# In memora_admin/memora_admin/api/voucher.py (add to existing file)
import hmac as hmac_module
import frappe

@frappe.whitelist(allow_guest=False)
def preview_voucher(pin_hmac: str, player_id: str) -> dict:
    """Preview what a voucher card unlocks (read-only, no state change).

    Validates card status, batch status, and season. Returns available
    grants (filtering out already-owned ones).

    Args:
        pin_hmac: HMAC-SHA256 hex digest of the PIN
        player_id: Memora Player Profile name

    Returns:
        dict with face_value, grants list, or error
    """
    # 1. Look up card by HMAC (no FOR UPDATE -- read-only)
    cards = frappe.db.sql("""
        SELECT name, status, batch
        FROM `tabMemora Voucher Card`
        WHERE pin_hmac = %s
        LIMIT 1
    """, (pin_hmac,), as_dict=True)

    if not cards:
        return {"error": "INVALID_PIN"}

    card = cards[0]

    # 2. Validate card status
    status_errors = {
        "Available": "NOT_ALLOCATED",
        "Redeemed": "ALREADY_REDEEMED",
        "Void": "VOID",
        "Expired": "EXPIRED",
    }
    if card.status in status_errors:
        return {"error": status_errors[card.status]}

    if card.status != "Allocated":
        return {"error": "INVALID_PIN"}  # Unknown status

    # 3. Validate batch is Active
    batch_status = frappe.db.get_value("Memora Voucher Batch", card.batch, "status")
    if batch_status != "Active":
        return {"error": "BATCH_INACTIVE"}

    # 4. Get batch face value and grants
    batch = frappe.get_doc("Memora Voucher Batch", card.batch)
    face_value = str(batch.face_value or "0")

    # 5. Build available grants (filter out already-owned)
    from memora_admin.api.products import get_grant_keys
    available_grants = []

    for bg in batch.batch_grants:
        grant_keys = get_grant_keys(bg.product_grant)
        # Check if player owns ALL keys for this grant
        all_owned = all(
            frappe.db.exists("Memora Player Subscription",
                             {"player": player_id, "access_key": key})
            for key in grant_keys
        )
        if not all_owned:
            # Get display name from Item
            item_code = frappe.get_value("Memora Product Grant", bg.product_grant, "item_code")
            item_name = frappe.get_value("Item", item_code, "item_name") if item_code else bg.product_grant
            available_grants.append({
                "grant_id": bg.product_grant,
                "name": item_name or bg.product_grant,
            })

    if not available_grants:
        return {"error": "ALL_GRANTS_OWNED"}

    return {
        "face_value": face_value,
        "grants": available_grants,
    }
```

### Example 2: Frappe redeem_voucher() with SELECT FOR UPDATE

```python
@frappe.whitelist(allow_guest=False)
def redeem_voucher(pin_hmac: str, player_id: str, product_grant_id: str, ip_address: str = "") -> dict:
    """Redeem a voucher card for a specific product grant.

    Uses SELECT FOR UPDATE for atomic card state transition.
    Creates Subscription Transaction triggering Phase 23 pipeline.

    Args:
        pin_hmac: HMAC-SHA256 hex digest of the PIN
        player_id: Memora Player Profile name
        product_grant_id: Chosen Product Grant from the batch
        ip_address: Client IP for audit logging
    """
    # 1. Lock card row
    cards = frappe.db.sql("""
        SELECT name, status, batch, pin_hmac
        FROM `tabMemora Voucher Card`
        WHERE pin_hmac = %s
        FOR UPDATE
    """, (pin_hmac,), as_dict=True)

    if not cards:
        _log_attempt(player_id, "****", None, None, None, product_grant_id, "Invalid PIN", ip_address)
        return {"error": "INVALID_PIN"}

    card = cards[0]

    # 2. Timing-safe HMAC verification (REDEEM-09)
    if not hmac_module.compare_digest(card.pin_hmac, pin_hmac):
        _log_attempt(player_id, pin_hmac[-4:], None, None, None, product_grant_id, "Invalid PIN", ip_address)
        return {"error": "INVALID_PIN"}

    # 3. Validate card status (must be Allocated)
    if card.status != "Allocated":
        error_map = {"Available": "NOT_ALLOCATED", "Redeemed": "ALREADY_REDEEMED",
                     "Void": "VOID", "Expired": "EXPIRED"}
        error = error_map.get(card.status, "INVALID_PIN")
        _log_attempt(player_id, pin_hmac[-4:], card.name, None, card.batch, product_grant_id, error, ip_address)
        return {"error": error}

    # 4. Validate batch
    batch_status = frappe.db.get_value("Memora Voucher Batch", card.batch, "status")
    if batch_status != "Active":
        _log_attempt(player_id, pin_hmac[-4:], card.name, None, card.batch, product_grant_id, "Batch Inactive", ip_address)
        return {"error": "BATCH_INACTIVE"}

    # 5. Validate grant belongs to batch
    valid_grants = frappe.get_all("Memora Voucher Batch Grant",
        filters={"parent": card.batch}, pluck="product_grant")
    if product_grant_id not in valid_grants:
        _log_attempt(player_id, pin_hmac[-4:], card.name, None, card.batch, product_grant_id, "Grant Not In Batch", ip_address)
        return {"error": "GRANT_NOT_IN_BATCH"}

    # 6. Check ALREADY_OWNED (does NOT consume card)
    from memora_admin.api.products import get_grant_keys
    grant_keys = get_grant_keys(product_grant_id)
    all_owned = all(
        frappe.db.exists("Memora Player Subscription",
                         {"player": player_id, "access_key": key})
        for key in grant_keys
    )
    if all_owned:
        _log_attempt(player_id, pin_hmac[-4:], card.name, None, card.batch, product_grant_id, "Already Owned", ip_address)
        return {"error": "ALREADY_OWNED"}

    # 7. Mark card as Redeemed
    face_value = frappe.db.get_value("Memora Voucher Batch", card.batch, "face_value") or 0
    library = frappe.db.get_value("Memora Voucher Card", card.name, "library")

    frappe.db.set_value("Memora Voucher Card", card.name, {
        "status": "Redeemed",
        "redeemed_by": player_id,
        "redeemed_at": frappe.utils.now(),
        "redeemed_grant": product_grant_id,
    })

    # 8. Create Subscription Transaction (two-step save!)
    trx = frappe.get_doc({
        "doctype": "Memora Subscription Transaction",
        "player": player_id,
        "payment_method": "Voucher",
        "status": "Pending Approval",  # Step 1: default
        "related_grant": product_grant_id,
        "amount_paid": face_value,
        "transaction_id": card.name,
    })
    trx.insert(ignore_permissions=True)

    # Step 2: trigger _handle_approval() via on_update
    trx.status = "Completed"
    trx.save(ignore_permissions=True)

    # Link transaction back to card
    frappe.db.set_value("Memora Voucher Card", card.name,
                        "subscription_transaction", trx.name)

    # 9. Create audit log
    _log_attempt(player_id, pin_hmac[-4:], card.name, library, card.batch,
                 product_grant_id, "Success", ip_address)

    # 10. Update batch redeemed_count
    redeemed = frappe.db.count("Memora Voucher Card",
                                {"batch": card.batch, "status": "Redeemed"})
    frappe.db.set_value("Memora Voucher Batch", card.batch,
                        "redeemed_count", redeemed, update_modified=True)

    frappe.db.commit()

    return {"status": "success", "transaction_id": trx.name}


def _log_attempt(player_id, pin_masked, card, library, batch,
                 requested_grant, status, ip_address):
    """Create immutable redemption log entry."""
    frappe.get_doc({
        "doctype": "Memora Voucher Redemption Log",
        "player": player_id,
        "pin_masked": f"****{pin_masked}",
        "card": card,
        "library": library,
        "batch": batch,
        "requested_grant": requested_grant,
        "status": status,
        "ip_address": ip_address,
        "timestamp": frappe.utils.now(),
    }).insert(ignore_permissions=True)
```

### Example 3: FastAPI VoucherService with Failed-Attempt Rate Limiting

```python
# fastapi_app/services/voucher.py
import hashlib
import hmac as hmac_module
import redis.asyncio as redis
import structlog
from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger(__name__)

# Lua: check if rate limit exceeded, return TTL if so, else 0
CHECK_LIMIT_SCRIPT = """
local count = redis.call("GET", KEYS[1])
if count and tonumber(count) >= tonumber(ARGV[1]) then
    local ttl = redis.call("TTL", KEYS[1])
    return ttl
end
return 0
"""

# Lua: atomic increment with conditional TTL set
INCREMENT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
"""

FAILURE_ERRORS = {
    "INVALID_PIN", "NOT_ALLOCATED", "ALREADY_REDEEMED", "EXPIRED",
    "VOID", "BATCH_INACTIVE", "SEASON_INACTIVE", "ALL_GRANTS_OWNED",
    "GRANT_NOT_IN_BATCH", "ALREADY_OWNED",
}

class VoucherService:
    PLAYER_LIMIT = 5       # per hour
    IP_LIMIT = 20          # per hour
    WINDOW_SECONDS = 3600  # 1 hour

    def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient,
                 hmac_secret: str):
        self.redis = redis_client
        self.frappe = frappe_client
        self.hmac_secret = hmac_secret
        self._check_script = None
        self._incr_script = None

    def _compute_hmac(self, pin: str) -> str:
        return hmac_module.new(
            self.hmac_secret.encode("utf-8"),
            pin.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def check_rate_limit(self, player_id: str, ip: str) -> int | None:
        """Check if rate limited. Returns retry_after seconds or None."""
        if self._check_script is None:
            self._check_script = self.redis.register_script(CHECK_LIMIT_SCRIPT)

        player_key = f"memora:voucher_fail:player:{player_id}"
        retry = await self._check_script(keys=[player_key], args=[self.PLAYER_LIMIT])
        if retry > 0:
            return retry

        ip_key = f"memora:voucher_fail:ip:{ip}"
        retry = await self._check_script(keys=[ip_key], args=[self.IP_LIMIT])
        if retry > 0:
            return retry

        return None

    async def record_failure(self, player_id: str, ip: str) -> None:
        """Increment failure counters (called only on failed attempts)."""
        if self._incr_script is None:
            self._incr_script = self.redis.register_script(INCREMENT_SCRIPT)

        player_key = f"memora:voucher_fail:player:{player_id}"
        ip_key = f"memora:voucher_fail:ip:{ip}"

        await self._incr_script(keys=[player_key], args=[self.WINDOW_SECONDS])
        await self._incr_script(keys=[ip_key], args=[self.WINDOW_SECONDS])
```

### Example 4: FastAPI Endpoint Pattern

```python
# fastapi_app/api/v1/endpoints/voucher.py
from fastapi import APIRouter, Request, status
from fastapi_app.api.deps import CurrentUser, VoucherServiceDep

router = APIRouter(prefix="/voucher", tags=["voucher"])

@router.post("/redeem")
async def redeem_voucher(
    request: Request,
    body: VoucherRedeemRequest,
    user: CurrentUser,
    voucher_service: VoucherServiceDep,
):
    client_ip = _get_client_ip(request)

    # 1. Check rate limit BEFORE operation
    retry_after = await voucher_service.check_rate_limit(user.sub, client_ip)
    if retry_after is not None:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": "RATE_LIMITED", "retry_after": retry_after},
        )

    # 2. Call Frappe
    result = await voucher_service.redeem(body.pin, user.sub, body.grant_id, client_ip)

    # 3. On failure, increment rate limit counters
    if "error" in result:
        if result["error"] in FAILURE_ERRORS:
            await voucher_service.record_failure(user.sub, client_ip)
        # Map errors to HTTP status codes
        return JSONResponse(
            status_code=_error_to_status(result["error"]),
            content=result,
        )

    return result
```

## Error Code to HTTP Status Mapping

Recommended HTTP status codes for each error (Claude's Discretion area):

| Error Code | HTTP Status | Rationale |
|-----------|-------------|-----------|
| INVALID_PIN | 404 | PIN not found (card doesn't exist) |
| NOT_ALLOCATED | 422 | Card exists but in wrong state |
| ALREADY_REDEEMED | 409 | Conflict -- card already used |
| EXPIRED | 410 | Gone -- card expired |
| VOID | 410 | Gone -- card voided |
| BATCH_INACTIVE | 422 | Batch not active |
| SEASON_INACTIVE | 422 | Season not active |
| ALL_GRANTS_OWNED | 409 | Conflict -- nothing to grant |
| GRANT_NOT_IN_BATCH | 422 | Invalid grant selection |
| ALREADY_OWNED | 409 | Conflict -- player already owns this grant |
| RATE_LIMITED | 429 | Too many requests |

## Preview Response Shape (Claude's Discretion)

Recommended preview response structure:

```json
{
    "face_value": "50",
    "grants": [
        {
            "grant_id": "GRNT-00001",
            "name": "Math Complete Package"
        },
        {
            "grant_id": "GRNT-00002",
            "name": "Science Track 1"
        }
    ]
}
```

**Rationale:**
- `face_value`: String from batch (user decision -- include so student confirms right card)
- `grants[].grant_id`: Product Grant docname (needed for redeem request)
- `grants[].name`: Human-readable name from Item.item_name (practical for display)
- Icons/images: NOT included -- Product Grant has no icon field, and the app can derive UI from grant_id

## Redis Key Naming (Claude's Discretion)

| Key Pattern | Purpose | TTL |
|-------------|---------|-----|
| `memora:voucher_fail:player:{player_id}` | Failed attempt count per player | 3600s (1 hour) |
| `memora:voucher_fail:ip:{ip_address}` | Failed attempt count per IP | 3600s (1 hour) |

**Rationale:** Prefix `voucher_fail` makes it clear these track failures only. Separate from auth rate limit keys (`memora:ratelimit:*`).

## Critical Integration Points

### Files to MODIFY

| File | Change | Confidence |
|------|--------|------------|
| `memora_admin/memora_admin/api/voucher.py` | Add `preview_voucher()` and `redeem_voucher()` whitelisted methods + `_log_attempt()` helper | HIGH |
| `memora_admin/events/purchase_sync.py` | Add early return for `payment_method == "Voucher"` to suppress admin notification | HIGH |
| `fastapi_app/api/v1/router.py` | Add `voucher` import and `router.include_router(voucher.router)` | HIGH |
| `fastapi_app/api/deps.py` | Add `VoucherServiceDep` type alias | HIGH |
| `fastapi_app/core/config.py` | Add `voucher_hmac_secret: str` to Settings | HIGH |
| `.env` | Add `VOUCHER_HMAC_SECRET=...` | HIGH |

### Files to CREATE

| File | Purpose | Confidence |
|------|---------|------------|
| `fastapi_app/api/v1/endpoints/voucher.py` | POST /voucher/preview, POST /voucher/redeem | HIGH |
| `fastapi_app/services/voucher.py` | VoucherService with rate limiting + FrappeClient delegation | HIGH |
| `fastapi_app/models/voucher.py` | Pydantic request/response schemas | HIGH |

### Existing Components REUSED (Not Modified)

| Component | How Reused |
|-----------|-----------|
| `MemoraSubscriptionTransaction._handle_approval()` | Triggered by two-step save (Completed status) |
| `on_subscription_change()` hook | Creates Player Subscriptions + Redis SADD |
| `FrappeClient.call()` | FastAPI delegates to Frappe whitelisted methods |
| `CurrentUser` dependency | JWT auth for both endpoints |
| `_get_client_ip()` helper | IP extraction from auth.py (copy or extract to utils) |
| `Memora Voucher Card` DocType | All fields already exist (status, redeemed_by, redeemed_at, etc.) |
| `Memora Voucher Redemption Log` DocType | All fields already exist |

## Season Validation

The phase context mentions SEASON_INACTIVE as an error code. The existing card/batch lifecycle doesn't have direct season linkage. Season validation should work as:

1. Get player's plan -> plan's season
2. Check season `is_published` and `end_date >= today`
3. If season is inactive, return SEASON_INACTIVE

This check happens INSIDE the Frappe whitelisted methods (both preview and redeem), using the same pattern as `_get_expires_at()` in subscription_transaction.py.

## Open Questions

### 1. grant_label Field Existence

**What we know:** `voucher.py:103` uses `frappe.db.get_value("Memora Product Grant", ..., "grant_label")` but this field is NOT in `memora_product_grant.json`. It might be a Custom Field added via Frappe UI, or the code may be using a fallback.
**What's unclear:** Whether `grant_label` exists in the database.
**Recommendation:** Use `Item.item_name` via the `item_code` link on Product Grant (same pattern as purchase_sync.py and subscription_transaction.py). Don't rely on `grant_label`.

### 2. `_handle_approval()` Commit Behavior

**What we know:** `_handle_approval()` calls `frappe.db.commit()` internally (line 59 in subscription_transaction.py). The voucher `redeem_voucher()` also calls `frappe.db.commit()` at the end.
**What's unclear:** Whether the double commit causes issues or if Frappe handles nested commits gracefully.
**Recommendation:** Let `_handle_approval()` commit first (it's inside the two-step save). Then the final `frappe.db.commit()` in `redeem_voucher()` commits the remaining changes (card status update, redemption log, batch counter).

### 3. Error Response Format: Dict vs Exception

**What we know:** The Frappe whitelisted methods return `{"error": "ERROR_CODE"}` for errors rather than using `frappe.throw()`. This is because `frappe.throw()` raises a 417 HTTP error with Frappe's error format, which would need mapping in FastAPI.
**Recommendation:** Use return-based errors (not exceptions) from Frappe methods. FastAPI maps the error dict to appropriate HTTP status codes. This gives cleaner control over the response format.

## Sources

### Primary (HIGH confidence)
- Codebase analysis: `memora_subscription_transaction.py` (Phase 23 pipeline, _handle_approval, two-step save requirement)
- Codebase analysis: `access_sync.py` (on_subscription_change -> Redis SADD)
- Codebase analysis: `memora_voucher_card.json` + `.py` (card DocType schema, state machine)
- Codebase analysis: `memora_voucher_batch.json` + `.py` (batch schema, status transitions)
- Codebase analysis: `memora_voucher_redemption_log.json` (log fields)
- Codebase analysis: `services/rate_limit.py` (Lua-based rate limiter)
- Codebase analysis: `services/purchase.py` (FastAPI->Frappe proxy pattern)
- Codebase analysis: `api/deps.py` (dependency injection, service deps, CurrentUser)
- Codebase analysis: `services/voucher/generator.py` (compute_hmac, PIN_ALPHABET)
- Codebase analysis: `purchase_sync.py` (after_insert hook, notification suppression needed)
- Codebase analysis: `hooks.py:145-221` (doc_events configuration)
- Codebase analysis: `api/products.py` (get_grant_keys for ownership check)

### Secondary (MEDIUM confidence)
- `.planning/research/ARCHITECTURE_voucher.md` (pre-existing architecture doc, Option A for two-step save)
- `.planning/STATE.md` (accumulated decisions, blockers/concerns)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in use, no new deps
- Architecture: HIGH -- follows exact patterns from existing purchase/subscription flows
- Pitfalls: HIGH -- all identified from direct codebase examination
- Integration points: HIGH -- every file path verified against actual codebase

**Research date:** 2026-02-14
**Valid until:** 2026-03-14 (stable -- no external dependency changes expected)
