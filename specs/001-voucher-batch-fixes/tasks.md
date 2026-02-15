# Tasks: Voucher Batch Counter Fixes & Auto-Close

**Input**: Design documents from `/specs/001-voucher-batch-fixes/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/batch-counters.md, quickstart.md

**Tests**: Not included (not explicitly requested in feature specification).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Exact file paths included in descriptions

## Path Conventions

All source paths are relative to repository root (`memora_admin/`):

```text
memora_admin/memora_admin/
├── doctype/memora_voucher_batch/memora_voucher_batch.json   # Schema
├── api/voucher.py                                            # redeem_voucher(), void_card()
├── services/voucher/batch_utils.py                           # NEW helper
└── tasks/season_expiration.py                                # expire_season_cards()
```

---

## Phase 1: Setup & Schema

**Purpose**: Add `expired_count` field to the Voucher Batch DocType and apply the migration.

- [ ] T001 Add `expired_count` field (Int, read_only, default "0") to `memora_admin/memora_admin/doctype/memora_voucher_batch/memora_voucher_batch.json` — insert after `voided_count` in both `field_order` array (after index of `voided_count`) and in the `fields` array (matching the pattern of `voided_count`: `fieldtype: "Int"`, `read_only: 1`, `default: "0"`, `label: "Expired Count"`, `fieldname: "expired_count"`)
- [ ] T002 Run `bench --site x.conanacademy.com migrate` to apply the schema change and verify with `bench --site x.conanacademy.com console` that `frappe.get_meta("Memora Voucher Batch").has_field("expired_count")` returns `True`

**Checkpoint**: Schema ready — `expired_count` field exists on Memora Voucher Batch.

---

## Phase 2: Foundational (Shared Helper)

**Purpose**: Create the shared `recount_and_maybe_close()` helper that ALL user story call sites will use. MUST complete before any wiring.

- [ ] T003 Create `memora_admin/memora_admin/services/voucher/batch_utils.py` implementing `recount_and_maybe_close(batch_name: str) -> dict` per the contract in `contracts/batch-counters.md`:
  1. Count cards by status using 4 `frappe.db.count("Memora Voucher Card", {"batch": batch_name, "status": X})` queries for Allocated, Redeemed, Void, Expired
  2. Update all 4 counter fields (`allocated_count`, `redeemed_count`, `voided_count`, `expired_count`) on the batch via single `frappe.db.set_value()` call with `update_modified=True`
  3. Check auto-close: if batch `status == "Active"` AND zero cards remain with status in `["Available", "Allocated"]` (use `frappe.db.count()`), set `status = "Closed"` via `frappe.db.set_value()`
  4. Return dict `{"allocated_count": int, "redeemed_count": int, "voided_count": int, "expired_count": int, "closed": bool}`
  5. Do NOT call `frappe.db.commit()` — caller manages transactions
  6. Do NOT modify `void_reason` or `generated_count`

**Checkpoint**: Helper exists and can be imported. All user story wiring can now begin.

---

## Phase 3: User Story 1 — Accurate Expired Card Tracking (Priority: P1)

**Goal**: After the season expiration job runs, batch counters (`expired_count`, `allocated_count`) reflect actual card states.

**Independent Test**: Create a batch with cards linked to an ended season → run `bench execute memora_admin.tasks.season_expiration.expire_season_cards` → verify `expired_count` matches expired cards and `allocated_count` is 0.

### Implementation for User Story 1

- [ ] T004 [US1] Wire `expire_season_cards()` in `memora_admin/memora_admin/tasks/season_expiration.py` to call `recount_and_maybe_close()` after expiring cards in each batch. In the loop that processes batches (around line 49-57), after the SQL UPDATE that sets card status to "Expired", add: `from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close` and call `recount_and_maybe_close(batch_name)` for each affected batch.

**Checkpoint**: Expired card tracking works — `expired_count` and `allocated_count` are accurate after expiration job. Acceptance scenarios US1-SC1 through US1-SC4 pass.

---

## Phase 4: User Story 2 — Automatic Batch Closure (Priority: P1)

**Goal**: Active batches automatically transition to Closed when all cards reach terminal states (Redeemed, Void, or Expired).

**Independent Test**: Create an Active batch with 2 cards → redeem one → void the other → verify batch status is "Closed" with no `void_reason`.

### Implementation for User Story 2

- [ ] T005 [P] [US2] Replace manual `redeemed_count` recount in `redeem_voucher()` at `memora_admin/memora_admin/api/voucher.py` (lines 685-689) with a call to `recount_and_maybe_close(card.batch)`. Remove the existing `frappe.db.count` + `frappe.db.set_value` for `redeemed_count` and replace with: `from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close` then `recount_and_maybe_close(card.batch)`
- [ ] T006 [P] [US2] Replace manual `voided_count` recount in `void_card()` at `memora_admin/memora_admin/api/voucher.py` (lines 354-356) with a call to `recount_and_maybe_close(card.batch)`. Remove the existing `frappe.db.count` + `frappe.db.set_value` for `voided_count` and replace with: `from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close` then `recount_and_maybe_close(card.batch)`

**Checkpoint**: Auto-close works from all 3 paths (redeem, void, expire). Acceptance scenarios US2-SC1 through US2-SC6 pass.

---

## Phase 5: Polish & Verification

**Purpose**: End-to-end validation and edge case verification.

- [ ] T007 Run quickstart.md verification: `bench --site x.conanacademy.com console` → confirm `expired_count` field exists, create test scenario per quickstart.md, verify counters and auto-close behavior
- [ ] T008 Verify idempotency: run `expire_season_cards()` twice for the same batch and confirm counter values are identical on both runs (recount, not increment)
- [ ] T009 Verify `void_batch()` in `memora_admin/memora_admin/api/voucher.py` (line ~274-326) is NOT affected — confirm it still sets `void_reason` and handles its own closure path independently of `recount_and_maybe_close()`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (schema must exist for counter updates)
- **US1 (Phase 3)**: Depends on Phase 2 (needs helper)
- **US2 (Phase 4)**: Depends on Phase 2 (needs helper). Independent of US1.
- **Polish (Phase 5)**: Depends on Phases 3 and 4

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) — no dependency on US2
- **User Story 2 (P1)**: Can start after Foundational (Phase 2) — no dependency on US1
- US1 and US2 can proceed **in parallel** after Phase 2 completes

### Within Each User Story

- US1: Single task (T004) — wire expiration job
- US2: Two parallel tasks (T005, T006) — wire redeem and void independently

### Parallel Opportunities

- **T005 + T006**: Both modify `voucher.py` but in non-overlapping regions (~330 lines apart). Can be executed in parallel by an LLM agent.
- **Phase 3 + Phase 4**: Entire user stories can run in parallel after foundational phase.

---

## Parallel Example: User Story 2

```bash
# Launch both call-site rewiring tasks in parallel (non-overlapping regions of voucher.py):
Task: "Replace redeemed_count recount in redeem_voucher() at api/voucher.py:685-689"
Task: "Replace voided_count recount in void_card() at api/voucher.py:354-356"
```

---

## Implementation Strategy

### MVP First (Both Stories — They're Both P1)

1. Complete Phase 1: Schema change + migrate
2. Complete Phase 2: Create shared helper
3. Complete Phase 3: Wire expiration job (US1 delivers value)
4. Complete Phase 4: Wire redeem + void (US2 delivers value)
5. **STOP and VALIDATE**: Run quickstart.md scenarios
6. Deploy

### Key Design Decisions (from research.md)

- **Recount, not increment**: All counter updates use `frappe.db.count()` — idempotent and safe after partial failures
- **Shared helper**: `recount_and_maybe_close()` centralizes logic to prevent counter drift
- **Auto-close only for Active**: Draft/Generated batches never auto-close
- **Race safety**: Read-check-then-write pattern; at most one transition succeeds
- **`void_batch()` unchanged**: Explicit admin closure with `void_reason` remains separate from auto-close

---

## Notes

- [P] tasks = non-overlapping modifications, no data dependencies
- [Story] label maps task to specific user story for traceability
- `void_batch()` is intentionally NOT modified — it has its own closure path (research.md R-5)
- `_update_batch_counters()` in allocation.py is intentionally NOT modified — it correctly handles allocation-only recounts (research.md R-6)
- Import `recount_and_maybe_close` at function scope (inside the calling function) to follow existing codebase patterns
- Max 1000 cards per batch — all COUNT queries are sub-millisecond
