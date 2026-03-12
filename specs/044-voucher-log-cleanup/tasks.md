# Tasks: Voucher Redemption Log Cleanup

**Input**: Design documents from `/specs/044-voucher-log-cleanup/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cleanup-task.md, quickstart.md

**Tests**: Included — spec explicitly mentions 12 automated test cases (SC-005) and research.md specifies test strategy (R-005).

**Organization**: Tasks grouped by user story. US1 and US2 are both P1 but US2 (batching) is integral to the core loop, so they share the implementation phase. US3 (logging) is P2.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: No new project structure needed — this feature adds files to existing directories. Phase is a no-op.

*(No tasks — project structure already exists per plan.md)*

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the cleanup task module with constants and register it in the scheduler.

- [x] T001 Create cleanup task module with constants and function signatures in `memora_admin/tasks/voucher_log_cleanup.py`
- [x] T002 Register scheduler entry `"30 5 * * *"` for `cleanup_voucher_redemption_logs` in `memora_admin/hooks.py`

**Checkpoint**: Module exists and is scheduler-registered. Inner logic not yet implemented.

---

## Phase 3: User Story 1 & 2 — Core Deletion with Batching (Priority: P1) 🎯 MVP

**Goal**: Old voucher redemption log rows (creation > 100 days) are deleted in batches of 1000 with commit-per-batch for restart safety.

**Independent Test**: Insert rows with `creation` older and newer than 100 days, run cleanup, verify only old rows removed. Insert >1000 eligible rows, verify multiple batches execute.

### Tests for User Stories 1 & 2

> **NOTE: Write tests FIRST, ensure they FAIL before implementation**

- [x] T003 [US1] Create test file with setUp/tearDown scaffolding (insert test rows, clean up after) in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T004 [US1] Test: zero eligible rows → zero deletions, clean exit in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T005 [US1] Test: rows older than 100 days are deleted in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T006 [US1] Test: rows within 100 days are NOT deleted in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T007 [US1] Test: row exactly 100 days old is NOT deleted (boundary — strictly less than) in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T008 [US1] Test: only `Memora Voucher Redemption Log` rows are affected, other DocTypes untouched in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T009 [US1] Test: idempotency — second run with no eligible rows returns (0, 0) in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T010 [US2] Test: 2500 eligible rows produce 3 batches (1000+1000+500) in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T011 [US2] Test: `frappe.db.commit()` is called after each batch in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T012 [US2] Test: deletion order is `creation ASC, name ASC` in `memora_admin/tests/test_voucher_log_cleanup.py`

### Implementation for User Stories 1 & 2

- [x] T013 [US1] Implement `_do_voucher_log_cleanup()` batched SELECT→DELETE→COMMIT loop in `memora_admin/tasks/voucher_log_cleanup.py`
- [x] T014 [US2] Implement `cleanup_voucher_redemption_logs()` wrapper with metrics and error handling in `memora_admin/tasks/voucher_log_cleanup.py`

**Checkpoint**: Core cleanup works — old rows deleted in batches, recent rows preserved, restart-safe. All US1/US2 tests pass.

---

## Phase 4: User Story 3 — Operational Logging (Priority: P2)

**Goal**: Cleanup task emits structured logs: start, cutoff datetime, batch counts, total deleted, duration, and errors.

**Independent Test**: Run the task and verify log output contains expected summary fields.

### Tests for User Story 3

- [x] T015 [US3] Test: successful run logs start, cutoff, batch size, per-batch count, total deleted, duration in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T016 [US3] Test: error during batch is logged with details before re-raise in `memora_admin/tests/test_voucher_log_cleanup.py`

### Implementation for User Story 3

- [x] T017 [US3] Add structured logging to `_do_voucher_log_cleanup()` and `cleanup_voucher_redemption_logs()` in `memora_admin/tasks/voucher_log_cleanup.py`

**Checkpoint**: Logs provide full operational visibility. All US3 tests pass.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [x] T018 Run all tests end-to-end and verify SC-005 (all 12 test cases pass) in `memora_admin/tests/test_voucher_log_cleanup.py`
- [x] T019 Run quickstart.md validation steps from `specs/044-voucher-log-cleanup/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 2 (Foundational)**: No dependencies — start immediately
- **Phase 3 (US1 & US2)**: Depends on T001 (module exists)
- **Phase 4 (US3)**: Depends on Phase 3 (logging wraps working logic)
- **Phase 5 (Polish)**: Depends on all prior phases

### User Story Dependencies

- **US1 + US2 (P1)**: Combined because batching is integral to the delete loop — cannot meaningfully separate
- **US3 (P2)**: Depends on US1/US2 completion (logs wrap the working task)

### Within Phase 3

- T003 (scaffolding) → T004–T012 (tests, can be parallel) → T013–T014 (implementation)

### Parallel Opportunities

- T001 and T002 can run in parallel (different files)
- T004–T012 can all run in parallel once T003 scaffolding is done (same file but independent test methods)
- T013 and T014 are sequential (wrapper depends on inner function)

---

## Parallel Example: Phase 2

```bash
# Launch foundational tasks together (different files):
Task T001: "Create cleanup task module in memora_admin/tasks/voucher_log_cleanup.py"
Task T002: "Register scheduler entry in memora_admin/hooks.py"
```

## Parallel Example: Phase 3 Tests

```bash
# Launch all test methods together (after T003 scaffolding):
Task T004: "Test zero eligible rows"
Task T005: "Test old rows deleted"
Task T006: "Test recent rows kept"
Task T007: "Test boundary cutoff"
Task T008: "Test DocType isolation"
Task T009: "Test idempotency"
Task T010: "Test multi-batch execution"
Task T011: "Test commit-per-batch"
Task T012: "Test deletion order"
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Complete Phase 2: Foundational (T001–T002)
2. Complete Phase 3: US1 + US2 tests then implementation (T003–T014)
3. **STOP and VALIDATE**: Run tests, verify core cleanup works
4. Deploy if ready — logging (US3) can follow

### Incremental Delivery

1. Phase 2 → Module + scheduler registered
2. Phase 3 → Core deletion works, restart-safe, batched → **MVP deployed**
3. Phase 4 → Operational logging added → Full feature complete
4. Phase 5 → Final validation
