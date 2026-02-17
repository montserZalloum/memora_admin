# Research: Characterization Tests for Known Bugs

**Feature**: 015-characterization-tests
**Date**: 2026-02-17

## Decision 1: Test Strategy for FINDING-01 (XP Hydration Failure)

**Decision**: Test at the WalletService unit level by mocking FrappeClient to raise an exception, then calling `award_xp` and asserting XP starts from 0.

**Rationale**:
- The bug is in `WalletService.ensure_hydrated()` at `wallet.py:205-213` — the except block catches ALL exceptions from `frappe.call()` and silently returns
- After hydration fails, HINCRBY on a missing hash field creates it with 0 and returns the increment amount only
- Testing at the service level isolates the bug from endpoint complexity
- A secondary endpoint-level test confirms the full flow (sessions.py:301-310 calling ensure_hydrated before pipeline HINCRBY)

**Alternatives considered**:
- Endpoint-only testing: Rejected — too many dependencies to mock (hierarchy, game session, settings), obscures the core bug
- Mocking Redis: Rejected — the bug depends on real Redis HINCRBY behavior on empty keys
- Combined service+endpoint: Selected — service-level proves the mechanism, endpoint-level proves production impact

**Key Code References**:
- `wallet.py:148-213` — `ensure_hydrated()` with silent error swallowing
- `wallet.py:205-213` — Exception handler that logs but doesn't propagate
- `sessions.py:301-310` — Caller assumes hydration succeeded
- Redis HINCRBY specification: creates field with 0 if missing, then increments

## Decision 2: Test Strategy for FINDING-02 (Interaction Buffer LTRIM)

**Decision**: Test at the Frappe sync task level using direct Redis operations and mocking `frappe.get_doc().insert()` to fail for specific items.

**Rationale**:
- The bug is in `sync.py:340-349` — LTRIM uses `inserted` count as a position index
- Sequential processing: items are at positions [0,1,2,3,4]. If items 0,2,4 succeed (inserted=3) and items 1,3 fail, `LTRIM(3, -1)` removes positions 0-2 (keeping 3-4)
- This means item 2 (successfully inserted) is ALSO removed, AND item 1 (which failed) is removed without retry
- The test must prove this index/count confusion by checking which items remain in the buffer after flush

**Alternatives considered**:
- Full bench run-tests: Rejected — characterization tests should run via pytest alongside FastAPI tests, not require Frappe runtime
- Mocking Redis entirely: Rejected — the LTRIM behavior is central to the bug and must use real Redis
- Testing only the happy path: Rejected — the bug only manifests on partial failure, which is the interesting case

**Key Code References**:
- `sync.py:298-337` — Loop with `inserted` counter that increments only on success
- `sync.py:340-349` — `r.ltrim(INTERACTION_BUFFER_KEY, inserted, -1)` using count as index
- The comment at line 340 ("removing only successfully processed ones") is incorrect

**Important**: This test runs against the sync task which uses synchronous `redis` (not `redis.asyncio`). The characterization test will need to simulate the LTRIM logic directly since `flush_interaction_buffer()` requires the full Frappe runtime. We'll test the LTRIM boundary math itself.

## Decision 3: Test Strategy for FINDING-03 (Stats Double-Counting Race)

**Decision**: Test at the service/endpoint level by simulating two concurrent cold-start stats computations for the same user+subject, then checking whether HINCRBY double-counts.

**Rationale**:
- The race is in `sessions.py:316-354` — two paths: cold start (compute + HSET) vs warm (HINCRBY)
- Race scenario: Both requests see `stats_exists=False`, both compute from bitmap and SET
- The second SET overwrites the first (no data corruption if both computed correctly)
- However, if Request 1 SETs stats, then Request 2 sees stats_exists=True and does HINCRBY, the lesson completed by Request 1 gets double-counted (once in the bitmap-computed stats, once by HINCRBY)
- The critical window: between Request 1's `set_stats()` and Request 2's `redis_client.exists()` check

**Alternatives considered**:
- True concurrent testing with asyncio.gather: Selected — demonstrates the race directly
- Sequential simulation: Rejected — doesn't prove the race exists
- Mocking the exists check: Rejected — too artificial, doesn't prove production scenario

**Key Code References**:
- `sessions.py:328-345` — Cold start path (compute + set_stats)
- `sessions.py:346-352` — Warm path (HINCRBY)
- `stats.py:122-144` — `set_stats()` using HSET (non-atomic vs concurrent EXISTS check)
- `stats.py:163-219` — `compute_stats_from_hierarchy()` pure function

## Decision 4: Test File Structure and Markers

**Decision**: Single file `fastapi_app/tests/test_findings.py` with 3 test classes, using a custom `characterization` pytest marker.

**Rationale**:
- The test plan specifies `test_findings.py` as the single Phase 7 file
- Existing markers in `pyproject.toml` include `slow` and `integration` — adding `characterization` follows the pattern
- Each class gets a detailed docstring per FR-004 (severity, location, current behavior, expected behavior)
- Tests use `# BUG:` and `# FIX:` comment pairs showing what to change when the bug is fixed per FR-005

**Alternatives considered**:
- Separate files per finding: Rejected — test plan specifies single file, findings are related
- No custom marker: Rejected — FR-008 requires tests to be distinguishable from standard regression tests
- Using xfail instead of asserting buggy behavior: Rejected — xfail tests pass when the bug exists (which is backwards for characterization tests)

## Decision 5: FINDING-02 Testing Without Frappe Runtime

**Decision**: Test the LTRIM boundary logic directly using Redis operations that simulate what `flush_interaction_buffer()` does, without importing or calling the actual Frappe function.

**Rationale**:
- `flush_interaction_buffer()` requires the full Frappe runtime (`frappe.get_doc`, `frappe.db.commit`, etc.)
- FastAPI tests run via `pytest`, not `bench run-tests`
- We can reproduce the exact bug by: (1) populating a Redis list, (2) simulating the insert loop with specific failures, (3) running the exact LTRIM command, (4) asserting which items remain
- This proves the LTRIM math bug without Frappe dependencies

**Alternatives considered**:
- Move test to Frappe test suite: Rejected — test plan places it in `fastapi_app/tests/test_findings.py`
- Import and mock Frappe: Rejected — complex, fragile, misses the point (the bug is in the LTRIM math)
