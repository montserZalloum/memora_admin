# Architecture

**Analysis Date:** 2026-02-01

## Pattern Overview

**Overall:** Frappe DocType-based application architecture

**Key Characteristics:**
- Modular DocType system where each data entity (Lesson, Topic, Player, etc.) is self-contained
- Separation of concerns across Python (backend logic), JSON (schema/configuration), and JavaScript (frontend handlers)
- Database-centric ORM leveraging Frappe's Document model
- No custom backend API layer; relies entirely on Frappe's REST API

## Layers

**DocType Definition Layer:**
- Purpose: Define data schemas and database structure
- Location: `memora_admin/memora_admin/doctype/[doctype_name]/[doctype_name].json`
- Contains: Field definitions, metadata, permissions, search indexing
- Depends on: Frappe DocType system
- Used by: All other layers

**Backend Logic Layer:**
- Purpose: Implement business logic and validation hooks
- Location: `memora_admin/memora_admin/doctype/[doctype_name]/[doctype_name].py`
- Contains: Document class extending `frappe.model.document.Document`
- Depends on: Frappe ORM, JSON schema
- Used by: Frappe framework to handle document lifecycle events

**Frontend Handler Layer:**
- Purpose: Manage client-side form behavior and interactions
- Location: `memora_admin/memora_admin/doctype/[doctype_name]/[doctype_name].js`
- Contains: Form refresh handlers, field change callbacks (in commented templates)
- Depends on: Frappe's form UI framework
- Used by: Frappe Desk UI

**Test Layer:**
- Purpose: Validate DocType behavior
- Location: `memora_admin/memora_admin/doctype/[doctype_name]/test_[doctype_name].py`
- Contains: Test cases using `frappe.tests.utils.FrappeTestCase`
- Depends on: Frappe testing framework
- Used by: Test runner

**Application Configuration:**
- Purpose: Register app with Frappe ecosystem
- Location: `memora_admin/hooks.py`
- Contains: Hook definitions for events, migrations, permissions
- Depends on: Frappe hook system
- Used by: Frappe initialization

## Data Flow

**Document Creation/Read/Update/Delete (CRUD):**

1. User interacts with Frappe Desk UI (frappe_admin/memora_admin/doctype/[doctype]/[doctype].js)
2. Frappe framework routes to backend REST API
3. Frappe loads Document class (memora_admin/memora_admin/doctype/[doctype]/[doctype].py)
4. Document class executes lifecycle hooks (validate, before_insert, on_update, etc.)
5. Frappe ORM persists to database using schema from .json file
6. Response sent to frontend
7. JavaScript handler updates UI (frappe.ui.form.on callback)

**State Management:**
- Stored in Frappe database (InnoDB backend)
- Document state maintained via Frappe's Document model
- No external state management system
- Child records (table fields) managed via Table fieldtype

## Key Abstractions

**DocType:**
- Purpose: Self-contained data entity with schema, logic, and handlers
- Examples: `memora_admin/memora_admin/doctype/memora_lesson/`, `memora_admin/memora_admin/doctype/memora_player_profile/`
- Pattern: Each DocType is a directory containing .py, .json, .js, and test file

**Document Model:**
- Purpose: Object representation of persisted entities
- Pattern: Each .py class extends `frappe.model.document.Document` (inherits CRUD, validation, lifecycle)

**Child Table Records:**
- Purpose: One-to-many relationships within parent documents
- Pattern: Child doctypes linked via parent using `fieldtype: "Table"` (e.g., Memora Lesson has Stages)
- Examples: `memora_lesson.stages` (type: Memora Lesson Stage), `memora_player_profile.authorized_devices` (type: Memora Player Device)

**Link Fields:**
- Purpose: Many-to-one and foreign key relationships
- Pattern: `fieldtype: "Link"` with `options: "DocTypeName"` specifies target DocType
- Examples: `memora_lesson.topic` -> Memora Topic, `memora_lesson.unit` -> Memora Unit

## Entry Points

**Frappe App Registration:**
- Location: `memora_admin/hooks.py`
- Triggers: Frappe initialization
- Responsibilities: Register app metadata, configure hooks for document events

**DocType Backend:**
- Location: `memora_admin/memora_admin/doctype/[doctype_name]/[doctype_name].py`
- Triggers: Document lifecycle events (insert, update, delete, validate)
- Responsibilities: Implement business logic, validation, computed fields

**DocType Frontend:**
- Location: `memora_admin/memora_admin/doctype/[doctype_name]/[doctype_name].js`
- Triggers: Form load, field change, save
- Responsibilities: UI behavior, field visibility, computed display values

## Error Handling

**Strategy:** Exception-based with Frappe error wrapping

**Patterns:**
- Python: Raise exceptions from Document.validate() or lifecycle methods
- Frappe catches and returns HTTP errors with message
- Frontend: Frappe framework displays error toast/notification
- Validation: Automatic field validation via `reqd: 1` in schema; custom via Python code

## Cross-Cutting Concerns

**Logging:** Not explicitly configured in app; uses Frappe's default logging (console/file)

**Validation:**
- Declarative: Field-level via `reqd: 1`, field types in .json
- Programmatic: Custom validation in `validate()` method in .py files

**Authentication:**
- Handled by Frappe framework
- DocType permissions defined in .json schema (role-based: System Manager role has full access)
- No custom auth layer in this app

**Database:**
- Engine: InnoDB (all doctypes specify `"engine": "InnoDB"` in .json)
- Naming conventions: Auto-generated (e.g., `autoname: "format:LES-{#####}"` for Memora Lesson)

---

*Architecture analysis: 2026-02-01*
