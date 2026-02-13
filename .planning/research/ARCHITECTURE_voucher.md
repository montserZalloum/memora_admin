# Architecture: Voucher Management System

**Domain:** Voucher/Recharge Card Distribution for Gamified Education Platform
**Researched:** 2026-02-13
**Confidence:** HIGH (based on direct codebase analysis of 17+ existing integration patterns)

## Executive Summary

The voucher system is architecturally a **new entry point into the existing access-grant pipeline**. It follows the same Frappe-as-source-of-truth, Redis-as-hot-cache pattern established across all 32+ phases. The critical insight is that voucher redemption creates a `Memora Subscription Transaction` with `payment_method="Voucher"` and `status="Completed"`, which triggers the **existing** Phase 23 `_handle_approval()` flow -- no new access-grant logic is needed.

The system spans both the Frappe admin (batch creation, allocation, invoicing) and the FastAPI sidecar (student-facing preview/redeem endpoints), connected via the existing `FrappeClient` HTTP bridge pattern.

---

## Recommended Architecture

### System Boundary Diagram

```
+------------------------------------------------------------------+
|                        ADMIN SIDE (Frappe Desk)                  |
|                                                                  |
|  [Voucher Batch]  [Voucher Allocation]  [Voucher Redemption Log] |
|       |                   |                       ^              |
|       v                   v                       |              |
|  [Voucher Card]    [Allocation Card]              |              |
|  (child table)     (child table)                  |              |
|       |                   |                       |              |
|       v                   v                       |              |
|  [PIN Generation]  [PDF/Encrypted Export]         |              |
|       |                                           |              |
|       +----> [Batch Grant] (child table)          |              |
|               links to Product Grant              |              |
|                                                   |              |
|  +--------------------------------------------+  |              |
|  | memora_admin/api/voucher.py                |  |              |
|  | @frappe.whitelist                          |  |              |
|  | redeem_voucher(pin, player_id)             |  |              |
|  |   1. SELECT FOR UPDATE on Voucher Card     |  |              |
|  |   2. Validate (status, expiry, plan)       |  |              |
|  |   3. Mark card "Redeemed"                  |  |              |
|  |   4. Create Subscription Transaction       |--+              |
|  |   5. Create Voucher Redemption Log         |                 |
|  |   6. COMMIT (all-or-nothing)               |                 |
|  +--------------------------------------------+                 |
|               ^                                                  |
+---------------|--------------------------------------------------+
                | HTTP POST /api/method/
                | memora_admin.api.voucher.redeem_voucher
+---------------|--------------------------------------------------+
|               |           STUDENT SIDE (FastAPI Sidecar)         |
|               |                                                  |
|  +--------------------------------------------+                 |
|  | fastapi_app/api/v1/endpoints/voucher.py    |                 |
|  |                                            |                 |
|  | POST /voucher/preview                      |                 |
|  |   JWT auth -> rate limit -> FrappeClient   |                 |
|  |   -> preview_voucher() -> return grants    |                 |
|  |                                            |                 |
|  | POST /voucher/redeem                       |                 |
|  |   JWT auth -> rate limit -> FrappeClient --+                 |
|  |   -> redeem_voucher() -> format response   |                 |
|  +--------------------------------------------+                 |
|               |                                                  |
|  +--------------------------------------------+                 |
|  | fastapi_app/services/voucher.py            |                 |
|  |   VoucherService(redis, frappe_client)     |                 |
|  |   - rate_limit_check() via Redis           |                 |
|  |   - preview() via FrappeClient.call()      |                 |
|  |   - redeem() via FrappeClient.call()       |                 |
|  +--------------------------------------------+                 |
+------------------------------------------------------------------+
```

### Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| **Voucher Batch** (Frappe DocType) | Admin creates batches, defines grant, triggers PIN generation | Voucher Card (child), Voucher Batch Grant (child) |
| **Voucher Card** (Frappe DocType, child of Batch) | Stores individual PINs with status lifecycle | Voucher Batch (parent), Voucher Allocation Card (link) |
| **Voucher Batch Grant** (Frappe DocType, child of Batch) | Links batch to one or more Product Grant | Memora Product Grant (link) |
| **Voucher Allocation** (Frappe DocType) | Tracks which Customer/library received cards | Voucher Allocation Card (child), Customer, Sales Invoice |
| **Voucher Allocation Card** (Frappe DocType, child of Allocation) | Junction between Allocation and individual Cards | Voucher Card (link) |
| **Voucher Redemption Log** (Frappe DocType) | Immutable audit trail of every redemption | Voucher Card (link), Player Profile (link), Subscription Transaction (link) |
| **memora_admin/api/voucher.py** (Frappe API) | Core redeem logic with DB transaction safety | MariaDB (SELECT FOR UPDATE), Subscription Transaction, Redemption Log |
| **fastapi_app/services/voucher.py** (FastAPI Service) | Rate limiting, FrappeClient delegation | Redis (rate limit keys), FrappeClient |
| **fastapi_app/api/v1/endpoints/voucher.py** (FastAPI Endpoint) | HTTP interface: JWT auth, request validation, response formatting | VoucherService, deps.py CurrentUser |

---

## Integration Points with Existing Components

### Files to MODIFY (Existing)

| File | Change | Reason |
|------|--------|--------|
| `memora_admin/hooks.py` | Add `doc_events` for new DocTypes + add to `scheduler_events` if needed | Wire Frappe hooks for Voucher Card status changes |
| `fastapi_app/api/v1/router.py` | Add `voucher` import and `router.include_router(voucher.router)` | Register new FastAPI endpoints |
| `fastapi_app/api/deps.py` | Add `VoucherServiceDep` type alias | Dependency injection for voucher endpoints |
| `memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.json` | No change needed -- already has `payment_method` options including "Voucher" | Schema already supports voucher method |

### Files to CREATE (New)

| File | Purpose |
|------|---------|
| **Frappe DocTypes (6 new):** | |
| `doctype/memora_voucher_batch/` | Batch container with settings (expiry, denomination, plan filter) |
| `doctype/memora_voucher_card/` | Individual card: PIN (hashed), status, batch link |
| `doctype/memora_voucher_batch_grant/` | Child table: links batch to Product Grant(s) |
| `doctype/memora_voucher_allocation/` | Allocation to Customer (library) with invoicing |
| `doctype/memora_voucher_allocation_card/` | Child table: tracks which cards in an allocation |
| `doctype/memora_voucher_redemption_log/` | Immutable audit log entry per redemption |
| **Frappe API:** | |
| `memora_admin/api/voucher.py` | Whitelisted methods: `preview_voucher()`, `redeem_voucher()` |
| **Frappe Events:** | |
| `memora_admin/events/voucher_sync.py` | Optional: event hooks for batch status changes |
| **FastAPI Endpoint:** | |
| `fastapi_app/api/v1/endpoints/voucher.py` | `POST /voucher/preview`, `POST /voucher/redeem` |
| **FastAPI Service:** | |
| `fastapi_app/services/voucher.py` | `VoucherService` with rate limiting and FrappeClient calls |
| **FastAPI Models:** | |
| `fastapi_app/models/voucher.py` | Pydantic schemas: `VoucherPreviewRequest`, `VoucherRedeemRequest`, `VoucherRedeemResponse` |
| **PIN Generation Utility:** | |
| `memora_admin/memora_admin/utils/pin_generator.py` | Secure PIN generation, HMAC signing, batch generation |
| **Export Utility:** | |
| `memora_admin/memora_admin/utils/pin_export.py` | Encrypted file export for physical card distribution |

### Existing Components REUSED (Not Modified)

| Component | How Reused |
|-----------|-----------|
| `Memora Subscription Transaction` | Redemption creates a new TRX with `payment_method="Voucher"`, `status="Completed"` |
| `MemoraSubscriptionTransaction._handle_approval()` | Triggered by the Completed status, creates Player Subscriptions, syncs to Redis |
| `Memora Player Subscription` | Created by `_handle_approval()` -- grants access via Redis SADD |
| `access_sync.on_subscription_change()` | Existing hook fires when Player Subscription is created, does `SADD memora:access:{player}` |
| `Memora Product Grant` | Batch Grant links to existing Product Grant for access key resolution |
| `FrappeClient` | FastAPI calls Frappe `redeem_voucher` via existing HTTP bridge |
| `RateLimiter` | Reuse existing Lua-script-based rate limiter with new key prefixes |
| `CurrentUser` dependency | JWT authentication for voucher endpoints |
| Redis pub/sub notification | Redemption publishes to `memora:notify:{player}` via existing `_publish_notification()` |

---

## Data Flow Diagrams

### Flow 1: Batch Creation (Admin)

```
Admin (Frappe Desk)
    |
    v
[Create Voucher Batch form]
    - Set: quantity, denomination, expiry_date, plan_filter (optional)
    - Add Batch Grant rows (link to Memora Product Grant)
    - Click "Generate PINs"
    |
    v
[Voucher Batch.generate_pins()]  (server script on Batch DocType)
    |
    +---> For each card (1..quantity):
    |       1. Generate cryptographic random PIN (e.g., 16 alphanumeric)
    |       2. Compute HMAC(PIN, secret) for storage
    |       3. Create Voucher Card child row:
    |           { pin_hash: HMAC, pin_last4: last 4 chars, status: "Available" }
    |
    +---> Set batch.status = "Generated"
    |
    v
[Batch saved with N child Voucher Card rows]
```

### Flow 2: Allocation to Library (Admin)

```
Admin (Frappe Desk)
    |
    v
[Create Voucher Allocation form]
    - Select: batch, customer (library), quantity
    - Click "Allocate"
    |
    v
[Voucher Allocation.allocate_cards()]
    |
    +---> SELECT {quantity} cards FROM Voucher Card
    |       WHERE batch = {batch} AND status = "Available"
    |       ORDER BY creation ASC
    |       FOR UPDATE
    |
    +---> For each selected card:
    |       1. Update card.status = "Allocated"
    |       2. Create Voucher Allocation Card child row
    |
    +---> Optionally: Generate encrypted export file
    |       - Write PINs to temp file
    |       - Encrypt with Fernet key (from site_config.json)
    |       - Save to: {bench}/sites/{site}/private/files/voucher_exports/
    |       - Attach File record to Allocation doc
    |
    +---> Optionally: Create Sales Invoice (linked to Customer)
    |
    v
[Allocation saved with N Allocation Card rows]
[Cards marked "Allocated" in batch]
```

### Flow 3: Voucher Redemption (Student via FastAPI)

This is the critical flow. It spans both FastAPI and Frappe with clear boundary.

```
Student Mobile App
    |
    | POST /api/v1/voucher/preview  (or /redeem)
    | Headers: Authorization: Bearer {JWT}, X-Device-ID: {id}
    | Body: { "pin": "ABCD-EFGH-1234-5678" }
    |
    v
[FastAPI: voucher.py endpoint]
    |
    +---> 1. JWT auth (CurrentUser dependency -- existing)
    |
    +---> 2. Rate limit check (VoucherService)
    |       Key: memora:redeem_attempts:{player_id}
    |       Limit: 5 attempts / 15 minutes per player
    |       Key: memora:redeem_attempts:ip:{ip}
    |       Limit: 10 attempts / 15 minutes per IP
    |       (Reuse existing RateLimiter with different prefix/limits)
    |
    +---> 3. Call FrappeClient (HTTP POST to Frappe)
    |       Method: memora_admin.api.voucher.redeem_voucher
    |       Params: { pin: "ABCD...", player_id: "PLAYER-00123" }
    |
    v
[Frappe: voucher.py whitelisted method]
    |
    +---> 4. Compute HMAC(pin, secret) to get pin_hash
    |
    +---> 5. SELECT ... FROM `tabMemora Voucher Card`
    |       WHERE pin_hash = {hash} AND status = "Allocated"
    |       FOR UPDATE
    |       (Row-level lock prevents double-redemption race condition)
    |
    +---> 6. Validate:
    |       - Card exists and status == "Allocated"
    |       - Batch not expired (batch.expiry_date >= today)
    |       - Plan filter (if batch.plan_filter, player's plan must match)
    |       - Card not already redeemed
    |
    +---> 7. Get Product Grant from Batch Grant child table
    |
    +---> 8. BEGIN TRANSACTION (implicit via Frappe)
    |       a. Mark card: status = "Redeemed", redeemed_by = player_id,
    |          redeemed_at = now()
    |       b. Create Subscription Transaction:
    |          { player: player_id,
    |            payment_method: "Voucher",
    |            status: "Completed",          <-- KEY: not "Pending Approval"
    |            related_grant: product_grant_id,
    |            amount_paid: batch.denomination,
    |            transaction_id: card.name }
    |       c. Create Voucher Redemption Log:
    |          { card: card.name, player: player_id,
    |            batch: batch.name, transaction: trx.name }
    |       d. frappe.db.commit()
    |
    +---> 9. Subscription Transaction on_update fires:
    |       MemoraSubscriptionTransaction._handle_approval()
    |         -> get_grant_keys(product_grant_id)
    |         -> create Player Subscription for each key
    |         -> SADD memora:access:{player_id} for each key (via hook)
    |         -> SREM memora:pending:{player_id} (cleanup pending set)
    |         -> Publish notification to memora:notify:{player_id}
    |
    v
[Response flows back through FastAPI]
    |
    +---> 10. FastAPI formats response:
    |       { success: true,
    |         granted_subjects: ["Math", "Science"],
    |         transaction_id: "TRX-00123",
    |         message: "Voucher redeemed successfully" }
    |
    v
[Student sees confirmation in app]
[WebSocket notification also delivered in real-time]
```

### Flow 4: Preview (Before Redeem)

```
Student Mobile App
    |
    | POST /api/v1/voucher/preview
    | Body: { "pin": "ABCD-EFGH-1234-5678" }
    |
    v
[FastAPI: voucher.py endpoint]
    +---> Rate limit check
    +---> FrappeClient.call("memora_admin.api.voucher.preview_voucher", {pin, player_id})
    |
    v
[Frappe: voucher.py]
    +---> HMAC(pin) -> lookup card
    +---> Validate card exists, is "Allocated", not expired
    +---> Get batch grant -> Product Grant -> Item Name
    +---> Return: { valid: true, product_name: "...", subjects: [...], expiry: "..." }
    |     (NO status change, NO row lock -- read-only)
    |
    v
[FastAPI returns preview to student]
```

---

## Key Architectural Decisions

### Decision 1: Core Redeem Logic Lives in Frappe, Not FastAPI

**Why:** The redeem operation requires `SELECT FOR UPDATE` (row-level locking) to prevent double-redemption. This needs a direct MariaDB transaction. FastAPI's connection to the database is indirect (via FrappeClient HTTP calls). Putting the critical section in Frappe guarantees atomic DB operations within a single process.

**Pattern:** This mirrors the existing `create_purchase_request()` in `memora_admin/api/purchase.py` -- Frappe holds the transactional logic, FastAPI is the auth/rate-limit/format proxy.

### Decision 2: Subscription Transaction with status="Completed" (Skip Approval)

**Why:** Voucher redemption is a **pre-paid** transaction. The customer/library already paid at allocation time. There is no approval step. Setting `status="Completed"` directly triggers `_handle_approval()` in the existing `MemoraSubscriptionTransaction.on_update()`, which creates Player Subscriptions and syncs to Redis.

**Contrast with Purchase Flow:** Manual purchases set `status="Pending Approval"` and wait for admin approval. Vouchers skip this entirely.

### Decision 3: HMAC PIN Storage (Not Plaintext, Not bcrypt)

**Why:**
- **Not plaintext:** PINs are bearer tokens. Stored plaintext = database breach exposes all unspent vouchers.
- **Not bcrypt:** PINs need to be looked up by hash (WHERE clause). bcrypt is intentionally non-deterministic (different salt per hash), so you cannot query by hash. You would need to scan all cards.
- **HMAC-SHA256:** Deterministic (same input = same output with same key), so you CAN query by hash. The secret key is stored in `site_config.json` (not in DB). If the DB is breached without the config file, PINs are safe.

### Decision 4: Rate Limiting at FastAPI Layer

**Why:** Frappe whitelisted methods are accessible via Frappe's API auth, but the rate limiting must happen BEFORE the Frappe call to prevent brute-force PIN guessing. The existing `RateLimiter` class with its Lua script is reusable with different key prefixes and limits.

**Keys:**
- `memora:redeem_attempts:{player_id}` -- 5 attempts / 15 min per player
- `memora:redeem_attempts:ip:{ip}` -- 10 attempts / 15 min per IP

### Decision 5: Encrypted File Export (Not Frappe File Manager)

**Why:** PIN export files contain plaintext PINs for physical card printing. They must NOT be stored in Frappe's public file area or accessible via the web. Using the `private/files/` directory with additional encryption (Fernet symmetric encryption, key from `site_config.json`) provides defense in depth.

**Path:** `{bench}/sites/{site}/private/files/voucher_exports/{batch_name}_{timestamp}.enc`

### Decision 6: No New Redis Keys for Voucher State

**Why:** Voucher cards are NOT hot data. They are looked up only during redemption (low frequency, max 1 lookup per student per voucher). The existing `SELECT FOR UPDATE` in MariaDB is sufficient and provides the atomicity guarantee that Redis cannot.

The only new Redis keys are for rate limiting (`memora:redeem_attempts:*`), which use the existing TTL-based pattern.

---

## DocType Schema Design

### Memora Voucher Batch

```
Autoname: VBATCH-.#####.
Fields:
  - batch_name (Data, reqd)           # Human-readable label e.g. "Spring 2026 - Math"
  - status (Select, reqd)             # Draft | Generated | Partially Allocated | Fully Allocated | Expired
  - quantity (Int, reqd)              # Number of cards to generate
  - denomination (Currency)           # Face value for invoicing
  - plan_filter (Link: Memora Academic Plan)  # Optional: restrict to players of this plan
  - expiry_date (Date, reqd)          # Cards expire after this date
  - product_grants (Table: Memora Voucher Batch Grant)  # What access the cards unlock
  - cards (Table: Memora Voucher Card)  # Generated PIN cards
  - generated_count (Int, read_only)  # Count of generated cards
  - allocated_count (Int, read_only)  # Count of allocated cards
  - redeemed_count (Int, read_only)   # Count of redeemed cards
  - notes (Small Text)               # Admin notes
```

### Memora Voucher Card (Child of Batch)

```
Autoname: VCARD-.#####.
Fields:
  - pin_hash (Data, reqd, hidden)     # HMAC-SHA256 of PIN
  - pin_last4 (Data, read_only)       # Last 4 chars for admin identification
  - status (Select, reqd)             # Available | Allocated | Redeemed | Expired | Void
  - allocated_to (Link: Customer)     # Which library/customer received this card
  - allocation (Link: Memora Voucher Allocation)  # Allocation record
  - redeemed_by (Link: Memora Player Profile)     # Player who redeemed
  - redeemed_at (Datetime)            # When redeemed
  - transaction (Link: Memora Subscription Transaction)  # Resulting transaction
```

### Memora Voucher Batch Grant (Child of Batch)

```
Fields:
  - product_grant (Link: Memora Product Grant, reqd)  # What this batch unlocks
  - description (Data, read_only, fetch_from: product_grant.item_code)  # Auto-fetched
```

### Memora Voucher Allocation

```
Autoname: VALLOC-.#####.
Fields:
  - batch (Link: Memora Voucher Batch, reqd)
  - customer (Link: Customer, reqd)   # Library or distributor
  - quantity (Int, reqd)              # How many cards allocated
  - allocation_date (Date, reqd)
  - cards (Table: Memora Voucher Allocation Card)  # Allocated cards
  - sales_invoice (Link: Sales Invoice)  # Optional invoicing link
  - export_file (Attach)              # Encrypted export file
  - notes (Small Text)
```

### Memora Voucher Allocation Card (Child of Allocation)

```
Fields:
  - voucher_card (Link: Memora Voucher Card, reqd)
  - pin_last4 (Data, read_only, fetch_from: voucher_card.pin_last4)
```

### Memora Voucher Redemption Log

```
Autoname: VLOG-.#####.
Fields:
  - voucher_card (Link: Memora Voucher Card, reqd)
  - batch (Link: Memora Voucher Batch, reqd)
  - player (Link: Memora Player Profile, reqd)
  - transaction (Link: Memora Subscription Transaction, reqd)
  - redeemed_at (Datetime, reqd)
  - ip_address (Data)                 # Client IP for audit
  - device_id (Data)                  # Device ID from request header
```

### Custom Fields on Customer

```
Fields (added via Custom Field, not modifying Customer DocType):
  - memora_library_code (Data)        # Internal code for the library
  - memora_contact_person (Data)      # Library contact name
  - memora_max_allocation (Int)       # Max cards per allocation (optional limit)
```

---

## FastAPI-Frappe Communication Pattern

The voucher system follows the **exact same pattern** as the existing purchase flow (`PurchaseService` -> `memora_admin/api/purchase.py`):

### FastAPI Side

```python
# fastapi_app/services/voucher.py
class VoucherService:
    """Voucher preview and redemption via Frappe delegation."""

    RATE_LIMIT_PREFIX = "memora:redeem_attempts:"

    def __init__(self, redis_client: redis.Redis, frappe_client: FrappeClient):
        self.redis = redis_client
        self.frappe = frappe_client

    async def preview(self, pin: str, player_id: str) -> dict:
        """Preview what a voucher unlocks (read-only, no state change)."""
        return await self.frappe.call(
            "memora_admin.api.voucher.preview_voucher",
            {"pin": pin, "player_id": player_id},
        )

    async def redeem(self, pin: str, player_id: str, ip: str, device_id: str) -> dict:
        """Redeem a voucher (creates transaction, grants access)."""
        return await self.frappe.call(
            "memora_admin.api.voucher.redeem_voucher",
            {"pin": pin, "player_id": player_id, "ip_address": ip, "device_id": device_id},
        )
```

### Frappe Side

```python
# memora_admin/api/voucher.py
@frappe.whitelist(allow_guest=False)
def redeem_voucher(pin: str, player_id: str, ip_address: str = "", device_id: str = "") -> dict:
    """
    Redeem a voucher card. Core transactional logic.

    Uses SELECT FOR UPDATE for race-condition-safe redemption.
    Creates Subscription Transaction with status="Completed" to trigger
    existing Phase 23 access grant pipeline.
    """
    import hashlib, hmac
    secret = frappe.conf.get("voucher_hmac_secret", "")
    pin_hash = hmac.new(secret.encode(), pin.encode(), hashlib.sha256).hexdigest()

    # Row lock: prevents two concurrent redemptions of same card
    card = frappe.db.sql("""
        SELECT vc.name, vc.status, vc.parent as batch_name
        FROM `tabMemora Voucher Card` vc
        WHERE vc.pin_hash = %s
        FOR UPDATE
    """, (pin_hash,), as_dict=True)

    if not card:
        frappe.throw("Invalid voucher code", frappe.ValidationError)

    card = card[0]
    # ... validate status, expiry, plan filter ...

    # Get product grant from batch
    batch_grants = frappe.get_all(
        "Memora Voucher Batch Grant",
        filters={"parent": card.batch_name},
        pluck="product_grant",
    )

    # Atomic: mark card + create transaction + create log
    frappe.db.set_value("Memora Voucher Card", card.name, {
        "status": "Redeemed",
        "redeemed_by": player_id,
        "redeemed_at": frappe.utils.now(),
    })

    # Create Subscription Transaction -- this triggers _handle_approval()
    for grant_id in batch_grants:
        trx = frappe.get_doc({
            "doctype": "Memora Subscription Transaction",
            "player": player_id,
            "payment_method": "Voucher",
            "status": "Completed",
            "related_grant": grant_id,
            "amount_paid": frappe.get_value("Memora Voucher Batch", card.batch_name, "denomination") or 0,
            "transaction_id": card.name,
        })
        trx.insert(ignore_permissions=True)
        # on_update fires _handle_approval() because status is "Completed"

    # Audit log
    frappe.get_doc({
        "doctype": "Memora Voucher Redemption Log",
        "voucher_card": card.name,
        "batch": card.batch_name,
        "player": player_id,
        "transaction": trx.name,
        "redeemed_at": frappe.utils.now(),
        "ip_address": ip_address,
        "device_id": device_id,
    }).insert(ignore_permissions=True)

    frappe.db.commit()
    return {"success": True, "transaction_id": trx.name, ...}
```

### Important: `_handle_approval()` Behavior with `status="Completed"` on Insert

The existing `MemoraSubscriptionTransaction.on_update()` checks `self.has_value_changed("status")`. When a new document is inserted with `status="Completed"`, the `after_insert` hook in `hooks.py` currently only calls `purchase_sync.on_purchase_request_created()`, which checks for `status="Pending Approval"` and returns early.

However, **`on_update`** is NOT called on insert -- only `after_insert`. So the voucher flow needs one of these approaches:

**Option A (Recommended):** Insert the transaction with `status="Pending Approval"` first, then immediately update to `status="Completed"`. This fires `on_update` -> `_handle_approval()`.

```python
trx.insert(ignore_permissions=True)
trx.status = "Completed"
trx.save(ignore_permissions=True)
# on_update fires, has_value_changed("status") is True -> _handle_approval()
```

**Option B:** Call `_handle_approval()` directly from the voucher redeem function after inserting with `status="Completed"`. This bypasses the hook but couples the code.

**Option C:** Add `after_insert` handler that also checks for `status="Completed"` and runs `_handle_approval()`.

**Recommendation: Option A.** It is the simplest, uses the existing hook infrastructure, and maintains the same behavioral contract. The two-save cost (insert + update) is negligible for a voucher redemption (not a hot path).

---

## File Storage for Encrypted PIN Exports

### Location

```
{bench_path}/sites/{site_name}/private/files/voucher_exports/
```

This directory is:
- Inside Frappe's `private` directory (not web-accessible)
- Protected by Frappe's file serving authentication
- Follows the same pattern as Frappe's private file attachments

### Encryption

```python
# Key stored in site_config.json
# {
#   "voucher_hmac_secret": "...",       # For PIN hashing
#   "voucher_export_key": "..."         # Fernet key for file encryption
# }

from cryptography.fernet import Fernet

def export_allocation_pins(allocation_name: str) -> str:
    """Generate encrypted export file for an allocation's PINs.

    Returns path to encrypted file, attached to Allocation doc.
    """
    key = frappe.conf.get("voucher_export_key")
    f = Fernet(key.encode())

    # Gather PINs (need raw PINs -- only available at generation time)
    # NOTE: Raw PINs are NOT stored. Export must happen at generation time
    # or allocation time when PINs are still in memory.
    ...
```

**Critical constraint:** Since PINs are HMAC-hashed in the database, raw PINs are only available at generation time. The export must be triggered during `generate_pins()`, not after. The encrypted file stores the plaintext PINs for physical printing.

---

## Suggested Build Order

Based on dependency analysis of the existing codebase, here is the recommended implementation order:

### Phase 1: DocType Foundation (No API, No Endpoints)

**Build:** All 6 new DocTypes + Custom Fields on Customer

**Rationale:** DocTypes must exist before any API code can reference them. This is a Frappe-only phase (no FastAPI changes).

**Dependencies:** None -- purely additive to the existing 33+ DocTypes.

**Deliverables:**
1. Voucher Batch DocType (with Batch Grant child table)
2. Voucher Card DocType (child of Batch)
3. Voucher Allocation DocType (with Allocation Card child table)
4. Voucher Allocation Card DocType (child of Allocation)
5. Voucher Redemption Log DocType
6. Custom Fields on Customer DocType
7. `bench migrate` to create tables

### Phase 2: PIN Generation + Batch Management

**Build:** PIN generation logic, batch status management, Frappe server scripts

**Rationale:** Admin must be able to create batches and generate PINs before any allocation or redemption can happen.

**Dependencies:** Phase 1 (DocTypes exist)

**Deliverables:**
1. `memora_admin/memora_admin/utils/pin_generator.py` -- secure PIN generation, HMAC hashing
2. `Voucher Batch.generate_pins()` server method -- creates child Voucher Card rows
3. HMAC secret configuration in `site_config.json`
4. Batch lifecycle management (Draft -> Generated -> Allocated -> Expired)
5. Voucher Batch DocType `.py` controller with validation

### Phase 3: Allocation + Export

**Build:** Card allocation to customers, encrypted export, optional invoicing

**Rationale:** Libraries need to receive cards before students can redeem them. Export is needed for physical distribution.

**Dependencies:** Phase 2 (batches with generated PINs exist)

**Deliverables:**
1. Voucher Allocation controller with `allocate_cards()` method
2. `SELECT ... FOR UPDATE` allocation logic (prevents double-allocation)
3. `memora_admin/memora_admin/utils/pin_export.py` -- Fernet-encrypted file generation
4. Frappe File attachment of encrypted export to Allocation doc
5. Optional Sales Invoice creation linked to Allocation

### Phase 4: Core Redeem API (Frappe Side)

**Build:** Frappe whitelisted methods for preview and redeem

**Rationale:** The transactional core must be built and testable via Frappe API before adding the FastAPI proxy layer.

**Dependencies:** Phase 2 (cards exist to redeem), existing Phase 23 pipeline (Subscription Transaction -> Player Subscription -> Redis SADD)

**Deliverables:**
1. `memora_admin/api/voucher.py` with `preview_voucher()` and `redeem_voucher()`
2. `SELECT FOR UPDATE` race-condition-safe redemption
3. Subscription Transaction creation with `payment_method="Voucher"`, `status="Completed"`
4. Voucher Redemption Log creation
5. Integration with existing `_handle_approval()` pipeline
6. `hooks.py` updates if needed for new doc_events
7. Manual testing via Frappe API directly

### Phase 5: FastAPI Proxy Layer

**Build:** FastAPI endpoints, service, models, rate limiting, dependency injection

**Rationale:** Student-facing API must go through FastAPI for JWT auth and rate limiting. Built last because it depends on all previous phases.

**Dependencies:** Phase 4 (Frappe API works), existing FastAPI infrastructure (deps.py, router.py, FrappeClient)

**Deliverables:**
1. `fastapi_app/models/voucher.py` -- Pydantic request/response schemas
2. `fastapi_app/services/voucher.py` -- VoucherService with rate limiting
3. `fastapi_app/api/v1/endpoints/voucher.py` -- POST /voucher/preview, POST /voucher/redeem
4. `fastapi_app/api/deps.py` update -- VoucherServiceDep
5. `fastapi_app/api/v1/router.py` update -- include voucher router
6. End-to-end testing: mobile app -> FastAPI -> Frappe -> Redis access grant

### Phase Ordering Rationale

```
Phase 1 (DocTypes) --> Phase 2 (PIN Gen) --> Phase 3 (Allocation) --> Phase 4 (Redeem API) --> Phase 5 (FastAPI)
     |                      |                      |                       |                       |
  Foundation            Admin can             Admin can              Testable via            Student-facing
  (tables exist)        create batches        distribute cards       Frappe directly         endpoints live
```

Each phase is independently shippable and testable:
- After Phase 1: Admin can see DocTypes in Desk (empty forms)
- After Phase 2: Admin can create batches with generated PINs
- After Phase 3: Admin can allocate cards to libraries and export
- After Phase 4: Redemption works via direct Frappe API call (testable without mobile app)
- After Phase 5: Full flow works from mobile app through FastAPI

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Storing Raw PINs in Database

**What:** Saving the plaintext PIN in a Voucher Card field.
**Why bad:** Database breach exposes all unspent vouchers. PINs are bearer tokens -- possession = access.
**Instead:** Store HMAC-SHA256(PIN, secret). Secret in `site_config.json`, not in database.

### Anti-Pattern 2: Implementing Redeem in FastAPI

**What:** Putting the SELECT FOR UPDATE + card status update + transaction creation in FastAPI.
**Why bad:** FastAPI connects to MariaDB via Frappe's HTTP API, not direct DB connection. No transactional guarantees. Race conditions possible.
**Instead:** Frappe whitelisted method holds the critical section. FastAPI is auth/rate-limit proxy.

### Anti-Pattern 3: Using Redis for Voucher Card State

**What:** Caching voucher card status in Redis for faster lookups.
**Why bad:** Voucher redemption is low-frequency (once per card, ever). Redis adds complexity and introduces cache coherence issues for a critical financial operation. The `SELECT FOR UPDATE` in MariaDB is the correct tool.
**Instead:** MariaDB only. No Redis caching for voucher state.

### Anti-Pattern 4: Separate Access Grant Logic for Vouchers

**What:** Writing new code to create Player Subscriptions and sync to Redis in the voucher redeem function.
**Why bad:** Duplicates the existing Phase 23 pipeline. Two code paths to maintain. Risk of divergence.
**Instead:** Create a Subscription Transaction with `status="Completed"` and let the existing `_handle_approval()` handle everything.

### Anti-Pattern 5: Generating PINs On-Demand

**What:** Generating PINs when an allocation is created rather than pre-generating in batch.
**Why bad:** Allocation may need to produce physical cards (printing). PINs must be generated, hashed, and exported before they leave the system. On-demand generation couples allocation with generation.
**Instead:** Two-step: Generate (batch creates cards) -> Allocate (assign existing cards to customer).

---

## Scalability Considerations

| Concern | At 100 cards | At 10K cards | At 1M cards |
|---------|-------------|-------------|-------------|
| PIN Generation | Instant (<1s) | ~10s (Frappe background job) | Must be background job with progress tracking |
| HMAC Lookup | Index on pin_hash, <1ms | Same (index) | Same (B-tree index scales) |
| Batch Child Table | Frappe handles fine | May hit Frappe form rendering limits (>500 rows). Use virtual list or server-side pagination | Split into separate table (not child) |
| Export File | Trivial | ~100KB encrypted file | Must stream, not load into memory |
| Allocation | Direct SELECT FOR UPDATE | Same (< 1s) | Batch UPDATE with LIMIT |

**Key index:** Add database index on `tabMemora Voucher Card`.`pin_hash` for O(log n) lookup during redemption.

**Frappe child table limit:** Frappe renders all child table rows in the form. For batches > 500 cards, consider making Voucher Card a **standalone DocType with a Link field to Batch** rather than a child table. This avoids form rendering performance issues. The admin can view cards via a linked list view instead.

**Recommendation for MVP:** Start with child tables (simpler). If batch sizes exceed 500, refactor Voucher Card to standalone DocType in a follow-up phase.

---

## Configuration Requirements

### site_config.json additions

```json
{
  "voucher_hmac_secret": "a-random-256-bit-hex-string",
  "voucher_export_key": "Fernet-key-from-Fernet.generate_key()"
}
```

### No .env changes needed

FastAPI does not store voucher state or secrets. All voucher logic flows through FrappeClient to Frappe, which reads from `site_config.json`.

### hooks.py additions

```python
doc_events = {
    # ... existing entries ...
    "Memora Voucher Batch": {
        "on_update": "memora_admin.events.voucher_sync.on_batch_updated",
    },
}
```

Minimal hook surface -- most voucher logic is in DocType controllers (`.py` files) and Frappe API methods, not event hooks.

---

## Sources

- **Codebase analysis** (HIGH confidence): Direct examination of 20+ files in the existing codebase
- `memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.py` -- Phase 23 approval pipeline
- `memora_admin/events/access_sync.py` -- Redis access grant sync
- `fastapi_app/services/purchase.py` -- Existing FastAPI->Frappe proxy pattern
- `fastapi_app/services/rate_limit.py` -- Reusable rate limiter
- `fastapi_app/api/deps.py` -- Dependency injection patterns
- `fastapi_app/services/frappe_client.py` -- FrappeClient HTTP bridge
- `memora_admin/memora_admin/services/build/storage/local.py` -- File storage pattern with permissions
- `memora_admin/events/catalog_sync.py` -- Two-pronged cache invalidation pattern
- `fastapi_app/core/pubsub.py` -- Notification relay for WebSocket
