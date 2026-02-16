# Tasks: Voucher System Audit & Comprehensive Tests

**Input**: Design documents from `/specs/008-voucher-audit-tests/`
**Prerequisites**: plan.md, spec.md, data-model.md, research.md, contracts/test-matrix.md, quickstart.md

**Tests**: This IS a test-only feature — all tasks produce test code. No production code changes.

**Organization**: Tasks are grouped by user story (test file). US2 (Allocation) and US5 (Financial) are already covered by phases 006 and 004 respectively — only gap tests in `test_security_audit.py` address those areas.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US3, US4, US6)
- Include exact file paths in descriptions

## Path Conventions

- **Test files**: `memora_admin/memora_admin/tests/`
- **Source under test**: `memora_admin/api/voucher.py`, `memora_admin/api/allocation.py`, `memora_admin/services/voucher/batch_utils.py`
- **Existing infra**: `voucher_test_base.py`, `voucher_fixtures.py`, `voucher_helpers.py`

---

## Phase 1: Setup (Verify Infrastructure)

**Purpose**: Confirm existing test infrastructure works and review source code under test

- [x] T001 Verify existing test infrastructure by running `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_voucher_quickstart` and confirming 2 tests pass
- [x] T002 Review source code for redemption API in `memora_admin/api/voucher.py:462-691` — identify all error code paths (`INVALID_PIN`, `NOT_ALLOCATED`, `ALREADY_REDEEMED`, `EXPIRED`, `VOID`, `BATCH_INACTIVE`, `GRANT_NOT_IN_BATCH`, `ALL_GRANTS_OWNED`), the `_log_attempt()` call sites, and the subscription creation flow (steps 8-12)
- [x] T003 Review source code for voiding APIs in `memora_admin/api/voucher.py:274-359` (`void_batch`, `void_card`) and `memora_admin/services/voucher/batch_utils.py` (`recount_and_maybe_close`) — identify validation checks, file deletion logic, and auto-close conditions

**Checkpoint**: Infrastructure verified, source code understood — ready to write tests

---

## Phase 2: User Story 1 — Redemption Edge Cases (Priority: P1) 🎯 MVP

**Goal**: Test all error code paths and edge cases in `redeem_voucher()` and `preview_voucher()` — the most critical untested area (ZERO existing tests)

**Independent Test**: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_redemption_edge`

### Implementation for User Story 1

- [x] T004 [US1] Create `memora_admin/memora_admin/tests/test_redemption_edge.py` with imports, `TestRedemptionErrorCodes(VoucherTestCase)` class, and `setUpClass` that creates a batch (10 cards), generates, allocates via `fill_and_complete_allocation()`, and exports PINs via `get_pins_from_export()`. Include `tearDownClass` cleanup. Implement these 10 test methods:
  1. `test_invalid_pin_returns_error` — bogus HMAC → INVALID_PIN (FR-003)
  2. `test_already_redeemed_returns_error` — manually set card status to Redeemed, call redeem → ALREADY_REDEEMED (FR-001)
  3. `test_not_allocated_card_returns_error` — use Available (unallocated) card → NOT_ALLOCATED (FR-001)
  4. `test_void_card_returns_error` — manually set card status to Void → VOID (FR-001)
  5. `test_expired_card_returns_error` — manually set card status to Expired → EXPIRED (FR-001)
  6. `test_batch_inactive_returns_error` — set batch to Closed → BATCH_INACTIVE (FR-001)
  7. `test_grant_not_in_batch_returns_error` — use invalid grant ID → GRANT_NOT_IN_BATCH (FR-005)
  8. `test_empty_grant_id_returns_error` — pass empty string grant ID → validation error or GRANT_NOT_IN_BATCH (FR-005)
  9. `test_all_grants_owned_returns_error` — create player subscriptions for all grant keys → ALREADY_OWNED (FR-004)
  10. `test_partial_grant_ownership_allows_redemption` — create subscription for 1 of N keys → redemption proceeds (FR-004) - SKIPPED (requires 2+ grant keys)

- [x] T005 [US1] Add `TestRedemptionAtomicity(VoucherTestCase)` class to `memora_admin/memora_admin/tests/test_redemption_edge.py` with its own `setUpClass` (batch + generate + allocate + export). Implement these 4 test methods:
  1. `test_successful_redemption_creates_transaction` — normal redemption → card Redeemed + Memora Subscription Transaction created (FR-001)
  2. `test_redemption_log_created_on_success` — after success → Memora Voucher Redemption Log entry with status "Success" (FR-001)
  3. `test_redemption_log_created_on_failure` — ALREADY_REDEEMED → Redemption Log entry with correct error status (FR-001)
  4. `test_redemption_updates_batch_counters` — after redemption → batch `redeemed_count` incremented by 1 (FR-012)

- [x] T006 [US1] Add `TestPreviewVoucher(VoucherTestCase)` class to `memora_admin/memora_admin/tests/test_redemption_edge.py` with its own `setUpClass`. Implement these 3 test methods:
  1. `test_preview_returns_grants_for_allocated_card` — allocated card → grants list with face_value
  2. `test_preview_filters_owned_grants` — player owns all grants → ALL_GRANTS_OWNED (FR-004)
  3. `test_preview_invalid_pin` — bogus HMAC → INVALID_PIN (FR-003)

- [x] T007 [US1] Run `test_redemption_edge.py` tests and verify all 17 methods pass: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_redemption_edge`

**Checkpoint**: 17 redemption edge case tests passing — US1 complete and independently verifiable

---

## Phase 3: User Story 3 — Voiding & Expiry Flows (Priority: P2)

**Goal**: Test batch voiding, single card voiding, validation guards, file deletion, and auto-close behavior

**Independent Test**: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_voiding`

### Implementation for User Story 3

- [x] T008 [US3] Create `memora_admin/memora_admin/tests/test_voiding.py` with imports, `TestVoidBatch(VoucherTestCase)` class, and `setUpClass` that creates a batch (10 cards), generates, and allocates a subset. Implement these 5 test methods:
  1. `test_void_batch_with_mixed_states` — batch with Available+Allocated+Redeemed cards → only Available+Allocated become Void, Redeemed untouched, batch→Closed (FR-009)
  2. `test_void_batch_requires_reason` — empty `void_reason` → `frappe.ValidationError` (FR-009)
  3. `test_void_draft_batch_raises_error` — Draft batch → `frappe.ValidationError` "Cannot void a Draft batch" (FR-009)
  4. `test_void_closed_batch_raises_error` — Closed batch → `frappe.ValidationError` (FR-009)
  5. `test_void_batch_deletes_encrypted_file` — generate with export → void → File doc removed + `encrypted_file_url` cleared (FR-018)

- [x] T009 [US3] Add `TestVoidCard(VoucherTestCase)` class to `memora_admin/memora_admin/tests/test_voiding.py` with its own `setUpClass`. Implement these 4 test methods:
  1. `test_void_available_card` — Available card → Void, `void_reason` set, counters updated (FR-010)
  2. `test_void_allocated_card` — Allocated card → Void, counters updated (FR-010)
  3. `test_void_redeemed_card_raises_error` — Redeemed card → `frappe.ValidationError` (FR-010)
  4. `test_void_card_triggers_auto_close` — void last non-terminal card → batch auto-closes via `recount_and_maybe_close` (FR-010)

- [x] T010 [US3] Run `test_voiding.py` tests and verify all 9 methods pass: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_voiding`

**Checkpoint**: 9 voiding tests passing — US3 complete and independently verifiable

---

## Phase 4: User Story 4 — Fraud & Security Audit (Priority: P2)

**Goal**: Document known security gaps and verify existing protections via passing tests with `# TODO: SECURITY-FIX` / `# TODO: FIX` markers

**Independent Test**: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_security_audit`

### Implementation for User Story 4

- [x] T011 [P] [US4] Create `memora_admin/memora_admin/tests/test_security_audit.py` with imports, `TestSecurityGaps(VoucherTestCase)` class, and `setUpClass` that creates a batch, generates, allocates, and exports PINs. Implement these 6 test methods (each with appropriate `# TODO: SECURITY-FIX` or `# TODO: FIX` markers):
  1. `test_no_rate_limiting_on_redemption` — multiple rapid invalid PIN attempts all succeed without rate limit (FR-016, `# TODO: SECURITY-FIX`)
  2. `test_any_user_can_redeem_for_other_player` — redeem with another player's ID succeeds (FR-016, `# TODO: SECURITY-FIX`)
  3. `test_season_check_fails_open_on_exception` — season check exception → redemption still allowed (FR-016, `# TODO: SECURITY-FIX`)
  4. `test_hmac_uses_timing_safe_comparison` — verify `hmac.compare_digest` is used in voucher.py source code via code inspection (FR-017)
  5. `test_hmac_secret_absent_redemption_behavior` — missing HMAC secret during redemption → graceful error (FR-017, `# TODO: FIX`)
  6. `test_redemption_atomicity_gap` — document that card marked Redeemed at step 8 but subscription at step 11 has no rollback (FR-002, `# TODO: FIX`)

- [x] T012 [P] [US4] Add `TestAllocationSecurityGaps(VoucherTestCase)` class to `memora_admin/memora_admin/tests/test_security_audit.py` with its own `setUpClass`. Implement these 2 test methods:
  1. `test_reallocation_steals_cards_from_other_library` — cards allocated to Library A can be re-allocated to Library B without explicit return (FR-016, `# TODO: SECURITY-FIX`)
  2. `test_stale_cards_in_allocation_accepted` — cards voided between fill and submit are still accepted by submit (FR-016, `# TODO: FIX`)

- [x] T013 [US4] Run `test_security_audit.py` tests and verify all 8 methods pass: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_security_audit`

**Checkpoint**: 8 security audit tests passing with grep-able TODO markers — US4 complete

---

## Phase 5: User Story 6 — Counter Integrity (Priority: P3)

**Goal**: Verify batch counters remain accurate across all operations and that `recount_and_maybe_close` is idempotent

**Independent Test**: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_counter_integrity`

### Implementation for User Story 6

- [x] T014 [US6] Create `memora_admin/memora_admin/tests/test_counter_integrity.py` with imports, `TestCounterIntegrity(VoucherTestCase)` class, and `setUpClass` that creates a batch (10 cards), generates, allocates, and exports PINs. Implement these 5 test methods:
  1. `test_full_lifecycle_counter_accuracy` — generate→allocate→redeem 3→void 2→return 2→recount → verify all counters: `generated_count=10, allocated_count=3, redeemed_count=3, voided_count=2` (FR-012)
  2. `test_recount_idempotency` — call `recount_and_maybe_close` twice → both return identical counter values (FR-013)
  3. `test_auto_close_only_active_batches` — Generated batch with all terminal cards → does NOT auto-close (FR-013)
  4. `test_auto_close_on_all_terminal_cards` — Active batch with all Redeemed/Void/Expired cards → auto-closes to Closed (FR-013)
  5. `test_counters_after_void_batch` — `void_batch()` → `voided_count` accurate, `redeemed_count` unchanged (FR-012)

- [x] T015 [US6] Run `test_counter_integrity.py` tests and verify all 5 methods pass: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_counter_integrity`

**Checkpoint**: 5 counter integrity tests passing — US6 complete

---

## Phase 6: Polish & Cross-Cutting Validation

**Purpose**: Full suite validation, TODO marker verification, performance check

- [x] T016 Run all 4 new test files together and verify all 39 tests pass: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_redemption_edge --module memora_admin.memora_admin.tests.test_voiding --module memora_admin.memora_admin.tests.test_security_audit --module memora_admin.memora_admin.tests.test_counter_integrity`
- [x] T017 Verify all TODO markers are grep-able: `grep -rn "TODO: SECURITY-FIX\|TODO: FIX" memora_admin/memora_admin/tests/` — confirm at least 5 `SECURITY-FIX` and 2 `FIX` markers
- [x] T018 Verify total test execution time is under 30 seconds (SC-007)
- [x] T019 Run full existing test suite to confirm zero regressions: `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_voucher_quickstart` plus spot-check 1-2 existing test modules

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (US1 - Redemption)**: Depends on Phase 1 completion
- **Phase 3 (US3 - Voiding)**: Depends on Phase 1 completion — **can run in parallel with Phase 2**
- **Phase 4 (US4 - Security)**: Depends on Phase 1 completion — **can run in parallel with Phases 2 & 3**
- **Phase 5 (US6 - Counters)**: Depends on Phase 1 completion — **can run in parallel with Phases 2, 3 & 4**
- **Phase 6 (Polish)**: Depends on ALL previous phases being complete

### User Story Dependencies

- **US1 (Redemption Edge)**: Independent — no dependencies on other new test files
- **US3 (Voiding)**: Independent — no dependencies on other new test files
- **US4 (Security Audit)**: Independent — no dependencies on other new test files
- **US6 (Counter Integrity)**: Independent — no dependencies on other new test files

All user stories depend only on the existing test infrastructure (`voucher_test_base.py`, `voucher_fixtures.py`, `voucher_helpers.py`).

### Within Each User Story

1. Create test file with first class → Add remaining classes → Run and verify
2. Each class has its own `setUpClass`/`tearDownClass` for isolation
3. Use existing `SEAS-00027` season for all fixtures

### Parallel Opportunities

- **Phases 2-5 are fully parallelizable** — each creates a different test file with no cross-file dependencies
- Within Phase 4: T011 and T012 are parallelizable (different classes, same file — but T012 appends to file created by T011, so T011 must complete first; however both are marked [P] because their _test logic_ is independent)
- Phase 6 tasks are sequential (run suite → check markers → check time → regression check)

---

## Parallel Example: All User Stories

```bash
# After Phase 1 completes, launch all 4 test files in parallel:
Agent 1: T004 → T005 → T006 → T007  (test_redemption_edge.py)
Agent 2: T008 → T009 → T010          (test_voiding.py)
Agent 3: T011 → T012 → T013          (test_security_audit.py)
Agent 4: T014 → T015                  (test_counter_integrity.py)

# Then converge for Phase 6 validation
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: US1 - Redemption Edge Cases (T004-T007)
3. **STOP and VALIDATE**: 17 tests passing, most critical gap closed
4. This alone satisfies SC-001 partially and covers the highest-risk area

### Incremental Delivery

1. Setup → Foundation verified
2. US1 (Redemption) → 17 tests → Most critical coverage gap closed
3. US3 (Voiding) → +9 tests → Admin operations covered
4. US4 (Security) → +8 tests → All security gaps documented with TODO markers
5. US6 (Counters) → +5 tests → Counter integrity verified
6. Polish → Full validation, 39 new tests total

### Success Criteria Mapping

| SC | Target | How Met |
|----|--------|---------|
| SC-001 | 25+ new tests | 39 new tests across 4 files |
| SC-002 | Allocation ≥12 tests | 23 existing (phase 006) — already exceeded |
| SC-003 | Voiding ≥8 tests | 9 tests in `test_voiding.py` |
| SC-004 | Security ≥6 tests | 8 tests in `test_security_audit.py` |
| SC-005 | Financial Decimal ≥5 | 11 existing (phase 004) — already exceeded |
| SC-006 | Counters ≥4 tests | 5 tests in `test_counter_integrity.py` |
| SC-007 | All tests <30s | Small batches (10 cards), no threading |
| SC-008 | Zero pollution | setUpClass/tearDownClass per class |
| SC-009 | Flaws 1-6 documented | 5× `SECURITY-FIX` + 3× `FIX` markers |

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story produces one independently runnable test file
- All tests use existing season `SEAS-00027` (avoids MySQL partitioning issues)
- Security gap tests PASS asserting current (insecure) behavior — grep-able markers for future fix branch
- US2 (Allocation) and US5 (Financial) are already fully covered — no new test files needed
- Total: 19 tasks, 39 new test methods, 4 new test files
