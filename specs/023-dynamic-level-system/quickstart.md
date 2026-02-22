# Quickstart: Dynamic Level System

**Feature**: 023-dynamic-level-system

## Files to Create

| File | Purpose |
|------|---------|
| `memora_admin/memora_admin/doctype/memora_level_settings/memora_level_settings.json` | Single DocType schema |
| `memora_admin/memora_admin/doctype/memora_level_settings/memora_level_settings.py` | DocType class + validation |
| `memora_admin/memora_admin/doctype/memora_level_settings/memora_level_settings.js` | Form handler (minimal) |
| `memora_admin/memora_admin/doctype/memora_level_settings/test_memora_level_settings.py` | DocType tests |
| `memora_admin/memora_admin/doctype/memora_level_settings/__init__.py` | Package marker |
| `memora_admin/memora_admin/doctype/memora_level_title/memora_level_title.json` | Child table schema |
| `memora_admin/memora_admin/doctype/memora_level_title/memora_level_title.py` | Child table class (pass) |
| `memora_admin/memora_admin/doctype/memora_level_title/__init__.py` | Package marker |
| `memora_admin/events/level_sync.py` | Frappe → Redis sync hook |
| `fastapi_app/core/level_config.py` | LevelConfig dataclass + calculate_level + get_level_config |

## Files to Modify

| File | Change |
|------|--------|
| `memora_admin/hooks.py` | Add `Memora Level Settings` to `doc_events` |
| `fastapi_app/core/constants.py` | Remove `LEVEL_THRESHOLDS`, `LEVEL_TITLES`, `calculate_level()` |
| `fastapi_app/services/profile_page.py` | Import from `level_config` instead of `constants` |
| `fastapi_app/core/pubsub.py` | Add `level_config` message type handler |
| `fastapi_app/tests/test_xp_calculation.py` | Migrate `TestLevelCalculation` tests to new signature |

## Implementation Order

1. **Create child table DocType** (`Memora Level Title`) — no dependencies
2. **Create parent Single DocType** (`Memora Level Settings`) — depends on child table
3. **Create FastAPI level_config module** — pure functions, no dependencies
4. **Create Frappe sync hook** (`level_sync.py`) — depends on DocType
5. **Register hook** in `hooks.py` — depends on sync hook
6. **Add pubsub handler** — depends on level_config module
7. **Update profile_page.py** — depends on level_config module
8. **Migrate tests** — depends on level_config module
9. **Remove old code** from constants.py — after all callers updated

## Key Patterns to Follow

### Frappe Sync Hook (reference: `catalog_sync.py`)
```python
from memora_admin.events.access_sync import get_fastapi_redis

def on_level_settings_updated(doc, method):
    r = get_fastapi_redis()
    payload = json.dumps({...})
    r.set("memora:config:levels", payload, ex=3600)
    r.publish("memora:cache:invalidate", json.dumps({"type": "level_config", ...}))
```

### FastAPI Config Read (reference: `settings.py`)
```python
async def get_level_config(redis_client) -> LevelConfig:
    cached = await redis_client.get("memora:config:levels")
    if cached:
        data = json.loads(cached if isinstance(cached, str) else cached.decode())
        return LevelConfig(a=data["a"], b=data["b"], ...)
    return DEFAULT_LEVEL_CONFIG
```

### Hooks Registration (reference: `hooks.py:145-234`)
```python
doc_events = {
    ...
    "Memora Level Settings": {
        "on_update": "memora_admin.events.level_sync.on_level_settings_updated",
    },
}
```

## Verification

After implementation, verify:
```bash
# No old references remain
grep -r "LEVEL_THRESHOLDS\|LEVEL_TITLES" fastapi_app/

# Tests pass
cd /home/corex/aurevia-bench && bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_level_settings

# FastAPI tests pass
pytest fastapi_app/tests/test_xp_calculation.py -v

# Health check
curl http://127.0.0.1:8002/api/v1/health/live
```
