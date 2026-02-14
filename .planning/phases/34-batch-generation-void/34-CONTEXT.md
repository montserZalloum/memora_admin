# Phase 34: Batch Generation & Void - Context

**Gathered:** 2026-02-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Admin can create a batch, generate all cards with cryptographically secure PINs via background job, download a decrypted CSV for physical card printing, and void batches or individual cards. Batch status transitions enforced: Draft → Generated → Active → Closed.

Allocation, distribution, and redemption are separate phases (35, 36).

</domain>

<decisions>
## Implementation Decisions

### Generation job behavior
- Maximum batch size: up to 1,000 cards — no need for complex chunking strategies
- If generation fails midway, rollback all partially created cards — batch stays Draft, admin retries from scratch
- No resume/partial-generation support needed

### Export & print security
- Fernet encryption key derived from existing `voucher_hmac_secret` in site_config — one secret to manage (use KDF to derive Fernet key from HMAC secret)
- Encrypted export file auto-deleted after a configurable period (e.g., 30 days) — reduces risk window
- Only System Manager role can export (download decrypted CSV)
- Unlimited re-exports allowed — every export logged in append-only export_log child table for full audit trail
- When a batch is voided, its encrypted export file is deleted immediately (PINs are worthless)

### Void operations
- Batch void: voids ALL non-final cards (Available AND Allocated) — batch becomes Closed
- Void is permanent — no undo/un-void capability
- Void reason is free text (no dropdown) — required for both batch and individual card void
- Voiding a batch also deletes the encrypted export file

### Serial number scheme
- Globally unique sequential numbers across all batches — VCH-000001 format
- Fixed VCH- prefix (not configurable)
- 6-digit zero-padded (supports up to 999,999 total cards)
- Serial numbers never reused — voided serials leave gaps, every number is unique across all time

### Claude's Discretion
- Progress reporting mechanism during generation (frappe.publish_progress vs status field polling)
- Export file timing (generate during card creation when PINs are in memory, vs on-demand)
- Auto-delete period for encrypted export files (exact number of days)
- Background job implementation details (frappe.enqueue patterns)

</decisions>

<specifics>
## Specific Ideas

- Batch sizes are small (up to 1,000) — keep the generation job simple
- Rollback-on-failure ensures no orphaned/partial card sets exist
- Single secret management (derive Fernet from HMAC key) reduces operational overhead
- System Manager-only export is the tightest access control for plaintext PINs

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 34-batch-generation-void*
*Context gathered: 2026-02-14*
