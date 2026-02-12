# Phase 29: DocType Schema Foundation - Research

**Researched:** 2026-02-12
**Domain:** Frappe DocType schema modification for phone+password identity on Memora Player Profile
**Confidence:** HIGH

## Summary

This phase modifies the `Memora Player Profile` DocType to support phone+password authentication instead of the current Frappe User-linked email model. The changes are well-understood Frappe patterns with one critical non-obvious trap: the Password fieldtype uses Fernet encryption (reversible), not PBKDF2-SHA256 hashing -- so we must bypass Frappe's default `_save_passwords()` mechanism and hash manually via `update_password()`.

The current DocType uses `autoname: "field:user"` (docname = email) with a required `user` Link field to Frappe User. The target uses `autoname: "PLAYER-.#####."` (docname = `PLAYER-00001`) with a new required `mobile` Data field (unique) and a `password` Password field (hidden). The `user` field is kept temporarily (nullable, not required) for backward compatibility -- event handlers and Frappe APIs still reference `doc.user` and will break if removed prematurely.

**Primary recommendation:** Implement the schema change in two distinct steps: (1) modify the JSON schema (add fields, change autoname, relax `user` constraints), and (2) modify the Python class to add `__setup__()`, `validate()`, and `after_insert()`/`on_update()` hooks for password hashing and phone normalization. Do NOT touch event handlers or Frappe APIs in this phase -- that is Phase 32.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `frappe.utils.password.update_password()` | Frappe v15 | Hash password as PBKDF2-SHA256 in `__Auth` table | Built-in, battle-tested, auto-migrates to Argon2 when available |
| `frappe.utils.password.check_password()` | Frappe v15 | Verify password against `__Auth` hash | Only queries `encrypted=0` rows (hashed, not Fernet) |
| `passlib` | 1.7.4 (in bench env) | PBKDF2-SHA256/Argon2 hashing backend | Already installed, used by Frappe internally |
| `re` (stdlib) | Python 3.10+ | Phone number normalization regex | No external library needed for digit extraction |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `frappe.utils.password.delete_all_passwords_for()` | Frappe v15 | Clean up `__Auth` entries on player deletion | When implementing player account deletion |
| `frappe.utils.password.rename_password()` | Frappe v15 | Update `__Auth` name when docname changes | Future: if ever using `rename_doc` on players |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `update_password()` | `set_encrypted_password()` | WRONG for auth. Uses Fernet (reversible), invisible to `check_password()` |
| Regex phone validation | `python-phonenumbers` | Overkill. Target audience is Saudi/Jordan only. 9-15 digit regex is sufficient. |
| Custom hashing | `bcrypt` / `argon2-cffi` directly | Unnecessary. `passlib` (via Frappe) already handles this with auto-upgrade. |

**Installation:**
```bash
# No new dependencies. Everything is already in the Frappe bench environment.
```

## Architecture Patterns

### Recommended DocType Modification Structure
```
memora_player_profile/
  memora_player_profile.json   # Schema: add mobile, password fields; change autoname; relax user
  memora_player_profile.py     # Class: __setup__, validate(), after_insert(), on_update()
  memora_player_profile.js     # Form: update search_fields reference (minor)
```

### Pattern 1: Password Bypass Pattern (from Frappe User DocType)

**What:** Prevent Frappe's `_save_passwords()` from storing the password field as Fernet-encrypted, and instead hash it manually with `update_password()`.

**When to use:** Any custom DocType that needs password-based authentication (login verification via `check_password()`).

**Source:** Verified from Frappe source code at `/home/corex/aurevia-bench/apps/frappe/frappe/core/doctype/user/user.py:140-144` and `/home/corex/aurevia-bench/apps/frappe/frappe/model/base_document.py:1126-1146`.

**Execution order during `insert()`:**
1. `before_insert()` -- pre-processing
2. `set_new_name()` -- `self.name` is set here (e.g., `PLAYER-00001`)
3. `run_before_save_methods()` -> `validate()` -- our custom validation runs here
4. `_validate()` -> `_save_passwords()` -- SKIPPED for `password` field due to `ignore_save_passwords`
5. `db_insert()` -- document row saved to MariaDB
6. `after_insert()` -- we hash and store password in `__Auth` here
7. `run_post_save_methods()` -> `on_update()` -- also handles password hashing for updates

**Example:**
```python
# Source: Frappe User DocType pattern (user.py:140-144, 168-171, 280-282)
class MemoraPlayerProfile(Document):
    __new_password = None

    def __setup__(self):
        # CRITICAL: Prevents _save_passwords() from storing as Fernet-encrypted.
        # Without this, check_password() will NEVER find the password (it queries encrypted=0 only).
        self.flags.ignore_save_passwords = ["password"]

    def validate(self):
        # Capture raw password before it gets cleared
        if self.password and not self.is_dummy_password(self.password):
            self.__new_password = self.password
            self.password = ""

        # Phone normalization
        if self.mobile:
            self.mobile = self._normalize_mobile(self.mobile)

        # Password policy
        if self.__new_password and len(self.__new_password) < 8:
            frappe.throw("Password must be at least 8 characters", frappe.ValidationError)

    def after_insert(self):
        self._hash_password()
        self._create_player_wallet()

    def on_update(self):
        self._hash_password()

    def _hash_password(self):
        if self.__new_password:
            from frappe.utils.password import update_password
            update_password(
                self.name,  # e.g., "PLAYER-00001"
                self.__new_password,
                doctype="Memora Player Profile",
                fieldname="password",
            )
            self.__new_password = None

    @staticmethod
    def _normalize_mobile(mobile: str) -> str:
        """Strip all non-digit characters. Returns digits only."""
        import re
        cleaned = re.sub(r"[^\d]", "", mobile)
        if not (9 <= len(cleaned) <= 15):
            frappe.throw(
                "Mobile number must be 9-15 digits after removing non-digit characters",
                frappe.ValidationError,
            )
        return cleaned
```

### Pattern 2: Autoname Series Pattern

**What:** `PLAYER-.#####.` generates sequential docnames like `PLAYER-00001`, `PLAYER-00002`, etc.

**Source:** Verified from Frappe source code at `/home/corex/aurevia-bench/apps/frappe/frappe/model/naming.py:257-279`. The `#` characters in the autoname string are parsed by `make_autoname()`, and the prefix before the `#` section becomes the Series counter key in the `tabSeries` table.

**How it works:**
- Frappe's `set_name_from_naming_options()` detects `#` in autoname (line 218)
- Calls `make_autoname("PLAYER-.#####.", doc=doc)`
- Splits by `.` into parts: `["PLAYER-", "#####", ""]`
- `#####` = 5-digit zero-padded counter
- Counter stored in `tabSeries` under prefix `PLAYER-`
- First document: `PLAYER-00001`, second: `PLAYER-00002`, etc.

**Key detail:** The existing `autoname: "field:user"` means ALL current Player Profiles have email-based docnames (e.g., `ahmed@example.com`). Changing to `PLAYER-.#####.` only affects NEW documents. Existing documents keep their old names. This is safe for backward compatibility but means the database will have mixed naming until a migration script renames old profiles.

### Pattern 3: Unique Constraint on Data Field

**What:** Setting `"unique": 1` on the `mobile` field in the DocType JSON creates a UNIQUE index in MariaDB.

**Source:** Frappe DocType field properties -- `unique: 1` translates to `ALTER TABLE ... ADD UNIQUE INDEX`.

**What Frappe does on `bench migrate`:**
1. Reads the updated JSON schema
2. Compares with current MariaDB table structure
3. Adds column `mobile VARCHAR(140)` if not exists
4. Creates unique index `unique_mobile` on the column
5. If duplicate values exist, migration FAILS -- must ensure no duplicates before migrating

**For this phase:** Since no existing records have `mobile` values yet (all NULL), the unique constraint will be created cleanly. The `reqd: 1` flag is a concern for existing records -- see Pitfalls section.

### Anti-Patterns to Avoid
- **Using Password fieldtype naively for auth:** Frappe's `_save_passwords()` stores Password fields as Fernet-encrypted (`encrypted=1`). `check_password()` only queries `encrypted=0`. If you skip the `ignore_save_passwords` bypass, logins will ALWAYS fail.
- **Passing phone number to `check_password()`:** The first argument to `check_password()` must be the document `name` (e.g., `PLAYER-00001`), NOT the phone number. The `__Auth` table key is `(doctype, name, fieldname)`.
- **Removing `user` field in this phase:** Event handlers (`access_sync.py`, `device_sync.py`, `plan_change_sync.py`, `profile_sync.py`) reference `doc.user`. Removing the field now causes silent failures in background event processing. Keep `user` field until Phase 32.
- **Setting `mobile` as `reqd: 1` in JSON without data migration:** Existing records have no `mobile` value. If `reqd: 1`, `bench migrate` may fail or block saves of existing records. Option: set `reqd: 0` initially, then enforce via `validate()` for new records, then set `reqd: 1` after all records are populated.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Password hashing | Custom PBKDF2/bcrypt | `frappe.utils.password.update_password()` | Handles INSERT ON DUPLICATE KEY UPDATE, correct `encrypted=0` flag, auto-upgrade to Argon2 |
| Password verification | Custom hash comparison | `frappe.utils.password.check_password()` | Returns canonical name, deletes failed login tracker cache, triggers auto-rehash |
| Sequential naming | Custom counter logic | `autoname: "PLAYER-.#####."` | Frappe's `tabSeries` handles concurrent inserts with row-level locking |
| Unique constraint | Application-level check-then-insert | `"unique": 1` in DocType JSON | Database-level UNIQUE index prevents race conditions |
| Dummy password detection | Custom `if pwd == "***"` | `self.is_dummy_password(pwd)` | Built-in on Document base class, checks if all chars are `*` |

**Key insight:** Frappe's User DocType already solves this exact problem (storing hashed passwords for a custom identity). The entire pattern can be copied almost verbatim from `user.py:140-282`. The only difference is our DocType name and field names.

## Common Pitfalls

### Pitfall 1: `reqd: 1` on `mobile` Blocks Existing Records

**What goes wrong:** Setting `"reqd": 1` on the `mobile` field means every save of an existing Player Profile requires a mobile number. Existing profiles (with email-based docnames) have no mobile value. Admin edits, event handler saves, or any code that calls `doc.save()` on an existing profile will fail with "Mobile is mandatory".

**Why it happens:** Frappe enforces `reqd` fields in `_validate_mandatory()` which runs on every `save()`, not just `insert()`.

**How to avoid:** Two options:
- Option A (recommended): Set `reqd: 0` in JSON. Enforce mandatory in `validate()` only for new documents (`if self.is_new() and not self.mobile: frappe.throw(...)`). Change to `reqd: 1` after all existing profiles have mobile populated (Phase 32 migration).
- Option B: Set `reqd: 1` but populate all existing records with a placeholder mobile BEFORE running `bench migrate`.

**Warning signs:** `bench migrate` succeeds but existing profile edits fail with "Mobile: Mandatory" error.

### Pitfall 2: Password Cleared by `_save_passwords` Before `validate()` Can Capture It

**What goes wrong:** If `ignore_save_passwords` is not set in `__setup__()`, Frappe's `_save_passwords()` (which runs inside `_validate()`, AFTER `validate()`) will: (a) store the password as Fernet-encrypted, (b) replace the field value with `*****`. This means your `after_insert()` hook sees `self.password == "*****"` and `self.__new_password` is None because `_save_passwords()` already consumed the raw value.

**Why it happens:** `_validate()` runs after `validate()` in the call chain. `_save_passwords()` is inside `_validate()`. If `ignore_save_passwords` is not set, the raw password is consumed before `after_insert()` runs.

**How to avoid:** Always set `self.flags.ignore_save_passwords = ["password"]` in `__setup__()`. This is the exact pattern from Frappe's User DocType (`user.py:144`).

**Warning signs:** Password field shows `*****` in `__Auth` table with `encrypted=1` instead of `$pbkdf2-sha256$...` with `encrypted=0`.

### Pitfall 3: `__setup__()` vs `__init__()`

**What goes wrong:** Using `__init__()` instead of `__setup__()` for setting `flags.ignore_save_passwords`. If `__init__()` does not call `super().__init__()` correctly, or if it runs at the wrong time, the flag may not be set when `_save_passwords()` checks it.

**Why it happens:** `__setup__()` is Frappe's designated method for per-instance setup that runs during Document initialization. It is called by the Document base class `__init__()`. Using `__init__()` requires careful `super()` management.

**How to avoid:** Use `__setup__()`, not `__init__()`. This is the documented Frappe pattern.

### Pitfall 4: `search_fields` Still References `user`

**What goes wrong:** The current DocType JSON has `"search_fields": "display_name, user"`. After making `user` nullable/optional, searches will still work but the field is becoming irrelevant. More critically, if `user` is removed in a future phase, the search_fields reference will cause Frappe errors.

**How to avoid:** Update `search_fields` to `"display_name, mobile"` in this phase.

### Pitfall 5: `on_update()` Fires on Every Save, Not Just Password Changes

**What goes wrong:** `on_update()` calls `_hash_password()` on every save. If `__new_password` is None (no password change), the `if self.__new_password:` guard prevents unnecessary work. But if the guard is missing, you'd re-hash on every save.

**How to avoid:** Always check `if self.__new_password:` before calling `update_password()`. The pattern from User DocType handles this correctly.

### Pitfall 6: `bench migrate` Order of Operations

**What goes wrong:** `bench migrate` applies JSON schema changes AND runs patches in a specific order. If you modify the JSON to add `mobile` field but also need to populate existing records, the migration might fail if mandatory validation fires before the patch runs.

**How to avoid:** For this phase: set `reqd: 0` on `mobile` in JSON. Data population of existing records happens in Phase 32 (migration phase). This decouples schema changes from data migration.

## Code Examples

Verified patterns from official sources:

### DocType JSON Schema Changes
```json
// Source: Verified against current memora_player_profile.json
// Changes needed:
{
  "autoname": "PLAYER-.#####.",
  "search_fields": "display_name, mobile",
  "field_order": [
    "mobile",
    "password",
    "user",
    "display_name",
    "gender",
    "plan",
    "avatar",
    "grade",
    "major",
    "season",
    "preferred_lang",
    "notifications",
    "authorized_devices"
  ],
  "fields": [
    {
      "fieldname": "mobile",
      "fieldtype": "Data",
      "label": "Mobile",
      "unique": 1,
      "in_list_view": 1,
      "in_standard_filter": 1,
      "description": "Phone number (digits only, 9-15 digits)"
    },
    {
      "fieldname": "password",
      "fieldtype": "Password",
      "label": "Password",
      "hidden": 1
    },
    {
      "fieldname": "user",
      "fieldtype": "Link",
      "label": "User",
      "options": "User",
      "unique": 1
    }
  ]
}
// NOTE: 'user' field changes: reqd removed, kept for backward compatibility
// NOTE: 'mobile' field: reqd NOT set in JSON (enforced in validate() for new docs)
```

### Full Python Class Implementation
```python
# Source: Pattern from frappe/core/doctype/user/user.py:140-282
# Verified against frappe/model/base_document.py:1126-1157

import re
import frappe
from frappe.model.document import Document

MOBILE_PATTERN = re.compile(r"^\d{9,15}$")

class MemoraPlayerProfile(Document):
    __new_password = None

    def __setup__(self):
        # Prevent Frappe's _save_passwords() from storing as Fernet-encrypted.
        # This mimics User DocType: user.py:144
        self.flags.ignore_save_passwords = ["password"]

    def validate(self):
        # 1. Capture raw password before clearing
        if self.password and not self.is_dummy_password(self.password):
            self.__new_password = self.password
            self.password = ""

        # 2. Password policy (only when password is being set)
        if self.__new_password and len(self.__new_password) < 8:
            frappe.throw(
                "Password must be at least 8 characters",
                frappe.ValidationError,
            )

        # 3. Normalize and validate phone number
        if self.mobile:
            self.mobile = self._normalize_mobile(self.mobile)

        # 4. Enforce mobile for new documents (reqd not set in JSON for migration safety)
        if self.is_new() and not self.mobile:
            frappe.throw("Mobile number is required for new players", frappe.ValidationError)

    def after_insert(self):
        self._hash_password()
        self._create_player_wallet()

    def on_update(self):
        self._hash_password()

    def _hash_password(self):
        if self.__new_password:
            from frappe.utils.password import update_password
            update_password(
                self.name,
                self.__new_password,
                doctype="Memora Player Profile",
                fieldname="password",
            )
            self.__new_password = None

    @staticmethod
    def _normalize_mobile(mobile: str) -> str:
        """Strip non-digit characters and validate length."""
        cleaned = re.sub(r"[^\d]", "", mobile)
        if not MOBILE_PATTERN.match(cleaned):
            frappe.throw(
                "Mobile number must be 9-15 digits",
                frappe.ValidationError,
            )
        return cleaned

    def _create_player_wallet(self):
        """Create a Player Wallet record for this player."""
        existing = frappe.db.get_value(
            "Memora Player Wallet", {"player": self.name}, "name"
        )
        if existing:
            return

        wallet = frappe.get_doc({
            "doctype": "Memora Player Wallet",
            "player": self.name,
            "total_xp": 0,
            "current_streak": 0,
            "dirty_flag": 0,
            "status": "Active",
            "total_lessons": 0,
            "total_time_min": 0,
        })
        wallet.insert(ignore_permissions=True)
        frappe.msgprint(f"Created wallet {wallet.name} for player {self.name}")
```

### Verifying Password Was Stored Correctly
```python
# Source: Frappe password.py, verified with live test on x.conanacademy.com
# Run in Frappe console after creating a player:

from frappe.utils.password import check_password

# This should return "PLAYER-00001" on success
result = check_password(
    "PLAYER-00001",           # document name, NOT phone number
    "TestPass123!",           # raw password
    doctype="Memora Player Profile",
    fieldname="password",
)
print(f"Verified: {result}")  # "PLAYER-00001"

# Verify __Auth table directly:
import frappe
entry = frappe.db.sql("""
    SELECT doctype, name, fieldname, encrypted, SUBSTRING(password, 1, 20) as pwd_prefix
    FROM `__Auth`
    WHERE doctype = 'Memora Player Profile' AND name = 'PLAYER-00001'
""", as_dict=True)
print(entry)
# Expected: encrypted=0, pwd_prefix starts with "$pbkdf2-sha256$"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `autoname: "field:user"` (email as docname) | `autoname: "PLAYER-.#####."` (sequential) | This phase | Decouples identity from phone number; stable docname |
| `user` Link field (required) | `user` Link field (optional, kept for compat) | This phase | Event handlers continue working; removed in Phase 32 |
| No mobile field | `mobile` Data field (unique) | This phase | Primary player identifier for login |
| No password field | `password` Password field (hidden, hashed via bypass) | This phase | Direct auth without Frappe User |

**Deprecated/outdated:**
- `field:user` autoname: Being replaced but NOT removed yet (old records keep email-based names)
- `user` Link required constraint: Relaxed to optional in this phase, removed entirely in Phase 32
- Password fieldtype default behavior (Fernet): Bypassed with `ignore_save_passwords`

## Open Questions

Things that couldn't be fully resolved:

1. **`reqd: 1` on `mobile` for existing records**
   - What we know: Setting `reqd: 1` in JSON blocks saves of existing records that lack a mobile value. Setting `reqd: 0` is safe but means the admin form does not enforce the field visually.
   - What's unclear: Whether `bench migrate` itself will fail if `reqd: 1` is set and existing records have no mobile.
   - Recommendation: Use `reqd: 0` in JSON. Enforce mandatory in `validate()` for new documents only (`self.is_new()`). This is the safest approach.

2. **`allow_rename: 1` on the DocType**
   - What we know: The current JSON has `"allow_rename": 1`. With `autoname: "PLAYER-.#####."`, renaming would change the docname and requires cascading FK updates.
   - What's unclear: Whether to disable rename to prevent accidental identity changes.
   - Recommendation: Set `"allow_rename": 0` to prevent accidental renames. If rename is needed (Phase 32 migration), it can be done programmatically with `frappe.rename_doc()`.

3. **Existing player data in production**
   - What we know: There are existing Player Profiles with email-based docnames. This phase adds fields and changes autoname but does NOT migrate existing data.
   - What's unclear: Exact number of existing players and whether any automated tests depend on the old autoname format.
   - Recommendation: This phase should include a note that existing records keep their email docnames. Data migration is Phase 32 scope.

## Sources

### Primary (HIGH confidence)
- Frappe `password.py` source: `/home/corex/aurevia-bench/apps/frappe/frappe/utils/password.py` -- `check_password()` at line 78, `update_password()` at line 117, verified `encrypted=0` query filter
- Frappe `base_document.py` source: `/home/corex/aurevia-bench/apps/frappe/frappe/model/base_document.py:1126-1157` -- `_save_passwords()` mechanism, `ignore_save_passwords` flag, `is_dummy_password()`
- Frappe `user.py` source: `/home/corex/aurevia-bench/apps/frappe/frappe/core/doctype/user/user.py:140-282` -- exact pattern for `__setup__()`, `validate()`, password capture and hashing
- Frappe `naming.py` source: `/home/corex/aurevia-bench/apps/frappe/frappe/model/naming.py:257-279` -- `make_autoname()` with `#####` pattern
- Frappe `document.py` source: `/home/corex/aurevia-bench/apps/frappe/frappe/model/document.py:261-348` -- `insert()` execution order (set_new_name -> validate -> _validate -> db_insert -> after_insert)
- Current Player Profile files: `memora_player_profile.json` and `.py` -- current schema and class implementation
- Live runtime test on `x.conanacademy.com` -- `check_password()` with custom DocType confirmed working (from milestone research)

### Secondary (MEDIUM confidence)
- Milestone research files: `.planning/research/STACK_mobile_auth.md`, `PITFALLS_mobile-auth.md`, `SUMMARY_mobile_auth.md` -- comprehensive analysis of the entire migration domain

### Tertiary (LOW confidence)
- None -- all findings verified against Frappe source code on disk

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- All APIs verified from Frappe source code and live runtime tests
- Architecture: HIGH -- Execution order traced through Frappe `document.py` source; User DocType pattern verified line by line
- Pitfalls: HIGH -- Each pitfall traced to specific source code lines in Frappe and the existing codebase
- Code examples: HIGH -- Based on verified Frappe User DocType pattern and confirmed `__Auth` table behavior

**Research date:** 2026-02-12
**Valid until:** Stable -- Frappe v15 patterns; valid for 90+ days unless Frappe major version change
