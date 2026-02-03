---
phase: 12
plan: 04
title: "Build Queue Integration for Plans"
subsystem: content-delivery
tags: [build-queue, plan-json, hooks, cache-invalidation, frappe-api]

dependency-graph:
  requires: ["12-02", "12-03"]
  provides: ["plan-build-triggers", "plan-hooks-registration", "frappe-plan-api"]
  affects: []

tech-stack:
  added: []
  patterns: ["doc_events hooks", "build queue routing", "cache invalidation pub/sub"]

key-files:
  created:
    - memora_admin/memora_admin/api/plan.py
  modified:
    - memora_admin/tasks/build_worker.py
    - memora_admin/events/build_trigger.py
    - memora_admin/hooks.py
    - memora_admin/memora_admin/api/__init__.py

decisions:
  - id: "plan-build-routing"
    title: "Route Plan builds via target_type"
    rationale: "Minimal change to existing build_worker; target_type field already exists"
  - id: "cache-invalidation-type"
    title: "Separate plan/hierarchy cache invalidation types"
    rationale: "FastAPI needs different cache keys for plans vs subjects"
  - id: "frappe-api-fallback"
    title: "Frappe API generates on-the-fly if CDN missing"
    rationale: "Ensures FastAPI can always get plan data even before first build"

metrics:
  duration: "15m"
  completed: "2026-02-03"
---

# Phase 12 Plan 04: Build Queue Integration Summary

**One-liner:** Plan JSON builds integrated with existing build queue via target_type routing, hooks registered for all Plan DocTypes, Frappe API provides fallback manifest endpoint.

## What Was Built

### 1. Build Worker Routing (build_worker.py)

Updated `_process_single_build` to route based on `target_type`:

```python
if target_type == "Memora Academic Plan":
    files = generate_plan_json(target_name)
else:
    files = generate_subject_json(target_name)
```

Updated cache invalidation to send correct message type:
- `type: "plan"` with `plan_id` for Plans
- `type: "hierarchy"` with `subject_id` for Subjects

Updated notifications to show correct entity type ("Plan" vs "Subject").

### 2. Plan Trigger Functions (build_trigger.py)

Added three new trigger functions:

| Function | Trigger | Action |
|----------|---------|--------|
| `on_plan_updated` | Plan saved | Queue Plan build |
| `on_plan_subject_changed` | Child table change | Queue parent Plan build |
| `on_plan_overrider_changed` | Override modified | Queue associated Plan build |

All functions use 2-minute debounce with Redis SET NX EX pattern (consistent with Subject builds).

### 3. Hooks Registration (hooks.py)

Registered doc_events for Plan-related DocTypes:

```python
"Memora Academic Plan": {
    "on_update": "memora_admin.events.build_trigger.on_plan_updated",
},
"Memora Plan Subject": {
    "after_insert": "memora_admin.events.build_trigger.on_plan_subject_changed",
    "on_update": "memora_admin.events.build_trigger.on_plan_subject_changed",
    "on_trash": "memora_admin.events.build_trigger.on_plan_subject_changed",
},
"Memora Plan Overrider": {
    "after_insert": "memora_admin.events.build_trigger.on_plan_overrider_changed",
    "on_update": "memora_admin.events.build_trigger.on_plan_overrider_changed",
    "on_trash": "memora_admin.events.build_trigger.on_plan_overrider_changed",
},
```

### 4. Frappe API Endpoint (api/plan.py)

Created `get_plan_manifest` endpoint for FastAPI fallback:

```python
@frappe.whitelist(allow_guest=False)
def get_plan_manifest(plan_id: str) -> dict | None:
    # 1. Try CDN file first
    # 2. Fall back to on-the-fly generation
```

## Integration Flow

```
User saves Plan
    |
    v
hooks.py triggers on_plan_updated
    |
    v
build_trigger.py queues Memora Build Queue entry
    |
    v (every 2 minutes)
build_worker.py processes pending builds
    |
    v
Detects target_type='Memora Academic Plan'
    |
    v
Calls generate_plan_json(plan_id)
    |
    v
Publishes to CDN via publisher
    |
    v
Redis pub/sub: memora:cache:invalidate {type: "plan", plan_id: ...}
    |
    v
FastAPI PlanService receives invalidation, clears cache
```

## Files Changed

| File | Change |
|------|--------|
| `memora_admin/tasks/build_worker.py` | +69/-32 lines - Plan routing, cache invalidation, notifications |
| `memora_admin/events/build_trigger.py` | +153 lines - Plan trigger functions |
| `memora_admin/hooks.py` | +14 lines - Plan doc_events |
| `memora_admin/memora_admin/api/plan.py` | +45 lines - Frappe API endpoint (new) |
| `memora_admin/memora_admin/api/__init__.py` | +1 line - Module comment |

## Deviations from Plan

None - plan executed exactly as written.

## Verification Results

1. **Build worker routing** - Verified `target_type` check routes to `generate_plan_json`
2. **Cache invalidation** - Verified message includes `type: "plan"` for Plans
3. **Trigger functions** - All three functions present and use debounce pattern
4. **Hooks registration** - All Plan DocTypes registered with correct events
5. **Frappe API** - `get_plan_manifest` function created with CDN + fallback

## Phase 12 Complete

This plan completes Phase 12 (Plan System Enhancement):

| Plan | Title | Status |
|------|-------|--------|
| 12-01 | Grade-Major Linking | Complete |
| 12-02 | Plan JSON Generation | Complete |
| 12-03 | FastAPI Plan Endpoint | Complete |
| 12-04 | Build Queue Integration | Complete |

**v1.2 Milestone Complete** - All 4 plans executed successfully.

## Commits

- `12e76ee`: feat(12-04): add Plan build routing to build_worker
- `c26a156`: feat(12-04): add Plan trigger functions to build_trigger
- `cf821b6`: feat(12-04): register Plan hooks and create Frappe API endpoint
