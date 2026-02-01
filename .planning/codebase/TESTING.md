# Testing Patterns

**Analysis Date:** 2026-02-01

## Test Framework

**Runner:**
- Framework: Frappe Test Suite (built on unittest)
- Config: No pytest.ini or conftest.py in codebase
- Base test class: `frappe.tests.utils.FrappeTestCase`
- Python version: 3.10+ (from `pyproject.toml`)

**Assertion Library:**
- Standard Python `unittest` assertions (inherited from `FrappeTestCase`)
- Methods: `assertEqual()`, `assertTrue()`, `assertFalse()`, `assertRaises()`, etc.

**Run Commands:**
```bash
# Frappe test runner for this app (from bench directory)
bench run-tests --app memora_admin

# Test specific doctype
bench run-tests --doctype "Memora Structure Progress"

# Test with coverage (if frappe-coverage installed)
bench run-tests --app memora_admin --coverage
```

## Test File Organization

**Location:**
- Co-located with implementation: each DocType has paired test file
- Pattern: Implementation file + test file in same directory
- Example: `memora_structure_progress.py` and `test_memora_structure_progress.py`

**Naming:**
- Pattern: `test_{doctype_name}.py`
- Example files:
  - `test_memora_structure_progress.py`
  - `test_memora_memory_state.py`
  - `test_memora_major.py`
  - `test_memora_player_subscription.py`

**Structure:**
```
memora_admin/doctype/{doctype_name}/
├── memora_{doctype_name}.py          # Implementation
├── test_memora_{doctype_name}.py     # Tests
├── memora_{doctype_name}.json        # Schema
└── memora_{doctype_name}.js          # Frontend
```

## Test Structure

**Suite Organization:**
```python
# Copyright (c) 2026, corex and Contributors
# See license.txt

# import frappe
from frappe.tests.utils import FrappeTestCase


class TestMemoraStructureProgress(FrappeTestCase):
	pass
```

**Patterns:**
- Single test class per file, named `Test{DocTypeName}`
- Inherits from `FrappeTestCase` for Frappe-specific setup/teardown
- Empty `pass` statement in current stub implementations (tests to be implemented)

**Frappe Test Setup/Teardown:**
```python
# FrappeTestCase provides:
# setUp() - Runs before each test method
# tearDown() - Runs after each test method
# setUpClass() - Runs once before all tests in class
# tearDownClass() - Runs once after all tests in class
```

## Mocking

**Framework:** Standard Python `unittest.mock`

**Patterns (Standard Frappe practice):**
```python
# Mock frappe methods
from unittest.mock import patch, MagicMock

@patch('frappe.db.get_value')
def test_get_value(self, mock_get_value):
    mock_get_value.return_value = expected_data
    result = MyClass.some_method()
    self.assertEqual(result, expected)
    mock_get_value.assert_called_once()

# Mock frappe API calls
@patch('frappe.call')
def test_api_call(self, mock_call):
    mock_call.return_value = {'status': 'success'}
```

**What to Mock:**
- External API calls (Frappe `frappe.call()`)
- Database queries (`frappe.db.get_value()`, `frappe.db.get_list()`)
- File system operations (not used in current stubs)
- Complex service dependencies

**What NOT to Mock:**
- Document lifecycle methods (validate, save, etc.) - test real implementation
- Frappe core utilities (`frappe.get_doc()`, `frappe.new_doc()`)
- Simple getter/setter methods on Document

## Fixtures and Factories

**Test Data:**
- No factory pattern currently implemented in codebase
- Standard Frappe approach (used elsewhere):
```python
def setUp(self):
    # Create test document
    self.doc = frappe.new_doc('Memora Structure Progress')
    self.doc.name = 'test-sp-1'
    self.doc.some_field = 'test_value'
    self.doc.insert()

    # Or get existing
    self.doc = frappe.get_doc('Memora Structure Progress', 'existing-name')
```

**Location:**
- Fixtures typically defined in test methods or `setUp()`
- No separate fixtures directory in current codebase
- Frappe standard: use `frappe.new_doc()` and `frappe.get_doc()`

## Coverage

**Requirements:**
- No coverage requirements enforced (no `.coveragerc` found)
- Coverage support available through Frappe bench commands
- Target: Not specified (best practice: aim for 80%+ on core business logic)

**View Coverage:**
```bash
# Run with coverage (if configured)
bench run-tests --app memora_admin --coverage

# Manual coverage with coverage.py
coverage run -m unittest discover -s memora_admin -p "test_*.py"
coverage report
coverage html  # generates htmlcov/index.html
```

## Test Types

**Unit Tests:**
- Scope: Individual Document methods and validation
- Approach: Test Document class methods in isolation
- Example (standard pattern for this codebase):
```python
class TestMemoraStructureProgress(FrappeTestCase):
    def test_structure_progress_creation(self):
        """Test creating a new structure progress record"""
        doc = frappe.new_doc('Memora Structure Progress')
        doc.player_id = 'player-123'
        doc.progress = 50
        doc.save()

        self.assertEqual(doc.progress, 50)
        self.assertTrue(frappe.db.exists('Memora Structure Progress', doc.name))
```

**Integration Tests:**
- Scope: Document interactions, hooks, workflows (not yet implemented)
- Approach: Test multiple documents together, database state
- Use Frappe's form/workflow hooks: `on_update()`, `after_insert()`, `before_save()`

**E2E Tests:**
- Framework: Cypress (mentioned in ESLint globals, not configured in this app)
- Status: Not currently used in memora_admin
- Location: Would be in `cypress/` directory if implemented

## Common Patterns

**Async Testing:**
```python
# For documents with async operations (not in current stubs)
# Frappe handles background jobs via RQ

def test_async_operation(self):
    # Trigger async job
    frappe.enqueue('memora_admin.module.async_function', arg1=value)

    # Job queue testing
    from frappe.client import get_response_data
    # Assertions on job state
```

**Error Testing:**
```python
# Test validation errors
def test_validation_error(self):
    doc = frappe.new_doc('Memora Structure Progress')
    doc.save()  # Missing required field

    # Or explicitly
    from frappe.exceptions import ValidationError
    with self.assertRaises(ValidationError):
        doc.validate()

# Test permission errors
def test_permission_denied(self):
    from frappe.permissions import PermissionError
    with self.assertRaises(PermissionError):
        frappe.get_doc('Memora Structure Progress', 'name').save()
```

**Database Testing:**
```python
# Test database state
def test_document_saved(self):
    doc = frappe.new_doc('Memora Structure Progress')
    doc.player_id = 'test'
    doc.insert()

    # Query database directly
    exists = frappe.db.exists('Memora Structure Progress', doc.name)
    self.assertTrue(exists)

    # Get from database
    retrieved = frappe.get_doc('Memora Structure Progress', doc.name)
    self.assertEqual(retrieved.player_id, 'test')
```

## Test Execution Environment

**Pre-commit Hooks:**
- Configured in `.pre-commit-config.yaml`
- Ruff linter runs on Python files
- ESLint runs on JavaScript files
- Tests can be run manually before commit

**Test Database:**
- Frappe uses a test database (configured via bench)
- `FrappeTestCase` provides isolated database transactions
- Automatic rollback after each test prevents data pollution

**Coverage Gaps:**

Current status: All 41+ DocTypes have stub test files

Files without implementation tests:
- `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/memora_admin/doctype/memora_structure_progress/test_memora_structure_progress.py` - Empty
- `/home/corex/aurevia-bench/apps/memora_admin/memora_admin/memora_admin/doctype/memora_memory_state/test_memora_memory_state.py` - Empty
- All other test files follow same pattern

Risk: Untested DocType implementations could break validation, hooks, or business logic undetected.
Priority: HIGH - Add meaningful tests for implemented business logic as features are added.

---

*Testing analysis: 2026-02-01*
