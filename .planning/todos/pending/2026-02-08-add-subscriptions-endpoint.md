---
created: 2026-02-08T22:00
title: Add dedicated subscriptions endpoint
area: api
files:
  - fastapi_app/services/access.py
  - fastapi_app/api/v1/endpoints/auth.py
  - fastapi_app/models/auth.py
---

## Problem

Client has no way to discover user's paid subscriptions after login. Current state:
- Redis stores explicit grants (`memora:access:{user_id}`) and plan subjects (`memora:plan:{plan_id}:free_subjects`)
- JWT only carries `plan` ID, no subscription list
- Login response subscription feature was built (694b88e) then reverted (1fd6aaf)
- Only workaround: call `GET /progress/` which mixes free/plan/paid with no distinction

Client needs to know what content user has access to, and distinguish between paid grants vs plan-included content.

## Solution

Create `GET /api/v1/subscriptions` endpoint that returns:
```json
{
  "grants": ["SUB-MATH", "TRK-MATH-01"],
  "plan_subjects": ["SUB-PHYSICS"]
}
```

- Uses existing `access_service.get_player_grants()` and `access_service.get_plan_free_subjects()`
- Fresh data on every call (not stale like JWT or login response)
- Client calls once after login, and again after any purchase
- Separates paid grants from plan membership (client can distinguish)

Chosen over JWT injection (bloat, staleness) and login response (stale after purchases).
