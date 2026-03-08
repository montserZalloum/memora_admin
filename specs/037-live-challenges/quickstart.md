# Quickstart: Live Challenges

**Feature**: `037-live-challenges` | **Date**: 2026-03-07

## Prerequisites

- Frappe bench running (`bench start`)
- FastAPI sidecar running on port 8002
- Redis on port 13001 (Memora dedicated instance)
- Site: `x.conanacademy.com`

## Files to Create/Modify

### New Frappe DocTypes (4)

```
memora_admin/memora_admin/doctype/
├── memora_live_challenge_event/
│   ├── memora_live_challenge_event.json     # Schema
│   ├── memora_live_challenge_event.py       # State machine + validation
│   └── memora_live_challenge_event.js       # Form handlers (conditional visibility)
├── memora_live_challenge_question/
│   ├── memora_live_challenge_question.json   # Child table schema
│   └── memora_live_challenge_question.py     # Minimal (child)
├── memora_live_challenge_eligible_plan/
│   ├── memora_live_challenge_eligible_plan.json  # Child table schema
│   └── memora_live_challenge_eligible_plan.py    # Minimal (child)
└── memora_live_challenge_participation/
    ├── memora_live_challenge_participation.json   # Schema
    └── memora_live_challenge_participation.py     # Minimal
```

### New Frappe API

```
memora_admin/memora_admin/api/
└── live_challenge.py          # Whitelist: get_dashboard, import_review_items
```

### New Frappe Scheduled Task

```
memora_admin/memora_admin/tasks/
└── live_challenge_transitions.py  # State transitions + post-event processing
```

### New FastAPI Files

```
fastapi_app/
├── api/v1/endpoints/
│   └── live_challenge.py      # REST endpoints + WebSocket
├── services/
│   └── live_challenge.py      # Core business logic, grading, queue
└── models/
    └── live_challenge.py      # Pydantic request/response schemas
```

### Modified Files

```
fastapi_app/core/redis_keys.py       # Add LC key builders + TTL constant
fastapi_app/api/v1/router.py         # Include live_challenge router
fastapi_app/api/deps.py              # Add LiveChallengeServiceDep
fastapi_app/main.py                  # Initialize LC service + queue task in lifespan
memora_admin/hooks.py                # Add scheduled job entry
```

## Development Order

1. **DocTypes first** — Create schemas, run `bench migrate`, verify admin panel
2. **Redis keys** — Add key builders to `redis_keys.py`
3. **Service layer** — Build `LiveChallengeService` with grading logic + queue
4. **FastAPI endpoints** — REST endpoints (join, submit, result, leaderboard)
5. **WebSocket** — Waiting room broadcast (reuse ConnectionManager patterns)
6. **Scheduled task** — State transitions + post-event processing
7. **Admin API** — Dashboard + review item import
8. **Integration** — Wire up lifespan, router, deps, hooks

## Key Commands

```bash
# After creating DocType JSON files
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com migrate

# After modifying FastAPI code
pkill -f "uvicorn fastapi_app.main:app"
# Wait for supervisor restart, then verify:
curl http://127.0.0.1:8002/api/v1/health/live

# After modifying hooks.py or tasks
bench restart

# Test endpoints
curl http://127.0.0.1:8002/api/v1/live-challenge/LC-00001 \
  -H "Authorization: Bearer <token>"
```

## Testing Strategy

- **Unit tests**: Grading logic, score calculation, rank computation (pure Python, no DB)
- **Integration tests**: Full flow with real Redis (join -> submit -> grade -> leaderboard)
- **WebSocket tests**: Extend `test_ws_broadcast.py` patterns for event-scoped broadcast
- **Load test**: Simulate 1000 concurrent submissions to verify batch queue performance
