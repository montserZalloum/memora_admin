# Quickstart: Player Plan Change

**Feature**: 028-player-plan-change

## Prerequisites

- Frappe bench running (`bench start`)
- FastAPI sidecar running on port 8002
- Redis on port 13001 (Memora dedicated instance)
- At least 2 published plans linked to active seasons in the system

## Setup

### 1. Create the DocType

```bash
# The DocType JSON will be created by the implementation
# After creating the file, run:
bench --site x.conanacademy.com migrate
```

### 2. Install Dependencies

No new dependencies required. All libraries (FastAPI, redis.asyncio, Pydantic v2, structlog) are already installed.

### 3. Restart Services

```bash
# After code changes:
bench restart                              # Frappe workers (hooks, API)
pkill -f "uvicorn fastapi_app.main:app"    # FastAPI (auto-restarts via supervisor)
sleep 3
curl http://127.0.0.1:8002/api/v1/health/live
```

## Testing the Feature

### Manual Test Flow

```bash
# 1. Get a valid JWT for a test player
TOKEN="<player JWT>"

# 2. Browse available plans
curl -s http://127.0.0.1:8002/api/v1/plans/available \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 3. Execute plan change
curl -s -X POST http://127.0.0.1:8002/api/v1/plans/change \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"new_plan_id": "PLAN-XXXXX"}' | python3 -m json.tool

# Expected: 200 with success=true, history_id, message to re-login

# 4. Verify session invalidation (old token should fail)
curl -s http://127.0.0.1:8002/api/v1/profile/hero \
  -H "Authorization: Bearer $TOKEN"
# Expected: 401 Unauthorized

# 5. Re-login and verify clean slate
# Login with player credentials → get new JWT
NEW_TOKEN="<new JWT>"
curl -s http://127.0.0.1:8002/api/v1/profile/hero \
  -H "Authorization: Bearer $NEW_TOKEN" | python3 -m json.tool
# Expected: total_xp=0, current_streak=0, total_lessons=0

# 6. Verify cooldown (attempt another change within 24h)
curl -s -X POST http://127.0.0.1:8002/api/v1/plans/change \
  -H "Authorization: Bearer $NEW_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"new_plan_id": "PLAN-YYYYY"}'
# Expected: 429 with COOLDOWN_ACTIVE error
```

### Automated Tests

```bash
# FastAPI endpoint tests (pytest)
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest tests/fastapi/test_plan_change.py -v

# Frappe API tests (bench)
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.doctype.memora_player_plan_history.test_memora_player_plan_history
```

### Verification Checklist

After a successful plan change, verify:

- [ ] Player profile shows new plan, grade, major, season
- [ ] Wallet shows zero XP, zero streak, zero lessons, zero time
- [ ] No subscriptions exist for the player
- [ ] No progress records exist for the player
- [ ] Player not on any leaderboard (alltime, daily, weekly)
- [ ] Old JWT returns 401
- [ ] History record exists with accurate snapshots
- [ ] `memora:freeze:{player_id}` key is removed
- [ ] Activity view shows empty (no stale daily XP data)
- [ ] Background sync does not overwrite zeroed data

## Key Files

| File | Purpose |
|------|---------|
| `memora_admin/memora_admin/doctype/memora_player_plan_history/` | New DocType (history record) |
| `memora_admin/api/plan_change.py` | Frappe whitelisted API (DB operations) |
| `fastapi_app/api/v1/endpoints/plan_change.py` | FastAPI endpoints (orchestration) |
| `fastapi_app/services/plan_change.py` | Redis cleanup service |
| `fastapi_app/models/plan_change.py` | Pydantic request/response models |
| `fastapi_app/core/redis_keys.py` | New key builders (freeze, cooldown) |
| `memora_admin/tasks/sync.py` | Modified — freeze check before sync |
| `fastapi_app/api/v1/endpoints/sessions.py` | Modified — freeze check before session ops |
| `fastapi_app/deps.py` | Modified — PlanChangeService dependency |
| `fastapi_app/main.py` | Modified — register plan_change router |
