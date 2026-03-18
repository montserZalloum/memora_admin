# Research: Single Live Event Purchase

## R1: Purchase Expiry Implementation

**Decision**: Add `expires_at` Datetime field to Memora Live Event Purchase DocType. Set `now_datetime() + timedelta(minutes=30)` at creation time. Scheduled job every 5 minutes scans and cancels expired pending purchases via batch SQL UPDATE.

**Rationale**:
- 30-minute window is spec-mandated (FR-001)
- 5-minute scan frequency gives max 5-minute delay beyond expiry — acceptable for non-critical cleanup
- Batch SQL UPDATE is efficient and avoids per-doc overhead
- Follows existing pattern: `expire_season_cards()` in `tasks/season_expiration.py`

**Alternatives Considered**:
- Redis TTL-based expiry: Rejected — purchase is a Frappe DocType, canonical state must live in MariaDB
- Per-purchase background job (enqueue with delay): Rejected — creates too many scheduled tasks at scale, unreliable if worker restarts
- 1-minute scan frequency: Rejected — unnecessary load for a 30-minute window; 5-min worst case is acceptable
- Frappe ORM `get_all` + loop: Rejected — batch SQL UPDATE is simpler and avoids loading full docs

## R2: Credit Note Creation in Refund Flow

**Decision**: Extend `refund_event_purchase()` in `services/premium/refund.py` to create a Credit Note via `frappe.new_doc("Sales Invoice")` with `is_return=1` and `return_against=original_invoice_name`. Follows exact pattern from `voucher/invoice.py:create_credit_note()`.

**Rationale**:
- Constitution Principle VI mandates Frappe ORM for all invoice / credit note creation
- Existing `create_credit_note()` in `voucher/invoice.py` provides the proven pattern
- Credit Note must be linked to original Sales Invoice (`return_against`) for accounting reconciliation
- Atomicity: all operations within same Frappe request transaction (purchase status + access status + credit note)
- Credit note is only created when the purchase has an associated `erpnext_invoice` — graceful no-op otherwise

**Alternatives Considered**:
- Reuse `create_credit_note()` from `voucher/invoice.py` directly: Rejected — that function uses `MEMORA-VOUCHER-CARD` item code; event purchase uses the event's `erpnext_item_code`. Cleaner to inline the pattern in refund.py with event-specific parameters.
- Extract a generic `create_credit_note()` helper: Considered but over-engineering for two callers with different item codes. Can be refactored later if a third caller appears.
- Separate credit note creation as a post-refund step: Rejected — FR-011 requires atomic all-or-nothing. Splitting creates risk of partial state.

## R3: ERPNext Item Auto-Creation

**Decision**: Register `before_save` doc event hook on Memora Live Challenge Event. When `is_paid=1` and `erpnext_item_code` is empty (or item doesn't exist), create an ERPNext Item with code `LIVE-EVENT-{event_name}` and set it on the event. When `is_paid=0`, do nothing (FR-014: never delete existing items).

**Rationale**:
- `before_save` runs after `doc.name` is assigned (Frappe lifecycle: `_set_name` -> `before_validate` -> `validate` -> `before_save` -> DB write), so `doc.name` is available for item code generation
- Item code pattern `LIVE-EVENT-{doc.name}` is deterministic, making idempotency check trivial: `frappe.db.exists("Item", item_code)`
- Item configuration follows `_ensure_voucher_service_item()` pattern in `setup.py`: Services group, Nos UOM, non-stock, sales item
- Setting `doc.erpnext_item_code` in `before_save` means the value is included in the same DB write — no extra round-trip
- Handles both new paid events (insert) and existing events where `is_paid` toggles 0 -> 1

**Alternatives Considered**:
- `after_insert` only: Rejected — must also handle `is_paid` toggling from 0 -> 1 on existing events
- `on_update` (after DB write): Rejected — would require a separate `db.set_value` to update `erpnext_item_code`, which triggers another `on_update` (recursion risk)
- Admin manual item creation: Rejected — spec explicitly requires automatic creation (User Story 6)
- Shared item code for all events (e.g., `MEMORA-LIVE-EVENT`): Rejected — each event needs a distinct item for invoice line-item clarity and per-event financial reporting
- Include event price in item: Rejected — price is on the purchase, not the item. Item rate is set per invoice line.

## R4: Existing Infrastructure Verification

**Decision**: No changes needed to the following 051 components after thorough verification.

| Component | Spec Requirement | Verification |
|-----------|-----------------|-------------|
| Purchase creation with duplicate check | FR-002, FR-003 | `create_event_purchase()` validates no active access + no pending purchase |
| Plan eligibility check at purchase time | FR-004 | `create_event_ticket_purchase()` in FastAPI validates plan before calling Frappe |
| Atomic payment confirmation | FR-005, FR-006 | `confirm_event_purchase()` marks paid + creates access + creates invoice in one transaction |
| One active access per (player, event) | FR-007 | Validation in Live Event Access DocType + duplicate check in purchase flow |
| Join-time access via Redis only | FR-008 | `_check_paid_event_access()` reads from EventAccessService (3-tier cache) |
| Plan eligibility at join time | FR-009 | `join()` checks `eligible_plans` before paid-event gate |
| Per-player-per-event Redis lock with TTL | FR-012, FR-017 | `event_access_lock_key` with SET NX EX 10 (10s auto-release) |
| Invoice after payment only | FR-015 | Invoice created inside `confirm_event_purchase()`, not at purchase creation |
| Access state query | FR-016 | `GET /events/{event_id}/access-state` returns full state object |
| Webhook idempotency | SC-003 | `monetized:webhook:monetized:{key}` with 24h TTL prevents duplicate processing |

**Rationale**: All core purchase-to-access flow requirements are already satisfied. The 052 feature is strictly about completing three peripheral gaps (expiry, credit note, item creation) without touching the critical path.
