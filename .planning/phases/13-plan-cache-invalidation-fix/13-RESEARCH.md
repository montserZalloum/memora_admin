# Phase 13: Plan Cache Invalidation Fix - Research

**Researched:** 2026-02-03
**Domain:** FastAPI pubsub cache invalidation
**Confidence:** HIGH

## Summary

This phase fixes a critical integration gap discovered during the v1.2 milestone audit. The FastAPI pubsub listener (`pubsub.py`) handles `type: "hierarchy"` cache invalidation messages but ignores `type: "plan"` messages, causing Plan manifest cache (1hr TTL) to never invalidate after rebuilds.

The fix is straightforward: extend the existing pubsub handler pattern to also handle Plan messages. The build worker already sends correctly formatted `type: "plan"` messages with `plan_id` field. PlanService already exists with an `invalidate(plan_id)` method. The only missing pieces are:
1. Registering PlanService in `app.state` during lifespan
2. Adding a condition in `_handle_invalidation()` for `type: "plan"` messages

**Primary recommendation:** Follow the exact HierarchyService pattern already established - register PlanService in main.py lifespan, then add parallel condition branch in pubsub.py `_handle_invalidation()` function.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis.asyncio | 4.x+ | Redis client for cache operations | Already used throughout codebase |
| structlog | 23.x+ | Structured logging | Already configured in core/logging.py |
| FastAPI | 0.100+ | App state management | Lifespan pattern already established |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| json | stdlib | Message parsing | Already used in pubsub.py |

### Alternatives Considered

None needed - this is a gap closure using existing patterns, not new technology selection.

## Architecture Patterns

### Recommended Project Structure

No new files needed. Changes only to existing files:
```
fastapi_app/
├── main.py              # ADD: PlanService registration (line ~49)
└── core/
    └── pubsub.py        # ADD: Plan handler in _handle_invalidation() (line ~91)
```

### Pattern 1: Service Registration in Lifespan

**What:** Register services in app.state during FastAPI lifespan for access across the application
**When to use:** Services that need Redis client and are accessed by pubsub or endpoints
**Example:**
```python
# Source: fastapi_app/main.py lines 44-49 (HierarchyService pattern)
# Create HierarchyService instance
hierarchy_service = HierarchyService(
    redis_client=redis_client,
    frappe_client=frappe_client,
)
app.state.hierarchy_service = hierarchy_service

# Pattern to replicate for PlanService:
plan_service = PlanService(
    redis_client=redis_client,
    frappe_client=frappe_client,
)
app.state.plan_service = plan_service
```

### Pattern 2: Pubsub Message Type Dispatch

**What:** Check message type field and dispatch to appropriate service invalidation method
**When to use:** Handling multiple cache invalidation message types on single channel
**Example:**
```python
# Source: fastapi_app/core/pubsub.py lines 91-106 (hierarchy pattern)
if msg_type == "hierarchy" and subject_id:
    hierarchy_service = getattr(app_state, "hierarchy_service", None)
    if hierarchy_service:
        await hierarchy_service.invalidate(subject_id)
        logger.info("hierarchy_cache_invalidated", subject_id=subject_id, timestamp=timestamp)
    else:
        logger.warning("hierarchy_service_not_available", subject_id=subject_id)

# Pattern to replicate for Plan:
elif msg_type == "plan" and plan_id:
    plan_service = getattr(app_state, "plan_service", None)
    if plan_service:
        await plan_service.invalidate(plan_id)
        logger.info("plan_cache_invalidated", plan_id=plan_id, timestamp=timestamp)
    else:
        logger.warning("plan_service_not_available", plan_id=plan_id)
```

### Pattern 3: Message Payload Structure

**What:** Frappe build worker publishes JSON messages with type and ID fields
**When to use:** Understanding expected payload format
**Example:**
```python
# Source: memora_admin/tasks/build_worker.py lines 180-191
# Plan invalidation message format:
{
    "type": "plan",
    "plan_id": "PLAN-00001",
    "timestamp": "2026-02-03T15:30:00+00:00"
}

# Hierarchy invalidation message format:
{
    "type": "hierarchy",
    "subject_id": "SUBJ-00001",
    "timestamp": "2026-02-03T15:30:00+00:00"
}
```

### Anti-Patterns to Avoid

- **Creating separate pubsub channels:** Don't create `memora:cache:invalidate:plan` - use existing channel with type field dispatch
- **Lazy service initialization in pubsub:** Don't create PlanService inside handler - register in lifespan for clean lifecycle management
- **Ignoring missing service gracefully:** Already handled - log warning but don't crash listener (matches existing pattern)

## Don't Hand-Roll

This phase is purely about wiring existing components. No new functionality is being built.

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Plan cache invalidation | New invalidation mechanism | Existing `PlanService.invalidate()` | Already implemented in Phase 12-03 |
| Pubsub listening | New listener | Existing `start_pubsub_listener()` | Already handles hierarchy, just needs Plan branch |
| Service registration | New patterns | Existing lifespan pattern | HierarchyService registration is the template |

**Key insight:** This is not new functionality - it's connecting existing pieces that were built in Phase 12 but not wired together.

## Common Pitfalls

### Pitfall 1: Forgetting to Import PlanService

**What goes wrong:** NameError at runtime because PlanService not imported in main.py
**Why it happens:** main.py currently only imports HierarchyService
**How to avoid:** Add `from fastapi_app.services.plan import PlanService` to main.py imports
**Warning signs:** ImportError or NameError when FastAPI starts

### Pitfall 2: Wrong Message Field Name

**What goes wrong:** Handler checks for wrong field (e.g., `subject_id` instead of `plan_id`)
**Why it happens:** Copy-paste from hierarchy handler without updating field names
**How to avoid:** Build worker sends `plan_id` for Plan messages - match this exactly
**Warning signs:** "unknown_invalidation_message" log entries for Plan rebuilds

### Pitfall 3: Service Order in Lifespan

**What goes wrong:** PlanService created before redis_client exists
**Why it happens:** Adding PlanService initialization in wrong position
**How to avoid:** Add after redis_client creation and frappe_client creation (same position as HierarchyService)
**Warning signs:** AttributeError about redis_client being None

### Pitfall 4: Missing else-if Condition

**What goes wrong:** Using separate `if` instead of `elif` for Plan check
**Why it happens:** Treating message types as independent checks
**How to avoid:** Use `elif` to maintain proper dispatch flow with final `else` for unknown
**Warning signs:** Both hierarchy and plan handlers firing for same message (unlikely but indicates logic error)

## Code Examples

Verified patterns from existing codebase:

### PlanService Invalidation Method

```python
# Source: fastapi_app/services/plan.py lines 88-98
async def invalidate(self, plan_id: str) -> None:
    """
    Invalidate plan manifest cache.

    Called when:
    - Plan JSON is regenerated (Phase 6 build worker)
    - Manual cache clear
    """
    key = self._cache_key(plan_id)
    await self.redis.delete(key)
    logger.info("plan_manifest_invalidated", plan_id=plan_id)
```

### Build Worker Plan Message

```python
# Source: memora_admin/tasks/build_worker.py lines 180-185
if target_type == "Memora Academic Plan":
    message = json.dumps({
        "type": "plan",
        "plan_id": target_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
```

### HierarchyService Registration (Pattern to Follow)

```python
# Source: fastapi_app/main.py lines 44-49
# Create HierarchyService instance
hierarchy_service = HierarchyService(
    redis_client=redis_client,
    frappe_client=frappe_client,
)
app.state.hierarchy_service = hierarchy_service
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single message type | Type-dispatched messages | Phase 12 (v1.2) | Enables multiple cache types on one channel |

**Deprecated/outdated:** None - this is greenfield integration of existing components.

## Open Questions

None. The implementation path is fully defined:

1. The message format is established (build_worker.py sends `type: "plan"` with `plan_id`)
2. The service exists (PlanService with `invalidate()` method)
3. The registration pattern is established (HierarchyService in main.py)
4. The dispatch location is known (pubsub.py `_handle_invalidation()`)

## Sources

### Primary (HIGH confidence)

- `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/core/pubsub.py` - Current pubsub implementation
- `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/main.py` - Service registration pattern
- `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/services/plan.py` - PlanService with invalidate()
- `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/services/hierarchy.py` - HierarchyService pattern
- `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/tasks/build_worker.py` - Message format (lines 180-199)
- `/home/corex/aurevia-bench/apps/memora_admin/.planning/v1.2-MILESTONE-AUDIT.md` - Gap definition

### Secondary (MEDIUM confidence)

- `/home/corex/aurevia-bench/apps/memora_admin/.planning/ROADMAP.md` - Phase requirements

### Tertiary (LOW confidence)

None - all findings verified from codebase.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - using existing libraries already in codebase
- Architecture: HIGH - following exact existing patterns (HierarchyService)
- Pitfalls: HIGH - derived from codebase analysis, not speculation

**Research date:** 2026-02-03
**Valid until:** No expiry - this is implementation guidance for existing code, not library research
