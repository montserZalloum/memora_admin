---
phase: 16-admin-device-management
plan: 01
subsystem: admin-api
tags: [frappe-api, redis, device-management, session-invalidation]
depends_on: []
provides:
  - Whitelisted Frappe APIs for device sync and removal
  - Fixed device_sync.py event handler
affects:
  - 16-02 (admin UI will call these APIs)
tech-stack:
  added: []
  patterns:
    - get_fastapi_redis() for all Redis operations in Frappe context
    - Redis hash field parsing for DeviceService format
key-files:
  created:
    - memora_admin/api/devices.py
  modified:
    - memora_admin/events/device_sync.py
decisions:
  - Both APIs use get_fastapi_redis() from access_sync (not frappe.cache)
  - Session invalidation via r.delete() (no Frappe prefix)
  - Redis errors caught and surfaced via frappe.throw (API) or frappe.log_error (hook)
  - device_sync.py hook kept as safety net for manual child table edits
metrics:
  duration: ~1.5 minutes
  completed: 2026-02-07
---

# Phase 16 Plan 01: Device Management APIs Summary

**Whitelisted Frappe APIs for Redis device sync and removal with session invalidation, plus device_sync.py bug fixes**

## What Was Done

### Task 1: Create whitelisted device APIs

Created `memora_admin/api/devices.py` with two whitelisted Frappe APIs:

**`sync_devices_from_redis(player_name)`**
- Fetches live device data from `memora:devices:{user_id}` Redis hash
- Parses DeviceService hash field format: `device:{id}:{attr}` where attr is name, ua, platform, last_login, fingerprint, push_token
- Maps Redis field names to child table fields (name -> device_name, ua -> user_agent, rest as-is)
- Clears and repopulates `authorized_devices` child table
- Returns list of device dicts for JS callback
- Handles bytes decoding from Redis

**`remove_device(player_name, device_id)`**
- Deletes all 6 hash fields for the specified device from Redis
- Invalidates session via `r.delete(f"memora:session:{user_id}")`
- Returns `{success: bool, device_id: str}`
- Both Redis operations wrapped in try/except with `frappe.throw` on error

### Task 2: Fix device_sync.py bugs

Fixed four critical bugs in the existing event handler:

| Bug | Before | After |
|-----|--------|-------|
| Wrong Redis namespace | `frappe.cache()` (adds site prefix) | `get_fastapi_redis()` (correct namespace) |
| Single-field hdel | `cache.hdel()` (Frappe wrapper) | `r.hdel()` (standard redis.Redis) |
| Session key prefix | `cache.delete_value(session_key)` (adds db_name) | `r.delete(session_key)` (exact key) |
| Empty devices guard | `if not doc.authorized_devices: return` (skips all-removed case) | Removed guard; uses ternary for empty set |

Additional improvement: wrapped Redis operations in try/except to prevent Redis errors from breaking the Frappe save operation.

## Decisions Made

1. **Redis client**: Both APIs and the event handler use `get_fastapi_redis()` from `access_sync.py` -- the established pattern for accessing the FastAPI Redis namespace from Frappe context.

2. **Error handling strategy**: APIs use `frappe.throw()` (surfaces error to admin UI). Event handler uses `frappe.log_error()` (logs silently, does not break save).

3. **hooks.py unchanged**: The existing `on_update` hook registration for `device_sync.on_player_profile_update` was kept as-is. It serves as a safety net for manual child table edits. The primary removal flow uses the `remove_device` API.

## Deviations from Plan

None -- plan executed exactly as written.

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| 92d701a | feat | Create whitelisted device APIs (sync + removal) |
| 25901ee | fix | Fix device_sync.py Redis namespace and session invalidation bugs |

## Next Phase Readiness

Plan 16-02 can proceed. The whitelisted APIs are ready to be called from the Frappe JS form handlers:
- `frappe.call({method: 'memora_admin.api.devices.sync_devices_from_redis', args: {player_name}})`
- `frappe.call({method: 'memora_admin.api.devices.remove_device', args: {player_name, device_id}})`
