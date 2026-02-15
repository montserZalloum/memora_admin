# Tasks: Voucher Test Infrastructure

**Input**: Design documents from `/specs/002-voucher-test-infra/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/factory-api.md, quickstart.md

**Tests**: Not explicitly requested in spec — test tasks omitted.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Frappe app module**: `memora_admin/memora_admin/`
- **Test infrastructure**: `memora_admin/memora_admin/tests/`
- **Existing services**: `memora_admin/memora_admin/services/voucher/`
- **Existing APIs**: `memora_admin/memora_admin/api/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create test module directory and package init

- [x] T001 Create test infrastructure package at `memora_admin/memora_admin/tests/__init__.py` (empty init to make it importable as a Python package)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prerequisite-checking base class that ALL voucher tests depend on — MUST be complete before US1/US2 work

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Implement `VoucherTestCase` base class in `memora_admin/memora_admin/tests/voucher_test_base.py`. Extends `FrappeTestCase`. In `setUpClass()`: call `super().setUpClass()`, then check `frappe.conf.get("voucher_hmac_secret")` exists — if missing, raise `unittest.SkipTest("voucher_hmac_secret not configured in site config. Run: bench --site <site> set-config voucher_hmac_secret <secret>")`. Then check `frappe.db.exists("Item", "MEMORA-VOUCHER-CARD")` — if missing, raise `unittest.SkipTest("MEMORA-VOUCHER-CARD Item not found. Create it in the test site before running voucher tests.")`. Import `unittest` at module level. Follow contract in `contracts/factory-api.md` exactly.

**Checkpoint**: Base class ready — US1/US2/US3 implementation can begin

---

## Phase 3: User Story 1 — Create Test Data with Fixture Factories (Priority: P1) 🎯 MVP

**Goal**: Provide 6 factory functions (`make_batch`, `make_product_grant`, `make_season`, `make_customer`, `make_player`, `make_allocation`) that create valid, saved documents with sensible defaults so test setup is ≤5 lines.

**Independent Test**: Import the fixture module and call each factory — each must return a saved document with correct defaults and relationships. Calling any factory 10 times must produce 10 distinct documents.

### Implementation for User Story 1

- [x] T003 [US1] Implement `make_season()` factory in `memora_admin/memora_admin/tests/voucher_fixtures.py`. Create the file with imports (`frappe`, `frappe.utils`). Function signature per `contracts/factory-api.md`: accepts `season_title` (auto-generated via `f"Test Season {frappe.utils.random_string(8)}"` if None), `season_seq=1`, `start_date` (defaults to `frappe.utils.today()`), `end_date` (defaults to `frappe.utils.add_days(today(), 365)`), `is_published=True`. Uses `frappe.get_doc({...}).insert(ignore_permissions=True)` pattern. Returns saved document.
- [x] T004 [US1] Implement internal `_make_grade()` and `_make_major()` helper factories in `memora_admin/memora_admin/tests/voucher_fixtures.py`. `_make_grade(grade_title=None)` creates `Memora Grade` with auto-generated title. `_make_major(major_title=None)` creates `Memora Major` with auto-generated title. Both use `random_string(8)` for uniqueness. Both use `.insert(ignore_permissions=True)`. These are internal helpers used by `make_product_grant()` and `make_player()`.
- [x] T005 [US1] Implement internal `_make_plan()` helper factory in `memora_admin/memora_admin/tests/voucher_fixtures.py`. Creates `Memora Academic Plan` requiring `grade` (name str) and `season` (name str). If not provided, calls `_make_grade()` and `make_season()` to create them. Returns saved document.
- [x] T006 [US1] Implement `make_product_grant()` factory in `memora_admin/memora_admin/tests/voucher_fixtures.py`. Signature per contract: `item_code="MEMORA-VOUCHER-CARD"`, `plan=None`, `is_published=True`, `season=None`, `grade=None`. If `plan` is None, calls `_make_plan(grade=grade, season=season)` to create one with its dependencies. Returns saved `Memora Product Grant` document.
- [x] T007 [US1] Implement `make_customer()` factory in `memora_admin/memora_admin/tests/voucher_fixtures.py`. Signature per contract: `customer_name=None` (auto-generated), `requires_approval=False`, `commission_type=None`, `commission_value=None`. Creates `Customer` doc with `customer_type="Company"`. After insert, sets voucher custom fields via `frappe.db.set_value()` per research.md R8 decision: `voucher_requires_approval`, `voucher_commission_type`, `voucher_commission_value`. Returns saved document.
- [x] T008 [US1] Implement `make_batch()` factory in `memora_admin/memora_admin/tests/voucher_fixtures.py`. Signature per contract: `batch_name=None` (auto-generated), `quantity=10`, `pin_length=12`, `face_value=5`, `grants=None` (list of Product Grant name strings), `status="Draft"`. If `grants` is provided, creates `Memora Voucher Batch Grant` child rows with `product_grant` set to each grant name. Returns saved `Memora Voucher Batch` document.
- [x] T009 [US1] Implement `make_player()` factory in `memora_admin/memora_admin/tests/voucher_fixtures.py`. Signature per contract: `display_name=None` (auto-generated), `plan=None`, `grade=None`, `major=None`, `season=None`. When deps are None, auto-creates them: `make_season()` → `_make_grade()` + `_make_major()` → `_make_plan(grade, season)`. Sets `avatar="pre"`. Returns saved `Memora Player Profile` document.
- [x] T010 [US1] Implement `make_allocation()` factory in `memora_admin/memora_admin/tests/voucher_fixtures.py`. Signature per contract: `batch` (required str), `customer` (required str), `allocation_type="Allocate"`, `sale_model="Prepaid"`. Creates `Memora Voucher Allocation` in Draft status. Returns saved document.
- [x] T011 [US1] Add module-level `__all__` export list to `memora_admin/memora_admin/tests/voucher_fixtures.py` listing all 6 public factories: `make_season`, `make_product_grant`, `make_customer`, `make_batch`, `make_player`, `make_allocation`.

**Checkpoint**: All 6 fixture factories are importable and return valid saved documents. Each call produces unique, non-colliding documents.

---

## Phase 4: User Story 2 — Execute Common Test Operations with Helpers (Priority: P1)

**Goal**: Provide 5 helper functions (`generate_batch_sync`, `get_card_statuses`, `fill_and_complete_allocation`, `redeem_card_by_pin`, `assert_batch_counters`) that encapsulate multi-step test operations so tests stay concise.

**Independent Test**: Call each helper in a test context — `generate_batch_sync()` must produce cards, `get_card_statuses()` must return accurate counts, `assert_batch_counters()` must correctly pass/fail assertions.

### Implementation for User Story 2

- [ ] T012 [US2] Implement `generate_batch_sync()` helper in `memora_admin/memora_admin/tests/voucher_helpers.py`. Create the file with imports. Per research.md R3: call `generate_cards_job(batch_name)` directly from `memora_admin.api.voucher` (not `generate_batch()` which enqueues). Function accepts `batch_name: str`, returns `None`. Any exception from the job function propagates directly.
- [ ] T013 [US2] Implement `get_card_statuses()` helper in `memora_admin/memora_admin/tests/voucher_helpers.py`. Accepts `batch_name: str`. Queries `Memora Voucher Card` where `batch=batch_name`, groups by `status`, returns `dict[str, int]` mapping status to count. Only includes statuses with count > 0. Use `frappe.get_all()` with `group_by` and `fields=["status", "count(name) as cnt"]`.
- [ ] T014 [US2] Implement `fill_and_complete_allocation()` helper in `memora_admin/memora_admin/tests/voucher_helpers.py`. Per research.md R5 and contract: accepts `batch_name`, `customer_name`, `quantity=0` (0=all), `sale_model="Prepaid"`. Steps: (1) create allocation via `make_allocation(batch_name, customer_name, sale_model=sale_model)` — import from voucher_fixtures, (2) call `fill_cards()` from `memora_admin.api.allocation` to populate card child rows, (3) call `submit_allocation()` from `memora_admin.api.allocation`. If customer has `voucher_requires_approval`, also call `approve_allocation()`. Returns the completed allocation document (reloaded).
- [ ] T015 [US2] Implement `redeem_card_by_pin()` helper in `memora_admin/memora_admin/tests/voucher_helpers.py`. Per research.md R4 and contract: accepts `pin` (plaintext), `player_id`, `grant_id`, `ip_address=""`. Computes HMAC via `compute_hmac(pin, frappe.conf.get("voucher_hmac_secret"))` from `memora_admin.services.voucher.generator`. Calls `redeem_voucher()` from `memora_admin.api.voucher` with the HMAC, player_id, grant_id, ip_address. Returns the result dict.
- [ ] T016 [US2] Implement `assert_batch_counters()` helper in `memora_admin/memora_admin/tests/voucher_helpers.py`. Per contract: accepts `test_case` (FrappeTestCase instance), `batch_name`, and keyword args `generated_count`, `allocated_count`, `redeemed_count`, `voided_count`, `expired_count` (all `int | None`, default None). Reloads batch from DB via `frappe.get_doc("Memora Voucher Batch", batch_name)`. For each non-None kwarg, calls `test_case.assertEqual(batch.<field>, expected, f"Expected <field>={expected}, got {batch.<field>}")`. Map kwarg names to batch fields: `generated_count`→`generated_count`, `allocated_count`→`allocated_count`, `redeemed_count`→`redeemed_count`, `voided_count`→`voided_count`, `expired_count`→`expired_count`.
- [ ] T017 [US2] Add module-level `__all__` export list to `memora_admin/memora_admin/tests/voucher_helpers.py` listing all 5 public helpers: `generate_batch_sync`, `get_card_statuses`, `fill_and_complete_allocation`, `redeem_card_by_pin`, `assert_batch_counters`.

**Checkpoint**: All 5 helpers execute successfully against a properly configured test site. `generate_batch_sync()` produces cards, `get_card_statuses()` returns accurate counts, `assert_batch_counters()` correctly passes/fails.

---

## Phase 5: User Story 3 — Verify Test Prerequisites Before Running Tests (Priority: P2)

**Goal**: Ensure test prerequisites (HMAC secret, Item record) are validated before tests execute, producing clear error messages rather than cryptic failures.

**Independent Test**: Run prerequisite check on a configured site (should pass) and verify it would skip with clear messages when prerequisites are missing.

### Implementation for User Story 3

> **Note**: The core `VoucherTestCase` base class was created in Phase 2 (T002). This phase validates it works correctly and ensures the skip messages are clear and actionable.

- [ ] T018 [US3] Validate `VoucherTestCase` prerequisite messages are descriptive and actionable in `memora_admin/memora_admin/tests/voucher_test_base.py`. Review the skip messages from T002: (1) HMAC secret message must include the `bench --site <site> set-config voucher_hmac_secret <secret>` command, (2) Item message must name the exact Item (`MEMORA-VOUCHER-CARD`) and state it needs to be created. Ensure both messages are formatted as single-line strings (no newlines that break test runner output). Adjust if needed.

**Checkpoint**: Running a test extending `VoucherTestCase` on a configured site proceeds normally. On a misconfigured site, tests are skipped with clear, actionable messages.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Ensure all modules are importable, consistent, and documented inline

- [ ] T019 Verify all 3 modules are importable without errors by running `python -c "from memora_admin.memora_admin.tests.voucher_fixtures import *; from memora_admin.memora_admin.tests.voucher_helpers import *; from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase"` from bench root
- [ ] T020 Run quickstart.md validation: create a minimal test file that follows the quickstart.md usage pattern (import all fixtures/helpers, extend `VoucherTestCase`, call `make_batch()` + `generate_batch_sync()` + `assert_batch_counters()`) and run it via `bench run-tests`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (`__init__.py` must exist) — BLOCKS all user stories
- **US1 Fixtures (Phase 3)**: Depends on Phase 2 (base class in same package)
- **US2 Helpers (Phase 4)**: Depends on Phase 3 (`fill_and_complete_allocation` imports `make_allocation` from fixtures)
- **US3 Prerequisites (Phase 5)**: Depends on Phase 2 (validates the base class)
- **Polish (Phase 6)**: Depends on all previous phases

### User Story Dependencies

- **US1 (P1 — Fixtures)**: Can start after Phase 2. No dependencies on other stories.
- **US2 (P1 — Helpers)**: Depends on US1 — `fill_and_complete_allocation()` imports `make_allocation()` from fixtures. `generate_batch_sync()` and other helpers are independent but live in the same file for cohesion.
- **US3 (P2 — Prerequisites)**: Can start after Phase 2 — validates the `VoucherTestCase` created there. Independent of US1/US2.

### Within Each User Story

- T003-T005 (season, grade/major, plan) must come before T006 (product_grant) and T009 (player) — dependency chain
- T006 (product_grant) should come before T008 (batch) — batch grants reference product grants
- T012 (generate_batch_sync) should come before T013-T016 — other helpers may need generated batches for their own testing
- T014 (fill_and_complete_allocation) depends on T012 (generate_batch_sync) and T010 (make_allocation)

### Parallel Opportunities

- **Phase 3**: T003+T007 can run in parallel (different entity chains — season vs customer)
- **Phase 4**: T012+T013+T016 can run in parallel (independent helpers in same file, but no cross-dependencies)
- **Phase 5**: T018 can run in parallel with Phase 4 (validates base class from Phase 2, independent of helpers)

---

## Parallel Example: User Story 1

```bash
# Wave 1 — Independent factories (different entity chains):
Task T003: "Implement make_season() in voucher_fixtures.py"
Task T007: "Implement make_customer() in voucher_fixtures.py"

# Wave 2 — Depend on season:
Task T004: "Implement _make_grade() and _make_major() in voucher_fixtures.py"

# Wave 3 — Depends on grade + season:
Task T005: "Implement _make_plan() in voucher_fixtures.py"

# Wave 4 — Depend on plan:
Task T006: "Implement make_product_grant() in voucher_fixtures.py"
Task T009: "Implement make_player() in voucher_fixtures.py"

# Wave 5 — Depend on product_grant:
Task T008: "Implement make_batch() in voucher_fixtures.py"
Task T010: "Implement make_allocation() in voucher_fixtures.py"

# Wave 6 — Finalize:
Task T011: "Add __all__ export list"
```

**Note**: Since all US1 tasks write to the same file (`voucher_fixtures.py`), true parallelism is limited. The waves above reflect logical dependency order — execute sequentially within a single agent for file coherence.

---

## Parallel Example: User Story 2

```bash
# Wave 1 — Independent helpers:
Task T012: "Implement generate_batch_sync() in voucher_helpers.py"
Task T013: "Implement get_card_statuses() in voucher_helpers.py"
Task T016: "Implement assert_batch_counters() in voucher_helpers.py"

# Wave 2 — Depends on fixtures:
Task T014: "Implement fill_and_complete_allocation() in voucher_helpers.py"
Task T015: "Implement redeem_card_by_pin() in voucher_helpers.py"

# Wave 3 — Finalize:
Task T017: "Add __all__ export list"
```

**Same note**: Single file — execute sequentially for coherence.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002)
3. Complete Phase 3: User Story 1 — Fixtures (T003-T011)
4. **STOP and VALIDATE**: Import all factories, call each one, verify saved documents
5. Fixture factories are usable immediately by other test files

### Incremental Delivery

1. Phase 1 + Phase 2 → Foundation ready
2. Add Phase 3 (US1 Fixtures) → Validate independently → MVP deliverable
3. Add Phase 4 (US2 Helpers) → Validate independently → Full test infra
4. Add Phase 5 (US3 Prerequisites) → Validate skip behavior → Complete
5. Phase 6 (Polish) → End-to-end quickstart validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- All 3 source files are in `memora_admin/memora_admin/tests/` directory
- Factories use `frappe.get_doc({...}).insert(ignore_permissions=True)` per research.md R1
- Unique names via `frappe.utils.random_string(8)` per research.md R2
- Sync generation via direct `generate_cards_job()` call per research.md R3
- HMAC via `compute_hmac()` from `services/voucher/generator.py` per research.md R4
- Custom fields on Customer set via `frappe.db.set_value()` after insert per research.md R8
- Commit after each task or logical group
