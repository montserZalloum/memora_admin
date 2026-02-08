# Phase 22: Purchase Request Flow - Context

**Gathered:** 2026-02-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Players can submit a purchase request for a Product Grant, creating a Memora Subscription Transaction record with "Pending Approval" status. Manual/cash payment only (no gateway integration). Admin receives notification and can approve or reject. Pending/purchased products are hidden from the catalog. Approval triggers and access grants are Phase 23.

</domain>

<decisions>
## Implementation Decisions

### Request Submission
- Player sends: Product Grant ID + payment method
- Payment method: "Manual-Admin" only for now (field already exists on DocType with 3 options)
- Payment proof: optional image attachment (Claude's discretion on upload mechanism)
- Buyer is always the authenticated player (no proxy purchases)

### Status Flow
- Simple flow: Submit → Pending Approval
- No intermediate states (no "Submitted" → "Pending" progression)
- Admin can Approve or Reject (with reason on rejection)
- No player notification on rejection — player sees status change passively

### Admin Notification
- On new purchase submission: send notification + email to all System Manager role users
- Rich notification: include player name, product name, price, and link to transaction in Frappe Desk

### Duplicate Prevention
- Block duplicate submissions: if player has a pending transaction for the same Product Grant, return error
- Rejected transactions do NOT block: player can submit again after rejection
- Already-purchased products are excluded (handled by existing catalog logic from Phase 21)

### Catalog Integration (CTLG-04)
- Products with pending transactions are hidden from catalog (not shown with "pending" badge)
- No separate "My Purchases" endpoint — status inferred from catalog visibility
- Populate `memora:pending:{player_id}` Redis set on submission (catalog reads this for filtering)

### API Response
- Simple success message on submission: "Purchase request submitted successfully"
- No transaction ID or details returned to player

### Claude's Discretion
- Payment proof upload mechanism (FastAPI multipart vs Frappe file attachment)
- Exact Redis key structure for pending transactions
- Admin email template design
- Error message wording for duplicate/validation failures

</decisions>

<specifics>
## Specific Ideas

- The `payment_method` field already exists on the Subscription Transaction DocType with 3 options including "Manual-Admin"
- Keep it simple — this is a straightforward request-and-wait flow
- Admin notification should be actionable (link directly to the transaction for quick approve/reject)

</specifics>

<deferred>
## Deferred Ideas

- Payment gateway integration (online auto-approved payments) — future phase
- "My Purchases" transaction history endpoint — add to backlog
- Player notification on rejection — revisit if needed

</deferred>

---

*Phase: 22-purchase-request-flow*
*Context gathered: 2026-02-08*
