# Phase 35: Allocation & Distribution - Research

**Researched:** 2026-02-14
**Domain:** Frappe whitelisted API methods, form button patterns, document lifecycle hooks, approval workflows, bulk card status updates
**Confidence:** HIGH

## Summary

Phase 35 implements the allocation and distribution workflow for voucher cards: admin can fill an allocation with cards from a batch (auto-fill or manual), submit allocations through an approval workflow (conditional on library settings), re-allocate cards between libraries, and process card returns. This is purely a Frappe admin-side phase -- no FastAPI changes, no new Redis keys, no new DocTypes.

The existing codebase provides everything needed. The Voucher Allocation DocType (Phase 33) already has the correct schema with `allocation_type` (Allocate/Return), `status` (Draft/Pending Approval/Approved/Rejected/Completed/Cancelled), `allocation_cards` child table, and the `sale_model` field. The Voucher Card DocType already has `library`, `allocation`, `return_allocation`, and `sale_model` fields, plus a state machine allowing Available->Allocated and Allocated->Available transitions. The Customer DocType already has the `voucher_requires_approval` custom field from Phase 33. The `idx_batch_status` composite index on `tabMemora Voucher Card` (batch, status) is already created in `setup.py` for efficient allocation queries.

The implementation pattern follows the established codebase conventions: whitelisted API methods in `api/voucher.py` (or a new `api/allocation.py`), JS buttons on the allocation form, and document lifecycle hooks in the allocation Python controller for status-driven side effects.

**Primary recommendation:** Add a `fill_cards` whitelisted method that queries Available cards from the batch and populates the allocation's child table. Add `submit_allocation` and `approve_allocation` whitelisted methods that handle the approval workflow. Use `on_update` in the allocation controller to trigger card status updates when status transitions to Completed. Use direct SQL UPDATE for bulk card updates (consistent with the void_batch pattern from Phase 34-03 decision).

## Standard Stack

### Core (No New Dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Frappe v15 | 15.93.0 | DocType forms, whitelisted API, ORM, lifecycle hooks | Already installed, all existing patterns use it |
| MariaDB / InnoDB | Installed | Card queries, bulk status updates | Existing engine, `idx_batch_status` composite index already created |

### Frappe APIs Used

| API | Purpose | When to Use |
|-----|---------|-------------|
| `@frappe.whitelist()` | Expose methods to JS form buttons | fill_cards, submit_allocation, approve/reject |
| `frappe.db.get_list()` | Query Available cards from batch | fill_cards auto-fill logic |
| `frappe.db.sql()` | Direct SQL UPDATE for bulk card status changes | Allocation completion (up to ~200 cards per allocation) |
| `frappe.db.get_value()` | Read `voucher_requires_approval` from Customer | Approval flow branching |
| `frappe.db.set_value()` | Update batch counters (`allocated_count`) | After allocation/return completion |
| `frappe.db.count()` | Count allocated cards for batch counter | After allocation/return |
| `frm.call()` / `frappe.call()` | JS -> Python whitelisted method calls | Form buttons |
| `frm.add_custom_button()` | Add action buttons to allocation form | Fill Cards, Submit, Approve, Reject buttons |

### No New Dependencies Required

All functionality uses existing Frappe APIs and patterns already demonstrated in the codebase.

## Architecture Patterns

### Recommended File Structure

```
memora_admin/memora_admin/
├── api/
│   └── allocation.py              # NEW: fill_cards, submit, approve, reject, return whitelisted methods
├── doctype/
│   └── memora_voucher_allocation/
│       ├── memora_voucher_allocation.py   # MODIFY: add on_update hook for card status updates
│       └── memora_voucher_allocation.js   # MODIFY: add Fill Cards, Submit, Approve, Reject buttons
```

### Pattern 1: Whitelisted API Method Called from Form Button

**What:** A `@frappe.whitelist()` method in `api/allocation.py` is invoked by a custom button on the allocation form.
**When to use:** For actions that go beyond simple field changes (e.g., querying and populating child tables, bulk card updates).
**Source:** Verified pattern from `api/voucher.py` (generate_batch, void_batch, void_card).

```python
# api/allocation.py
@frappe.whitelist()
def fill_cards(allocation_name: str) -> dict:
    """Auto-fill available cards from the batch into the allocation's child table."""
    alloc = frappe.get_doc("Memora Voucher Allocation", allocation_name)

    if alloc.status != "Draft":
        frappe.throw("Can only fill cards in Draft status.")

    # Query available cards from the batch
    # For Allocate type: status = Available
    # For Return type: status = Allocated AND library = alloc.customer
    if alloc.allocation_type == "Allocate":
        cards = frappe.db.get_list(
            "Memora Voucher Card",
            filters={"batch": alloc.batch, "status": "Available"},
            fields=["name", "serial_no", "status"],
            order_by="name asc",
            page_length=0,  # all available
        )
    else:  # Return
        cards = frappe.db.get_list(
            "Memora Voucher Card",
            filters={"batch": alloc.batch, "status": "Allocated", "library": alloc.customer},
            fields=["name", "serial_no", "status"],
            order_by="name asc",
            page_length=0,
        )

    # Clear existing child rows and fill with queried cards
    alloc.allocation_cards = []
    for card in cards:
        alloc.append("allocation_cards", {"voucher_card": card.name})

    alloc.save(ignore_permissions=True)
    frappe.db.commit()

    return {"filled_count": len(cards)}
```

```javascript
// memora_voucher_allocation.js
frm.add_custom_button(__("Fill Cards"), function () {
    frappe.call({
        method: "memora_admin.memora_admin.api.allocation.fill_cards",
        args: { allocation_name: frm.doc.name },
        freeze: true,
        freeze_message: __("Filling cards..."),
        callback: function (r) {
            if (r.message) {
                frappe.show_alert({
                    message: __("{0} cards filled.", [r.message.filled_count]),
                    indicator: "green",
                });
                frm.reload_doc();
            }
        },
    });
});
```

### Pattern 2: Conditional Approval Workflow via on_update

**What:** The allocation controller's `on_update` hook checks when status changes to detect approval requirements and trigger card updates.
**When to use:** For status-driven side effects (approval branching, card status updates).
**Source:** Verified pattern from `memora_subscription_transaction.py` (`on_update` checking `has_value_changed("status")`).

```python
# memora_voucher_allocation.py
class MemoraVoucherAllocation(Document):
    def on_update(self):
        if not self.has_value_changed("status"):
            return

        if self.status == "Completed":
            if self.allocation_type == "Allocate":
                self._apply_allocation()
            else:
                self._apply_return()

    def _apply_allocation(self):
        """Update each card to Allocated status with library, allocation, sale_model."""
        card_names = [row.voucher_card for row in self.allocation_cards]
        if not card_names:
            return

        placeholders = ", ".join(["%s"] * len(card_names))
        frappe.db.sql(f"""
            UPDATE `tabMemora Voucher Card`
            SET status = 'Allocated', library = %s, allocation = %s, sale_model = %s,
                modified = NOW(), modified_by = %s
            WHERE name IN ({placeholders}) AND status = 'Available'
        """, [self.customer, self.name, self.sale_model, frappe.session.user] + card_names)

    def _apply_return(self):
        """Return cards to Available status."""
        card_names = [row.voucher_card for row in self.allocation_cards]
        if not card_names:
            return

        placeholders = ", ".join(["%s"] * len(card_names))
        frappe.db.sql(f"""
            UPDATE `tabMemora Voucher Card`
            SET status = 'Available', library = NULL, allocation = NULL,
                sale_model = NULL, return_allocation = %s,
                modified = NOW(), modified_by = %s
            WHERE name IN ({placeholders}) AND status = 'Allocated'
        """, [self.name, frappe.session.user] + card_names)
```

### Pattern 3: Submit Allocation with Approval Branching

**What:** A `submit_allocation` whitelisted method checks the library's `voucher_requires_approval` flag and routes to either Pending Approval or directly to Completed.
**When to use:** When the same action has different outcomes based on configuration.
**Source:** Customer `voucher_requires_approval` custom field verified in `customer_fields.py`.

```python
@frappe.whitelist()
def submit_allocation(allocation_name: str) -> dict:
    alloc = frappe.get_doc("Memora Voucher Allocation", allocation_name)

    if alloc.status != "Draft":
        frappe.throw("Allocation must be in Draft status to submit.")

    if not alloc.allocation_cards:
        frappe.throw("No cards in allocation. Use Fill Cards first.")

    requires_approval = frappe.db.get_value(
        "Customer", alloc.customer, "voucher_requires_approval"
    )

    if requires_approval:
        alloc.status = "Pending Approval"
    else:
        alloc.status = "Approved"

    alloc.save(ignore_permissions=True)

    # Auto-approve transitions immediately to Completed
    if alloc.status == "Approved":
        alloc.status = "Completed"
        alloc.save(ignore_permissions=True)

    frappe.db.commit()
    return {"status": alloc.status}
```

### Pattern 4: Re-allocation (Allocate cards already allocated to another library)

**What:** For re-allocation, the fill_cards query includes cards with status `Allocated` belonging to a specific batch. The admin creates a new allocation of type `Allocate` and fills it with cards that are currently allocated to a different library. When the new allocation completes, each card's library and allocation fields are updated.
**When to use:** When cards need to move between libraries without returning to Available first.

The key insight: the Voucher Card state machine already allows `Allocated -> Allocated` implicitly through `Available -> Allocated`. For re-allocation, we need an explicit path. Since `Allocated` -> `Available` is already allowed (for returns), and `Available` -> `Allocated` is allowed (for allocation), re-allocation can be modeled as:
1. The new allocation's `_apply_allocation` SQL UPDATE targets cards that are `Allocated` (not just `Available`), matching both the batch and the source library.
2. This means the SQL WHERE clause should be `status IN ('Available', 'Allocated')` for re-allocation use cases.

However, for safety, a simpler approach: allow the admin to manually add Allocated cards to a new allocation. The `_apply_allocation` method updates the card's library and allocation regardless of whether it was Available or Allocated, as long as the card is not in a terminal state.

### Pattern 5: Batch Counter Updates

**What:** After an allocation completes (or a return completes), update the parent batch's `allocated_count`.
**When to use:** To keep batch counters accurate.

```python
# After _apply_allocation or _apply_return:
allocated_count = frappe.db.count(
    "Memora Voucher Card", {"batch": self.batch, "status": "Allocated"}
)
frappe.db.set_value(
    "Memora Voucher Batch", self.batch,
    "allocated_count", allocated_count, update_modified=True
)
```

### Anti-Patterns to Avoid

- **ORM per-card save for bulk updates:** Loading and saving each card individually (frappe.get_doc + save) for 200 cards in an allocation would be slow. Use direct SQL UPDATE consistent with Phase 34-03 decision [34-03].
- **Using Frappe Workflow module:** The Frappe Workflow module (docstatus-based) does not map cleanly to this custom status field. Use explicit status transitions in Python (consistent with all existing voucher DocTypes).
- **Putting all logic in the controller:** Business logic like fill_cards and submit_allocation should be in `api/allocation.py` (whitelisted, callable from JS). The controller's `on_update` should only handle status-driven side effects.
- **Forgetting to commit:** All whitelisted methods that modify data must call `frappe.db.commit()` at the end (consistent with `void_batch`, `void_card` patterns).
- **Modifying the allocation after Draft:** The JS already makes fields read-only after Draft. The Python methods must also validate status before allowing changes.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Approval workflow state machine | Custom event system or Frappe Workflow module | Simple `voucher_requires_approval` check + status transitions in Python | Frappe Workflow is overkill for a two-branch flow; custom events add unnecessary complexity |
| Batch counter maintenance | Manual increment/decrement | `frappe.db.count()` with status filter | Counting is always accurate, no risk of drift from missed increment |
| Card availability query | Custom SQL with hand-built WHERE clauses | `frappe.db.get_list()` with filters | ORM handles escaping, permissions (with `ignore_permissions` where needed) |
| Form field locking by status | Custom permission rules | `frm.set_df_property("field", "read_only", 1)` in JS refresh | Already established pattern in the existing allocation JS |

## Common Pitfalls

### Pitfall 1: Race Condition in Card Allocation

**What goes wrong:** Two admins simultaneously allocate from the same batch. Both read the same Available cards, both try to allocate them, resulting in double-allocation.
**Why it happens:** `fill_cards` queries Available cards, but between the query and the UPDATE, another admin may have claimed those cards.
**How to avoid:** The `_apply_allocation` SQL UPDATE includes `WHERE status = 'Available'` (or `'Allocated'` for re-allocation). After the UPDATE, check `ROW_COUNT()` or verify the actual number of rows affected. If fewer cards were updated than expected, the allocation has a conflict. For Phase 35, this is acceptable because:
1. Memora is a single-admin system (System Manager role only)
2. Max batch size is 1,000 cards, and allocations are typically 50-200 cards
3. If concurrent allocation becomes a concern, `SELECT ... FOR UPDATE` can be added later

**Warning signs:** `allocated_count` on the batch exceeds `generated_count`.

### Pitfall 2: Approval Status Change Not Triggering Card Updates

**What goes wrong:** Admin changes allocation status to Completed but cards remain Available.
**Why it happens:** If the card update logic is in `validate()` instead of `on_update()`, the changes happen before the database commit and can be lost on rollback. Or if `frappe.db.set_value()` is used to change status (bypasses controller hooks).
**How to avoid:** Put card update logic in `on_update()` (runs after successful save). Use `doc.save()` for status changes (triggers controller hooks), not `frappe.db.set_value()`.
**Warning signs:** Allocation shows "Completed" but linked cards still show "Available" status.
**Blocker note:** STATE.md records: "_handle_approval() commit behavior needs integration test in Phase 36 (on_update vs after_insert for status=Completed)". The `MemoraSubscriptionTransaction` uses `on_update` for the same pattern and it works. Follow this established pattern.

### Pitfall 3: Return Allocation Not Clearing All Card Fields

**What goes wrong:** A return allocation sets `status = 'Available'` but forgets to clear `library`, `allocation`, or `sale_model` fields on the card. The card appears Available but still has stale library/allocation references.
**Why it happens:** Incomplete UPDATE statement in the return logic.
**How to avoid:** The return SQL UPDATE must explicitly set: `status='Available', library=NULL, allocation=NULL, sale_model=NULL, return_allocation=%s`. Verify all five fields are handled.
**Warning signs:** Card list view shows Available cards with non-empty Library column.

### Pitfall 4: Allocation Child Table Not Reflecting Card Status Changes

**What goes wrong:** After an allocation is Completed, the `card_status` column in the child table still shows "Available" because it uses `fetch_from` which only triggers on initial row creation.
**Why it happens:** Frappe's `fetch_from` only runs when the child row is first added/modified, not when the linked document changes.
**How to avoid:** This is cosmetic and acceptable. The child table's `card_status` is a snapshot at the time of allocation creation. The actual card status is on the card itself. Alternatively, re-fetch after completion, but this adds complexity for minimal value.
**Warning signs:** Allocation child table shows stale card statuses.

### Pitfall 5: Re-allocation Without Proper Source Validation

**What goes wrong:** Admin creates a re-allocation that moves cards between libraries, but the source library is not validated. Cards from any library could be moved.
**Why it happens:** The fill_cards or manual add does not restrict which Allocated cards can be added to a new allocation.
**How to avoid:** For re-allocation, the admin creates a standard "Allocate" type allocation targeting the new library. The fill_cards query for re-allocation should have a filter parameter for the source library, or the admin manually adds specific card names. The `_apply_allocation` UPDATE should handle both Available and Allocated cards in the WHERE clause.
**Warning signs:** Cards allocated to Library A appear under Library B without an audit trail.

### Pitfall 6: Batch Status Not Updated to Active

**What goes wrong:** After first successful allocation, the batch remains in "Generated" status instead of transitioning to "Active".
**Why it happens:** No logic to transition the batch from Generated -> Active when the first allocation completes.
**How to avoid:** In `_apply_allocation`, after updating cards, check if the batch status is "Generated" and transition it to "Active". The batch state machine allows `Generated -> Active`.
**Warning signs:** Batch remains in "Generated" status even after cards are allocated.

## Code Examples

### Fill Cards Query (Using Composite Index)

```python
# Uses idx_batch_status (batch, status) composite index from setup.py
# Source: Verified in memora_admin/memora_admin/setup.py:_ensure_voucher_card_indexes()
cards = frappe.db.get_list(
    "Memora Voucher Card",
    filters={"batch": alloc.batch, "status": "Available"},
    fields=["name", "serial_no", "status"],
    order_by="name asc",
    page_length=0,  # return all matching
    ignore_permissions=True,
)
```

### Bulk Card Status Update (Direct SQL)

```python
# Consistent with Phase 34-03 decision: direct SQL UPDATE for bulk operations
# Source: Established pattern from api/voucher.py:void_batch()
card_names = [row.voucher_card for row in self.allocation_cards]
placeholders = ", ".join(["%s"] * len(card_names))

frappe.db.sql(f"""
    UPDATE `tabMemora Voucher Card`
    SET status = 'Allocated',
        library = %s,
        allocation = %s,
        sale_model = %s,
        modified = NOW(),
        modified_by = %s
    WHERE name IN ({placeholders})
      AND status IN ('Available', 'Allocated')
""", [self.customer, self.name, self.sale_model, frappe.session.user] + card_names)
```

### Read Customer Approval Setting

```python
# Source: Verified custom field from memora_admin/memora_admin/custom/customer_fields.py
requires_approval = frappe.db.get_value(
    "Customer", alloc.customer, "voucher_requires_approval"
)
# Returns 0 or 1 (Check fieldtype)
```

### Form Button with Freeze UI

```javascript
// Source: Established pattern from memora_voucher_batch.js, memora_player_profile.js
frm.add_custom_button(__("Submit"), function () {
    frappe.call({
        method: "memora_admin.memora_admin.api.allocation.submit_allocation",
        args: { allocation_name: frm.doc.name },
        freeze: true,
        freeze_message: __("Submitting allocation..."),
        callback: function (r) {
            if (r.message) {
                frappe.show_alert({
                    message: __("Allocation {0}", [r.message.status]),
                    indicator: r.message.status === "Completed" ? "green" : "blue",
                });
                frm.reload_doc();
            }
        },
    });
}, __("Actions"));
```

### Allocation Controller with on_update Side Effects

```python
# Source: Verified pattern from memora_subscription_transaction.py
class MemoraVoucherAllocation(Document):
    def validate(self):
        self._validate_status_transition()
        self._update_quantity()

    def on_update(self):
        if not self.has_value_changed("status"):
            return
        if self.status == "Completed":
            if self.allocation_type == "Allocate":
                self._apply_allocation()
            elif self.allocation_type == "Return":
                self._apply_return()
```

## Implementation Design Decisions

### 1. API File Organization

**Decision:** Create `api/allocation.py` as a separate file from `api/voucher.py`.
**Rationale:** `voucher.py` handles batch generation and void operations. Allocation is a distinct workflow with 4-5 whitelisted methods. Separate files match the existing pattern (`api/auth.py`, `api/devices.py`, `api/products.py` are all separate).

### 2. Submit + Complete as Two-Step vs One-Step

**Decision:** Submit transitions to Pending Approval or Approved. A separate approve action transitions Approved -> Completed. For auto-approve libraries, submit goes directly Draft -> Approved -> Completed in one call.
**Rationale:** The status field has both "Approved" and "Completed" states. The Approved state exists so that an admin can review before applying card changes. For non-approval libraries, the flow is: Draft -> Approved -> Completed (auto). For approval libraries: Draft -> Pending Approval -> (admin approves) -> Approved -> Completed.

Actually, reviewing the existing status flow more carefully:
- `Draft` -> `Pending Approval` (needs approval) or `Approved` (auto-approve)
- `Pending Approval` -> `Approved` (admin approves) or `Rejected` (admin rejects)
- `Approved` -> `Completed` (card status updates applied)

For simplicity and to match the state machine, the `submit_allocation` method should:
1. For `requires_approval=True`: Draft -> Pending Approval
2. For `requires_approval=False`: Draft -> Completed directly (skip Pending Approval and Approved)

Then `approve_allocation` handles: Pending Approval -> Completed (applies card updates).

The "Approved" intermediate state between Pending Approval and Completed can be used if there is a need to separate approval from execution, but for this phase, approval immediately completes the allocation.

**Revised flow:**
- Auto-approve: Draft -> Completed (on_update triggers card updates)
- Requires approval: Draft -> Pending Approval -> Approved -> Completed (approve method sets Approved, then immediately Completed)

Wait -- the VALID_TRANSITIONS map shows `Approved -> Completed` as a valid transition, and `Draft -> Approved` is also valid. So the cleanest flow:
- Auto-approve: Draft -> Approved -> Completed (two saves in submit_allocation)
- Requires approval: Draft -> Pending Approval (submit), then Pending Approval -> Approved (approve_allocation), then Approved -> Completed (approve_allocation continues)

The on_update hook fires on each save. When status reaches Completed, it applies card updates.

### 3. Re-allocation as Standard Allocation

**Decision:** Re-allocation uses the same "Allocate" type allocation. The fill_cards method for re-allocation queries Allocated cards (not just Available) from the batch. The `_apply_allocation` UPDATE targets `status IN ('Available', 'Allocated')`.
**Rationale:** No new DocType or allocation_type needed. Re-allocation is just an allocation where source cards are Allocated instead of Available. The card state machine already allows Allocated -> Allocated implicitly (we just need to update the library and allocation fields).

### 4. Return as Separate Allocation Type

**Decision:** Return uses `allocation_type = "Return"`. Fill cards queries cards Allocated to a specific library. On Completed, cards go back to Available with fields cleared and `return_allocation` set.
**Rationale:** Already designed in Phase 33 schema with the Return option in the Select field.

### 5. Batch Status Transition to Active

**Decision:** When the first allocation completes for a batch, transition the batch from Generated to Active.
**Rationale:** The batch state machine has Generated -> Active. This provides a clear signal that distribution has begun.

## Workflow Summary

### Allocate Flow (ALLOC-02 through ALLOC-05)

```
1. Admin creates allocation: type=Allocate, batch, library, sale_model
2. Admin clicks "Fill Cards" -> queries Available cards, populates child table
3. Admin can manually add/remove cards from child table (ALLOC-03)
4. Admin clicks "Submit"
   a. Library requires_approval=True:
      Draft -> Pending Approval
      Admin clicks "Approve" -> Pending Approval -> Approved -> Completed
      on_update fires -> _apply_allocation: UPDATE cards to Allocated
   b. Library requires_approval=False:
      Draft -> Approved -> Completed
      on_update fires -> _apply_allocation: UPDATE cards to Allocated
5. Batch allocated_count updated
6. Batch status transitions Generated -> Active (if first allocation)
```

### Re-allocate Flow (ALLOC-06)

```
1. Admin creates new allocation: type=Allocate, batch, NEW library, sale_model
2. Admin manually adds specific card names that are currently Allocated to another library
   (or uses a re-allocate variant of fill_cards that queries Allocated cards from a source library)
3. Admin clicks "Submit" -> same approval flow as above
4. on_update fires -> _apply_allocation: UPDATE cards (now targeting 'Allocated' status too)
5. Card's library and allocation fields updated to new library and new allocation
```

### Return Flow (ALLOC-07)

```
1. Admin creates allocation: type=Return, batch, library
2. Admin clicks "Fill Cards" -> queries Allocated cards for this library+batch
3. Admin can manually add/remove cards
4. Admin clicks "Submit" -> same approval flow
5. on_update fires -> _apply_return: UPDATE cards to Available, clear fields, set return_allocation
6. Batch allocated_count updated (decremented)
```

## Open Questions

1. **Should fill_cards have a quantity parameter?**
   - What we know: ALLOC-02 says "queries available/allocated cards by batch and quantity"
   - Recommendation: Add an optional `quantity` parameter to fill_cards. If provided, LIMIT the query. If not provided, fill all available. The allocation form could have the `quantity` field (already exists, currently read-only/computed) repurposed as input for fill_cards. However, since quantity is computed from the child table length, it is simpler to add a separate prompt or dialog asking "How many cards to fill?" before calling fill_cards.
   - **Decision:** Add a quantity parameter to fill_cards. The JS button shows a prompt dialog asking for quantity. Default behavior fills the requested number of Available cards.

2. **Should the allocation prevent adding cards from different batches?**
   - What we know: The allocation has a `batch` Link field (required). All cards in the child table should belong to that batch.
   - Recommendation: Validate in `validate()` that all child table cards belong to the allocation's batch. This prevents data integrity issues.
   - **Decision:** Add validation in the controller.

3. **How should re-allocation identify source cards?**
   - What we know: ALLOC-06 says "Allocated cards can be re-allocated to a different library"
   - The simplest approach: admin manually adds card names to the child table (ALLOC-03 already supports this). Cards that are Allocated to a different library can be added. The `_apply_allocation` UPDATE handles both Available and Allocated source states.
   - **Decision:** The fill_cards method for Allocate type queries Available cards. For re-allocation, admin uses manual add (ALLOC-03). The UPDATE in _apply_allocation accepts `status IN ('Available', 'Allocated')`.

## Sources

### Primary (HIGH confidence)
- Existing codebase: `memora_voucher_allocation.py` -- status transition map, validate hook
- Existing codebase: `memora_voucher_allocation.js` -- read-only field pattern by status
- Existing codebase: `memora_voucher_allocation.json` -- schema with all required fields
- Existing codebase: `memora_voucher_allocation_card.json` -- child table schema with fetch_from
- Existing codebase: `memora_voucher_card.py` -- VALID_TRANSITIONS allows Available<->Allocated
- Existing codebase: `memora_voucher_card.json` -- library, allocation, return_allocation, sale_model fields
- Existing codebase: `api/voucher.py` -- whitelisted method patterns (void_batch bulk SQL UPDATE)
- Existing codebase: `memora_subscription_transaction.py` -- on_update + has_value_changed approval pattern
- Existing codebase: `custom/customer_fields.py` -- voucher_requires_approval Check field
- Existing codebase: `setup.py:_ensure_voucher_card_indexes()` -- idx_batch_status composite index
- Existing codebase: `memora_voucher_batch.json` -- allocated_count field (read-only, Int)
- Existing codebase: `memora_voucher_batch.py` -- VALID_TRANSITIONS allows Generated->Active
- Context7 `/websites/frappe_io_framework_user_en` -- frappe.db.get_list, set_value, whitelist, on_update lifecycle

### Secondary (MEDIUM confidence)
- Context7 `/websites/frappe_io_framework_user_en` -- frm.call, frappe.call patterns
- STATE.md blocker: "_handle_approval() commit behavior needs integration test in Phase 36"

### Tertiary (LOW confidence)
- None -- all findings verified against codebase or official Frappe docs

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all tools are existing Frappe patterns verified in codebase, no new dependencies
- Architecture: HIGH -- patterns directly extracted from `api/voucher.py`, `subscription_transaction.py`, and existing allocation DocType schema
- Pitfalls: HIGH -- race condition is acknowledged and acceptable for single-admin use case; all other pitfalls have verified prevention strategies
- Workflow design: HIGH -- state machine already defined in Phase 33, card fields already exist, Customer approval flag already created

**Research date:** 2026-02-14
**Valid until:** 2026-03-14 (stable -- Frappe DocType patterns change slowly, and all schema is already in place)
