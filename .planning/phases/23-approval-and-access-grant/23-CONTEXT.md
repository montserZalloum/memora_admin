# Phase 23: Approval and Access Grant - Context

**Gathered:** 2026-02-08
**Status:** Ready for planning

<domain>
## Phase Boundary

When an admin approves a Subscription Transaction, the system automatically creates Memora Player Subscription records for each subject in the Product Grant, syncs the player's Redis access set (immediate access without re-login), and cleans up the pending set. Rejection cleans up pending state so the player can re-submit. No player notifications — status is implicit via catalog visibility.

</domain>

<decisions>
## Implementation Decisions

### Approval workflow
- Use existing `status` field on Memora Subscription Transaction (no Frappe Submit/docstatus)
- One transaction at a time — no bulk approval from list view
- Access grant happens immediately (synchronous) on save when status changes to Approved
- No confirmation dialog — changing status and saving is the confirmation
- Approval is final — status cannot be changed back from Approved

### Rejection handling
- No rejection reason field — just change status to Rejected
- Immediate cleanup: SREM from `memora:pending:{player_id}` on rejection
- Player can re-submit a purchase request for the same product after rejection (product reappears in catalog)
- Rejected transaction records kept forever for audit trail

### Player notification
- No notification on approval or rejection
- Player discovers outcome implicitly: approved product disappears from catalog (already purchased), rejected product reappears in catalog

### Edge cases & safety
- All-or-nothing subscription creation: if any subject subscription fails, roll back all and keep transaction in current state
- Allow approval even if player already has active subscriptions for those subjects (overlapping subscriptions OK)
- Status transitions are flexible — trust the admin, no enforced state machine
- On approval: SREM from pending set + SADD to access set + create subscription records

### Claude's Discretion
- Hook implementation approach (validate/on_update/on_change)
- Error handling and logging strategy
- Whether to reuse existing `on_subscription_change` hook from access_sync.py or create new logic
- Redis operation ordering and atomicity approach

</decisions>

<specifics>
## Specific Ideas

- Existing hooks in `access_sync.py` (`on_subscription_change`) may already handle Redis access sync on subscription creation — verify and reuse if possible
- Pending set key pattern from Phase 22: `memora:pending:{player_id}`
- Phase 22 created the Subscription Transaction with "Pending Approval" status — approval changes this to "Approved" or "Rejected"

</specifics>

<deferred>
## Deferred Ideas

- Payment gateway auto-approval (mentioned in roadmap as deferred)
- Subscription revocation/cancellation workflow
- Player-facing purchase history or status checking endpoint
- Bulk approval for admins processing many transactions

</deferred>

---

*Phase: 23-approval-and-access-grant*
*Context gathered: 2026-02-08*
