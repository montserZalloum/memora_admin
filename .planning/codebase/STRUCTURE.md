# Codebase Structure

**Analysis Date:** 2026-02-01

## Directory Layout

```
memora_admin/
├── memora_admin/                          # App package directory
│   ├── __init__.py                        # Package initialization
│   ├── hooks.py                           # Frappe hooks and app configuration
│   ├── modules.txt                        # Module registration
│   ├── patches.txt                        # Data migration patches
│   ├── config/                            # Configuration module
│   │   └── __init__.py
│   ├── memora_admin/                      # Core app module
│   │   ├── __init__.py
│   │   └── doctype/                       # All DocType definitions
│   │       ├── __init__.py
│   │       ├── memora_academic_plan/
│   │       ├── memora_achievement/
│   │       ├── memora_analytics_aggregate/
│   │       ├── memora_build_queue/
│   │       ├── memora_content_report/
│   │       ├── memora_grade/
│   │       ├── memora_grant_component/
│   │       ├── memora_interaction_log/
│   │       ├── memora_lesson/
│   │       ├── memora_lesson_stage/
│   │       ├── memora_lesson_stage_settings/
│   │       ├── memora_major/
│   │       ├── memora_memory_state/
│   │       ├── memora_plan_overrider/
│   │       ├── memora_plan_subject/
│   │       ├── memora_player_device/
│   │       ├── memora_player_profile/
│   │       ├── memora_player_subscription/
│   │       ├── memora_player_wallet/
│   │       ├── memora_product_grant/
│   │       ├── memora_season/
│   │       ├── memora_settings/
│   │       ├── memora_structure_progress/
│   │       ├── memora_subject/
│   │       ├── memora_subscription_transaction/
│   │       ├── memora_sync_log/
│   │       ├── memora_topic/
│   │       ├── memora_track/
│   │       └── memora_unit/
│   ├── public/                            # Static assets
│   │   ├── css/
│   │   └── js/
│   ├── templates/                         # Jinja templates
│   │   ├── includes/
│   │   └── pages/
│   └── www/                               # Web-accessible directory
├── pyproject.toml                         # Python project metadata
├── README.md                              # Installation and setup instructions
├── license.txt                            # MIT license
├── .pre-commit-config.yaml                # Pre-commit hooks (ruff, eslint, prettier)
├── .eslintrc                              # JavaScript linting
├── .editorconfig                          # Editor configuration
└── .gitignore                             # Git ignore rules
```

## Directory Purposes

**memora_admin/memora_admin/:**
- Purpose: Python package containing core application code
- Contains: Hooks, configuration, and all DocType implementations
- Key files: `hooks.py` (app registration)

**memora_admin/memora_admin/memora_admin/:**
- Purpose: Namespace to avoid package naming conflicts (Frappe convention)
- Contains: DocType definitions organized in doctype/ subdirectory

**memora_admin/memora_admin/memora_admin/doctype/:**
- Purpose: Central registry of all data entities
- Contains: 31 DocType directories (each following [name]/ pattern)
- Pattern: Each directory contains: [name].py, [name].json, [name].js, test_[name].py, __init__.py

**memora_admin/memora_admin/public/:**
- Purpose: Static assets served to frontend
- Contains: CSS and JavaScript files (currently unused; Frappe serves from doctypes)

**memora_admin/memora_admin/templates/:**
- Purpose: Jinja2 templates for dynamic content rendering
- Contains: includes/ and pages/ subdirectories
- Usage: Web forms and custom pages (not heavily used in this DocType-centric app)

**memora_admin/memora_admin/www/:**
- Purpose: Publicly accessible web pages
- Contains: Web-based documentation or public-facing forms

## Key File Locations

**Entry Points:**
- `memora_admin/hooks.py`: Frappe app initialization and hook registration
- `memora_admin/memora_admin/doctype/[doctype]/[doctype].py`: Document class entry for backend logic

**Configuration:**
- `pyproject.toml`: Python dependencies, build system (flit), ruff/linting rules
- `.pre-commit-config.yaml`: Pre-commit hooks for code quality
- `hooks.py`: Frappe hooks (currently minimal)

**Core Logic:**
- `memora_admin/memora_admin/doctype/[doctype]/[doctype].py`: Business logic for each DocType

**Testing:**
- `memora_admin/memora_admin/doctype/[doctype]/test_[doctype].py`: Unit tests per DocType

## Naming Conventions

**Files:**
- DocType Python: `memora_[entity_name].py` (snake_case) - e.g., `memora_lesson.py`
- DocType Schema: `memora_[entity_name].json` - e.g., `memora_lesson.json`
- DocType Frontend: `memora_[entity_name].js` - e.g., `memora_lesson.js`
- Test files: `test_memora_[entity_name].py` - e.g., `test_memora_lesson.py`

**Directories:**
- DocType folders: `memora_[entity_name]/` (snake_case) - e.g., `memora_lesson/`, `memora_player_profile/`
- All follow pattern: `/memora_admin/memora_admin/doctype/[doctype_name]/`

**Classes:**
- Document classes use PascalCase with Memora prefix: `class MemoraLesson(Document):`, `class MemoraPlayerProfile(Document):`
- Test classes: `class TestMemora[EntityName](FrappeTestCase):`

**Fields:**
- JSON field names use snake_case: `lesson_title`, `base_xp`, `max_hearts`, `content_hash`
- Field labels use Title Case: "Lesson Title", "Base XP", "Max Hearts"

## Where to Add New Code

**New DocType:**
1. Create directory: `memora_admin/memora_admin/doctype/memora_[new_entity]/`
2. Create files in that directory:
   - `__init__.py` (empty)
   - `memora_[new_entity].py` (class extending Document)
   - `memora_[new_entity].json` (schema with field_order, fields, metadata)
   - `memora_[new_entity].js` (form handlers, can be empty with commented template)
   - `test_memora_[new_entity].py` (test class extending FrappeTestCase)

**Extending Existing DocType:**
- Logic changes: Edit `memora_[doctype].py` - add methods or override lifecycle hooks (validate, before_insert, on_update, etc.)
- Schema changes: Edit `memora_[doctype].json` - add fields to field_order and fields array
- UI behavior: Edit `memora_[doctype].js` - implement frappe.ui.form.on handlers
- Tests: Add cases to `test_memora_[doctype].py`

**Shared Utilities:**
- Current structure: No utils/ directory yet
- Recommendation: Create `memora_admin/memora_admin/utils/` if shared helpers needed
- Pattern: Import via `from memora_admin.utils import [function]`

**Configuration/Hooks:**
- Global app hooks: Edit `memora_admin/hooks.py`
- DocType-specific: Use hooks in JSON schema (permissions, autoname, etc.)

## Special Directories

**__pycache__/:**
- Purpose: Python bytecode cache
- Generated: Yes (automatic)
- Committed: No (.gitignore)

**memora_admin/memora_admin/doctype/__pycache__/:**
- Purpose: Bytecode cache for doctype modules
- Generated: Yes (automatic)
- Committed: No

**public/css/ and public/js/:**
- Purpose: App-wide CSS and JavaScript
- Generated: No
- Committed: Yes (tracked but currently empty)
- Usage: Can include assets served to all forms (via doctype_include_js/css in hooks)

---

*Structure analysis: 2026-02-01*
