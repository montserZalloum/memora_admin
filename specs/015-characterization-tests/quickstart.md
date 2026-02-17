# Quickstart: Characterization Tests

**Feature**: 015-characterization-tests
**Date**: 2026-02-17

## Prerequisites

- Python 3.11+ with pytest 8.4.2, pytest-asyncio 0.26.0 (already installed)
- Redis running at `redis://127.0.0.1:13000` (shared Frappe instance)
- Working directory: `/home/corex/aurevia-bench/apps/memora_admin`

## What This Feature Does

Creates `fastapi_app/tests/test_findings.py` containing 6 characterization tests that document 3 known bugs by asserting their current (buggy) behavior. When a bug is fixed, its test will fail, prompting the developer to flip the assertion.

## Files Changed

| File | Change |
| ---- | ------ |
| `fastapi_app/tests/test_findings.py` | **NEW** — 3 test classes, ~6 tests |
| `pyproject.toml` | **MODIFIED** — add `characterization` marker |

## How to Run

```bash
cd /home/corex/aurevia-bench/apps/memora_admin

# Run all characterization tests
python3 -m pytest fastapi_app/tests/test_findings.py -v

# Run by marker
python3 -m pytest -m characterization -v

# Exclude from normal test runs
python3 -m pytest fastapi_app/tests/ -m "not characterization" -v

# Run full test suite (characterization tests included)
python3 -m pytest fastapi_app/tests/ -v
```

## How to Fix a Bug and Update the Test

Each test has `# BUG:` and `# FIX:` comments showing what to change:

```python
# BUG: XP resets to award amount only (hydration failure swallowed)
assert new_xp == 50  # BUG: should be 550 (500 + 50)
# FIX: When bug is fixed, change assertion to:
# assert new_xp == 550
```

## Architecture Notes

- Tests use real Redis (prefix-isolated) — same as all other FastAPI tests
- FrappeClient is mocked via `AsyncMock` — no Frappe runtime needed
- FINDING-02 (LTRIM) is tested by simulating the buffer flush logic directly in Redis, since `flush_interaction_buffer()` requires the Frappe runtime
- FINDING-04 (OTP "1111") is intentionally excluded
