---
phase: 16-admin-device-management
plan: 02
subsystem: frappe-ui
tags: [frappe-js, child-table, device-management, admin-ui]
depends_on: ["16-01"]
provides:
  - Admin device management UI in Memora Player Profile form
affects: []
tech-stack:
  added: []
  patterns:
    - frm.add_child + refresh_field for child table updates (avoids reload_doc loops)
    - Script-level guard flag to prevent infinite sync loops
key-files:
  modified:
    - memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.js
decisions:
  - ISO 8601 last_login converted to MySQL datetime format via _parse_last_login()
  - Anti-loop flag (frm.__device_sync_in_progress) prevents infinite reload cycle
  - Remove buttons added via grid row iteration (works with read-only grids)
  - frm.add_child + refresh_field used instead of reload_doc (prevents getdoc loop)
metrics:
  duration: ~2 hours (including 5 fix iterations)
  completed: 2026-02-07
---

# Phase 16 Plan 02: Admin Device Management UI Summary

**Form-load sync from Redis, read-only device table, per-row Remove button with confirmation dialog**

## What Was Done

### Task 1: Device sync on form load + read-only child table

Modified `memora_player_profile.js` to sync devices from Redis every time an admin opens a Player Profile:

- **Sync trigger**: `onload` event calls `sync_devices_from_redis` API
- **Anti-loop guard**: Script-level `frm.__device_sync_in_progress` flag prevents infinite sync cycles
- **Child table population**: Uses `frm.add_child()` + `frm.refresh_field()` instead of `frm.reload_doc()` (avoids getdoc infinite loop)
- **Read-only enforcement**: `frm.set_df_property("authorized_devices", "read_only", 1)` prevents hand-editing
- **Error handling**: Redis errors show red msgprint (does not display stale data)

### Task 2: Per-row Remove button with confirmation dialog

Added red "Remove" button to each device row via grid row iteration:

- **Button placement**: Iterates `grid.grid_rows` and appends button to each row
- **Confirmation dialog**: `frappe.confirm("Remove {device_name}? Player will be logged out immediately.")`
- **Removal flow**: Calls `remove_device` API → green toast → re-sync table
- **Freeze UI**: `freeze: true` during removal to prevent double-clicks

## Decisions Made

1. **frm.add_child over reload_doc**: `reload_doc` triggers getdoc which re-triggers refresh, creating an infinite loop. Using `add_child` + `refresh_field` updates the UI without a server round-trip.

2. **Script-level guard flag**: `frm.__device_sync_in_progress` checked at the start of sync to prevent re-entrance. Cleared in both success and error callbacks.

3. **ISO datetime conversion**: Redis stores `last_login` in ISO 8601 format; Frappe child table expects MySQL datetime format. Added `_parse_last_login()` helper.

4. **Tasks 1 & 2 in single commit**: The sync flow and Remove button are structurally inseparable (Remove triggers re-sync).

## Deviations from Plan

- **5 fix commits after initial feat**: The initial implementation used `reload_doc` which caused infinite getdoc loops. Iterated through `onload` vs `refresh`, `reload_doc` vs `refresh_field`, and script-level vs form-level guard approaches before settling on the final pattern.

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| 9c19f50 | feat | Add device sync on form load and read-only child table |
| 151aaba | fix | Convert ISO 8601 last_login to MySQL datetime format |
| 26b2d1c | fix | Use onload instead of refresh for device sync |
| 3dec1a4 | fix | Eliminate reload_doc to fix infinite getdoc loop |
| bce1954 | fix | Use script-level guard to prevent device sync loop |
| b10e313 | fix | Use frm.add_child + refresh_field instead of reload_doc |

## UAT Result

All 6 tests passed (see 16-UAT.md):
1. Device table populates on form load
2. Device table is read-only
3. Remove button visible on each row
4. Remove button shows confirmation dialog
5. Device removal completes successfully
6. Grant Access button still works
