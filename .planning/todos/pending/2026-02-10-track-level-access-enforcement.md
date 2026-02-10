---
created: 2026-02-10T08:49
title: Implement track-level access enforcement and CDN flag
area: api
files:
  - fastapi_app/api/v1/endpoints/sessions.py:156-167
  - memora_admin/api/plan_generator.py:430-438
  - fastapi_app/services/access.py
  - fastapi_app/services/hierarchy.py
---

## Problem

Two gaps in track-level access control:

### 1. Enforcement gap (backend)
The session endpoint (`sessions.py:156-167`) only checks `SUB-{subject_id}` grants. A player who purchased a single track (`TRK-{track_id}`) is denied access because `TRK-*` grants are never checked during session start. The Redis storage and hydration already support `TRK-*` keys — only the enforcement logic is missing.

Fix: After `SUB-*` check fails, look up which track the lesson belongs to (from already-loaded hierarchy), then check `TRK-{track_id}` as fallback. Deny only if both fail. May need a helper like `get_track_for_lesson(lesson_id) -> str | None` on the hierarchy model.

### 2. Client-side differentiation gap (CDN)
The `_h.json` hierarchy file does not include `is_sold_separately` on track objects. The client cannot distinguish between "buy this track" vs "buy the full subject" when showing purchase UI for locked content.

Fix: Add `is_sold_separately` flag to the track object in `plan_generator.py` (line ~430-438) when building `_h.json`. The field already exists on the `Memora Track` DocType — just needs to be serialized.

### Access check fallback chain (target behavior)
```
1. Is lesson free?              → allow (existing)
2. Has SUB-{subject_id}?        → allow (existing)
3. Has TRK-{track_id}?          → allow (NEW)
4. Neither?                     → deny
```

Plan-level access stays subject-only (by design).

## Solution

1. Add `is_sold_separately: bool` to track object in `_generate_hierarchy()` in `plan_generator.py`
2. Add `get_track_for_lesson()` helper to hierarchy service/model
3. Update session endpoint to check `TRK-{track_id}` after `SUB-{subject_id}` fails
4. Rebuild affected `_h.json` files after deploy
