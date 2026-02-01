# Coding Conventions

**Analysis Date:** 2026-02-01

## Naming Patterns

**Files:**
- Python modules: `snake_case.py` (e.g., `memora_structure_progress.py`, `test_memora_structure_progress.py`)
- JavaScript files: `snake_case.js` (e.g., `memora_structure_progress.js`)
- Test files: `test_{doctype_name}.py` paired with implementation file
- Doctypes follow kebab-case in JSON schema files, converted to PascalCase in Python classes

**Functions/Methods:**
- Python: `snake_case` for function and method names
- Use underscores to separate words (e.g., `get_field_obj`, `refresh_field`)
- Frappe callbacks follow convention: `on_update`, `on_cancel`, `on_trash`, `before_save`, `after_save`, `validate`, `before_validate`

**Variables:**
- Python: `snake_case` for local variables and instance variables
- Private methods: prefix with single underscore (e.g., `_helper_method`)
- Module-level constants: `UPPER_SNAKE_CASE` (not heavily used in codebase)

**Types/Classes:**
- Python: `PascalCase` for Document classes (e.g., `MemoraStructureProgress`, `MemoraMemoryState`)
- Derived from `frappe.model.document.Document` base class
- Always inherit from parent Document class

## Code Style

**Formatting:**
- Tool: `ruff` with `ruff-format`
- Indentation: tab characters (4-tab indent)
- Line length: 110 characters (configured in `pyproject.toml`)
- Quote style: double quotes (configured in `[tool.ruff.format]`)
- End of line: LF (Unix style, configured in `.editorconfig`)

**Linting:**
- Tool: `ruff` linter (Python)
- Tool: `eslint` (JavaScript)
- Pre-commit hooks: configured in `.pre-commit-config.yaml`
- Selected rules: `F` (pyflakes), `E` (pycodestyle), `W` (warnings), `I` (isort), `UP` (pyupgrade), `B` (flake8-bugbear), `RUF` (ruff-specific)
- Ignored rules for Python (E501 line-length, E101/E741 naming, F401/F403/F405 imports, W191 tabs)

**JavaScript/ESLint:**
- ESLint config: `.eslintrc`
- Extends: `eslint:recommended`
- Key rules disabled: `indent`, `brace-style`, `camelcase`, `quotes`, `semi` (flexible formatting for Frappe globals)
- Key rules enabled: `no-console` (warn), `space-unary-ops` (error)

## Import Organization

**Order:**
1. Standard library imports (e.g., `import frappe`)
2. Frappe framework imports (e.g., `from frappe.model.document import Document`)
3. Local application imports (commented out stubs in most files)

**Path Aliases:**
- No path aliases configured in current codebase
- Frappe uses absolute imports from application root

**Pattern Example:**
```python
# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class MemoraStructureProgress(Document):
	pass
```

## Error Handling

**Patterns:**
- Most current DocType classes are stubs (empty `pass` statements)
- Frappe provides built-in error handling through Document validation system
- Use `frappe.throw()` for validation errors (not shown in current stubs but standard Frappe pattern)
- Use `frappe.log_error()` for logging exceptions
- Exceptions bubble through document lifecycle hooks (`validate`, `before_save`, `on_update`)

## Logging

**Framework:** Frappe logger (via `frappe` module)

**Patterns:**
- Use `frappe.log_error()` for error logging
- Use `frappe.msgprint()` for user-facing messages
- Use `frappe.get_logger()` for custom logger instances
- No custom logging configuration detected; uses Frappe's defaults

## Comments

**When to Comment:**
- Copyright headers required at top of every Python and JavaScript file
- Example: `# Copyright (c) 2026, corex and contributors`
- File-level comments for purpose/license information
- Inline comments for non-obvious logic (none present in current stubs)

**JSDoc/TSDoc:**
- Not currently used in this codebase
- Frappe uses inline docstrings for method documentation
- Example pattern (not present but standard):
  ```python
  def method_name(self, arg):
      """Short description of what method does.

      Args:
          arg: parameter description

      Returns:
          description of return value
      """
  ```

## Function Design

**Size:** No enforced limits; most current DocType classes are minimal stubs

**Parameters:**
- Python methods use `self` for instance methods
- Frappe hooks use standard signatures (e.g., `refresh(frm)` for form refresh hooks)
- Keep parameter count minimal; use configuration objects for multiple options

**Return Values:**
- Python methods typically return results or None
- Frappe validators typically return nothing; errors thrown via `frappe.throw()`
- Form event handlers return nothing; side effects managed via form object mutations

## Module Design

**Exports:**
- Each DocType file exports single Document class
- File naming matches class name (converted to snake_case)
- Path: `memora_admin/doctype/{doctype_name}/{doctype_name}.py`

**Barrel Files:**
- No barrel/index files pattern in current structure
- Each doctype is independent; no aggregated exports

**Doctype Structure Example:**
```
memora_admin/doctype/memora_structure_progress/
├── __init__.py                          # Empty init file
├── memora_structure_progress.py         # Document class
├── memora_structure_progress.json       # Schema definition
├── memora_structure_progress.js         # Frontend form hooks
└── test_memora_structure_progress.py    # Unit tests
```

## Frappe-Specific Patterns

**Document Classes:**
- All data models inherit from `frappe.model.document.Document`
- JSON schema files define fields and properties (not Python code)
- JavaScript files define frontend form behavior via `frappe.ui.form.on(doctype_name, {...})`

**Globals Available:**
- ESLint config declares Frappe globals: `frappe`, `cur_frm`, `cur_list`, `cur_page`, `cur_dialog`
- Form methods: `refresh_field()`, `set_field_options()`, `hide_field()`, `unhide_field()`
- Validation functions: `validate_email()`, `validate_url()`, `validate_phone()`

---

*Convention analysis: 2026-02-01*
