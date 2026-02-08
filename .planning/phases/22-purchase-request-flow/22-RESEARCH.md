# Phase 22: Purchase Request Flow - Research

**Researched:** 2026-02-08
**Domain:** Purchase request submission (FastAPI endpoint + Frappe DocType creation + Redis pending set + admin notifications)
**Confidence:** HIGH

## Summary

This phase adds a single FastAPI endpoint (POST) that creates a Memora Subscription Transaction in Frappe with "Pending Approval" status, populates the `memora:pending:{player_id}` Redis set for catalog filtering, and sends admin notifications. The codebase already has all the foundational pieces: the Subscription Transaction DocType with the correct fields, the CatalogService already reads the pending set, the FrappeClient has a `.call()` method for invoking whitelisted Frappe APIs, and there's an established pattern for admin email notifications via `frappe.sendmail`.

The main implementation work is: (1) a new Frappe whitelisted API to create the transaction + check duplicates, (2) a new FastAPI purchase service + endpoint, (3) Redis SADD for pending set, and (4) a Frappe doc_event hook for admin notification on transaction insert.

**Primary recommendation:** Follow the exact same patterns as the catalog endpoint (service + endpoint + Frappe whitelisted API), adding the pending Redis set write in the FastAPI service and the admin notification in a Frappe doc_event hook.

## Standard Stack

### Core (Already in Project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | existing | POST endpoint for purchase submission | Already the sidecar framework |
| redis.asyncio | existing | Write to `memora:pending:{player_id}` set | Already used for all Redis ops |
| Pydantic | existing | Request/response models | Already used for all models |
| frappe | v15 | DocType creation, notifications, whitelisted API | Already the admin framework |
| httpx | existing | FrappeClient calls from FastAPI to Frappe | Already used in frappe_client.py |
| structlog | existing | Structured logging | Already used everywhere |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-multipart | existing | File upload for payment proof | Only if using FastAPI multipart upload |

### No New Dependencies Required

This phase requires zero new libraries. Everything needed is already in the project.

## Architecture Patterns

### Recommended File Structure
```
fastapi_app/
├── api/v1/endpoints/
│   └── purchase.py          # NEW: POST /purchase endpoint
├── services/
│   └── purchase.py          # NEW: PurchaseService (validation + Redis + Frappe call)
├── models/
│   └── purchase.py          # NEW: PurchaseRequest, PurchaseResponse models

memora_admin/
├── api/
│   └── purchase.py          # NEW: Frappe whitelisted API (create_purchase_request)
├── events/
│   └── purchase_sync.py     # NEW: doc_event for admin notification on transaction insert
```

### Pattern 1: FastAPI Service + Frappe Whitelisted API (Established Pattern)

**What:** FastAPI endpoint calls a Frappe whitelisted API via FrappeClient to create the DocType record. Business logic (validation, Redis writes) stays in the FastAPI service.
**When to use:** Any time FastAPI needs to create/modify Frappe DocTypes.
**Already proven in:** CatalogService calls `memora_admin.api.catalog.get_plan_catalog`, FrappeClient has `create_subscription()`.

```python
# FastAPI service pattern (from existing codebase)
class PurchaseService:
    def __init__(self, redis_client, frappe_client):
        self.redis = redis_client
        self.frappe = frappe_client

    async def submit_purchase(self, player_id: str, product_grant_id: str, ...):
        # 1. Validate: check pending set for duplicates
        # 2. Call Frappe API to create transaction
        # 3. Write to Redis pending set
        # 4. Return result
```

### Pattern 2: Dependency Injection (Established Pattern)

**What:** Service created via `Depends()` factory, injected into endpoint handler.
**Already proven in:** Every service in `deps.py` follows this exact pattern.

```python
# deps.py addition
async def get_purchase_service(request: Request) -> PurchaseService:
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    frappe_client = await get_frappe_client()
    return PurchaseService(redis_client, frappe_client)

PurchaseServiceDep = Annotated[PurchaseService, Depends(get_purchase_service)]
```

### Pattern 3: Frappe doc_events Hook for Side Effects (Established Pattern)

**What:** Admin notification fires as a Frappe doc_event on Subscription Transaction `after_insert`.
**Why:** Notification logic belongs in Frappe (where emails, users, and roles live), not in FastAPI.
**Already proven in:** `catalog_sync.on_product_grant_changed`, `access_sync.on_subscription_change`.

```python
# hooks.py addition
doc_events = {
    "Memora Subscription Transaction": {
        "after_insert": "memora_admin.events.purchase_sync.on_purchase_request_created",
    },
}
```

### Pattern 4: Admin Email Notification (Established Pattern)

**What:** Send email to all System Manager role users using `frappe.sendmail`.
**Already proven in:** `task_utils.py` lines 220-248 - exact same pattern of querying System Manager users and sending email.

```python
# Existing pattern from task_utils.py
admin_users = frappe.get_all(
    "Has Role",
    filters={"role": "System Manager", "parenttype": "User"},
    fields=["parent"],
)
recipients = []
for u in admin_users:
    user = frappe.get_doc("User", u.parent)
    if user.enabled and user.email:
        recipients.append(user.email)

frappe.sendmail(recipients=recipients, subject=..., message=..., now=True)
```

### Anti-Patterns to Avoid
- **Sending email from FastAPI:** Notifications belong in Frappe where the user/role system lives. The doc_event hook is the right place.
- **Checking duplicates only in Redis:** Always check Frappe DB as source of truth for pending transactions. Redis is a cache, not the authority.
- **Writing Redis pending set from Frappe:** The FastAPI service should write to Redis (async, consistent with other Redis writes). Frappe hook handles notification only.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Admin email delivery | Custom SMTP client | `frappe.sendmail(now=True)` | Frappe handles email queuing, templates, retry |
| Admin user discovery | Custom role queries | `frappe.get_all("Has Role", ...)` pattern from task_utils.py | Already proven, handles enabled/disabled users |
| Desk notification bell | Custom notification system | `frappe.publish_realtime()` for desk alerts | Built into Frappe v15, shows in notification bell |
| File attachment to DocType | Custom file handling | Frappe File DocType via REST API `/api/method/upload_file` | Handles storage, permissions, linking to parent |
| Duplicate check race conditions | Distributed locks | Frappe `frappe.db.exists()` check + unique constraint | Single-writer (Frappe API), no concurrency issue |
| Payment proof upload | FastAPI multipart upload | Frappe `/api/method/upload_file` directly from client | Simpler: client uploads directly to Frappe, gets file_url, sends URL in purchase request |

**Key insight:** The Frappe framework already handles admin notifications, file attachments, and user role queries. The FastAPI sidecar should only handle the player-facing API, Redis writes, and delegation to Frappe for DocType operations.

## Common Pitfalls

### Pitfall 1: Race Condition on Duplicate Check
**What goes wrong:** Two simultaneous requests from same player for same product both pass the duplicate check.
**Why it happens:** Check-then-act pattern without atomicity.
**How to avoid:** The FastAPI-to-Frappe call is serialized through HTTP. Check in Frappe whitelisted API (single-threaded per request in Frappe). Also check Redis pending set first as a fast path.
**Warning signs:** Multiple "Pending Approval" transactions for same player + product grant.

### Pitfall 2: Redis-Frappe Inconsistency
**What goes wrong:** Redis pending set gets written but Frappe transaction creation fails, or vice versa.
**Why it happens:** Two separate data stores, no distributed transaction.
**How to avoid:** Write order: (1) Create Frappe transaction first, (2) then SADD to Redis. If Frappe fails, no Redis write. If Redis fails after Frappe succeeds, the catalog will still show the product but admin has the transaction -- acceptable degradation.
**Warning signs:** Player sees product in catalog but admin sees pending transaction.

### Pitfall 3: Forgetting to Clean Pending Set on Rejection
**What goes wrong:** Admin rejects transaction but product stays hidden from player catalog.
**Why it happens:** No doc_event to remove from pending set on status change.
**How to avoid:** Phase 23 (approval flow) must handle SREM on rejection/approval. For Phase 22, document this dependency clearly.
**Warning signs:** Player can never re-purchase after rejection.

### Pitfall 4: Player ID vs User ID Confusion
**What goes wrong:** Using `user.sub` (which is the User email/ID) where `player_id` (Player Profile docname like "PLR-00001") is expected, or vice versa.
**Why it happens:** JWT `sub` claim contains the user ID, but Frappe DocTypes reference Player Profile name.
**How to avoid:** The pending Redis set should use `user.sub` (same as catalog service uses for `memora:pending:{player_id}`). The Frappe transaction should use the Player Profile docname. Lookup: `frappe.get_value("Memora Player Profile", {"user": user_sub}, "name")`.
**Warning signs:** Redis key mismatch between purchase write and catalog read.

### Pitfall 5: Missing Product Grant Validation
**What goes wrong:** Player submits purchase for a non-existent, unpublished, or wrong-plan product grant.
**Why it happens:** No validation that the product grant is valid for the player's plan.
**How to avoid:** In the Frappe whitelisted API, verify: (1) Product Grant exists, (2) is_published=1, (3) plan matches player's plan. Return specific error codes.
**Warning signs:** Transactions created for products outside player's plan.

## Code Examples

### Example 1: Purchase Request Pydantic Models

```python
# fastapi_app/models/purchase.py
from pydantic import BaseModel, Field

class PurchaseRequest(BaseModel):
    """Purchase request from player."""
    product_grant_id: str = Field(..., description="Product Grant ID e.g. GRNT-00239")
    payment_method: str = Field(default="Manual-Admin", description="Payment method")
    payment_proof_url: str | None = Field(None, description="URL of uploaded payment proof")

class PurchaseResponse(BaseModel):
    """Purchase submission response."""
    message: str = Field(default="Purchase request submitted successfully")
```

### Example 2: Purchase Service Core Logic

```python
# fastapi_app/services/purchase.py
class PurchaseService:
    PENDING_KEY_PREFIX = "memora:pending:"

    async def submit_purchase(
        self, user_id: str, plan_id: str, req: PurchaseRequest
    ) -> PurchaseResponse:
        # 1. Fast path: check Redis pending set for duplicate
        pending_key = f"{self.PENDING_KEY_PREFIX}{user_id}"
        is_pending = await self.redis.sismember(pending_key, req.product_grant_id)
        if is_pending:
            raise HTTPException(409, detail="Purchase request already pending")

        # 2. Call Frappe to create transaction (validates + creates DocType)
        result = await self.frappe.call(
            "memora_admin.api.purchase.create_purchase_request",
            {
                "user_id": user_id,
                "product_grant_id": req.product_grant_id,
                "payment_method": req.payment_method,
                "payment_proof_url": req.payment_proof_url,
                "plan_id": plan_id,
            },
        )

        # 3. Write to Redis pending set (catalog will filter this out)
        await self.redis.sadd(pending_key, req.product_grant_id)

        return PurchaseResponse()
```

### Example 3: Frappe Whitelisted API for Transaction Creation

```python
# memora_admin/api/purchase.py
@frappe.whitelist(allow_guest=False)
def create_purchase_request(
    user_id: str,
    product_grant_id: str,
    payment_method: str,
    payment_proof_url: str | None = None,
    plan_id: str | None = None,
) -> dict:
    # 1. Validate product grant exists and is published
    grant = frappe.get_doc("Memora Product Grant", product_grant_id)
    if not grant.is_published:
        frappe.throw("Product is not available", frappe.ValidationError)
    if plan_id and grant.plan != plan_id:
        frappe.throw("Product not available for your plan", frappe.ValidationError)

    # 2. Get player profile from user
    player_id = frappe.get_value("Memora Player Profile", {"user": user_id}, "name")
    if not player_id:
        frappe.throw("Player profile not found", frappe.DoesNotExistError)

    # 3. Check for existing pending transaction
    existing = frappe.db.exists(
        "Memora Subscription Transaction",
        {"player": player_id, "related_grant": product_grant_id, "status": "Pending Approval"},
    )
    if existing:
        frappe.throw("Purchase request already pending", frappe.DuplicateEntryError)

    # 4. Get price from Item Price
    price = frappe.get_value(
        "Item Price",
        {"item_code": grant.item_code, "price_list": "Standard Selling"},
        "price_list_rate",
    )

    # 5. Create transaction
    trx = frappe.get_doc({
        "doctype": "Memora Subscription Transaction",
        "player": player_id,
        "payment_method": payment_method,
        "status": "Pending Approval",
        "related_grant": product_grant_id,
        "amount_paid": float(price) if price else 0.0,
    })
    trx.insert(ignore_permissions=True)

    return {"name": trx.name, "status": "Pending Approval"}
```

### Example 4: Admin Notification Hook

```python
# memora_admin/events/purchase_sync.py
def on_purchase_request_created(doc, method):
    """Send notification to admins when new purchase request is created."""
    if doc.status != "Pending Approval":
        return

    # Get player display name
    player_name = frappe.get_value("Memora Player Profile", doc.player, "display_name") or doc.player

    # Get product name
    grant = frappe.get_doc("Memora Product Grant", doc.related_grant)
    item_name = frappe.get_value("Item", grant.item_code, "item_name") or grant.item_code

    # Build link to transaction in Frappe Desk
    site_url = frappe.utils.get_url()
    trx_link = f"{site_url}/app/memora-subscription-transaction/{doc.name}"

    # 1. Desk notification (bell icon)
    frappe.publish_realtime(
        event="eval_js",
        message=f'frappe.show_alert("New purchase request from {player_name}")',
        user="Administrator",
    )

    # 2. Email to all System Manager users
    admin_users = frappe.get_all(
        "Has Role",
        filters={"role": "System Manager", "parenttype": "User"},
        fields=["parent"],
    )
    recipients = []
    for u in admin_users:
        user = frappe.get_doc("User", u.parent)
        if user.enabled and user.email:
            recipients.append(user.email)

    if recipients:
        frappe.sendmail(
            recipients=recipients,
            subject=f"New Purchase Request: {item_name} - {player_name}",
            message=f"""
            <h3>New Purchase Request</h3>
            <p><strong>Player:</strong> {player_name}</p>
            <p><strong>Product:</strong> {item_name}</p>
            <p><strong>Amount:</strong> {doc.amount_paid}</p>
            <p><strong>Payment Method:</strong> {doc.payment_method}</p>
            <p><a href="{trx_link}">Review Transaction in Frappe Desk</a></p>
            """,
            now=True,
        )
```

## Decisions: Claude's Discretion Items

### Payment Proof Upload Mechanism
**Recommendation:** Client uploads directly to Frappe via `/api/method/upload_file`, gets back a `file_url`, then includes `payment_proof_url` in the purchase request body. This avoids FastAPI handling multipart uploads entirely.
**Rationale:** Frappe already handles file storage, permissions, and linking. Adding multipart to FastAPI adds complexity (python-multipart, temp files, proxying to Frappe) for no benefit.
**Implementation note:** The Subscription Transaction DocType needs a new `Attach Image` field for `payment_proof`. The Frappe whitelisted API stores the file_url on the transaction record.
**Confidence:** HIGH - simpler architecture, follows Frappe's existing file handling.

### Redis Key Structure for Pending Transactions
**Recommendation:** `memora:pending:{user_id}` as a Redis SET containing Product Grant IDs (e.g., "GRNT-00239").
**Rationale:** This is already what CatalogService reads (line 105 in catalog.py: `pipe.smembers(f"{self.prefix}pending:{player_id}")`). The key uses `user_id` (JWT `sub`), not Player Profile docname. Values are Product Grant IDs matching `product.product_grant_id` in catalog filtering.
**Confidence:** HIGH - must match existing CatalogService code exactly.

### Admin Email Template Design
**Recommendation:** Simple HTML email with player name, product name, amount, payment method, and a direct link to the transaction in Frappe Desk. No fancy template -- matches existing `task_utils.py` email pattern.
**Confidence:** HIGH - matches existing project patterns.

### Error Message Wording
**Recommendation:**
- Duplicate pending: HTTP 409 `{"detail": "Purchase request already pending for this product"}`
- Product not found: HTTP 404 `{"detail": "Product not found"}`
- Product not in plan: HTTP 403 `{"detail": "Product not available for your plan"}`
- Product unpublished: HTTP 404 `{"detail": "Product not found"}` (don't reveal unpublished products exist)
- No player profile: HTTP 404 `{"detail": "Player profile not found"}`
**Confidence:** HIGH - follows existing error patterns in the codebase (see `deps.py` HTTP exceptions).

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Subscription Transaction DocType is empty shell | Will get purchase flow logic | Phase 22 | First real use of this DocType |
| No pending detection in catalog | Pending set already wired in CatalogService | Phase 21 | Phase 22 just needs to populate it |

**Key observation:** The CatalogService (Phase 21) was built with Phase 22 in mind. The `memora:pending:{player_id}` set is already read but never written to. Phase 22 completes this circuit.

## DocType Schema Notes

### Memora Subscription Transaction (existing fields)
| Field | Type | Options | Required | Notes |
|-------|------|---------|----------|-------|
| player | Link | Memora Player Profile | Yes | Player Profile docname |
| payment_method | Select | Payment Gateway / Manual-Admin / Voucher | Yes | Use "Manual-Admin" for Phase 22 |
| status | Select | Pending Approval / Completed / Failed / Cancelled | Yes | Default: "Pending Approval" |
| transaction_id | Data | - | No | External transaction ID (not needed for manual) |
| amount_paid | Currency | - | Yes | Price from Item Price |
| erpnext_invoice | Link | Sales Invoice | No | Future use |
| related_grant | Link | Memora Product Grant | No | **Critical: must be set for pending detection** |

### Schema Changes Needed
- **Add `payment_proof` field**: Type `Attach Image` on Subscription Transaction DocType (for optional payment proof upload)
- **No other schema changes needed** - all required fields already exist

### Memora Product Grant (reference)
| Field | Type | Notes |
|-------|------|-------|
| plan | Link | To Memora Academic Plan |
| item_code | Link | To ERPNext Item |
| is_published | Check | Must be 1 for purchasable |
| grant_components | Table | Child table of Memora Grant Component |

## Open Questions

1. **Payment proof field on DocType**
   - What we know: DocType needs an `Attach Image` field for optional payment proof
   - What's unclear: Whether to add via JSON schema edit or Frappe customize
   - Recommendation: Add directly to the DocType JSON (it's our own DocType, not a core one)

2. **Pending set cleanup on server restart**
   - What we know: If Redis is flushed, pending set is lost but transactions still exist in Frappe
   - What's unclear: Whether to add a startup/periodic task to rebuild pending set from Frappe DB
   - Recommendation: Add a simple rebuild function (similar to `rebuild_plan_free_subjects`) but defer scheduled task to Phase 23 which handles approval/rejection anyway

3. **DocType "Rejected" status missing**
   - What we know: Current status options are: Pending Approval / Completed / Failed / Cancelled
   - What's unclear: Whether "Cancelled" serves as "Rejected" or if we need a new status
   - Recommendation: Add "Rejected" as a new status option. "Cancelled" implies player-initiated, "Rejected" implies admin-initiated. This is a simple JSON schema change.

## Sources

### Primary (HIGH confidence)
- Existing codebase: `fastapi_app/services/catalog.py` - CatalogService already reads `memora:pending:{player_id}`
- Existing codebase: `fastapi_app/services/frappe_client.py` - FrappeClient.call() pattern
- Existing codebase: `memora_admin/tasks/task_utils.py` lines 220-248 - Admin email notification pattern
- Existing codebase: `memora_admin/events/catalog_sync.py` - doc_event hook pattern
- Existing codebase: `memora_admin/api/subscriptions.py` - Frappe whitelisted API for DocType creation
- DocType JSON: `memora_subscription_transaction.json` - Field structure and options

### Secondary (MEDIUM confidence)
- [Frappe Notifications Documentation](https://docs.frappe.io/framework/notifications) - Notification system reference
- [Frappe File Attachments](https://frappeframework.com/docs/v15/user/en/desk/attachments) - File upload approach
- [Frappe Forum: File Attachment](https://discuss.frappe.io/t/how-to-create-an-attachment-in-python/81388) - Programmatic attachment

### Tertiary (LOW confidence)
- None - all findings verified against existing codebase patterns

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - zero new dependencies, all existing patterns
- Architecture: HIGH - follows established service/endpoint/Frappe API pattern exactly
- Pitfalls: HIGH - identified from codebase analysis (Redis-Frappe consistency, player ID confusion)
- Admin notification: HIGH - exact pattern exists in task_utils.py
- Payment proof upload: MEDIUM - recommended approach (client-to-Frappe direct) is standard but not yet used in this codebase

**Research date:** 2026-02-08
**Valid until:** 2026-03-08 (stable -- no external dependencies to go stale)
