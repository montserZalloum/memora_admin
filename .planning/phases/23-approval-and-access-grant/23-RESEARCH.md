# Phase 23: Approval and Access Grant - Research

**Researched:** 2026-02-08
**Domain:** Frappe doc_events, Redis access sync, subscription creation
**Confidence:** HIGH

## Summary

This phase adds approval/rejection handling to Memora Subscription Transaction. When an admin changes a transaction's status to "Completed" (approved), the system must: (1) read grant components from the related Product Grant, (2) create Memora Player Subscription records for each subject/track, (3) SREM from the pending set, and (4) rely on existing doc_events hooks to sync access to Redis. On rejection, only the pending set cleanup is needed.

The codebase already has all the building blocks: `on_subscription_change` in `access_sync.py` handles Redis SADD when a subscription is created, `get_grant_keys` in `api/products.py` derives access keys from grant components, and `api/subscriptions.py` has `create_subscription` that creates the Player Subscription doc (which triggers the hook). The new code is a `on_update` handler on Subscription Transaction that orchestrates these existing pieces.

**Primary recommendation:** Add an `on_update` doc_event handler on `Memora Subscription Transaction` that detects status changes and calls existing Frappe APIs/functions to create subscriptions and clean up Redis pending state.

## Standard Stack

No new libraries needed. This phase uses only existing Frappe and Redis patterns already in the codebase.

### Core (Already Present)
| Library | Purpose | Location |
|---------|---------|----------|
| frappe | Document events, ORM, hooks | `memora_admin/hooks.py` |
| redis (sync) | Direct Redis operations from Frappe handlers | `memora_admin/events/access_sync.py` |

### Existing Code to Reuse
| Component | File | What It Does |
|-----------|------|--------------|
| `get_grant_keys()` | `memora_admin/api/products.py` | Derives access keys from Product Grant components (`SUB-{subject}`, `TRK-{track}`) |
| `create_subscription()` | `memora_admin/api/subscriptions.py` | Creates Player Subscription with duplicate check, returns `{name, created}` |
| `on_subscription_change()` | `memora_admin/events/access_sync.py` | Auto-syncs to Redis `memora:access:{user_id}` on subscription insert/update (already wired in hooks.py) |
| `get_fastapi_redis()` | `memora_admin/events/access_sync.py` | Gets Redis connection for FastAPI sidecar |
| `on_product_grant_changed()` | `memora_admin/events/catalog_sync.py` | Invalidates catalog cache (may need to call after approval) |

## Architecture Patterns

### Flow: Approval (Status -> "Completed")

```
Admin saves transaction with status="Completed"
  |
  v
on_update handler fires (new code)
  |
  v
1. Check: was status changed TO "Completed"?
   (use doc.has_value_changed("status") or compare with db_get)
  |
  v
2. Load related Product Grant -> get grant_components
   (reuse logic from api/products.get_grant_keys)
  |
  v
3. For each grant component:
   - Derive access_key (SUB-{subject} or TRK-{track})
   - Get expires_at from player's season end_date
   - Create Memora Player Subscription via frappe.get_doc().insert()
   - (on_subscription_change hook auto-fires -> Redis SADD)
  |
  v
4. SREM product_grant_id from memora:pending:{user_id}
  |
  v
5. Invalidate catalog cache for player's plan (SREM from access set
   changes what's "purchased", catalog needs refresh)
```

### Flow: Rejection (Status -> "Rejected")

```
Admin saves transaction with status="Rejected"
  |
  v
on_update handler fires
  |
  v
1. Check: was status changed TO "Rejected"?
  |
  v
2. SREM product_grant_id from memora:pending:{user_id}
   (product reappears in catalog)
```

### Recommended Implementation Location

**Option A (Recommended): Doc_event in hooks.py + new handler function**

Add `on_update` event handler to `Memora Subscription Transaction` in hooks.py, pointing to a new function in the transaction's document class or a dedicated event module.

Best location: **`memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.py`** using Frappe's built-in `on_update` method on the Document class. This is the simplest approach and matches how `MemoraPlayerProfile` uses `after_insert`.

```python
class MemoraSubscriptionTransaction(Document):
    def on_update(self):
        if self.has_value_changed("status"):
            if self.status == "Completed":
                self._handle_approval()
            elif self.status == "Rejected":
                self._handle_rejection()
```

This avoids needing hooks.py changes since Document class methods are automatically called by Frappe.

### Redis Key Patterns

| Key | Type | Purpose |
|-----|------|---------|
| `memora:pending:{user_id}` | SET | Product grant IDs with pending transactions |
| `memora:access:{user_id}` | SET | Access keys (SUB-xxx, TRK-xxx) for granted content |
| `memora:catalog:{plan_id}` | STRING | Cached catalog JSON (needs invalidation on approval) |

**Important:** `user_id` here is the User email/ID (from `Memora Player Profile.user`), NOT the Player Profile docname. The pending set uses user_id (from JWT sub claim in FastAPI) while Player Subscription uses player profile docname.

### Access Key Derivation

From `api/products.py:get_grant_keys()`:
- `Memora Subject` component -> `SUB-{target_name}` (e.g., `SUB-SUBJ-00028`)
- `Memora Track` component -> `TRK-{target_name}` (e.g., `TRK-TRK-00015`)

### Expires At Value

Player Subscription requires `expires_at` (Date, required field). Two approaches:
1. **Season end_date** - Get from player's plan -> season -> end_date
2. **Far-future sentinel** - Use `2099-12-31` as in webhooks.py (line 69)

The webhook endpoint already uses the sentinel approach. For consistency, use the same unless there's a reason to tie to season. **Recommendation: Use season end_date** since the plan has a `season` field linking to `Memora Season` which has `end_date`. This is semantically correct - the subscription should expire when the season ends.

```python
# Get season end_date from player's plan
player = frappe.get_doc("Memora Player Profile", self.player)
plan = frappe.get_doc("Memora Academic Plan", player.plan)
season = frappe.get_doc("Memora Season", plan.season)
expires_at = season.end_date
```

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Redis access sync | Manual SADD in approval handler | Let `on_subscription_change` hook fire automatically | Already wired in hooks.py for after_insert on Player Subscription |
| Grant key derivation | Re-implement SUB-/TRK- logic | Import/call `get_grant_keys()` from `api/products.py` | Single source of truth for key format |
| Subscription creation | Direct frappe.get_doc in handler | Use the pattern from `api/subscriptions.create_subscription` | Has duplicate check, proper field mapping |
| Status change detection | Compare old vs new manually | Use `self.has_value_changed("status")` | Built-in Frappe Document method |
| Catalog invalidation | Manual Redis delete | Call catalog_sync pattern (direct delete + pubsub) | Two-pronged invalidation is established pattern |

## Common Pitfalls

### Pitfall 1: Pending Set Uses user_id, Not player_id
**What goes wrong:** Using the Player Profile docname as the Redis key for pending set
**Why it happens:** Player Subscription uses `player` field (docname like "moonzallou19@gmail.com"), but the pending set was written by FastAPI using `user_id` from JWT
**How to avoid:** Look up `player_doc.user` (which IS the docname since autoname is `field:user`) - actually for this DocType, the player field IS the user email. Verify: `Memora Player Profile` has `autoname: "field:user"`, so `doc.player` in the transaction IS the user email.
**Key insight:** Since `autoname: "field:user"`, the Player Profile docname IS the user's email. So `transaction.player` = player profile name = user email = user_id used in Redis keys.

### Pitfall 2: All-or-Nothing Subscription Creation
**What goes wrong:** Partial grant if one subscription insert fails (e.g., 2 of 3 subjects granted)
**Why it happens:** Frappe doesn't have built-in distributed transactions
**How to avoid:** Create all subscription docs first, catch any errors, and only proceed if ALL succeed. If any fails, delete the ones already created. Use try/except with rollback list.

```python
created_subs = []
try:
    for access_key in grant_keys:
        sub = frappe.get_doc({...})
        sub.insert(ignore_permissions=True)
        created_subs.append(sub.name)
    frappe.db.commit()
except Exception:
    # Rollback: delete created subscriptions
    for sub_name in created_subs:
        frappe.delete_doc("Memora Player Subscription", sub_name, force=True)
    frappe.db.commit()
    frappe.throw("Failed to create all subscriptions")
```

### Pitfall 3: Hook Fires on Every Save, Not Just Status Change
**What goes wrong:** Approval logic runs every time admin saves the transaction
**Why it happens:** `on_update` fires on every save
**How to avoid:** Always check `self.has_value_changed("status")` before doing anything. Additionally, only act on specific status values ("Completed", "Rejected").

### Pitfall 4: Catalog Cache Staleness After Approval
**What goes wrong:** Player still sees the product in catalog after approval because catalog cache wasn't invalidated
**Why it happens:** The access set is updated (via subscription hook) but the catalog cache for the plan is stale
**How to avoid:** After approval, invalidate the catalog cache for the player's plan using the same pattern as `catalog_sync.py` (direct delete + pubsub).

### Pitfall 5: Missing Pending Cleanup on Rejection
**What goes wrong:** Rejected product stays hidden from catalog permanently
**Why it happens:** The pending set entry was added during purchase request but never removed on rejection
**How to avoid:** Both approval AND rejection must SREM from `memora:pending:{user_id}`.

## Code Examples

### Status Change Detection (Frappe Built-in)
```python
# Source: Frappe Document class has_value_changed method
def on_update(self):
    if self.has_value_changed("status"):
        old_status = self.get_doc_before_save().status if self.get_doc_before_save() else None
        # Only handle transitions FROM "Pending Approval"
        if self.status == "Completed":
            self._handle_approval()
        elif self.status == "Rejected":
            self._handle_rejection()
```

### Grant Key Derivation (Reuse Existing)
```python
# Source: memora_admin/api/products.py
from memora_admin.api.products import get_grant_keys

# Inside handler:
grant_keys = get_grant_keys(self.related_grant)
# Returns: ["SUB-SUBJ-00028", "SUB-SUBJ-00031"]
```

### Subscription Creation Pattern
```python
# Source: memora_admin/api/subscriptions.py pattern
sub = frappe.get_doc({
    "doctype": "Memora Player Subscription",
    "player": self.player,           # Player Profile name (= user email)
    "access_key": access_key,         # e.g., "SUB-SUBJ-00028"
    "expires_at": expires_at,         # Season end_date or 2099-12-31
    "is_active": 1,
})
sub.insert(ignore_permissions=True)
# This triggers on_subscription_change hook -> Redis SADD automatically
```

### Pending Set Cleanup
```python
# Source: Pattern from PurchaseService (FastAPI) adapted for sync Redis
from memora_admin.events.access_sync import get_fastapi_redis

r = get_fastapi_redis()
user_id = self.player  # Player Profile name = user email (autoname: field:user)
r.srem(f"memora:pending:{user_id}", self.related_grant)
```

### Catalog Cache Invalidation
```python
# Source: memora_admin/events/catalog_sync.py pattern
import json
r = get_fastapi_redis()

# Get plan_id from player profile
plan_id = frappe.get_value("Memora Player Profile", self.player, "plan")

# Direct delete + pubsub (two-pronged)
r.delete(f"memora:catalog:{plan_id}")
r.publish("memora:cache:invalidate", json.dumps({
    "type": "catalog",
    "plan_id": plan_id,
    "timestamp": str(frappe.utils.now()),
}))
```

## State of the Art

| Old Approach | Current Approach | Context |
|--------------|------------------|---------|
| Payment webhook handles grant | Phase 23 adds manual admin approval | Webhook path exists in webhooks.py but is for future auto-approval |
| N/A | Event-driven doc_events | All Redis sync uses doc_events hooks, not polling |

**Transaction Status Values (from JSON schema):**
- `Pending Approval` (default, set by purchase request)
- `Completed` (admin approval - triggers grant)
- `Failed` (not used in Phase 23)
- `Cancelled` (not used in Phase 23)
- `Rejected` (admin rejection - triggers cleanup)

**Note:** The CONTEXT.md says "Approved" but the actual DocType status options use "Completed". The handler should use "Completed" as the approval status since that's what's in the schema.

## Open Questions

1. **Completed vs Approved status naming**
   - What we know: DocType has "Completed" not "Approved" in status options
   - What's unclear: Should we add "Approved" as a status option, or use "Completed"?
   - Recommendation: Use "Completed" as-is (matches existing schema). The context doc says "Approved" but that may have been conceptual. Using the existing "Completed" value avoids schema migration.

2. **Expires_at strategy**
   - What we know: Webhook uses sentinel `2099-12-31`, but plan has season with end_date
   - What's unclear: Should purchased subscriptions expire with the season?
   - Recommendation: Use season end_date for correctness. If no season, fall back to `2099-12-31`.

3. **Catalog invalidation necessity**
   - What we know: Catalog filters by access set (purchased detection) AND pending set
   - What's unclear: Is catalog invalidation needed? The access set check is live (not cached)
   - Recommendation: The catalog product list itself is cached (`memora:catalog:{plan_id}`), but the filtering against access/pending sets happens at query time (see `catalog.py` lines 102-133). So the cached catalog data doesn't need invalidation - only the pending set SREM matters. **No catalog cache invalidation needed.**

## Sources

### Primary (HIGH confidence)
- `memora_admin/hooks.py` - doc_events configuration, lines 143-219
- `memora_admin/events/access_sync.py` - `on_subscription_change()`, subscription Redis sync
- `memora_admin/api/products.py` - `get_grant_keys()`, grant component -> access key mapping
- `memora_admin/api/subscriptions.py` - `create_subscription()`, Player Subscription creation
- `memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.json` - status field options
- `memora_admin/memora_admin/doctype/memora_player_subscription/memora_player_subscription.json` - required fields
- `fastapi_app/services/purchase.py` - pending set key pattern (`memora:pending:{user_id}`)
- `fastapi_app/services/catalog.py` - catalog filtering logic (live access/pending check)
- `memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json` - autoname: field:user

### Secondary (MEDIUM confidence)
- `fastapi_app/api/v1/endpoints/webhooks.py` - reference implementation for grant flow (async version)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - all code is in the existing codebase, verified by reading source
- Architecture: HIGH - follows established event-driven patterns already in use
- Pitfalls: HIGH - derived from actual code analysis (key patterns, field names, hook behavior)

**Research date:** 2026-02-08
**Valid until:** 2026-03-08 (stable internal codebase)
