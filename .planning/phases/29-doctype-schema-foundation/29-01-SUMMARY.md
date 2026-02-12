# Phase 29 Plan 01: DocType Schema Foundation Summary

Player Profile DocType updated with PLAYER-.#####. autoname, mobile field (unique, digits-only normalized), PBKDF2-SHA256 password hashing via __Auth table (not Fernet), and backward-compatible nullable user field.

## Tasks Completed

| # | Task | Commit | Key Files |
|---|------|--------|-----------|
| 1 | Update DocType JSON schema | 4233404 | memora_player_profile.json |
| 2 | Implement password hashing and phone normalization hooks | 381c012 | memora_player_profile.py |

## Changes Made

### Task 1: DocType JSON Schema

- Changed `autoname` from `field:user` to `PLAYER-.#####.` (sequential PLAYER-00001, PLAYER-00002, etc.)
- Set `allow_rename` to 0 (prevent accidental identity renames)
- Updated `search_fields` from `display_name, user` to `display_name, mobile`
- Added `mobile` field: Data type, unique, in_list_view, in_standard_filter, NOT reqd (existing records have no mobile)
- Added `password` field: Password type, hidden (we handle hashing manually)
- Removed `reqd: 1` from `user` field (nullable for backward compatibility)
- Updated `field_order` to put mobile, password first

### Task 2: Python Class Hooks

- `__setup__()`: Sets `flags.ignore_save_passwords = ["password"]` to prevent Frappe's Fernet encryption -- critical for check_password() to work since it queries `encrypted=0` only
- `validate()`: Captures raw password (avoiding dummy password placeholder), enforces 8-character minimum policy, normalizes phone to digits-only, enforces mobile mandatory for new docs only
- `after_insert()`: Calls `_hash_password()` then `_create_player_wallet()` (existing method preserved)
- `on_update()`: Calls `_hash_password()` (guarded by `__new_password` check)
- `_hash_password()`: Uses `frappe.utils.password.update_password()` to store PBKDF2-SHA256 hash in `__Auth` table with `encrypted=0`
- `_normalize_mobile()`: Static method, strips non-digits via regex, validates 9-15 digit length
- `_create_player_wallet()`: Unchanged from original implementation

## Verification Results

All verification tests passed:

1. **PLAYER-##### autoname**: New profile created as `PLAYER-00001` (confirmed)
2. **Phone normalization**: Input `+962-799-123456` stored as `962799123456` (digits only, confirmed)
3. **Password as PBKDF2-SHA256**: `check_password()` returned docname, `__Auth.encrypted=0` (confirmed)
4. **Wallet creation**: Auto-created `WALT-00375` for new player (confirmed)
5. **Password policy**: 5-character password rejected with "at least 8 characters" error (confirmed)
6. **Backward compatibility**: Existing email-based profile `moonzalloum19@gmail.com` loaded successfully, `doc.user` accessible (confirmed)
7. **bench migrate**: Completed without errors, schema applied to database (confirmed)

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| `__new_password` uses Python name mangling (double underscore) | Prevents subclasses or external code from accidentally accessing the raw password -- matches Frappe User.py pattern |
| Mobile not reqd in JSON schema | Existing records have no mobile value; mandatory enforcement done in Python validate() for new docs only |
| `__setup__()` not `__init__()` | Frappe calls `__setup__()` after document initialization; `__init__()` would run before flags are available |

## Performance

- **Duration**: ~2m 47s
- **Completed**: 2026-02-12

## Next Phase Readiness

Phase 30 (Frappe Auth API Bridge) can proceed. It depends on:
- `PLAYER-.#####.` autoname (delivered)
- `check_password()` working with doctype/fieldname params (verified)
- Mobile field for phone-to-docname lookup (delivered, unique constraint in place)
- Password stored as PBKDF2-SHA256 in __Auth (verified, encrypted=0)

**Note for Phase 30**: The `profile_sync.py` event hook references `doc.user` which will be `None` for new phone-based players. This will need updating in Phase 32 (Event Handler Migration) -- it logs but does not block current functionality.
