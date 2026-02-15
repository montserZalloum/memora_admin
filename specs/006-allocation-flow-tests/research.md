# Research: Integration Tests — Allocation Flow

**Feature**: 006-allocation-flow-tests | **Date**: 2026-02-15

## R1: Allocation API Function Signatures & Behavior

**Decision**: Test the 4 whitelisted API functions directly (not via HTTP).

**Rationale**: All existing tests (`test_invoice.py`, `test_commission.py`) call API functions directly via Python imports. This is faster, gives direct access to Frappe context, and matches the established pattern.

**Functions under test** (from `memora_admin/api/allocation.py`):

| Function | Parameters | Returns | Key Validations |
|----------|-----------|---------|-----------------|
| `fill_cards(allocation_name, quantity=0)` | allocation name, optional qty limit | `{"filled_count": int}` | Must be Draft status; quantity=0 means all |
| `submit_allocation(allocation_name)` | allocation name | `{"status": str}` | Must be Draft; must have cards; cards must match batch |
| `approve_allocation(allocation_name)` | allocation name | `{"status": "Completed"}` | Must be Pending Approval |
| `reject_allocation(allocation_name, reject_reason="")` | allocation name, optional reason | `{"status": "Rejected"}` | Must be Pending Approval |

**Alternatives considered**: HTTP requests via `frappe.client.get_api()` — rejected because slower and adds network layer complexity without testing anything new.

## R2: State Machine (VALID_TRANSITIONS)

**Decision**: Test the state machine via the allocation DocType's `_validate_status_transition()` method, triggered by `.save()`.

**Rationale**: The VALID_TRANSITIONS dict in `memora_voucher_allocation.py` is the authoritative state machine. Testing via API functions covers happy paths; direct `.save()` with invalid status covers rejection paths.

**State machine** (from DocType class):
```
Draft → {Pending Approval, Approved, Cancelled}
Pending Approval → {Approved, Rejected}
Approved → {Completed}
Rejected → {} (terminal)
Completed → {} (terminal)
Cancelled → {} (terminal)
```

**Key insight**: The API functions enforce specific transitions (e.g., `submit_allocation` only allows Draft→Pending Approval or Draft→Approved→Completed). Testing invalid transitions requires directly manipulating `alloc.status` and calling `.save()` to trigger the validate hook.

## R3: Card State Mutations

**Decision**: Verify card state by querying individual card documents after allocation completion.

**Rationale**: The `_apply_allocation()` and `_apply_return()` methods use bulk SQL UPDATE, so we verify the outcome via Frappe ORM reads.

**Allocate completion** (`_apply_allocation`):
- Cards WHERE status IN ('Available', 'Allocated') → SET status='Allocated', library=customer, allocation=alloc.name, sale_model=alloc.sale_model

**Return completion** (`_apply_return`):
- Cards WHERE status='Allocated' → SET status='Available', library=NULL, allocation=NULL, sale_model=NULL, return_allocation=alloc.name

**Batch activation** (`_activate_batch_if_needed`):
- If batch.status == 'Generated' → SET status='Active'
- Only triggers on Allocate type, not Return

## R4: Batch Counter Update Mechanism

**Decision**: Use `assert_batch_counters()` helper for counter verification.

**Rationale**: The existing helper already handles reload + assertion pattern. The DocType's `_update_batch_counters()` recounts `allocated_count` from actual card data on every allocation completion.

**Counter update logic** (from `_update_batch_counters`):
```python
allocated_count = frappe.db.count("Memora Voucher Card", {"batch": self.batch, "status": "Allocated"})
frappe.db.set_value("Memora Voucher Batch", self.batch, "allocated_count", allocated_count)
```

**Key insight**: Only `allocated_count` is updated by allocation. Other counters (`redeemed_count`, `voided_count`, etc.) are updated by redemption/void operations.

## R5: Prepaid Invoice Integration

**Decision**: Test invoice creation indirectly through allocation completion, verifying Sales Invoice fields.

**Rationale**: Invoice creation is tested in detail in `test_invoice.py` (Phase 4). Allocation flow tests only need to verify: (1) invoice is created and linked, (2) amount reflects commission.

**Invoice creation path**:
1. Allocation completes → `on_update` hook fires
2. If `allocation_type == "Allocate"` and `sale_model == "Prepaid"`: calls `_create_prepaid_invoice()`
3. Which calls `create_prepaid_allocation_invoice(alloc.name)` from `invoice.py`
4. Resolves commission via priority chain, calculates net amount, creates submitted Sales Invoice
5. Links invoice to allocation via `db.set_value()`

**Error handling**: Invoice failure is caught and logged but does NOT roll back the allocation. Tests should verify both successful and Consignment (no invoice) paths.

## R6: Existing Test Infrastructure Compatibility

**Decision**: Use all existing fixtures and helpers without modification.

**Rationale**: The Phase 2 infrastructure (`voucher_fixtures.py`, `voucher_helpers.py`, `voucher_test_base.py`) provides everything needed:

| Need | Provided By |
|------|------------|
| Base test class with prereq checks | `VoucherTestCase` |
| Batch creation + generation | `make_batch()` + `generate_batch_sync()` |
| Customer/Library creation | `make_customer(requires_approval=, commission_type=, commission_value=)` |
| Allocation creation | `make_allocation(batch=, customer=, allocation_type=, sale_model=)` |
| Full allocation workflow | `fill_and_complete_allocation()` |
| Card status verification | `get_card_statuses()` |
| Batch counter verification | `assert_batch_counters()` |
| Product grant creation | `make_product_grant(season="SEAS-00027")` |

**No new fixtures or helpers needed.**

## R7: Test Isolation Strategy

**Decision**: Use `setUpClass` for shared expensive setup (batch generation), individual tests for specific scenarios.

**Rationale**: Batch generation is expensive (~1s per batch). Using `setUpClass` amortizes this cost across multiple tests in a class. For scenarios needing unique state (e.g., different library types), use separate test classes.

**Grouping strategy**:
- `TestFillCards`: Shared generated batch, individual allocations per test
- `TestSubmitAndApproval`: Shared generated batch, separate libraries (approval/no-approval)
- `TestCardStateOnAllocate`: Shared completed allocation, verify card fields
- `TestCardStateOnReturn`: Shared allocate→return cycle, verify card fields
- `TestBatchCountersAndStatus`: Shared generated batch, verify counter updates
- `TestPrepaidInvoiceOnAllocation`: Shared prepaid allocation, verify invoice
- `TestStateMachineEnforcement`: Shared allocation, attempt invalid transitions

## R8: Fill Cards Edge Cases

**Decision**: Test idempotent fill (re-fill replaces existing), zero-result fill, and sequential depletion.

**Rationale**: The `fill_cards()` function clears `allocation_cards = []` before filling, making it idempotent. Edge cases from spec:
- Re-fill: existing child rows cleared and replaced
- Zero cards: Return type targeting library with no allocated cards → filled_count=0
- Sequential: Multiple allocations from same batch deplete Available pool

**Code path** (from `allocation.py:66-68`):
```python
alloc.allocation_cards = []
for card in cards:
    alloc.append("allocation_cards", {"voucher_card": card.name})
```
