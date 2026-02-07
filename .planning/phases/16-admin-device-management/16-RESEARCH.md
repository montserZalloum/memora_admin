# Phase 16: Admin Device Management - Research

**Researched:** 2026-02-07
**Domain:** Frappe Desk UI (form scripts, child tables), Redis sync from Frappe to FastAPI Redis
**Confidence:** HIGH

## Summary

Phase 16 adds admin-facing device visibility and removal to the Frappe Desk Player Profile form. The core infrastructure already exists: `Memora Player Device` child table (with correct fields), `DeviceService` in FastAPI (with `get_devices` and `remove_device` methods), and a `device_sync.py` event handler. However, the existing `device_sync.py` has **critical bugs** that must be fixed as part of this phase. The file uses `frappe.cache()` which prefixes keys with the site `db_name|` and pickle-serializes values, but FastAPI uses plain `redis.asyncio` with `decode_responses=True` -- meaning the two systems are writing to entirely different Redis key namespaces and data formats. The fix is to use `get_fastapi_redis()` (from `access_sync.py`) for all Redis operations that target keys shared with FastAPI.

The UI work involves: (1) a Frappe whitelisted API that reads devices from Redis and populates the child table on form load, (2) making all child table fields read-only, (3) adding a per-row "Remove" button with confirmation dialog, and (4) a whitelisted API that removes the device from Redis and invalidates the session.

**Primary recommendation:** Fix `device_sync.py` to use `get_fastapi_redis()` instead of `frappe.cache()`, rewrite the form load sync as a whitelisted API called from the JS `refresh` event, and add a separate whitelisted "remove device" API that the per-row button calls directly. Keep all child table fields read-only since Redis is source of truth.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Device display:**
- Use existing `Memora Player Device` child table fields: device_id, device_name, platform, last_login, user_agent, push_token
- All fields are **read-only** -- data comes from Redis sync only, admin cannot hand-edit
- No device count badge or summary -- admin scrolls to the table to see devices
- Empty table shown when player has no devices (standard Frappe behavior)

**Sync behavior:**
- Sync happens **on form load only** -- every time admin opens Player Profile, fetch fresh from Redis and populate child table
- **Redis always wins** -- on sync, Frappe child table is completely replaced with current Redis data
- **Silent sync** -- no "last synced" indicator, data just appears
- No manual "Refresh Devices" button -- form load always fetches latest

**Removal flow:**
- **Button per row** -- each device row has a "Remove" button
- **Confirmation dialog always** -- "Remove [device_name]? Player will be logged out immediately."
- **One at a time only** -- no bulk "Remove All" action, forces admin to be deliberate
- After removal: **green toast message** ("Device removed successfully") + row disappears from table
- Removal clears device from Redis (source of truth) and triggers immediate session invalidation

**Edge cases:**
- **Active session on removed device** -- player's session is invalidated immediately, logged out on next request
- **Device limit** -- controlled globally via `max_devices_per_player` in Memora Settings, no per-player admin override needed
- **Redis unavailable** -- show error message ("Could not fetch live device data"), do not show stale Frappe data

### Claude's Discretion
- Device table placement on Player Profile form (inline section vs separate tab)
- Exact confirmation dialog wording and styling
- How session invalidation is implemented (Redis key deletion, token blacklist, etc.)
- Error message styling when Redis is unavailable

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Frappe v15 | 15.x | Desk UI, form scripts, whitelisted APIs | Already in use, provides form events and child table handling |
| redis (sync) | 5.x | Direct Redis access from Frappe hooks | Already used in `access_sync.py` via `get_fastapi_redis()` |
| frappe.ui.form | Built-in | Client-side form manipulation (refresh, child table, buttons, dialogs) | Standard Frappe Desk API |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | 24.x | Structured logging in FastAPI services | Already in use (not needed for Frappe-side code) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `get_fastapi_redis()` in events | `frappe.cache()` | `frappe.cache()` adds `db_name|` prefix and pickle-serializes -- **incompatible with FastAPI Redis** |
| Whitelisted API for sync | `doc.load_from_db()` override | Cleaner separation; override would couple Redis into ORM layer |
| Per-row custom button | Grid button column | Per-row button via JS is more flexible and follows existing codebase patterns |

**No new installations required.** All libraries already in the project.

## Architecture Patterns

### Existing Code Map (What Already Exists)

```
memora_admin/
├── memora_admin/doctype/
│   ├── memora_player_profile/
│   │   ├── memora_player_profile.json    # Has authorized_devices Table field
│   │   ├── memora_player_profile.py      # Empty Document class (pass)
│   │   └── memora_player_profile.js      # Has "Grant Access" button, needs device sync
│   └── memora_player_device/
│       ├── memora_player_device.json      # Child table: device_id, device_name, platform, last_login, user_agent, push_token
│       └── memora_player_device.py        # Empty Document class (pass)
├── events/
│   ├── device_sync.py                     # BUG: Uses frappe.cache() instead of get_fastapi_redis()
│   └── access_sync.py                     # REFERENCE: Correct pattern using get_fastapi_redis()
├── hooks.py                               # device_sync.on_player_profile_update registered on Memora Player Profile.on_update
│
fastapi_app/
├── services/
│   ├── device.py                          # DeviceService: get_devices(), remove_device(), validate_device()
│   └── session.py                         # SessionService: invalidate_session()
└── models/
    └── device.py                          # DeviceInfo, DeviceRegistrationResult
```

### Pattern 1: Form Load Device Sync (Whitelisted API + JS refresh event)

**What:** On every form `refresh` event, call a whitelisted Python API that reads devices from Redis and populates the child table. The child table in MariaDB serves purely as a display cache -- Redis is source of truth.

**When to use:** Every time admin opens or refreshes a Player Profile form.

**Implementation approach:**

```python
# In memora_admin/api/devices.py (NEW FILE)
# Source: Codebase pattern from memora_admin/api/profile.py

import frappe
from memora_admin.events.access_sync import get_fastapi_redis

@frappe.whitelist()
def sync_devices_from_redis(player_name):
    """Fetch devices from Redis and update child table.

    Called from Player Profile form JS on refresh.
    Redis key: memora:devices:{user_id}
    """
    profile = frappe.get_doc("Memora Player Profile", player_name)
    user_id = profile.user

    r = get_fastapi_redis()
    devices_key = f"memora:devices:{user_id}"
    data = r.hgetall(devices_key)

    # Parse hash fields into device objects (same logic as DeviceService.get_devices)
    devices = {}
    for field, value in data.items():
        if isinstance(field, bytes):
            field = field.decode("utf-8")
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        parts = field.split(":")
        if len(parts) == 3 and parts[0] == "device":
            device_id, attr = parts[1], parts[2]
            if device_id not in devices:
                devices[device_id] = {"device_id": device_id}
            if attr == "name":
                devices[device_id]["device_name"] = value
            elif attr == "ua":
                devices[device_id]["user_agent"] = value
            elif attr == "last_login":
                devices[device_id]["last_login"] = value
            else:
                devices[device_id][attr] = value

    # Clear and repopulate child table
    profile.authorized_devices = []
    for d in devices.values():
        profile.append("authorized_devices", {
            "device_id": d.get("device_id", ""),
            "device_name": d.get("device_name", ""),
            "platform": d.get("platform", ""),
            "last_login": d.get("last_login"),
            "user_agent": d.get("user_agent", ""),
            "push_token": d.get("push_token", ""),
        })

    profile.save(ignore_permissions=True)
    return [d for d in devices.values()]
```

```javascript
// In memora_player_profile.js (MODIFY existing)
// Source: Frappe form API docs (Context7)

frappe.ui.form.on("Memora Player Profile", {
    refresh: function(frm) {
        if (!frm.is_new()) {
            // Sync devices from Redis on every form load
            frappe.call({
                method: "memora_admin.api.devices.sync_devices_from_redis",
                args: { player_name: frm.doc.name },
                callback: function(r) {
                    if (r.message) {
                        // Refresh the child table display
                        frm.refresh_field("authorized_devices");
                    }
                },
                error: function() {
                    frappe.msgprint({
                        title: __("Redis Error"),
                        message: __("Could not fetch live device data"),
                        indicator: "red"
                    });
                }
            });
        }
    }
});
```

### Pattern 2: Per-Row Remove Button

**What:** Add a "Remove" button to each device row in the child table that triggers a confirmation dialog and then calls a whitelisted API to remove the device from Redis + invalidate session.

**When to use:** Admin wants to kick a player off a specific device.

**Implementation approach:**

```javascript
// Source: Frappe form API docs (Context7) - custom buttons
// In memora_player_profile.js

frappe.ui.form.on("Memora Player Device", {
    form_render: function(frm, cdt, cdn) {
        // Add remove button to each row
        let row = frappe.get_doc(cdt, cdn);
        let grid_row = frm.fields_dict.authorized_devices.grid
            .grid_rows_by_docname[cdn];

        // Use wrapper to add a button
        grid_row.wrapper.find(".btn-remove-device").remove(); // prevent duplicates
        let $btn = $('<button class="btn btn-xs btn-danger btn-remove-device">')
            .text(__("Remove"))
            .on("click", function() {
                frappe.confirm(
                    __("Remove {0}? Player will be logged out immediately.", [row.device_name || row.device_id]),
                    function() {
                        // Call removal API
                        frappe.call({
                            method: "memora_admin.api.devices.remove_device",
                            args: {
                                player_name: frm.doc.name,
                                device_id: row.device_id
                            },
                            callback: function(r) {
                                if (r.message && r.message.success) {
                                    frappe.show_alert({
                                        message: __("Device removed successfully"),
                                        indicator: "green"
                                    });
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                );
            });

        grid_row.wrapper.find(".row-actions, .grid-row-check").after($btn);
    }
});
```

### Pattern 3: Device Removal API (Redis + Session Invalidation)

**What:** Whitelisted API that removes a device from Redis and invalidates the player's session.

**When to use:** Admin clicks "Remove" button on a device row.

**Implementation approach:**

```python
# In memora_admin/api/devices.py
# Source: Codebase pattern from access_sync.py + session invalidation pattern from plan_change_sync.py

@frappe.whitelist()
def remove_device(player_name, device_id):
    """Remove a device from Redis and invalidate session.

    Per CONTEXT.md:
    - Removal clears device from Redis (source of truth)
    - Session invalidated immediately
    """
    profile = frappe.get_doc("Memora Player Profile", player_name)
    user_id = profile.user

    r = get_fastapi_redis()
    devices_key = f"memora:devices:{user_id}"
    session_key = f"memora:session:{user_id}"

    # Remove device fields from Redis hash
    fields = [
        f"device:{device_id}:name",
        f"device:{device_id}:ua",
        f"device:{device_id}:platform",
        f"device:{device_id}:last_login",
        f"device:{device_id}:fingerprint",
        f"device:{device_id}:push_token",
    ]
    deleted = r.hdel(devices_key, *fields)

    # Invalidate session
    r.delete(session_key)

    return {"success": deleted > 0, "device_id": device_id}
```

### Pattern 4: Read-Only Child Table

**What:** Make all fields in the authorized_devices child table read-only so admins cannot hand-edit device data.

**When to use:** Always -- Redis is the source of truth, admin edits would be overwritten on next sync.

**Implementation approach:**

```javascript
// In memora_player_profile.js
// Source: Frappe form API (Context7) - frm.set_df_property

frappe.ui.form.on("Memora Player Profile", {
    refresh: function(frm) {
        if (!frm.is_new()) {
            // Make device table read-only
            frm.set_df_property("authorized_devices", "read_only", 1);
        }
    }
});
```

### Anti-Patterns to Avoid

- **Using `frappe.cache()` for FastAPI-shared keys:** `frappe.cache()` wraps Redis with site-specific `db_name|` prefix and pickle serialization. FastAPI uses plain Redis with `decode_responses=True`. These are incompatible. Always use `get_fastapi_redis()` for shared keys.
- **Saving child table on sync without `ignore_permissions`:** The sync API modifies the document programmatically. Without `ignore_permissions=True`, Frappe may block the save for users without explicit write permission to the DocType.
- **Relying on `on_update` hook for device removal detection:** The current approach of detecting removals in `on_update` (comparing before/after) requires the admin to first delete a child table row, then save the form. Phase 16 replaces this with a direct API call from the "Remove" button -- the button calls the removal API, which operates on Redis directly, then the form reloads (re-syncing from Redis). The `on_update` hook-based removal is no longer the primary path.
- **Empty child table early return bug:** `if not doc.authorized_devices: return` in `device_sync.py` line 20 exits early when ALL devices have been removed, which is exactly when sync should happen most.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Redis field parsing in Frappe | Custom Redis parser | Copy `DeviceService.get_devices()` logic | Same hash structure, same parsing -- keep consistent |
| Session invalidation | Custom token blacklist | `r.delete(session_key)` | SessionService uses `memora:session:{user_id}` key; deleting it invalidates all tokens for that user |
| Confirmation dialog | Custom modal | `frappe.confirm()` | Built-in Frappe confirm dialog, matches other admin patterns |
| Toast notifications | Custom alert | `frappe.show_alert()` | Standard Frappe pattern already used in existing JS |

**Key insight:** The session invalidation mechanism is simple by design -- deleting the `memora:session:{user_id}` Redis key means the next time the player's access token is refreshed (or session is validated), the `SessionService.validate_session()` call returns `(False, None)`, which triggers a 401 response. The player is effectively logged out on their next API call. Access tokens have a 15-minute expiry, so worst case the player continues for up to 15 minutes before being forced to re-authenticate.

## Common Pitfalls

### Pitfall 1: frappe.cache() vs get_fastapi_redis() -- Wrong Redis Namespace

**What goes wrong:** `frappe.cache()` adds `db_name|` prefix to all keys (e.g., `x_conanacademy_com|memora:devices:user@email.com`), while FastAPI writes to `memora:devices:user@email.com`. Admin removal deletes from the wrong namespace; device stays in FastAPI's Redis.

**Why it happens:** `frappe.cache()` is the "natural" Redis wrapper in Frappe code, and it works for Frappe-only data. But device keys are shared with FastAPI which uses plain Redis.

**How to avoid:** Always use `get_fastapi_redis()` from `access_sync.py` for any Redis key that starts with `memora:`. This returns a plain `redis.Redis` client without prefix.

**Warning signs:** Device removal appears to work in Frappe but player is NOT actually logged out. Redis CLI shows the device still exists under `memora:devices:{user_id}`.

**Existing bug:** `device_sync.py` currently uses `frappe.cache()` -- this MUST be fixed in this phase.

### Pitfall 2: frappe.cache().hdel() Signature Mismatch

**What goes wrong:** `frappe.cache().hdel(name, key)` accepts only a **single hash field** (not `*fields`). The current code passes `cache.hdel(devices_key, *fields_to_delete)` which only deletes the first field and silently ignores the rest.

**Why it happens:** Frappe's `RedisWrapper.hdel()` wraps the signature differently from standard `redis.Redis.hdel()`. It accepts `(name, key, shared=False)` -- one key at a time.

**How to avoid:** Use `get_fastapi_redis()` which returns a standard `redis.Redis` client that supports `hdel(key, *fields)`.

**Warning signs:** Partially deleted device entries in Redis (fingerprint and push_token fields remain).

### Pitfall 3: frappe.cache().delete_value() Adds Prefix to Session Key

**What goes wrong:** `cache.delete_value("memora:session:user@email.com")` deletes `db_name|memora:session:user@email.com` instead of `memora:session:user@email.com`. Session is NOT invalidated.

**Why it happens:** `delete_value()` calls `make_key()` which prepends `frappe.conf.db_name|`.

**How to avoid:** Use `r.delete(session_key)` on the `get_fastapi_redis()` connection.

**Warning signs:** Admin removes device but player continues to use the app without re-authenticating.

### Pitfall 4: Empty authorized_devices Early Return

**What goes wrong:** `device_sync.py` line 20: `if not doc.authorized_devices: return` -- when admin removes ALL devices, the list IS empty, and the function returns without doing anything.

**Why it happens:** Defensive check that's too aggressive -- it should check whether previous state HAD devices, not whether current state has them.

**How to avoid:** In the rewrite, this code path is replaced by direct API calls. The `on_update` hook becomes a no-op or backup sync rather than the primary removal mechanism.

### Pitfall 5: Child Table Read-Only vs Editable

**What goes wrong:** Admin edits device_name or platform in the child table, saves, but next form load overwrites their edits from Redis.

**Why it happens:** Redis is source of truth; Frappe child table is display-only cache. If not enforced as read-only, admin gets confused.

**How to avoid:** Set `read_only=1` on the entire `authorized_devices` table field via JS on form refresh.

### Pitfall 6: Form Dirty State After Sync

**What goes wrong:** After programmatically updating the child table from Redis, the form shows as "dirty" (unsaved changes indicator), confusing the admin.

**Why it happens:** Modifying `doc.authorized_devices` via the API marks the document as changed.

**How to avoid:** After sync, call `frm.dirty()` to check, and `frm.save()` or reset the dirty flag. Alternatively, do the sync server-side in the API call (save with `ignore_permissions`) and reload the form on the client.

## Code Examples

### Existing DeviceService.get_devices() (for reference -- Frappe API should mirror this parsing)

```python
# Source: fastapi_app/services/device.py lines 297-337
# Key: memora:devices:{user_id}
# Fields: device:{id}:name, device:{id}:ua, device:{id}:platform,
#          device:{id}:last_login, device:{id}:fingerprint, device:{id}:push_token

async def get_devices(self, user_id: str) -> list[DeviceInfo]:
    key = self._device_key(user_id)
    data = await self.redis.hgetall(key)

    devices: dict[str, dict] = {}
    for field, value in data.items():
        if isinstance(field, bytes):
            field = field.decode("utf-8")
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        parts = field.split(":")
        if len(parts) == 3 and parts[0] == "device":
            device_id, attr = parts[1], parts[2]
            if device_id not in devices:
                devices[device_id] = {"device_id": device_id}
            if attr == "name":
                devices[device_id]["device_name"] = value
            elif attr == "ua":
                devices[device_id]["user_agent"] = value
            elif attr == "last_login":
                devices[device_id]["last_login"] = value
            else:
                devices[device_id][attr] = value

    return [DeviceInfo(**d) for d in devices.values()]
```

### Existing DeviceService.remove_device() (for reference)

```python
# Source: fastapi_app/services/device.py lines 339-371
async def remove_device(self, user_id: str, device_id: str) -> bool:
    key = self._device_key(user_id)
    fields = [
        f"device:{device_id}:name",
        f"device:{device_id}:ua",
        f"device:{device_id}:platform",
        f"device:{device_id}:last_login",
        f"device:{device_id}:fingerprint",
        f"device:{device_id}:push_token",
    ]
    deleted = await self.redis.hdel(key, *fields)
    return deleted > 0
```

### Session Invalidation via Redis Key Deletion

```python
# Source: fastapi_app/services/session.py lines 65-74
# Key: memora:session:{user_id}
# Deletion invalidates ALL sessions for the user

async def invalidate_session(self, user_id: str) -> bool:
    key = f"{self.prefix}{user_id}"  # memora:session:{user_id}
    deleted = await self.redis.delete(key)
    return deleted > 0
```

### Correct Redis Connection Pattern in Frappe Events

```python
# Source: memora_admin/events/access_sync.py lines 21-32
# REFERENCE PATTERN: This is the correct way to access FastAPI Redis from Frappe

from memora_admin.events.access_sync import get_fastapi_redis

def some_frappe_handler(doc, method):
    r = get_fastapi_redis()
    # r is a standard redis.Redis client -- no prefix, no pickle
    r.hdel("memora:devices:user@email.com", "device:uuid:name", "device:uuid:ua")
    r.delete("memora:session:user@email.com")
```

## Discretion Recommendations

### Device Table Placement
**Recommendation:** Keep as inline section (not a separate tab). The child table `authorized_devices` is already defined in the Player Profile schema. An inline section below the main profile fields keeps it visible without extra navigation. Add a `Section Break` with label "Authorized Devices" above the table field for visual grouping.

### Confirmation Dialog Wording
**Recommendation:** Use Frappe's built-in `frappe.confirm()`:
```
"Remove [device_name]? Player will be logged out immediately."
```
This is concise, mentions the consequence, and matches the CONTEXT.md specification exactly.

### Session Invalidation Method
**Recommendation:** Delete the `memora:session:{user_id}` Redis key using `r.delete()` on the `get_fastapi_redis()` connection. This is the same mechanism used by `SessionService.invalidate_session()` and `plan_change_sync.py`. When the player's next API call validates the session via `SessionService.validate_session()`, it returns `(False, None)` and the player gets a 401. Access tokens expire in 15 minutes, so worst case is 15 minutes of continued access -- but the CONTEXT.md says "immediate," and this IS immediate for the next API call the player makes. The session check happens on token refresh, which is called before or at access token expiry.

### Error Message When Redis Unavailable
**Recommendation:** Use `frappe.msgprint()` with red indicator:
```javascript
frappe.msgprint({
    title: __("Error"),
    message: __("Could not fetch live device data. Redis may be unavailable."),
    indicator: "red"
});
```
Keep the child table empty per the decision "do not show stale Frappe data."

## State of the Art

| Old Approach (device_sync.py) | New Approach (Phase 16) | Why Changed |
|-------------------------------|-------------------------|-------------|
| `frappe.cache()` for Redis | `get_fastapi_redis()` | Fix namespace mismatch bug |
| `on_update` hook detects removed devices | Direct API call from "Remove" button | Cleaner UX, avoids diff detection bugs |
| Admin removes row then saves form | Admin clicks "Remove" button, API handles everything | Simpler flow, immediate feedback |
| No form-load sync | Sync on every form `refresh` event | Redis is source of truth, child table is display cache |
| `cache.delete_value(session_key)` | `r.delete(session_key)` | Fix session invalidation (wrong key prefix) |

## Open Questions

1. **Should `device_sync.py` `on_player_profile_update` be removed entirely?**
   - What we know: Phase 16 replaces the removal flow with a direct API call. The `on_update` hook was the old approach.
   - What's unclear: Whether any other code path depends on the hook (e.g., automated scripts).
   - Recommendation: Replace the hook's content with the corrected Redis logic as a safety net for any non-UI code paths that might modify the child table. Or remove it entirely and rely solely on the API. The planner should decide.

2. **Should the sync API save the child table to MariaDB or just return data?**
   - What we know: CONTEXT.md says "Redis always wins" and form load fetches fresh data. Saving to MariaDB creates a persistent cache that's overwritten every time.
   - What's unclear: Whether the child table data needs to persist in MariaDB for list views, reports, or exports.
   - Recommendation: Save to MariaDB (current approach) -- it provides persistence for Frappe reports and list views, and is automatically overwritten on next form load.

## Sources

### Primary (HIGH confidence)
- Codebase files directly reviewed:
  - `fastapi_app/services/device.py` -- DeviceService with get_devices, remove_device, validate_device
  - `fastapi_app/services/session.py` -- SessionService with invalidate_session
  - `fastapi_app/core/security.py` -- JWT token creation with family_id
  - `memora_admin/events/device_sync.py` -- Existing (buggy) device sync handler
  - `memora_admin/events/access_sync.py` -- Reference pattern for `get_fastapi_redis()`
  - `memora_admin/events/plan_change_sync.py` -- Session invalidation reference pattern
  - `memora_admin/hooks.py` -- Event registration
  - DocType JSONs for Player Profile and Player Device -- Schema verification
  - `apps/frappe/frappe/utils/redis_wrapper.py` -- Verified `frappe.cache()` adds `db_name|` prefix, single-key `hdel()`
- Context7 `/websites/frappe_io-framework-user-en` -- Frappe form events, whitelisted methods, child table manipulation, custom buttons

### Secondary (MEDIUM confidence)
- `.planning/phases/08-device-management/08-RESEARCH.md` -- Phase 8 research (device management foundation)

### Tertiary (LOW confidence)
- None -- all claims verified with primary sources

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in use, no new dependencies
- Architecture: HIGH -- patterns verified against existing codebase (access_sync.py, profile_sync.py, plan_change_sync.py)
- Pitfalls: HIGH -- bugs verified by reading `frappe/utils/redis_wrapper.py` source code directly
- Discretion recommendations: HIGH -- based on existing patterns in codebase and CONTEXT.md constraints

**Research date:** 2026-02-07
**Valid until:** 2026-03-07 (30 days -- stable domain, well-understood patterns)
