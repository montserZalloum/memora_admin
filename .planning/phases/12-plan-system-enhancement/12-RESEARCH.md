# Phase 12: Plan System Enhancement - Research

**Researched:** 2026-02-03
**Domain:** Frappe DocTypes, Frappe Form JS, Build Queue Extension, FastAPI JSON Serving
**Confidence:** HIGH

## Summary

Phase 12 implements Grade-Major linking via a child table and Plan-centric JSON generation for mobile app consumption. This builds on the existing build queue infrastructure from Phase 6, extending it to support Plan-level builds alongside Subject-level builds.

The research confirms that:
1. Frappe child tables (`istable: 1`) are the correct pattern for Grade -> Majors relationship
2. `frm.set_query()` is the standard Frappe API for filtering Link fields based on other field values
3. The existing `build_worker.py` and `build_trigger.py` patterns can be directly extended for Plan builds
4. FastAPI Redis caching patterns already exist in `HierarchyService` and can be replicated for Plan JSON serving
5. Doc_events hooks in `hooks.py` work correctly with parent DocType updates when child tables change

**Primary recommendation:** Extend the existing build infrastructure with Plan-specific handlers, add Grade-Major child table using existing child table patterns, and create a new PlanService for FastAPI that mirrors HierarchyService patterns.

## Standard Stack

The established libraries/tools for this domain:

### Core (Already in Project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Frappe v15 | 15.x | DocType framework, hooks, form JS | Project foundation |
| FastAPI | Latest | High-performance API | Existing sidecar |
| redis.asyncio | Latest | Async Redis client | Existing caching layer |
| Pydantic | v2 | Data validation/schemas | Existing models |

### Supporting (Already in Project)
| Library | Purpose | When to Use |
|---------|---------|-------------|
| structlog | Structured logging | All FastAPI logging |
| frappe.cache | Redis operations in Frappe | Build trigger debounce |
| frappe.publish_realtime | Frappe notifications | Build completion alerts |

### No New Dependencies Required
This phase uses only existing project dependencies. No new libraries needed.

## Architecture Patterns

### Recommended Project Structure

New/modified files for Phase 12:

```
memora_admin/
├── memora_admin/memora_admin/
│   ├── doctype/
│   │   ├── memora_grade/
│   │   │   └── memora_grade.json          # ADD: majors Table field
│   │   ├── memora_grade_major/            # NEW: child table DocType
│   │   │   ├── __init__.py
│   │   │   ├── memora_grade_major.py
│   │   │   └── memora_grade_major.json
│   │   └── memora_academic_plan/
│   │       └── memora_academic_plan.js    # ADD: Major filtering by Grade
│   ├── events/
│   │   └── build_trigger.py               # ADD: Plan-level trigger logic
│   ├── tasks/
│   │   └── build_worker.py                # ADD: Plan build handler
│   └── services/build/
│       └── generator.py                   # ADD: generate_plan_json()
│
└── fastapi_app/
    ├── api/v1/endpoints/
    │   └── plans.py                       # NEW: Plan JSON endpoint
    ├── services/
    │   └── plan.py                        # NEW: PlanService with caching
    └── models/
        └── plan.py                        # NEW: Plan Pydantic schemas
```

### Pattern 1: Frappe Child Table DocType

**What:** Child table for Grade -> Majors relationship
**When to use:** One-to-many relationships where child rows are owned by parent

**JSON Schema for Child Table:**
```json
// Source: Existing memora_plan_subject.json pattern
{
  "doctype": "DocType",
  "module": "Memora Admin",
  "name": "Memora Grade Major",
  "istable": 1,
  "editable_grid": 1,
  "field_order": ["major"],
  "fields": [
    {
      "fieldname": "major",
      "fieldtype": "Link",
      "label": "Major",
      "options": "Memora Major",
      "reqd": 1,
      "in_list_view": 1
    }
  ]
}
```

**Parent DocType Update (memora_grade.json):**
```json
// Add to field_order and fields arrays
{
  "fieldname": "majors",
  "fieldtype": "Table",
  "label": "Majors",
  "options": "Memora Grade Major"
}
```

### Pattern 2: Form Link Field Filtering

**What:** Dynamic filtering of Major dropdown based on selected Grade
**When to use:** When Link field options should be constrained by another field's value

**JavaScript (memora_academic_plan.js):**
```javascript
// Source: Frappe docs + manual.buildwithhussain.com
frappe.ui.form.on("Memora Academic Plan", {
    refresh(frm) {
        // Set query on form load and grade change
        frm.trigger("grade");
    },

    grade(frm) {
        if (frm.doc.grade) {
            frm.set_query("major", () => {
                return {
                    query: "memora_admin.api.plan.get_majors_for_grade",
                    filters: {
                        grade: frm.doc.grade
                    }
                };
            });
        } else {
            // Clear filter if no grade selected
            frm.set_query("major", () => {
                return {};
            });
        }
        // Clear major if grade changes
        if (frm.doc.major) {
            frm.set_value("major", null);
        }
    }
});
```

**Server-side Query (api/plan.py):**
```python
# Source: Existing Frappe patterns
import frappe

@frappe.whitelist()
def get_majors_for_grade(doctype, txt, searchfield, start, page_len, filters):
    """Return majors linked to the specified grade."""
    grade = filters.get("grade")
    if not grade:
        return []

    # Get majors from Grade's child table
    majors = frappe.get_all(
        "Memora Grade Major",
        filters={"parent": grade},
        pluck="major"
    )

    if not majors:
        return []

    # Return matching majors with standard search
    return frappe.get_all(
        "Memora Major",
        filters={
            "name": ["in", majors],
            "major_title": ["like", f"%{txt}%"]
        },
        fields=["name", "major_title"],
        as_list=True
    )
```

### Pattern 3: Build Trigger Extension

**What:** Add Plan-level debounce alongside existing Subject-level
**When to use:** When Plan or its subjects change

**Extension to build_trigger.py:**
```python
# Source: Existing build_trigger.py patterns
PLAN_DEBOUNCE_KEY_PREFIX = "memora:build:pending:plan:"

def on_plan_content_updated(doc, method):
    """Queue Plan build when Plan-related DocTypes change."""
    plan_id = _get_plan_id(doc)
    if not plan_id:
        return

    cache = frappe.cache
    debounce_key = f"{PLAN_DEBOUNCE_KEY_PREFIX}{plan_id}"

    timestamp = str(int(time.time()))
    was_set = cache.set(debounce_key, timestamp, nx=True, ex=DEBOUNCE_SECONDS)

    if not was_set:
        return

    frappe.get_doc({
        "doctype": "Memora Build Queue",
        "target_type": "Memora Academic Plan",  # NEW target type
        "target_name": plan_id,
        "trigger_reason": "plan_content_update",
        "triggered_by": frappe.session.user,
        "status": "Pending",
    }).insert(ignore_permissions=True)

def _get_plan_id(doc) -> str | None:
    """Extract plan ID from Plan-related DocTypes."""
    doctype = doc.doctype

    if doctype == "Memora Academic Plan":
        return doc.name

    if doctype == "Memora Plan Subject":
        return doc.parent

    if doctype == "Memora Plan Overrider":
        return doc.plan

    return None
```

### Pattern 4: Build Worker Extension

**What:** Handle Plan builds alongside Subject builds
**When to use:** When processing Memora Build Queue items

**Extension to build_worker.py:**
```python
# Source: Existing build_worker.py patterns
def _process_single_build(build: dict):
    build_doc = frappe.get_doc("Memora Build Queue", build["name"])
    target_type = build_doc.target_type
    target_name = build_doc.target_name

    # Dispatch based on target type
    if target_type == "Memora Subject":
        from memora_admin.memora_admin.services.build.generator import generate_subject_json
        files = generate_subject_json(target_name)
    elif target_type == "Memora Academic Plan":
        from memora_admin.memora_admin.services.build.generator import generate_plan_json
        files = generate_plan_json(target_name)
    else:
        raise ValueError(f"Unknown target_type: {target_type}")

    # ... rest of processing (same as existing)
```

### Pattern 5: Plan JSON Generator

**What:** Generate plan-centric JSON structure
**When to use:** When building Plan JSON files

**Key function structure:**
```python
# Source: Existing generator.py patterns + v1.2-ROADMAP.md spec
def generate_plan_json(plan_id: str) -> list[dict]:
    """Generate all JSON files for a plan."""
    files: list[dict] = []

    plan_doc = frappe.get_doc("Memora Academic Plan", plan_id)

    # 1. Generate manifest.json
    manifest = _generate_plan_manifest(plan_doc)
    files.append({
        "filename": f"plans/{plan_id}/manifest.json",
        "content": _to_json(manifest),
    })

    # 2. For each subject in plan
    for plan_subject in plan_doc.plan_subjects:
        subject_id = plan_subject.subject

        # Get Plan Overrides for this subject
        overrides = _get_plan_overrides(plan_id, subject_id)

        # Generate hierarchy with overrides applied
        hierarchy = _generate_subject_hierarchy_for_plan(
            subject_id, plan_id, overrides
        )
        files.append({
            "filename": f"plans/{plan_id}/subjects/{subject_id}/_h.json",
            "content": _to_json(hierarchy),
        })

        # Generate unit content files
        unit_files = _generate_unit_files_for_plan(
            subject_id, plan_id, overrides
        )
        files.extend(unit_files)

        # Calculate is_free_preview
        is_free_preview = _calculate_is_free_preview(
            subject_id, plan_id, overrides
        )
        manifest["subjects"][-1]["is_free_preview"] = is_free_preview

    # 3. Generate shared lesson files (if not exist)
    lesson_files = _generate_lesson_files_if_needed(plan_doc)
    files.extend(lesson_files)

    # Update manifest with calculated values
    files[0]["content"] = _to_json(manifest)

    return files
```

### Pattern 6: FastAPI Plan Service

**What:** Plan JSON serving with Redis caching
**When to use:** Mobile app fetching Plan data

```python
# Source: Existing HierarchyService pattern
class PlanService:
    """Cache plan manifests for fast mobile app access."""

    CACHE_TTL = 3600  # 1 hour

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _cache_key(self, plan_id: str) -> str:
        return f"{self.prefix}plan:{plan_id}:manifest"

    async def get_plan_manifest(self, plan_id: str) -> Optional[PlanManifest]:
        """Get plan manifest from cache or storage."""
        key = self._cache_key(plan_id)

        # Try cache first
        cached = await self.redis.get(key)
        if cached:
            data = cached.decode() if isinstance(cached, bytes) else cached
            return PlanManifest.model_validate_json(data)

        # Cache miss - read from CDN storage
        storage_path = f"plans/{plan_id}/manifest.json"
        content = await self._read_from_storage(storage_path)

        if not content:
            return None

        manifest = PlanManifest.model_validate_json(content)

        # Cache with TTL
        await self.redis.set(key, content, ex=self.CACHE_TTL)

        return manifest

    async def invalidate(self, plan_id: str) -> None:
        """Invalidate plan cache."""
        key = self._cache_key(plan_id)
        await self.redis.delete(key)
```

### Anti-Patterns to Avoid

- **Storing is_free_preview in database:** This is derived from subject's free units/topics. Always calculate at build time, not store.
- **Duplicating lesson files per plan:** Lessons are shared at root `/lessons/` level. Never duplicate content per plan.
- **Using frappe.db.set_value for Plan updates:** This bypasses doc_events hooks. Always use doc.save() to trigger build queue.
- **Filtering Major without clearing on Grade change:** When Grade changes, clear the Major field to prevent invalid combinations.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic file writes | Custom temp file logic | LocalStorageBackend.upload() | Handles fsync, atomic rename, cleanup |
| Debounce logic | Custom timer/scheduler | Redis SET NX EX pattern | Already proven in build_trigger.py |
| Cache invalidation | Custom notification | Redis pub/sub + existing pubsub.py | Already wired up for hierarchy |
| Build queue processing | Custom job queue | Memora Build Queue DocType | Already has retry logic, status tracking |
| Link field filtering | Custom SQL query | frm.set_query() + @frappe.whitelist | Standard Frappe pattern |

**Key insight:** Phase 6 built comprehensive build infrastructure. This phase extends it, not replaces it.

## Common Pitfalls

### Pitfall 1: Hook Order for Child Table Changes

**What goes wrong:** Child table modifications don't trigger parent's on_update hook as expected
**Why it happens:** Frappe triggers parent doc_events when child rows are modified, but the timing can be confusing
**How to avoid:**
- Add hooks for both parent DocType AND child DocType in hooks.py
- For child tables, use `after_insert` on the child to catch new rows
- Parent `on_update` catches child modifications through parent save
**Warning signs:** Build not triggering when only child rows change

### Pitfall 2: Race Condition in Debounce Key

**What goes wrong:** Two simultaneous saves both pass the debounce check
**Why it happens:** Redis SET NX is atomic, but check-then-act patterns aren't
**How to avoid:**
- Use Redis SET NX EX in single atomic operation (already done in existing code)
- Don't add separate EXISTS check before SET
**Warning signs:** Duplicate builds for same Plan within debounce window

### Pitfall 3: Plan Override Application Order

**What goes wrong:** is_free_preview calculated before overrides applied, giving wrong result
**Why it happens:** Processing order matters - overrides can Hide units or Set Free flags
**How to avoid:**
1. Load Plan Overrides FIRST
2. Filter hidden content SECOND
3. Apply is_free overrides THIRD
4. Calculate is_free_preview LAST (after all overrides applied)
**Warning signs:** is_free_preview=true when all free units are hidden by overrides

### Pitfall 4: Major Dropdown Not Updating

**What goes wrong:** Major dropdown keeps showing old options after Grade change
**Why it happens:** Frappe caches Link field queries; set_query doesn't force refresh
**How to avoid:**
- Clear Major field value when Grade changes
- Call `frm.refresh_field("major")` after set_query
- Use server-side query method (not just filters) for complex filtering
**Warning signs:** Can select Major not belonging to current Grade

### Pitfall 5: Lesson File Regeneration

**What goes wrong:** Every Plan build regenerates all lesson files, even unchanged ones
**Why it happens:** Generator doesn't check if lesson file already exists
**How to avoid:**
- Check storage.exists() before generating lesson files
- Use version/hash comparison for changed lessons
- Lessons are shared - only generate if missing or content hash changed
**Warning signs:** Build times growing linearly with total lessons across all plans

## Code Examples

Verified patterns from codebase analysis:

### Creating Child Table DocType (from memora_plan_subject.json)
```json
{
  "doctype": "DocType",
  "istable": 1,
  "module": "Memora Admin",
  "name": "Memora Grade Major",
  "fields": [
    {
      "fieldname": "major",
      "fieldtype": "Link",
      "in_list_view": 1,
      "label": "Major",
      "options": "Memora Major",
      "reqd": 1
    }
  ]
}
```

### Adding Table Field to Parent (pattern from existing DocTypes)
```json
{
  "fieldname": "majors",
  "fieldtype": "Table",
  "label": "Majors",
  "options": "Memora Grade Major"
}
```

### Redis Cache Pattern (from HierarchyService)
```python
async def get_cached(self, key: str, fetch_fn, ttl: int = 3600):
    cached = await self.redis.get(key)
    if cached:
        return json.loads(cached.decode() if isinstance(cached, bytes) else cached)

    data = await fetch_fn()
    if data:
        await self.redis.set(key, json.dumps(data), ex=ttl)
    return data
```

### Build Trigger Pattern (from build_trigger.py)
```python
def queue_build(target_type: str, target_name: str, prefix: str):
    cache = frappe.cache
    debounce_key = f"{prefix}{target_name}"

    timestamp = str(int(time.time()))
    was_set = cache.set(debounce_key, timestamp, nx=True, ex=120)

    if was_set:
        frappe.get_doc({
            "doctype": "Memora Build Queue",
            "target_type": target_type,
            "target_name": target_name,
            "status": "Pending",
        }).insert(ignore_permissions=True)
```

### Cache Invalidation Publish (from build_worker.py)
```python
def notify_cache_invalidation(plan_id: str):
    channel = "memora:cache:invalidate"
    message = json.dumps({
        "type": "plan",  # New type for plans
        "plan_id": plan_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    frappe.cache.publish(channel, message)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Flat subject files | Plan-centric nested structure | Phase 12 | Same subject can have different visibility per plan |
| Subject-only builds | Subject + Plan builds | Phase 12 | Plan-level JSON generation |
| Direct Major link | Grade-filtered Major | Phase 12 | Enforces valid Grade-Major combinations |

**Deprecated/outdated (to remove):**
- Flat `_subjects.json` generation - replaced by plan manifest
- Flat `track_{id}.json` generation - replaced by plan-nested hierarchy
- Flat `topic_{id}.json` generation - replaced by plan-nested unit content

## Open Questions

Things that couldn't be fully resolved:

1. **Cascading Plan Builds from Subject Changes**
   - What we know: Subject content changes trigger Subject builds
   - What's unclear: Should Subject changes also trigger builds for ALL Plans containing that Subject?
   - Recommendation: Yes, implement in build_trigger.py - when Subject updates, find all Plans containing it and queue Plan builds

2. **Partial Plan Regeneration**
   - What we know: Plan builds generate all files for all subjects
   - What's unclear: Should we support regenerating only changed subjects within a plan?
   - Recommendation: Start with full Plan regeneration for simplicity; optimize later if needed

3. **Version Tracking for Plans**
   - What we know: Subjects have json_version field
   - What's unclear: Should Plans track versions independently of their subjects?
   - Recommendation: Use manifest.version timestamp (Unix epoch) for cache busting

## Sources

### Primary (HIGH confidence)
- Existing codebase analysis:
  - `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/tasks/build_worker.py`
  - `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/events/build_trigger.py`
  - `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/memora_admin/services/build/generator.py`
  - `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/services/hierarchy.py`
  - `/home/corex/aurevia-bench/apps/memora_admin/fastapi_app/core/pubsub.py`
- [Frappe Forum - Document Event Hooks](https://support.aakvatech.com/wiki/document-event-hooks-in-frappe)
- [Frappe Docs - Hooks](https://docs.frappe.io/framework/user/en/python-api/hooks)

### Secondary (MEDIUM confidence)
- [The Missing Frappe Manual - Filtering Link Fields](https://manual.buildwithhussain.com/cookbook/client-script/recipe-02/)
- [Redis.io - FastAPI with Redis](https://redis.io/tutorials/develop/python/fastapi/)

### Tertiary (LOW confidence)
- WebSearch results for Frappe form filtering patterns - verified against existing codebase

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Using existing project dependencies only
- Architecture: HIGH - Extending proven patterns from Phase 6
- Pitfalls: HIGH - Based on codebase analysis and Frappe documentation
- Code examples: HIGH - Derived from existing working code

**Research date:** 2026-02-03
**Valid until:** 2026-03-03 (30 days - stable domain)
