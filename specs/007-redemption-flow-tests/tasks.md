# Tasks: Integration Tests — Redemption Flow

**Input**: Design documents from `/specs/007-redemption-flow-tests/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/test-matrix.md

**Tests**: This feature IS the test suite — 22 integration tests across 5 user stories.

**Organization**: Tasks grouped by user story. All tests live in a single file (`test_memora_voucher_card.py`) with shared `setUpClass()` setup. Fixture/helper enhancements are in separate files.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

```text
memora_admin/memora_admin/
├── doctype/memora_voucher_card/
│   └── test_memora_voucher_card.py        # PRIMARY: All 22 tests
├── tests/
│   ├── voucher_test_base.py               # EXISTING: VoucherTestCase base class
│   ├── voucher_fixtures.py                # MODIFY: Add grant_components support
│   └── voucher_helpers.py                 # MODIFY: Add get_pins_from_export(), preview_card_by_pin()
└── api/
    └── voucher.py                         # READ-ONLY: redeem_voucher(), preview_voucher()
```

---

## Phase 1: Setup (Helper & Fixture Enhancements)

**Purpose**: Enhance existing test infrastructure with capabilities needed by redemption tests

- [X] T001 [P] Add `grant_components` parameter to `make_product_grant()` in `memora_admin/memora_admin/tests/voucher_fixtures.py`
  - Accept optional `grant_components` param: list of `{"target_doctype": str, "target_name": str}` dicts
  - When provided, append each as a `Memora Grant Component` child row to the grant's `grant_components` table
  - Backward compatible: existing callers that omit the param continue to work unchanged
  - This is needed because `get_grant_keys()` returns `[]` for grants without components, and `all([])` is `True` in Python, causing every redemption to return `ALREADY_OWNED`
  - Reference: research.md R2 for rationale

- [X] T002 [P] Add `get_pins_from_export(batch_name)` helper in `memora_admin/memora_admin/tests/voucher_helpers.py`
  - Import `csv`, `io` at top of file
  - Import `export_for_print` from `memora_admin.api.voucher`
  - Implementation: call `frappe.set_user("Administrator")`, then `export_for_print(batch_name)`, read `frappe.local.response.filecontent` (decode if bytes), parse with `csv.DictReader`, return `dict[str, str]` mapping `serial_no` to plaintext `pin`
  - The encrypted export is the ONLY way to obtain plaintext PINs — the DB only stores HMAC hashes
  - Reference: research.md R1, existing pattern in `test_memora_voucher_batch.py` line ~139

- [X] T003 Add `preview_card_by_pin(pin, player_id)` helper in `memora_admin/memora_admin/tests/voucher_helpers.py`
  - Import `preview_voucher` from `memora_admin.api.voucher`
  - Import `compute_hmac` from wherever the existing `redeem_card_by_pin()` imports it (follow same pattern)
  - Implementation: read `voucher_hmac_secret` from `frappe.conf`, compute HMAC via `compute_hmac(pin, hmac_secret)`, call and return `preview_voucher(pin_hmac=pin_hmac, player_id=player_id)`
  - Follows exact same pattern as existing `redeem_card_by_pin()` helper
  - Reference: research.md R3

**Checkpoint**: Helper infrastructure ready. T001 and T002 can run in parallel (different files). T003 must follow T002 (same file).

---

## Phase 2: Foundational (Test Class Shared Setup)

**Purpose**: Create the test class skeleton with `setUpClass()` that all 22 tests depend on

**CRITICAL**: No test tasks can begin until this phase is complete

- [X] T004 Create `TestMemoraVoucherCard` class with `setUpClass()` in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - Replace the existing test stub file with a full test class
  - Import: `frappe`, `hmac`, `inspect`, `VoucherTestCase` (from `memora_admin.memora_admin.tests.voucher_test_base`), fixture functions (`make_product_grant`, `make_player`, `make_batch`, etc.), helper functions (`generate_batch_sync`, `allocate_batch`, `redeem_card_by_pin`, `get_pins_from_export`, `preview_card_by_pin`)
  - Class inherits from `VoucherTestCase`
  - `setUpClass()` must:
    1. Create a `Memora Subject` (or find existing one) for grant component target — store as `cls.subject`
    2. Create 2 `Product Grant` records using `make_product_grant(season="SEAS-00027", grant_components=[{"target_doctype": "Memora Subject", "target_name": cls.subject.name}])` — store as `cls.grant1`, `cls.grant2`
    3. Create a `Voucher Batch` with both grants in `batch_grants` and a `face_value` (e.g., 100) — store as `cls.batch`
    4. Generate cards via `generate_batch_sync(cls.batch.name)` — use qty of 30
    5. Create a `Customer` (library) — store as `cls.library`
    6. Allocate all cards via `allocate_batch(cls.batch.name, cls.library.name)`
    7. Extract PINs via `get_pins_from_export(cls.batch.name)` — store as `cls.pins` (dict: serial_no → PIN)
    8. Create a `Player Profile` via `make_player(season="SEAS-00027")` — store as `cls.player`
    9. Build `cls.cards` list: query all `Memora Voucher Card` where batch=cls.batch.name, status="Allocated"
    10. Store `cls.card_index = 0` as a counter for per-test card allocation
  - Add helper method `_next_card(cls)` that returns `cls.cards[cls.card_index]` and increments `cls.card_index` — ensures each test uses a unique card
  - Reference: research.md R7 (test independence), R9 (subject for grant components), data-model.md (field names)

**Checkpoint**: Foundation ready — all user story test implementations can now begin

---

## Phase 3: User Story 1 — Successful Redemption (Priority: P1)

**Goal**: Verify the core redemption happy path — card status, transaction creation, field population, batch counters, and audit logging

**Independent Test**: Run these 4 tests after Phase 2. They prove the revenue-generating flow works end-to-end.

### Implementation

- [X] T005 [US1] Implement `test_redeem_success_card_status_and_transaction` (TC-01) and `test_redeem_success_card_fields_populated` (TC-02) in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - **TC-01** (`test_redeem_success_card_status_and_transaction`):
    - Get a unique card via `_next_card()`, look up its PIN from `cls.pins`
    - Call `redeem_card_by_pin(pin, player_id=cls.player.name, grant_id=cls.grant1.name, ip_address="127.0.0.1")`
    - Reload card: `frappe.get_doc("Memora Voucher Card", card.name)`
    - Assert card.status == "Redeemed"
    - Query `Memora Subscription Transaction` where `transaction_id=card.name`
    - Assert transaction exists, status == "Completed", payment_method == "Voucher", amount_paid == cls.batch face_value
  - **TC-02** (`test_redeem_success_card_fields_populated`):
    - Get a unique card, redeem it successfully
    - Reload card, assert: `redeemed_by` == cls.player.name, `redeemed_at` is not None, `redeemed_grant` == cls.grant1.name, `subscription_transaction` is not None
  - Reference: data-model.md (Voucher Card fields, Subscription Transaction fields), test-matrix.md (TC-01, TC-02)

- [X] T006 [US1] Implement `test_redeem_success_batch_counter_incremented` (TC-03) and `test_redeem_success_log_entry_created` (TC-04) in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - **TC-03** (`test_redeem_success_batch_counter_incremented`):
    - Record `before_count = frappe.get_value("Memora Voucher Batch", cls.batch.name, "redeemed_count")`
    - Get a unique card, redeem it
    - `after_count = frappe.get_value("Memora Voucher Batch", cls.batch.name, "redeemed_count")`
    - Assert `after_count == before_count + 1`
  - **TC-04** (`test_redeem_success_log_entry_created`):
    - Get a unique card, note its `pin_hmac` last 4 chars
    - Redeem it with `ip_address="10.0.0.1"`
    - Query `Memora Voucher Redemption Log` where `card=card.name`
    - Assert: status == "Success", player == cls.player.name, `pin_masked` starts with "****" and ends with last 4 of pin_hmac, ip_address == "10.0.0.1"
  - Reference: data-model.md (Batch counters, Redemption Log fields), test-matrix.md (TC-03, TC-04)

**Checkpoint**: US1 complete — core redemption flow verified. Run: `bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_card.test_memora_voucher_card --test test_redeem_success -v`

---

## Phase 4: User Story 2 — Error Paths (Priority: P1)

**Goal**: Verify all 9 error codes are returned correctly and cards are never consumed on failure

**Independent Test**: Each error test sets up a specific invalid state, attempts redemption, verifies the correct error code, and confirms the card state is unchanged.

### Implementation

- [X] T007 [US2] Implement `test_error_invalid_pin` (TC-05), `test_error_not_allocated` (TC-06), and `test_error_already_redeemed` (TC-07) in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - **TC-05** (`test_error_invalid_pin`):
    - Call `redeem_voucher()` (or `redeem_card_by_pin` with a fabricated wrong PIN)
    - Assert error code `INVALID_PIN` is returned
    - Query Redemption Log where `pin_masked` matches the wrong PIN's mask — assert status is "Invalid PIN"
  - **TC-06** (`test_error_not_allocated`):
    - Get a unique card, set its status to "Available" via `frappe.db.set_value("Memora Voucher Card", card.name, "status", "Available")` + `frappe.db.commit()`
    - Attempt redemption with correct PIN
    - Assert error code `NOT_ALLOCATED`, card status still "Available"
    - Assert Redemption Log entry with status "Not Allocated"
  - **TC-07** (`test_error_already_redeemed`):
    - Get a unique card, redeem it successfully first
    - Attempt redemption again with same PIN
    - Assert error code `ALREADY_REDEEMED`
    - Assert Redemption Log entry with status "Already Redeemed"
  - Reference: data-model.md (Error Code → Log Status Mapping), test-matrix.md (TC-05, TC-06, TC-07)

- [X] T008 [US2] Implement `test_error_expired` (TC-08), `test_error_void` (TC-09), and `test_error_batch_inactive` (TC-10) in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - **TC-08** (`test_error_expired`):
    - Get a unique card, set status to "Expired" via `frappe.db.set_value`
    - Attempt redemption, assert error code `EXPIRED`
    - Assert Redemption Log entry with status "Expired"
  - **TC-09** (`test_error_void`):
    - Get a unique card, set status to "Void" via `frappe.db.set_value`
    - Attempt redemption, assert error code `VOID`
    - Assert Redemption Log entry with status "Void"
  - **TC-10** (`test_error_batch_inactive`):
    - Get a unique card, set batch status to "Closed" via `frappe.db.set_value("Memora Voucher Batch", cls.batch.name, "status", "Closed")`
    - Attempt redemption, assert error code `BATCH_INACTIVE`
    - **CRITICAL**: Restore batch status to "Active" in a `finally` block to avoid polluting other tests
    - Assert Redemption Log entry with status "Batch Inactive"
  - Reference: data-model.md (Error Code → Log Status Mapping), test-matrix.md (TC-08, TC-09, TC-10)

- [X] T009 [US2] Implement `test_error_season_inactive` (TC-11), `test_error_grant_not_in_batch` (TC-12), and `test_error_already_owned` (TC-13) in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - **TC-11** (`test_error_season_inactive`):
    - Save original `end_date` of season `SEAS-00027`
    - Set season `end_date` to a past date (e.g., "2020-01-01") via `frappe.db.set_value("Memora Season", "SEAS-00027", "end_date", "2020-01-01")` + commit
    - Attempt redemption in a `try` block, assert error code `SEASON_INACTIVE`
    - In `finally` block: restore original `end_date` + commit
    - Assert Redemption Log entry with status "Season Inactive"
    - Reference: research.md R4 (SEASON_INACTIVE strategy)
  - **TC-12** (`test_error_grant_not_in_batch`):
    - Create a separate Product Grant (not in the batch's `batch_grants`) via `make_product_grant(season="SEAS-00027")`
    - Get a unique card, attempt redemption with the non-batch grant
    - Assert error code `GRANT_NOT_IN_BATCH`
    - Assert Redemption Log entry with status "Grant Not In Batch"
  - **TC-13** (`test_error_already_owned`):
    - Get a unique card
    - Determine the grant's access keys via the grant_components (format: `SUB-{target_name}`)
    - Create a `Memora Player Subscription` for `cls.player` with matching `access_key` to simulate prior ownership
    - Attempt redemption, assert error code `ALREADY_OWNED`, card status still "Allocated" (not consumed)
    - Clean up: delete the Player Subscription in `finally` to avoid polluting other tests
    - Assert Redemption Log entry with status "Already Owned"
    - Reference: research.md R5 (ALREADY_OWNED strategy), data-model.md (Player Subscription fields)

**Checkpoint**: US2 complete — all 9 error codes verified. Run: `bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_card.test_memora_voucher_card --test test_error -v`

---

## Phase 5: User Story 3 — Preview (Priority: P2)

**Goal**: Verify preview returns correct grant information, filters owned grants, and errors when all grants owned

**Independent Test**: Call `preview_card_by_pin()` with various card/player combinations and verify response without any state mutation.

### Implementation

- [X] T010 [US3] Implement `test_preview_returns_grants_and_face_value` (TC-14), `test_preview_filters_owned_grants` (TC-15), and `test_preview_all_grants_owned_error` (TC-16) in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - **TC-14** (`test_preview_returns_grants_and_face_value`):
    - Get a unique card, call `preview_card_by_pin(pin, cls.player.name)`
    - Assert response contains `face_value` matching batch face_value
    - Assert response contains `grants` list with entries for the batch's grants
    - Verify card status is still "Allocated" (no mutation)
  - **TC-15** (`test_preview_filters_owned_grants`):
    - Create a `Memora Player Subscription` for cls.player with access_key matching grant1's key
    - Call `preview_card_by_pin(pin, cls.player.name)`
    - Assert only grant2 appears in the response grants (grant1 is filtered out as already owned)
    - Clean up: delete the Player Subscription in `finally`
  - **TC-16** (`test_preview_all_grants_owned_error`):
    - Create `Memora Player Subscription` records for cls.player matching ALL grants' access keys
    - Call `preview_card_by_pin(pin, cls.player.name)`
    - Assert response contains error code `ALL_GRANTS_OWNED`
    - Clean up: delete the Player Subscriptions in `finally`
  - Reference: research.md R3 (preview helper), test-matrix.md (TC-14, TC-15, TC-16)

**Checkpoint**: US3 complete — preview functionality verified. Run: `bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_card.test_memora_voucher_card --test test_preview -v`

---

## Phase 6: User Story 4 — Audit Logging & Security (Priority: P2)

**Goal**: Verify every redemption attempt produces correct audit logs with masked PINs and IP addresses, and that HMAC uses timing-safe comparison

**Independent Test**: Attempt redemptions (valid and invalid), query log entries, verify fields. Inspect source for `compare_digest`.

### Implementation

- [X] T011 [US4] Implement `test_log_success_entry` (TC-17), `test_log_failure_entries_all_codes` (TC-18), `test_log_pin_masked` (TC-19), and `test_log_ip_address_captured` (TC-20) in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - **TC-17** (`test_log_success_entry`):
    - Get a unique card, redeem successfully
    - Query Redemption Log where card=card.name
    - Assert: status == "Success", card == card.name, batch == cls.batch.name, player == cls.player.name, requested_grant == cls.grant1.name
  - **TC-18** (`test_log_failure_entries_all_codes`):
    - This test verifies that each error code maps to the correct human-readable log status
    - For each error scenario (at minimum: INVALID_PIN, NOT_ALLOCATED), trigger the error and verify the log entry's `status` matches the expected mapping from data-model.md Error Code → Log Status table
    - Can reuse error setups similar to US2 tests, but focused on verifying the log `status` field specifically
    - Map: INVALID_PIN → "Invalid PIN", NOT_ALLOCATED → "Not Allocated", ALREADY_REDEEMED → "Already Redeemed", EXPIRED → "Expired", VOID → "Void", BATCH_INACTIVE → "Batch Inactive", SEASON_INACTIVE → "Season Inactive", GRANT_NOT_IN_BATCH → "Grant Not In Batch", ALREADY_OWNED → "Already Owned"
  - **TC-19** (`test_log_pin_masked`):
    - Get a unique card, note its `pin_hmac` (from DB) — take last 4 characters
    - Redeem the card
    - Query Redemption Log, assert `pin_masked` == f"****{pin_hmac[-4:]}"
  - **TC-20** (`test_log_ip_address_captured`):
    - Get a unique card, redeem with `ip_address="192.168.1.100"`
    - Query Redemption Log, assert `ip_address` == "192.168.1.100"
  - Reference: data-model.md (Redemption Log fields, Error Code → Log Status Mapping), test-matrix.md (TC-17 through TC-20)

- [X] T012 [US4] Implement `test_hmac_uses_compare_digest` (TC-21) in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - Import `inspect` module
  - Get source code of `redeem_voucher` function: `source = inspect.getsource(redeem_voucher)`
  - Assert `"compare_digest"` appears in the source string (verifies timing-safe HMAC comparison)
  - This is a code-inspection assertion, NOT a runtime timing test
  - Reference: research.md R6 (FR-011 strategy), test-matrix.md (TC-21)

**Checkpoint**: US4 complete — audit and security verified. Run: `bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_card.test_memora_voucher_card --test test_log -v && bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_card.test_memora_voucher_card --test test_hmac -v`

---

## Phase 7: User Story 5 — Batch Auto-Close (Priority: P3)

**Goal**: Verify that redeeming the last non-terminal card in a batch triggers automatic batch closure

**Independent Test**: Create a minimal 1-card batch, redeem the only card, verify batch transitions to "Closed".

### Implementation

- [X] T013 [US5] Implement `test_batch_auto_close_on_last_redemption` (TC-22) in `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py`
  - This test needs its OWN batch (not the shared one) since it must redeem ALL cards
  - Create a new batch with qty=1, single grant (cls.grant1), face_value=50, season="SEAS-00027"
  - Generate cards via `generate_batch_sync()`
  - Allocate the single card to cls.library
  - Extract the PIN via `get_pins_from_export()`
  - Verify batch status is "Active" before redemption
  - Redeem the only card
  - Reload batch, assert status == "Closed"
  - Reference: test-matrix.md (TC-22), data-model.md (Batch status lifecycle)

**Checkpoint**: US5 complete — batch auto-close verified

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Validate full suite, verify compliance with success criteria

- [X] T014 Run full test suite and verify all 22 tests pass within 60 seconds via `bench run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_voucher_card.test_memora_voucher_card -v`
  - All 22 tests must pass (SC-001)
  - Suite must complete within 60 seconds (SC-004)
  - No test should leave dirty state that fails other tests (SC-005)
  - If any test fails: debug, fix, and re-run until green

- [X] T015 Validate quickstart.md instructions by running the documented commands in `specs/007-redemption-flow-tests/quickstart.md`
  - Verify the single-test command works
  - Verify the all-tests command works
  - Confirm output matches expected format

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion (needs enhanced fixtures/helpers)
- **US1 (Phase 3)**: Depends on Phase 2 (needs setUpClass)
- **US2 (Phase 4)**: Depends on Phase 2 (needs setUpClass)
- **US3 (Phase 5)**: Depends on Phase 2 (needs setUpClass + preview helper from Phase 1)
- **US4 (Phase 6)**: Depends on Phase 2 (needs setUpClass)
- **US5 (Phase 7)**: Depends on Phase 2 (needs setUpClass + helpers)
- **Polish (Phase 8)**: Depends on all user story phases being complete

### User Story Dependencies

- **US1 (P1)**: Independent — no cross-story dependencies
- **US2 (P1)**: Independent — no cross-story dependencies
- **US3 (P2)**: Independent — uses `preview_card_by_pin()` from Phase 1
- **US4 (P2)**: Independent — some overlap with US1/US2 patterns (log verification)
- **US5 (P3)**: Independent — creates its own separate batch

### Within Each User Story

All tests are in the same file, so tasks within a phase execute sequentially. Between phases, US1-US5 can theoretically be implemented in parallel by branching the test file, but the recommended approach is sequential (P1 → P1 → P2 → P2 → P3) since they share a single file.

### Parallel Opportunities

```text
Phase 1 parallel group:
  T001 (voucher_fixtures.py) || T002 (voucher_helpers.py)
  T003 must follow T002 (same file)

Phases 3-7 are sequential (all modify test_memora_voucher_card.py)
```

---

## Parallel Example: Phase 1

```bash
# These can run simultaneously (different files):
Task: "Add grant_components to make_product_grant() in voucher_fixtures.py"
Task: "Add get_pins_from_export() in voucher_helpers.py"

# Then sequentially (same file as T002):
Task: "Add preview_card_by_pin() in voucher_helpers.py"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (fixture + helper enhancements)
2. Complete Phase 2: Foundational (test class + setUpClass)
3. Complete Phase 3: US1 — Successful Redemption (4 tests)
4. **STOP and VALIDATE**: Run 4 tests, verify green
5. This proves the core redemption flow works

### Incremental Delivery

1. Phase 1 + 2 → Infrastructure ready
2. Add US1 (4 tests) → Core happy path verified
3. Add US2 (9 tests) → All error paths covered (13 tests total)
4. Add US3 (3 tests) → Preview verified (16 tests total)
5. Add US4 (5 tests) → Audit + security verified (21 tests total)
6. Add US5 (1 test) → Batch auto-close verified (22 tests total)
7. Phase 8 → Full suite validation

### Single Developer Flow

Since all tests share one file, work sequentially through phases. Each phase adds independently verifiable test methods. Commit after each phase checkpoint.

---

## Notes

- All 22 tests go in a SINGLE file: `test_memora_voucher_card.py`
- `setUpClass()` creates shared batch with ~30 allocated cards
- Each test consumes a unique card via `_next_card()` to avoid state conflicts
- Tests that modify shared state (batch status, season end_date) MUST use `try/finally` cleanup
- US5 creates its own separate 1-card batch (doesn't use the shared batch)
- Season `SEAS-00027` is reused (not created) to avoid MySQL partition constraints
- The `voucher_hmac_secret` must be in site_config.json for PIN operations to work
