# Test Contract: Characterization Tests

**Feature**: 015-characterization-tests
**Date**: 2026-02-17

## Overview

This feature produces no APIs or endpoints — it creates a single test file. The "contract" here defines the test interface: what each test class asserts, how to run them, and the marker used to identify them.

## Test Interface

### File

```
fastapi_app/tests/test_findings.py
```

### Pytest Marker

```python
# Register in pyproject.toml:
# markers = [..., "characterization: documents known bugs with current behavior assertions"]

# Usage in test file:
pytestmark = [pytest.mark.asyncio, pytest.mark.characterization]
```

### Execution

```bash
# Run only characterization tests
python3 -m pytest fastapi_app/tests/test_findings.py -v

# Run by marker
python3 -m pytest -m characterization -v

# Exclude characterization tests from normal run
python3 -m pytest fastapi_app/tests/ -m "not characterization" -v
```

## Test Classes

### TestXPHydrationFailure

| Test | Fixtures | Mock Setup | Assertion |
| ---- | -------- | ---------- | --------- |
| `test_xp_resets_on_hydration_failure` | `redis_client`, `test_prefix` | `mock_frappe.call` raises `ConnectionError` | `award_xp` returns award amount only (not old + award) |
| `test_xp_correct_when_hydrated` | `redis_client`, `test_prefix` | `mock_frappe.call` returns `{total_xp: 500}` | `award_xp` returns 500 + award amount |

### TestInteractionBufferLtrimRisk

| Test | Fixtures | Setup | Assertion |
| ---- | -------- | ----- | --------- |
| `test_partial_failure_drops_failed_item` | `redis_client` | Push 5 items, simulate items 1,3 failing | LTRIM based on `inserted` count drops item 1 without retry |
| `test_partial_failure_drops_succeeded_item` | `redis_client` | Push 5 items, simulate item 1 failing | Item at position 2 (succeeded) is trimmed along with position 0 and 1 |

### TestStatsDoubleCounting

| Test | Fixtures | Setup | Assertion |
| ---- | -------- | ----- | --------- |
| `test_concurrent_cold_start_may_double_count` | `redis_client`, `test_prefix`, `mock_frappe` | No stats hash, two simulated cold-start completions | Stats `completed` field shows higher value than expected |
| `test_warm_path_no_double_count` | `redis_client`, `test_prefix` | Stats hash pre-seeded | HINCRBY increments correctly without double-counting |
