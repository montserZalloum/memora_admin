# Phase 33: DocType Foundation - Research

**Researched:** 2026-02-14
**Domain:** Frappe DocType schema design for voucher management system
**Confidence:** HIGH

## Summary

Phase 33 creates all voucher-related DocType schemas, enforces field-level constraints, adds database indexes, and configures permissions -- with no business logic beyond state machine validation in Python controllers. This is a purely structural phase that establishes the foundation for all subsequent voucher phases (34-38).

The existing codebase has 32+ DocTypes following a consistent pattern: JSON schema defines fields/permissions, Python class extends `Document` with lifecycle hooks, JavaScript handles form behavior. This phase adds 6 new DocTypes (4 standalone, 2 child tables), adds custom fields to the ERPNext Customer DocType, and documents the `voucher_hmac_secret` site_config requirement.

**Primary recommendation:** Use standalone DocTypes with Link fields for Voucher Card and Voucher Allocation (not child tables) because batch sizes of 1K-10K cards would cripple Frappe's form rendering. Use `create_custom_fields()` from `frappe.custom.doctype.custom_field.custom_field` for Customer fields -- this is the established pattern already used by `corex_fotara` in this bench. Add `search_index: 1` in JSON for indexed fields, and use `after_migrate` hook for the critical `pin_hmac` index which needs a specific index name.

## Standard Stack

### Core (No New Dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Frappe v15 | 15.93.0 | DocType framework, ORM, permissions | Already installed, all existing DocTypes use it |
| MariaDB / InnoDB | Installed | Database engine for all tables | Existing engine, `"engine": "InnoDB"` in all DocType JSONs |
| ERPNext v15 | 15.93.0 | Provides Customer DocType and Sales Invoice | Already installed (verified: `bench --site x.conanacademy.com list-apps`) |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `frappe.custom.doctype.custom_field.custom_field.create_custom_fields` | Built-in | Programmatically add fields to Customer DocType | In `after_install` / `after_migrate` setup |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Standalone Voucher Card | Child table of Batch | Child tables render ALL rows in Frappe form -- unusable at 1K+ cards. Standalone with Link field uses list view with pagination. |
| `create_custom_fields()` | Fixtures JSON export | Fixtures require manual web UI creation first, then export. `create_custom_fields()` is code-defined, versioned, idempotent. Used by `corex_fotara` in this bench already. |
| `search_index: 1` in JSON | Manual SQL index via `after_migrate` | `search_index` creates a standard Frappe index. Use `after_migrate` only for specialized indexes (composite, unique) that Frappe's JSON property cannot express. |

## Architecture Patterns

### Recommended DocType Structure

```
memora_admin/memora_admin/doctype/
├── memora_voucher_batch/           # Standalone - batch container
│   ├── __init__.py
│   ├── memora_voucher_batch.py     # State machine validation (BATCH-09)
│   ├── memora_voucher_batch.json   # Schema: quantity, pin_length, face_value, status
│   ├── memora_voucher_batch.js     # Form behavior (hide fields by status)
│   └── test_memora_voucher_batch.py
├── memora_voucher_batch_grant/     # Child table - links batch to Product Grant(s)
│   ├── __init__.py
│   ├── memora_voucher_batch_grant.py
│   ├── memora_voucher_batch_grant.json  # istable: 1
│   └── test_memora_voucher_batch_grant.py
├── memora_voucher_card/            # Standalone - individual card (NOT child of batch)
│   ├── __init__.py
│   ├── memora_voucher_card.py      # State machine validation (CARD-02)
│   ├── memora_voucher_card.json    # Schema: serial_no, pin_hmac, status, redemption fields
│   ├── memora_voucher_card.js      # Hide pin_hmac, make redemption fields read-only
│   └── test_memora_voucher_card.py
├── memora_voucher_allocation/      # Standalone - allocation to library
│   ├── __init__.py
│   ├── memora_voucher_allocation.py
│   ├── memora_voucher_allocation.json  # Schema: batch, customer, type, allocation_cards child
│   ├── memora_voucher_allocation.js
│   └── test_memora_voucher_allocation.py
├── memora_voucher_allocation_card/ # Child table - junction between allocation and card
│   ├── __init__.py
│   ├── memora_voucher_allocation_card.py
│   ├── memora_voucher_allocation_card.json  # istable: 1
│   └── test_memora_voucher_allocation_card.py
└── memora_voucher_redemption_log/  # Standalone - immutable audit log
    ├── __init__.py
    ├── memora_voucher_redemption_log.py  # Empty class (no writes allowed after creation)
    ├── memora_voucher_redemption_log.json  # Strict permissions: read-only after creation
    ├── memora_voucher_redemption_log.js
    └── test_memora_voucher_redemption_log.py
```

### Pattern 1: Standalone DocType with Link (Voucher Card)

**What:** Voucher Card is a standalone DocType with a `batch` Link field to Voucher Batch, instead of being a child table.
**When to use:** When the child record count can exceed ~500 per parent.
**Why:** Frappe renders ALL child table rows in the parent form. At 5,000 cards per batch, the form would be unusable. Standalone DocType uses Frappe's paginated list view.

```json
{
  "fieldname": "batch",
  "fieldtype": "Link",
  "label": "Batch",
  "options": "Memora Voucher Batch",
  "reqd": 1,
  "in_list_view": 1,
  "in_standard_filter": 1,
  "search_index": 1
}
```

The admin views cards via the standard Frappe list view filtered by batch, not via a child table in the batch form.

### Pattern 2: Child Table for Small Collections (Batch Grant, Allocation Card)

**What:** Batch Grant (1-5 entries per batch) and Allocation Card (up to ~200 entries per allocation) are child tables.
**When to use:** When the child record count is reliably small (<500).

```json
{
  "istable": 1,
  "permissions": []
}
```

Child tables have no permissions of their own (inherited from parent) and are rendered inline in the parent form.

### Pattern 3: Immutable Audit Log (Redemption Log)

**What:** DocType with no write/delete permissions after creation.
**When to use:** For append-only audit trails.

```json
{
  "permissions": [
    {
      "create": 1,
      "read": 1,
      "report": 1,
      "role": "System Manager"
    }
  ]
}
```

Note: `write`, `delete`, and `cancel` are all absent. Only `create` and `read` are granted. This means after a log entry is created, it cannot be modified or deleted through the Frappe UI or standard API.

### Pattern 4: Custom Fields on External DocTypes

**What:** Add voucher-related fields to ERPNext's Customer DocType without modifying ERPNext source.
**When to use:** When extending a DocType owned by another app.

Verified pattern from `corex_fotara/custom/company_fields.py` in this bench:

```python
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def add_customer_voucher_fields():
    custom_fields = {
        "Customer": [
            {
                "fieldname": "voucher_settings_section",
                "fieldtype": "Section Break",
                "label": "Voucher Settings",
                "insert_after": "default_currency",
            },
            {
                "fieldname": "voucher_requires_approval",
                "fieldtype": "Check",
                "label": "Voucher Requires Approval",
                "insert_after": "voucher_settings_section",
                "description": "Allocations to this library require admin approval",
            },
            # ... more fields
        ]
    }
    create_custom_fields(custom_fields)
```

This function is idempotent and safe to call from `after_install` or `after_migrate`.

### Pattern 5: Hidden Fields (SEC-05)

**What:** Fields that exist in the database but are invisible in all Frappe Desk views.
**When to use:** For sensitive data like `pin_hmac` that must never be shown to admins.

```json
{
  "fieldname": "pin_hmac",
  "fieldtype": "Data",
  "label": "PIN HMAC",
  "hidden": 1,
  "report_hide": 1,
  "print_hide": 1,
  "search_index": 1
}
```

Existing codebase precedent: `memora_player_profile.json` has `"hidden": 1` on the `password` field.

### Pattern 6: State Machine in Python Controller

**What:** Enforce valid status transitions in the `validate()` lifecycle hook.
**When to use:** For any DocType with a status workflow.

```python
VALID_TRANSITIONS = {
    "Draft": {"Generated"},
    "Generated": {"Active", "Closed"},
    "Active": {"Closed"},
    "Closed": set(),  # Terminal
}

class MemoraVoucherBatch(Document):
    def validate(self):
        if not self.is_new() and self.has_value_changed("status"):
            old_doc = self.get_doc_before_save()
            if old_doc:
                old_status = old_doc.status
                allowed = VALID_TRANSITIONS.get(old_status, set())
                if self.status not in allowed:
                    frappe.throw(
                        f"Cannot change status from {old_status} to {self.status}. "
                        f"Allowed transitions: {allowed or 'none (terminal state)'}",
                        frappe.ValidationError,
                    )
```

Existing codebase precedent: `memora_season.py` validates `season_seq` immutability using `self.get_doc_before_save()`.

### Anti-Patterns to Avoid

- **Child table for Voucher Card:** Batch sizes of 1K-10K cards make child tables unusable in Frappe Desk forms. Use standalone DocType with Link field.
- **Modifying Customer DocType JSON directly:** Customer belongs to ERPNext. Use `create_custom_fields()` to add fields safely.
- **Storing `pin_hmac` as Password fieldtype:** Password fields in Frappe are Fernet-encrypted in the `__Auth` table, not stored in the DocType table. This makes SQL `WHERE pin_hmac = %s` impossible. Use `Data` fieldtype with `hidden: 1` instead.
- **Omitting `index_web_pages_for_search: 0` on Voucher Card:** Default is 1, which adds cards to global search index. At 10K+ cards this wastes resources. Explicitly set to 0.
- **Using `allow_rename: 1` on Voucher Card:** Renaming a card would break all references. Set to 0.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Custom fields on Customer | Manual SQL ALTER TABLE or editing Customer JSON | `create_custom_fields()` from `frappe.custom.doctype.custom_field.custom_field` | Frappe-sanctioned, idempotent, survives `bench migrate`, used by `corex_fotara` |
| Database indexes | Raw SQL in migrations | `search_index: 1` in DocType JSON for simple indexes; `after_migrate` hook for specialized (composite/unique) | Frappe manages standard indexes during migration |
| Permission system | Custom permission checking code | JSON `permissions` array with role-based CRUD flags | Frappe enforces these automatically at ORM level |
| Status field UI indicators | Custom JavaScript for status colors | `states` array in DocType JSON (or indicator mapping in JS) | Frappe renders status indicators natively |
| Naming series | Custom autoname code | `autoname` property in DocType JSON (e.g., `"VBATCH-.#####."`) | Frappe handles uniqueness and counter atomically |

## Common Pitfalls

### Pitfall 1: Password Fieldtype for pin_hmac

**What goes wrong:** Using `"fieldtype": "Password"` for `pin_hmac` causes Frappe to store the value in the `__Auth` table with Fernet encryption, NOT in the DocType table column. SQL queries like `WHERE pin_hmac = %s` find nothing because the column in `tabMemora Voucher Card` is empty.
**Why it happens:** Frappe's Password fieldtype is designed for secrets that should never be queried by value -- exactly the opposite of what we need for PIN lookup.
**How to avoid:** Use `"fieldtype": "Data"` with `"hidden": 1, "report_hide": 1, "print_hide": 1`. This stores the HMAC hash directly in the table column where it can be indexed and queried.
**Warning signs:** `pin_hmac` column in database is empty or NULL for all rows; SQL queries return no results.

### Pitfall 2: Child Table Rendering at Scale

**What goes wrong:** Making Voucher Card a child table (istable: 1) of Voucher Batch causes the batch form to load ALL 5,000+ card rows into the browser DOM. The form becomes unresponsive or crashes.
**Why it happens:** Frappe's child table rendering has no built-in pagination -- it renders all rows.
**How to avoid:** Use standalone DocType with Link field to Batch. View cards via filtered list view.
**Warning signs:** Batch form takes 30+ seconds to load; browser memory spikes to 2GB+.

### Pitfall 3: Missing Database Index on pin_hmac

**What goes wrong:** Redemption performs `SELECT ... WHERE pin_hmac = %s`. Without an index, this is a full table scan on potentially 100K+ rows. At 100K rows, lookup takes 500ms+ instead of <1ms.
**Why it happens:** `search_index: 1` in JSON creates a standard Frappe index, which is sufficient. But verify after `bench migrate` that the index actually exists.
**How to avoid:** Set `"search_index": 1` on the `pin_hmac` field in the JSON schema. Verify with `SHOW INDEX FROM tabMemora Voucher Card WHERE Column_name = 'pin_hmac'` after migration.
**Warning signs:** Redemption endpoint latency >100ms; MariaDB slow query log shows full table scan on `tabMemora Voucher Card`.

### Pitfall 4: Forgetting `allow_rename: 0` on Cards and Logs

**What goes wrong:** Admin accidentally renames a Voucher Card or Redemption Log. All foreign key references (allocation cards, redemption logs, subscription transactions) break silently.
**Why it happens:** Default `allow_rename` is 1 in Frappe.
**How to avoid:** Explicitly set `"allow_rename": 0` on Voucher Card, Voucher Allocation, and Voucher Redemption Log.
**Warning signs:** Orphaned references in child tables; "Document not found" errors.

### Pitfall 5: Redemption Log Permissions Allow Write

**What goes wrong:** The redemption log permission block includes `"write": 1`, allowing admins to modify audit records after creation.
**Why it happens:** Copy-paste from other DocType JSON schemas where write is standard.
**How to avoid:** Explicitly grant ONLY `create: 1, read: 1, report: 1, export: 1` in permissions. Omit `write`, `delete`, `cancel`, and `share`.
**Warning signs:** Audit log entries have `modified` timestamps after `creation` timestamp.

### Pitfall 6: Custom Fields Not Created After Fresh Install

**What goes wrong:** Customer DocType custom fields exist in development but not on a fresh site installation because the `create_custom_fields()` call was only in a one-time patch, not in `after_migrate`.
**Why it happens:** `after_install` runs only once at app installation. If a new field is added later, existing sites never get it.
**How to avoid:** Call `create_custom_fields()` from the `after_migrate` hook (it is idempotent -- safe to run repeatedly). Also call from `after_install` for new installations.
**Warning signs:** Custom fields visible in dev but missing in production.

## Code Examples

### DocType JSON: Voucher Batch (Key Fields)

```json
{
  "autoname": "VBATCH-.#####.",
  "module": "Memora Admin",
  "engine": "InnoDB",
  "allow_rename": 0,
  "index_web_pages_for_search": 1,
  "field_order": [
    "batch_name", "status", "quantity", "pin_length", "face_value",
    "section_grants", "batch_grants",
    "section_stats", "generated_count", "allocated_count", "redeemed_count",
    "section_notes", "notes"
  ],
  "fields": [
    {
      "fieldname": "batch_name",
      "fieldtype": "Data",
      "label": "Batch Name",
      "reqd": 1,
      "in_list_view": 1
    },
    {
      "fieldname": "status",
      "fieldtype": "Select",
      "label": "Status",
      "options": "Draft\nGenerated\nActive\nClosed",
      "default": "Draft",
      "reqd": 1,
      "in_list_view": 1,
      "in_standard_filter": 1
    },
    {
      "fieldname": "quantity",
      "fieldtype": "Int",
      "label": "Quantity",
      "reqd": 1,
      "in_list_view": 1
    },
    {
      "fieldname": "pin_length",
      "fieldtype": "Select",
      "label": "PIN Length",
      "options": "12\n14\n16",
      "default": "12",
      "reqd": 1
    },
    {
      "fieldname": "face_value",
      "fieldtype": "Currency",
      "label": "Face Value",
      "reqd": 1,
      "in_list_view": 1
    },
    {
      "fieldname": "batch_grants",
      "fieldtype": "Table",
      "label": "Product Grants",
      "options": "Memora Voucher Batch Grant",
      "reqd": 1
    }
  ]
}
```

### DocType JSON: Voucher Card (Standalone, not child)

```json
{
  "autoname": "VCH-.#####.",
  "module": "Memora Admin",
  "engine": "InnoDB",
  "allow_rename": 0,
  "index_web_pages_for_search": 0,
  "fields": [
    {
      "fieldname": "serial_no",
      "fieldtype": "Data",
      "label": "Serial No",
      "unique": 1,
      "in_list_view": 1,
      "read_only": 1
    },
    {
      "fieldname": "pin_hmac",
      "fieldtype": "Data",
      "label": "PIN HMAC",
      "hidden": 1,
      "report_hide": 1,
      "print_hide": 1,
      "search_index": 1
    },
    {
      "fieldname": "batch",
      "fieldtype": "Link",
      "label": "Batch",
      "options": "Memora Voucher Batch",
      "reqd": 1,
      "in_list_view": 1,
      "in_standard_filter": 1,
      "search_index": 1
    },
    {
      "fieldname": "status",
      "fieldtype": "Select",
      "label": "Status",
      "options": "Available\nAllocated\nRedeemed\nVoid\nExpired",
      "default": "Available",
      "reqd": 1,
      "in_list_view": 1,
      "in_standard_filter": 1
    },
    {
      "fieldname": "library",
      "fieldtype": "Link",
      "label": "Library",
      "options": "Customer",
      "in_standard_filter": 1
    },
    {
      "fieldname": "allocation",
      "fieldtype": "Link",
      "label": "Allocation",
      "options": "Memora Voucher Allocation"
    },
    {
      "fieldname": "redeemed_by",
      "fieldtype": "Link",
      "label": "Redeemed By",
      "options": "Memora Player Profile",
      "read_only": 1
    },
    {
      "fieldname": "redeemed_at",
      "fieldtype": "Datetime",
      "label": "Redeemed At",
      "read_only": 1
    },
    {
      "fieldname": "redeemed_grant",
      "fieldtype": "Link",
      "label": "Redeemed Grant",
      "options": "Memora Product Grant",
      "read_only": 1
    },
    {
      "fieldname": "subscription_transaction",
      "fieldtype": "Link",
      "label": "Subscription Transaction",
      "options": "Memora Subscription Transaction",
      "read_only": 1
    }
  ]
}
```

### Python Controller: State Machine Enforcement

```python
# Source: Verified pattern from memora_season.py (get_doc_before_save + validate)
import frappe
from frappe.model.document import Document

VALID_TRANSITIONS = {
    "Available": {"Allocated", "Void", "Expired"},
    "Allocated": {"Redeemed", "Void", "Expired", "Available"},
    "Redeemed": set(),   # Terminal
    "Void": set(),       # Terminal
    "Expired": set(),    # Terminal
}

class MemoraVoucherCard(Document):
    def validate(self):
        if not self.is_new() and self.has_value_changed("status"):
            old_doc = self.get_doc_before_save()
            if old_doc:
                old_status = old_doc.status
                allowed = VALID_TRANSITIONS.get(old_status, set())
                if self.status not in allowed:
                    frappe.throw(
                        f"Invalid card status transition: {old_status} -> {self.status}. "
                        f"Allowed: {', '.join(allowed) if allowed else 'none (terminal state)'}",
                        frappe.ValidationError,
                    )
```

### Custom Fields on Customer DocType

```python
# Source: Verified pattern from corex_fotara/custom/company_fields.py
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def add_customer_voucher_fields():
    """Add voucher settings to Customer DocType. Idempotent."""
    custom_fields = {
        "Customer": [
            {
                "fieldname": "voucher_settings_section",
                "fieldtype": "Section Break",
                "label": "Voucher Settings",
                "insert_after": "default_currency",
            },
            {
                "fieldname": "voucher_requires_approval",
                "fieldtype": "Check",
                "label": "Voucher Requires Approval",
                "insert_after": "voucher_settings_section",
                "default": "0",
                "description": "If checked, allocations to this library require admin approval before cards are activated",
            },
            {
                "fieldname": "voucher_commission_type",
                "fieldtype": "Select",
                "label": "Commission Type",
                "insert_after": "voucher_requires_approval",
                "options": "\nPercentage\nFixed Amount",
                "description": "How commission is calculated for this library",
            },
            {
                "fieldname": "voucher_commission_value",
                "fieldtype": "Data",
                "label": "Commission Value",
                "insert_after": "voucher_commission_type",
                "description": "Rate (%) or fixed amount per card. Use Data + Decimal in Python, not Float.",
            },
        ]
    }
    create_custom_fields(custom_fields)
```

### Redemption Log Permissions (Read-Only After Creation)

```json
{
  "permissions": [
    {
      "create": 1,
      "email": 1,
      "export": 1,
      "print": 1,
      "read": 1,
      "report": 1,
      "role": "System Manager"
    }
  ]
}
```

No `write`, `delete`, `cancel`, or `share` permissions. This makes the log immutable after creation at the Frappe permission level.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Child tables for large collections | Standalone DocType + Link field | Standard Frappe best practice | Prevents form rendering issues at >500 rows |
| `search_index` only for indexing | `search_index` for simple, `after_migrate` for composite | Established pattern | Composite indexes like `(batch, status)` need `after_migrate` |
| Manual custom field creation | `create_custom_fields()` utility | Available since Frappe v13+ | Idempotent, version-controlled, survives migrations |

**Deprecated/outdated:**
- Using fixtures CSV for custom fields: Works but is harder to version control and review. Code-based `create_custom_fields()` is preferred.
- Using `docstatus` workflow for voucher states: Frappe's docstatus (Draft/Submitted/Cancelled) does not map cleanly to the 5-state voucher card lifecycle. Use a Select field with Python-enforced transitions instead.

## DocType Schema Details

### Complete List of DocTypes to Create

| DocType | Type | Autoname | Key Fields | Notes |
|---------|------|----------|------------|-------|
| Memora Voucher Batch | Standalone | `VBATCH-.#####.` | batch_name, status, quantity, pin_length, face_value, batch_grants (Table) | Parent for batch grants; status enforced in Python |
| Memora Voucher Batch Grant | Child table | N/A | product_grant (Link: Memora Product Grant) | `istable: 1`, no permissions |
| Memora Voucher Card | Standalone | `VCH-.#####.` | serial_no (unique), pin_hmac (hidden, indexed), batch (Link), status, library, allocation, redemption fields | NOT a child table; `index_web_pages_for_search: 0` |
| Memora Voucher Allocation | Standalone | `VALLOC-.#####.` | batch (Link), customer/library (Link: Customer), allocation_type (Allocate/Return), quantity, allocation_cards (Table) | |
| Memora Voucher Allocation Card | Child table | N/A | voucher_card (Link: Memora Voucher Card) | `istable: 1`, no permissions |
| Memora Voucher Redemption Log | Standalone | `VRLOG-.#####.` | player, pin_masked, card, library, batch, requested_grant, status, failure_reason, ip_address, timestamp | Immutable: no write/delete permissions |

### Custom Fields on Customer

| Fieldname | Fieldtype | Label | Notes |
|-----------|-----------|-------|-------|
| voucher_settings_section | Section Break | Voucher Settings | Inserted after `default_currency` |
| voucher_requires_approval | Check | Voucher Requires Approval | Default 0 |
| voucher_commission_type | Select | Commission Type | Options: empty, Percentage, Fixed Amount |
| voucher_commission_value | Data | Commission Value | String stored, parsed as Decimal in Python (avoids float) |

### Database Indexes Required

| Table | Column(s) | Type | Method | Why |
|-------|-----------|------|--------|-----|
| `tabMemora Voucher Card` | `pin_hmac` | Simple | `search_index: 1` in JSON | O(1) lookup during redemption (CARD-03) |
| `tabMemora Voucher Card` | `batch` | Simple | `search_index: 1` in JSON | Filter cards by batch for allocation queries |
| `tabMemora Voucher Card` | `serial_no` | Unique | `unique: 1` in JSON | Frappe creates unique index automatically |
| `tabMemora Voucher Card` | `(batch, status)` | Composite | `after_migrate` hook | Allocation queries: "get N Available cards from batch X" |

### Voucher Redemption Log Fields (SEC-03)

| Fieldname | Fieldtype | Label | Required | Purpose |
|-----------|-----------|-------|----------|---------|
| player | Link (Memora Player Profile) | Player | Yes | Who attempted |
| pin_masked | Data | PIN (Masked) | Yes | Last 4 digits only (e.g., "****5678") |
| card | Link (Memora Voucher Card) | Card | No | Null for invalid PIN attempts |
| library | Link (Customer) | Library | No | From card's allocation |
| batch | Link (Memora Voucher Batch) | Batch | No | From card's batch |
| requested_grant | Link (Memora Product Grant) | Requested Grant | No | What the player tried to redeem |
| status | Select | Status | Yes | Success, Invalid PIN, Already Redeemed, Expired, Void, Rate Limited, etc. |
| failure_reason | Data | Failure Reason | No | Human-readable reason for failures |
| ip_address | Data | IP Address | No | Client IP for audit |
| timestamp | Datetime | Timestamp | Yes | When the attempt occurred |

## Integration Points

### Files to MODIFY

| File | Change | Reason |
|------|--------|--------|
| `memora_admin/hooks.py` | No changes needed in Phase 33 | No doc_events for schema-only phase; hooks added in Phase 34+ |
| `memora_admin/memora_admin/setup.py` | Add `add_customer_voucher_fields()` call in `after_migrate()` + `after_install()` | Ensure custom fields exist on Customer DocType |

### Files to CREATE

6 new DocType directories (4 standalone + 2 child tables) following existing naming convention:
- Each directory: `__init__.py`, `.json`, `.py`, `.js`, `test_.py`

### site_config.json Documentation (SEC-06)

The `voucher_hmac_secret` is NOT created by Phase 33 code. It is documented as a manual site configuration requirement. The actual HMAC usage happens in Phase 34 (generation) and Phase 36 (redemption).

Document in the phase's implementation notes:
```json
// site_config.json -- add manually before Phase 34
{
  "voucher_hmac_secret": "generate-with: python3 -c 'import secrets; print(secrets.token_hex(32))'"
}
```

## Open Questions

1. **Commission value as Data vs Currency fieldtype on Customer**
   - What we know: `Currency` fieldtype uses Python float internally; Decimal precision is critical for financial calculations (verified in PITFALLS research)
   - What's unclear: Whether Frappe's Currency field stores as DECIMAL(21,9) in MariaDB (which IS exact) but converts to float in Python (which is NOT)
   - Recommendation: Use `Data` fieldtype for commission_value, parse as `Decimal(str_value)` in Python. This ensures exact arithmetic. The Financial Integration phase (37) handles actual calculations.

2. **Voucher Card `serial_no` format**
   - What we know: Requirement says "VCH-000001" sequential format
   - What's unclear: Whether serial_no should be the same as the `name` field (autoname) or a separate field
   - Recommendation: Use `autoname: "VCH-.#####."` for the `name` field, which makes `name` serve as the serial number. Add a read-only `serial_no` field that mirrors `name` for display clarity. OR simply use `name` as the serial_no (it already IS the serial number via autoname). Going with separate `serial_no` field to maintain explicit traceability requirement (CARD-01 lists serial_no as a distinct field).

3. **Allocation `type` field values**
   - What we know: ALLOC-01 says "supporting both Allocate and Return types"
   - Recommendation: Use Select field with options "Allocate\nReturn". Allocate moves cards from Available to Allocated; Return moves from Allocated to Available.

## Sources

### Primary (HIGH confidence)
- Existing codebase: 10+ DocType JSON schemas examined for pattern consistency
- `memora_admin/memora_admin/doctype/memora_season/memora_season.py` -- state validation pattern
- `memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json` -- hidden field pattern
- `memora_admin/memora_admin/doctype/memora_plan_subject/memora_plan_subject.json` -- `search_index: 1` pattern
- `memora_admin/memora_admin/doctype/memora_grant_component/memora_grant_component.json` -- child table (`istable: 1`) pattern
- `memora_admin/memora_admin/doctype/memora_interaction_log/memora_interaction_log.json` -- audit log DocType pattern
- `memora_admin/memora_admin/setup.py` -- `after_migrate` hook pattern for schema management
- `memora_admin/hooks.py` -- `after_install`, `after_migrate` hook registration
- `corex_fotara/custom/company_fields.py` -- `create_custom_fields()` pattern (verified in this bench)
- `.planning/research/ARCHITECTURE_voucher.md` -- comprehensive schema design
- `.planning/research/STACK_voucher.md` -- HMAC and crypto stack decisions
- `.planning/research/PITFALLS_voucher.md` -- security and financial pitfalls
- `.planning/REQUIREMENTS.md` -- requirement-to-phase mapping (13 requirements for Phase 33)
- `.planning/ROADMAP.md` -- success criteria for Phase 33

### Secondary (MEDIUM confidence)
- [Frappe Custom Fields Guide](https://docs.frappe.io/framework/user/en/guides/app-development/how-to-create-custom-fields-during-app-installation) -- official docs
- [Frappe Forum: Creating custom fields in core modules](https://discuss.frappe.io/t/creating-custom-fields-in-core-modules/116427) -- community pattern
- [Frappe Forum: Best practice for custom fields](https://discuss.frappe.io/t/what-is-best-practice-to-create-custom-fields-fixtures-or-coding/135904) -- community recommendations

### Tertiary (LOW confidence)
- None -- all findings verified against codebase or official docs

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all tools are existing Frappe patterns verified in codebase
- Architecture: HIGH -- patterns extracted from 10+ existing DocTypes in this app
- Pitfalls: HIGH -- verified against existing codebase behavior and Frappe internals
- Custom fields: HIGH -- exact pattern used by `corex_fotara` in same bench

**Research date:** 2026-02-14
**Valid until:** 2026-03-14 (stable -- Frappe DocType patterns change slowly)
