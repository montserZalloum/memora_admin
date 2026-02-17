# Implementation Plan: Characterization Tests for Known Bugs

**Branch**: `015-characterization-tests` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/015-characterization-tests/spec.md`

## Summary

Create `fastapi_app/tests/test_findings.py` with 6+ characterization tests documenting 3 known bugs (FINDING-01: XP hydration failure, FINDING-02: interaction buffer LTRIM boundary, FINDING-03: stats double-counting race). Tests assert current buggy behavior and include `# BUG:`/`# FIX:` comments for easy flipping when bugs are resolved. Add `characterization` pytest marker for selective execution.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: pytest 8.4.2, pytest-asyncio 0.26.0, redis.asyncio, unittest.mock.AsyncMock
**Storage**: Redis at `redis://127.0.0.1:13000` (real, shared with Frappe — prefix isolation mandatory)
**Testing**: pytest + pytest-asyncio (auto mode), existing conftest.py fixtures
**Target Platform**: Linux server (Ubuntu 22.04)
**Project Type**: Single project — FastAPI test suite extension
**Performance Goals**: All 6 tests complete in <10 seconds total
**Constraints**: No Frappe runtime imports in FastAPI tests; FINDING-02 simulates LTRIM logic directly
**Scale/Scope**: 1 new test file (~150-200 lines), 1 line added to pyproject.toml

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Check

| Principle | Status | Notes |
| --------- | ------ | ----- |
| I. Source-of-Truth Awareness | PASS | FINDING-01 tests verify Redis state after hydration failure; FINDING-02 tests verify Redis list state after LTRIM |
| II. Atomic Operation Integrity | PASS | FINDING-03 tests the race between EXISTS + HSET/HINCRBY (non-atomic pair); tests don't decompose existing atomic ops |
| III. Edge-Case-First Design | PASS | All 3 findings ARE edge cases (hydration failure, partial flush, concurrent cold start) |
| IV. Test Isolation | PASS | Tests use existing fixtures (test_prefix, cleanup_keys), unique player IDs, no shared state |
| V. Business Flow Completeness | N/A | Characterization tests document bugs, not complete user journeys |

### Post-Design Check

| Principle | Status | Notes |
| --------- | ------ | ----- |
| I. Source-of-Truth Awareness | PASS | FINDING-01 specifically tests the dual-storage gap (MariaDB has XP, Redis doesn't, hydration fails) |
| II. Atomic Operation Integrity | PASS | FINDING-03 identifies the non-atomic EXISTS+HSET/HINCRBY pattern as the root cause |
| III. Edge-Case-First Design | PASS | 3 classes × 2 tests each = 6 tests, all edge cases |
| IV. Test Isolation | PASS | Per-test Redis prefix, mock FrappeClient, autouse cleanup |
| V. Business Flow Completeness | N/A | Bug documentation, not flow coverage |

**Excluded Scope Compliance**: No imports from voucher or library systems. Tests cover wallet, stats, and interaction buffer only.

## Project Structure

### Documentation (this feature)

```text
specs/015-characterization-tests/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: bug mechanics analysis
├── data-model.md        # Phase 1: Redis structures under test
├── quickstart.md        # Phase 1: how to run and maintain
├── contracts/
│   └── test-contract.md # Phase 1: test interface definition
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
fastapi_app/tests/
├── conftest.py              # Existing — fixtures reused
├── test_findings.py         # NEW — 3 test classes, 6+ tests
└── ...                      # Existing test files unchanged

pyproject.toml               # MODIFIED — add characterization marker
```

**Structure Decision**: Single new test file in existing `fastapi_app/tests/` directory. No new directories, no structural changes. Follows existing test file naming convention (`test_*.py`).

## Implementation Details

### Task 1: Add `characterization` pytest marker to pyproject.toml

**File**: `pyproject.toml`
**Change**: Add one line to `[tool.pytest.ini_options].markers`

```toml
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: marks tests as integration tests",
    "characterization: documents known bugs with current behavior assertions",
]
```

### Task 2: Create test_findings.py — FINDING-01 (XP Hydration Failure)

**Class**: `TestXPHydrationFailure`
**File**: `fastapi_app/tests/test_findings.py`

```python
class TestXPHydrationFailure:
    """FINDING-01: XP resets to 0 when hydration fails during completion.

    Severity: CRITICAL
    Location: fastapi_app/services/wallet.py:205-213
    Caller:   fastapi_app/api/v1/endpoints/sessions.py:301-310

    Current behavior: ensure_hydrated() catches all exceptions from
    frappe.call() and returns silently. The subsequent HINCRBY on an
    empty wallet hash starts from 0, resetting the player's XP.

    Expected behavior: Either propagate the error to prevent the
    HINCRBY, or queue the completion for retry when hydration fails.
    """
```

**Test 1**: `test_xp_resets_on_hydration_failure`
- Setup: No wallet hash in Redis; mock_frappe.call raises ConnectionError
- Create WalletService with test_prefix and failing mock_frappe
- Call ensure_hydrated (should swallow error)
- Call award_xp with 50 XP
- Assert: new_xp == 50 (BUG: should be old_xp + 50 if hydrated correctly)

**Test 2**: `test_xp_correct_when_cache_populated`
- Setup: Pre-seed wallet hash with xp=500 via seed_wallet helper
- Create WalletService with test_prefix
- Call award_xp with 50 XP
- Assert: new_xp == 550 (correct — cache was populated, no hydration needed)

### Task 3: Create test_findings.py — FINDING-02 (LTRIM Boundary)

**Class**: `TestInteractionBufferLtrimRisk`
**File**: `fastapi_app/tests/test_findings.py`

```python
class TestInteractionBufferLtrimRisk:
    """FINDING-02: LTRIM off-by-one on partial insert failure.

    Severity: MEDIUM
    Location: memora_admin/tasks/sync.py:340-349

    Current behavior: flush_interaction_buffer() uses `inserted` count
    (number of successful inserts) as the LTRIM start index. When items
    fail at non-sequential positions, the count != position mapping causes:
    - Failed items in the middle to be trimmed (dropped without retry)
    - Successfully inserted items after a failure to remain in the buffer

    Expected behavior: Track actual positions of processed items and
    only trim consecutive successfully-processed items from the head.
    """
```

**Test 1**: `test_partial_failure_drops_failed_item`
- Setup: Push 5 JSON items to a Redis list
- Simulate the flush loop: items 0,2,4 succeed (inserted=3), items 1,3 fail
- Execute LTRIM(buffer_key, 3, -1) — exactly what sync.py does
- Assert: Remaining items are [item3, item4]
- BUG: item1 (which failed) was trimmed and lost forever

**Test 2**: `test_all_succeed_correct_trim`
- Setup: Push 5 JSON items to a Redis list
- All 5 succeed (inserted=5)
- Execute LTRIM(buffer_key, 5, -1)
- Assert: Buffer is empty (correct behavior when no failures)

### Task 4: Create test_findings.py — FINDING-03 (Stats Double-Counting)

**Class**: `TestStatsDoubleCounting`
**File**: `fastapi_app/tests/test_findings.py`

```python
class TestStatsDoubleCounting:
    """FINDING-03: Stats double-count on cold start race condition.

    Severity: LOW
    Location: fastapi_app/api/v1/endpoints/sessions.py:316-354

    Current behavior: Two concurrent session completions can both see
    stats_exists=False, causing both to compute from bitmap and SET.
    If Request 1's SET completes before Request 2's EXISTS check,
    Request 2 takes the HINCRBY path and double-counts the completion
    (once in the bitmap-computed stats, once via HINCRBY).

    Expected behavior: Use SETNX or Lua script for stats initialization
    to prevent the check-then-act race.
    """
```

**Test 1**: `test_concurrent_cold_start_race`
- Setup: No stats hash in Redis; seed hierarchy and progress bitmap
- Simulate the race: two coroutines both check EXISTS (both see 0)
- First coroutine: compute stats → set_stats (HSET)
- Second coroutine: check EXISTS again (now sees 1) → HINCRBY completed +1
- Assert: stats `completed` > expected (demonstrates double-count)

**Test 2**: `test_warm_path_increments_correctly`
- Setup: Pre-seed stats hash with completed=5
- Single HINCRBY completed +1
- Assert: completed == 6 (correct — no race on warm path)

### Task 5: Verify all tests pass

- Run: `python3 -m pytest fastapi_app/tests/test_findings.py -v`
- Verify: All 6 tests pass (asserting buggy behavior)
- Run: `python3 -m pytest -m characterization -v`
- Verify: Marker selection works correctly
- Run: `python3 -m pytest fastapi_app/tests/ -v --tb=short`
- Verify: No regressions in existing tests

## Complexity Tracking

> No constitution violations. No complexity justifications needed.
