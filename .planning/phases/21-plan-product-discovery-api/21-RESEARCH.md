# Phase 21: Product Catalog API - Research

**Researched:** 2026-02-07
**Domain:** FastAPI endpoint + Redis caching for product catalog
**Confidence:** HIGH

## Summary

This phase adds a single FastAPI endpoint that returns a cached list of purchasable products for a player's plan. The codebase already has well-established patterns for every component needed: Redis caching (PlanService), event-driven cache invalidation (pubsub.py + hooks.py doc_events), Frappe whitelisted APIs (products.py), dependency injection (deps.py), and Pydantic response models.

The Product Grant DocType already exists with `plan`, `item_code` (Link to Item), `is_published`, and `grant_components` (child table linking to subjects/tracks). Item Price provides `price_list_rate`. Plan Subject provides `alias_title` and `notes`. Subscription Transaction provides `status` (including "Pending Approval") and `related_grant` (Link to Product Grant).

**Primary recommendation:** Follow the PlanService pattern exactly -- Frappe whitelisted API builds the catalog payload, FastAPI service caches it in Redis with event-driven invalidation, endpoint applies per-player filtering (purchased/pending) as a post-cache step.

## Standard Stack

No new libraries needed. This phase uses only existing dependencies.

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | existing | API endpoint | Already in stack |
| redis.asyncio | existing | Cache storage | Already in stack |
| Pydantic v2 | existing | Response models | Already in stack |
| structlog | existing | Logging | Already in stack |
| Frappe v15 | existing | Data source + event hooks | Already in stack |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | existing | FrappeClient calls | Fetching catalog data from Frappe API |

No `pip install` needed. No new dependencies.

## Architecture Patterns

### Recommended Structure

New files to create:
```
fastapi_app/
├── api/v1/endpoints/
│   └── catalog.py              # GET /catalog endpoint
├── models/
│   └── catalog.py              # Pydantic response models
└── services/
    └── catalog.py              # CatalogService (Redis cache + Frappe fallback)

memora_admin/
├── api/
│   └── catalog.py              # Frappe whitelisted API (build catalog payload)
└── events/
    └── catalog_sync.py         # Event hooks for cache invalidation
```

Modified files:
```
fastapi_app/api/v1/router.py    # Register catalog router
fastapi_app/api/deps.py         # Add CatalogServiceDep
fastapi_app/main.py             # Add CatalogService to lifespan (for pubsub)
fastapi_app/core/pubsub.py      # Handle "catalog" invalidation message type
memora_admin/hooks.py           # Register doc_events for Product Grant
```

### Pattern 1: Two-Layer Cache (Per-Plan + Per-Player Filtering)

**What:** Cache the full plan catalog in Redis (shared by all players on same plan). Apply per-player exclusions (purchased + pending) at request time as a post-cache filter.

**When to use:** Always. This is the decided approach from CONTEXT.md.

**Why this split:**
- Plan catalog changes rarely (admin action only) -- cacheable indefinitely
- Player purchases/pending status change frequently -- must be fresh
- O(P) post-cache filter where P = purchased products (typically <10) is negligible vs cache miss

**Implementation:**
```python
# CatalogService pattern (follows PlanService exactly)
class CatalogService:
    def __init__(self, redis_client, frappe_client, key_prefix="memora:"):
        self.redis = redis_client
        self.frappe = frappe_client
        self.prefix = key_prefix

    def _cache_key(self, plan_id: str) -> str:
        return f"{self.prefix}catalog:{plan_id}"

    async def get_catalog(self, plan_id: str) -> list[CatalogProduct] | None:
        """Get plan catalog from cache or Frappe. No TTL -- infinite cache."""
        key = self._cache_key(plan_id)
        cached = await self.redis.get(key)
        if cached:
            return [CatalogProduct.model_validate_json(p) for p in json.loads(cached)]

        # Cache miss: fetch from Frappe whitelisted API
        result = await self.frappe.call(
            "memora_admin.api.catalog.get_plan_catalog",
            {"plan_id": plan_id},
        )
        if result is None:
            return []

        products = [CatalogProduct.model_validate(p) for p in result]
        # Cache with no TTL (infinite)
        await self.redis.set(key, json.dumps([p.model_dump_json() for p in products]))
        return products

    async def invalidate(self, plan_id: str) -> None:
        """Delete cached catalog for plan."""
        key = self._cache_key(plan_id)
        await self.redis.delete(key)
```

### Pattern 2: Post-Cache Player Filtering

**What:** After retrieving the shared plan catalog from cache, filter out products the player has already purchased or has pending transactions for.

**Implementation approach -- use Redis sets for purchased/pending tracking:**
```python
# Redis keys for per-player state:
# memora:purchased:{player_id} -> SET of product_grant_ids (e.g., "GRNT-00239")
# memora:pending:{player_id}   -> SET of product_grant_ids

async def get_player_catalog(self, plan_id: str, player_id: str) -> list[CatalogProduct]:
    products = await self.get_catalog(plan_id)
    if not products:
        return []

    # Get player's purchased and pending sets (pipeline for single round-trip)
    pipe = self.redis.pipeline()
    pipe.smembers(f"{self.prefix}purchased:{player_id}")
    pipe.smembers(f"{self.prefix}pending:{player_id}")
    purchased_raw, pending_raw = await pipe.execute()

    purchased = {m.decode() if isinstance(m, bytes) else m for m in purchased_raw}
    pending = {m.decode() if isinstance(m, bytes) else m for m in pending_raw}

    result = []
    for product in products:
        if product.product_grant_id in purchased:
            continue  # Hide purchased
        if product.product_grant_id in pending:
            continue  # Hide pending (per CONTEXT.md decision)
        result.append(product)

    return result
```

**Alternative considered (post-cache annotation instead of filtering):** Mark pending products with `status: "pending"` instead of hiding them. However, CONTEXT.md explicitly says "Hide from catalog entirely if product has pending purchase transaction." So we filter them out.

### Pattern 3: Event-Driven Cache Invalidation (Frappe Hooks)

**What:** Use Frappe doc_events hooks to publish Redis invalidation messages when Product Grant or related data changes.

**Existing pattern from hooks.py:**
```python
# In hooks.py doc_events:
"Memora Product Grant": {
    "after_insert": "memora_admin.events.catalog_sync.on_product_grant_changed",
    "on_update": "memora_admin.events.catalog_sync.on_product_grant_changed",
    "on_trash": "memora_admin.events.catalog_sync.on_product_grant_changed",
},
```

**Event handler pattern (follows access_sync.py):**
```python
def on_product_grant_changed(doc, method):
    """Invalidate catalog cache when Product Grant changes."""
    r = get_fastapi_redis()
    plan_id = doc.plan

    # Delete cached catalog for this plan
    r.delete(f"memora:catalog:{plan_id}")

    # Also publish invalidation for FastAPI pubsub listener
    import json
    r.publish("memora:cache:invalidate", json.dumps({
        "type": "catalog",
        "plan_id": plan_id,
        "timestamp": str(frappe.utils.now()),
    }))
```

### Pattern 4: Frappe Whitelisted API for Catalog Data Assembly

**What:** A Frappe whitelisted API that queries Product Grant + Item Price + Plan Subject to build the full catalog payload. This runs server-side with full database access.

**Data assembly requires joining:**
1. `Memora Product Grant` (filtered by plan, is_published=1)
2. `Item Price` (for price_list_rate, filtered by price_list="Standard Selling")
3. `Memora Grant Component` (child table for subject list)
4. `Memora Plan Subject` (for alias_title, notes per subject)

```python
@frappe.whitelist(allow_guest=False)
def get_plan_catalog(plan_id: str) -> list[dict]:
    """Build catalog payload for a plan. Called by FastAPI CatalogService on cache miss."""
    grants = frappe.get_all(
        "Memora Product Grant",
        filters={"plan": plan_id, "is_published": 1},
        fields=["name", "item_code"],
    )

    products = []
    for grant in grants:
        # Get price
        price = frappe.get_value(
            "Item Price",
            {"item_code": grant.item_code, "price_list": "Standard Selling"},
            "price_list_rate",
        )

        # Get item name (bundle name)
        item_name = frappe.get_value("Item", grant.item_code, "item_name")

        # Get grant components (subjects)
        components = frappe.get_all(
            "Memora Grant Component",
            filters={"parent": grant.name},
            fields=["target_doctype", "target_name"],
        )

        # Enrich subjects with plan-level metadata
        subjects = []
        for comp in components:
            if comp.target_doctype == "Memora Subject":
                ps = frappe.get_value(
                    "Memora Plan Subject",
                    {"parent": plan_id, "subject": comp.target_name},
                    ["alias_title", "notes"],
                    as_dict=True,
                )
                subjects.append({
                    "subject_id": comp.target_name,
                    "alias_title": ps.alias_title if ps else None,
                    "notes": ps.notes if ps else None,
                })

        products.append({
            "product_grant_id": grant.name,
            "bundle_name": item_name,
            "price": float(price) if price else 0.0,
            "subjects": subjects,
        })

    return products
```

### Anti-Patterns to Avoid
- **Querying Frappe from FastAPI endpoint directly:** Always go through FrappeClient + whitelisted API. Never import frappe in FastAPI code.
- **TTL-based invalidation for catalog cache:** Decision says no TTL, event-driven only. TTL would add unnecessary cache misses.
- **Per-player catalog caching:** Decision says per-plan cache. Per-player would multiply cache size by player count with minimal benefit.
- **Returning stale data on cache failure:** Decision says 503, not fallback. Cache is critical dependency.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Redis caching pattern | Custom cache wrapper | Follow PlanService pattern | Proven pattern in codebase, handles bytes/str, cache miss, invalidation |
| Event-driven invalidation | Custom listener | Extend pubsub.py handler | Already handles hierarchy/plan/profile; add "catalog" type |
| Frappe data assembly | Raw SQL in FastAPI | Frappe whitelisted API | Frappe ORM handles permissions, field resolution, site context |
| Dependency injection | Manual service creation | Follow deps.py Annotated+Depends | Consistent with 10+ existing services |
| Player exclusion data | Query Frappe per-request | Redis sets (purchased/pending) | O(1) SISMEMBER vs 30ms+ Frappe API call |

## Common Pitfalls

### Pitfall 1: Redis Port Mismatch (Frappe vs FastAPI)
**What goes wrong:** Frappe hooks write to one Redis, FastAPI reads from another
**Why it happens:** Frappe uses `redis://127.0.0.1:13000`, FastAPI uses REDIS_URL from .env
**How to avoid:** Use `get_fastapi_redis()` in event hooks (already established in access_sync.py). Verify .env REDIS_URL matches.
**Warning signs:** Cache invalidation appears to fire but catalog stays stale

### Pitfall 2: Missing pubsub Handler for New Message Type
**What goes wrong:** Frappe publishes "catalog" invalidation but FastAPI ignores it
**Why it happens:** pubsub.py only handles "hierarchy", "plan", "profile" message types
**How to avoid:** Add "catalog" handler to `_handle_invalidation()` in pubsub.py, register CatalogService in main.py lifespan
**Warning signs:** Catalog cache never invalidates despite Frappe events firing

### Pitfall 3: Forgetting to Register Router and Dependencies
**What goes wrong:** Endpoint exists but 404s
**Why it happens:** New router not added to `router.py`, new service not in `deps.py`
**How to avoid:** Checklist: router.py import + include_router, deps.py service factory + type alias, main.py lifespan service creation
**Warning signs:** 404 on catalog endpoint, or dependency injection error

### Pitfall 4: Purchased/Pending Sets Not Populated
**What goes wrong:** Players see products they already purchased
**Why it happens:** Redis sets `memora:purchased:{player_id}` and `memora:pending:{player_id}` are never written to
**How to avoid:** Phase 22/23 will manage these sets. For Phase 21, the Frappe API can check Subscription Transaction status at query time during cache build, OR the endpoint can query these sets and gracefully handle empty sets (show all products if no purchase history).
**Warning signs:** All products shown regardless of purchase history

**Important note on Phase 21 scope:** The purchased/pending exclusion logic needs data that Phase 22-23 will fully manage. For Phase 21:
- "Already purchased" = Player has a `Memora Player Subscription` with `access_key` matching grant components. Can check via existing `memora:access:{player_id}` Redis set.
- "Pending" = `Memora Subscription Transaction` with `related_grant` = this grant AND `status` = "Pending Approval". Must query Frappe or maintain a Redis set.

**Recommended approach for Phase 21:**
- Check "purchased" via existing `memora:access:{player_id}` set (already populated by access_sync.py)
- Check "pending" via Frappe query in the whitelisted API at cache-build time is NOT viable (it's per-player, not per-plan)
- Instead: maintain `memora:pending:{player_id}` Redis set. Phase 22 populates it. Phase 21 reads it (empty set = no pending = show all). This is forward-compatible.

### Pitfall 5: Item Price Not Found
**What goes wrong:** Products return price 0.0
**Why it happens:** Item Price requires matching price_list. Currently "Standard Selling" is used.
**How to avoid:** Always filter by `price_list="Standard Selling"` and handle None gracefully (skip product or show 0.0)
**Warning signs:** All prices are 0.0

## Code Examples

### Pydantic Response Models
```python
# fastapi_app/models/catalog.py
from pydantic import BaseModel, Field

class CatalogSubject(BaseModel):
    """Subject within a product bundle."""
    subject_id: str
    alias_title: str | None = None
    notes: str | None = None

class CatalogProduct(BaseModel):
    """A purchasable product in the catalog."""
    product_grant_id: str = Field(..., description="DocType name e.g. GRNT-00239")
    bundle_name: str = Field(..., description="Item name from ERPNext")
    price: float = Field(..., description="Raw price_list_rate number")
    subjects: list[CatalogSubject] = Field(default_factory=list)

class CatalogResponse(BaseModel):
    """Catalog endpoint response."""
    products: list[CatalogProduct] = Field(default_factory=list)
```

### Endpoint Pattern
```python
# fastapi_app/api/v1/endpoints/catalog.py
router = APIRouter(prefix="/catalog", tags=["catalog"])

@router.get("/", response_model=CatalogResponse)
async def get_catalog(
    user: CurrentUser,
    catalog_service: CatalogServiceDep,
) -> CatalogResponse:
    """Get product catalog for player's plan."""
    if not user.plan:
        return CatalogResponse(products=[])

    try:
        products = await catalog_service.get_player_catalog(
            plan_id=user.plan,
            player_id=user.sub,
        )
    except redis.RedisError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )

    return CatalogResponse(products=products)
```

### Cache Invalidation Event Hook
```python
# memora_admin/events/catalog_sync.py
def on_product_grant_changed(doc, method):
    """Invalidate catalog cache when Product Grant changes."""
    import json
    r = get_fastapi_redis()
    plan_id = doc.plan

    # Direct cache delete (immediate)
    r.delete(f"memora:catalog:{plan_id}")

    # Pub/sub notification for FastAPI sidecar
    r.publish("memora:cache:invalidate", json.dumps({
        "type": "catalog",
        "plan_id": plan_id,
        "timestamp": str(frappe.utils.now()),
    }))

    frappe.logger().info(f"Catalog cache invalidated for plan {plan_id}")
```

### Pubsub Handler Extension
```python
# In pubsub.py _handle_invalidation():
elif msg_type == "catalog" and plan_id:
    catalog_service = getattr(app_state, "catalog_service", None)
    if catalog_service:
        await catalog_service.invalidate(plan_id)
        logger.info("catalog_cache_invalidated", plan_id=plan_id)
```

### Purchased Check via Existing Access Set
```python
# In CatalogService - check if player already has access to all subjects in a grant
async def _is_grant_purchased(self, player_id: str, grant_subjects: list[str]) -> bool:
    """Check if player has access to ALL subjects in grant (= purchased)."""
    if not grant_subjects:
        return False
    key = f"{self.prefix}access:{player_id}"
    pipe = self.redis.pipeline()
    for subject_id in grant_subjects:
        pipe.sismember(key, f"SUB-{subject_id}")
    results = await pipe.execute()
    return all(results)  # Purchased only if ALL subjects are accessible
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| N/A | This is new functionality | Phase 21 | First product catalog API |

**Existing infrastructure leveraged:**
- Redis caching pattern (PlanService, HierarchyService) -- Phase 12
- Event-driven invalidation (pubsub.py) -- Phase 6/13
- Frappe doc_events hooks (access_sync.py) -- Phase 3
- Access set checking (AccessService) -- Phase 3
- FrappeClient for whitelisted API calls -- Phase 12

## Open Questions

1. **"Purchased" detection without explicit tracking**
   - What we know: Existing `memora:access:{player_id}` set tracks granted access keys like `SUB-{subject_id}`. A grant is "purchased" if player has access to all its component subjects.
   - What's unclear: Access can also come from plan membership (is_premium=0), not just purchase. Checking access set alone may produce false positives (plan-included subjects look "purchased").
   - Recommendation: Check `Memora Player Subscription` records for the specific `access_key` values rather than the access set. This requires a Frappe API call per-player (not cacheable per-plan). Best approach: maintain a `memora:purchased:{player_id}` set populated when transactions are completed (Phase 23). For Phase 21, do a lightweight Frappe query or accept that plan-included subjects may incorrectly mark a grant as "purchased."

2. **Subject metadata source: Grant Component vs Plan Subject**
   - What we know: Grant components link to `Memora Subject` (target_name). Plan Subject has `alias_title` and `notes` per plan.
   - What's unclear: If a grant component references a subject not in the Plan Subject table (e.g., a premium subject added to grant but not to plan), there's no alias_title/notes.
   - Recommendation: Fall back to subject's own title if Plan Subject entry doesn't exist. This is an edge case but should be handled gracefully.

3. **Item Price price_list selection**
   - What we know: Currently only "Standard Selling" price list has entries. Only one price exists per item.
   - What's unclear: If multiple price lists exist in future, which takes precedence?
   - Recommendation: Hardcode "Standard Selling" for now. If multi-price-list is needed later, add plan-level price_list config.

## Sources

### Primary (HIGH confidence)
- Codebase inspection: `memora_admin/doctype/memora_product_grant/memora_product_grant.json` -- DocType schema
- Codebase inspection: `memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.json` -- Transaction schema
- Codebase inspection: `memora_admin/doctype/memora_plan_subject/memora_plan_subject.json` -- Plan Subject schema with alias_title, notes
- Codebase inspection: `fastapi_app/services/plan.py` -- PlanService caching pattern
- Codebase inspection: `fastapi_app/core/pubsub.py` -- Cache invalidation pub/sub pattern
- Codebase inspection: `memora_admin/events/access_sync.py` -- Event hook pattern with get_fastapi_redis()
- Codebase inspection: `fastapi_app/api/deps.py` -- Dependency injection pattern
- Codebase inspection: `memora_admin/hooks.py` -- doc_events registration pattern
- Live database: Verified GRNT-00239 exists with item_code, Item Price at 10.0, Plan Subject with alias_title

### Secondary (MEDIUM confidence)
- Architecture inference: Post-cache filtering approach based on cache granularity decision and existing patterns

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries, all patterns exist in codebase
- Architecture: HIGH -- follows established PlanService/pubsub patterns exactly
- Pitfalls: HIGH -- identified from real codebase issues (Redis port mismatch documented in MEMORY.md)
- Purchased detection: MEDIUM -- multiple approaches possible, depends on Phase 22/23 design

**Research date:** 2026-02-07
**Valid until:** 2026-03-07 (stable domain, internal project)
