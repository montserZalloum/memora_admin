# Requirements: Memora v3.0 Voucher Management System

**Defined:** 2026-02-13
**Core Value:** Students can purchase physical cards from libraries and instantly unlock educational content by entering a PIN code in the app.

## v1 Requirements

Requirements for v3.0 milestone. Each maps to roadmap phases.

### Batch Management (BATCH)

- [x] **BATCH-01**: Admin can create a Voucher Batch with quantity, pin_length (12/14/16), face_value, and one or more allowed Product Grants
- [x] **BATCH-02**: Admin can click "Generate" to produce all Voucher Cards for a batch (background job with chunked processing)
- [x] **BATCH-03**: Each generated card gets a sequential serial number (VCH-000001) and HMAC-SHA256 hashed PIN
- [x] **BATCH-04**: PIN generation uses `secrets` module for cryptographic randomness; HMAC uses secret key from `site_config.json`
- [x] **BATCH-05**: An encrypted export file (Fernet) is produced at generation time containing plaintext PINs for printing
- [x] **BATCH-06**: Admin can click "Export for Print" to download the decrypted CSV (serial_no, pin, product_names, face_value)
- [x] **BATCH-07**: Every export is logged in an append-only export_log child table (exported_by, exported_at, card_count)
- [x] **BATCH-08**: Admin can void an entire batch (all non-final cards → Void, batch → Closed, void_reason required)
- [x] **BATCH-09**: Batch status transitions: Draft → Generated → Active → Closed (enforced)

### Voucher Card (CARD)

- [x] **CARD-01**: Voucher Card DocType with serial_no (unique), pin_hmac (indexed, hidden), batch, library, allocation, status, and redemption fields
- [x] **CARD-02**: State machine enforced in code: Available → Allocated → Redeemed/Void/Expired; Redeemed/Void/Expired are final states
- [x] **CARD-03**: pin_hmac has a database index for fast lookup during redemption
- [x] **CARD-04**: Admin can void a single card (Available or Allocated → Void, void_reason required)
- [x] **CARD-05**: Redemption fields (redeemed_by, redeemed_at, redeemed_grant, subscription_transaction) are read-only in Desk

### Allocation & Distribution (ALLOC)

- [x] **ALLOC-01**: Voucher Allocation DocType supporting both Allocate and Return types
- [x] **ALLOC-02**: Admin can auto-fill cards into allocation by clicking "Fill Cards" (queries available/allocated cards by batch and quantity)
- [x] **ALLOC-03**: Admin can manually add/remove cards from the allocation child table before submitting
- [x] **ALLOC-04**: Allocation approval flow: libraries with `requires_approval=Yes` go through Pending Approval → Approved; others auto-approve on submit
- [x] **ALLOC-05**: On approved allocation: each card updates to Allocated with library, allocation, and sale_model fields set
- [x] **ALLOC-06**: Re-allocation supported: Allocated cards can be re-allocated to a different library
- [x] **ALLOC-07**: Return flow: Allocated cards return to Available (library, allocation, sale_model cleared; return_allocation set)
- [x] **ALLOC-08**: Custom fields on Customer DocType for per-library settings (voucher_requires_approval, commission type/value)

### Redemption API (REDEEM)

- [x] **REDEEM-01**: FastAPI `POST /api/v1/voucher/preview` endpoint — validates PIN, returns available grants (filters out already-owned)
- [x] **REDEEM-02**: FastAPI `POST /api/v1/voucher/redeem` endpoint — redeems card for a chosen product grant
- [x] **REDEEM-03**: Frappe whitelisted method `preview_voucher(pin_hmac, player_id)` with full validation chain
- [x] **REDEEM-04**: Frappe whitelisted method `redeem_voucher(pin_hmac, player_id, product_grant_id, ip_address)` with SELECT FOR UPDATE locking
- [x] **REDEEM-05**: Redemption creates a Subscription Transaction (payment_method="Voucher", status="Completed") triggering existing Phase 23 hook
- [x] **REDEEM-06**: Content unlocks instantly via existing on_subscription_change → SADD to memora:access:{player_id}
- [x] **REDEEM-07**: All error codes return machine-readable codes (INVALID_PIN, NOT_ALLOCATED, ALREADY_REDEEMED, EXPIRED, VOID, BATCH_INACTIVE, SEASON_INACTIVE, ALL_GRANTS_OWNED, GRANT_NOT_IN_BATCH, ALREADY_OWNED, RATE_LIMITED) — English per user decision
- [x] **REDEEM-08**: ALREADY_OWNED does not consume the card (stays Allocated — student can give card to someone else)
- [x] **REDEEM-09**: HMAC comparison uses `hmac.compare_digest()` (timing-attack safe)

### Security & Audit (SEC)

- [x] **SEC-01**: Rate limiting on redemption endpoints: 5 attempts/hour per player, 20 attempts/hour per IP (Redis-based)
- [x] **SEC-02**: Voucher Redemption Log DocType — immutable audit trail of every attempt (success + failure)
- [x] **SEC-03**: Redemption log captures: player, masked PIN (last 4 digits), card, library, batch, requested grant, status, failure_reason, IP, timestamp
- [x] **SEC-04**: Redemption Log is read-only after creation (no write, no delete permissions)
- [x] **SEC-05**: PINs never visible in Desk UI; pin_hmac field hidden from all views
- [x] **SEC-06**: `voucher_hmac_secret` stored in site_config.json (not in database or version control)

### Financial (FIN)

- [ ] **FIN-01**: Prepaid allocation creates a Sales Invoice for the library (quantity × face_value minus commission)
- [ ] **FIN-02**: Return of prepaid cards creates a Credit Note (negative Sales Invoice)
- [ ] **FIN-03**: Commission calculation: product-level override (Voucher Batch Grant) → library default (Customer fields) → zero
- [ ] **FIN-04**: Commission types: Percentage (face_value × rate / 100) or Fixed Amount per card
- [ ] **FIN-05**: Consignment monthly billing: scheduled job invoices redeemed consignment cards from the previous month
- [ ] **FIN-06**: Consignment returns require no financial action (cards were never invoiced)
- [ ] **FIN-07**: Each card tracks its invoice link (for both prepaid and consignment)

### Scheduled Jobs (SCHED)

- [ ] **SCHED-01**: Daily job: expire cards linked to ended/unpublished seasons (Available/Allocated → Expired, void_reason="Season Ended")
- [ ] **SCHED-02**: Monthly job (1st): generate consignment invoices for redeemed cards from previous month
- [x] **SCHED-03**: Rate limit keys auto-expire via Redis TTL (no cleanup job needed)

### Reports (RPT)

- [ ] **RPT-01**: Sales by Library report — redeemed cards per library with face value, commission, net revenue, invoice status
- [ ] **RPT-02**: Batch Performance report — card status distribution per batch with redemption rate and days until season end
- [ ] **RPT-03**: Consignment Reconciliation report — allocated/redeemed/uninvoiced cards per consignment library with amount due
- [ ] **RPT-04**: Security Audit report — failed redemption attempts per player/IP with failure reason breakdown

## v2 Requirements

Deferred to future milestone.

### Library Portal

- **PORTAL-01**: Libraries can view their allocated cards and statuses via Frappe Portal
- **PORTAL-02**: Libraries can view their sales (redeemed cards) and revenue
- **PORTAL-03**: Libraries can view their invoices and balances
- **PORTAL-04**: Libraries can request new card allocations

### Enhanced Analytics

- **ANLYT-01**: Dashboard with real-time batch and redemption metrics
- **ANLYT-02**: Geographic heat map of library sales performance

## Out of Scope

| Feature | Reason |
|---------|--------|
| Library Portal (Frappe Portal) | Separate phase after v3.0 — all library interaction mediated through admin for now |
| Digital/e-voucher delivery | Physical cards only for initial rollout; digital delivery is a future enhancement |
| QR code on cards | Serial + PIN is sufficient; QR adds print complexity without clear value at this scale |
| Multi-use cards (balance-based) | Each card is single-use — one product unlock. Balance cards add significant complexity |
| Payment gateway integration | Cards are physical purchases at libraries — no online payment needed |
| Card-level allocation (pick specific cards) | Admin sets quantity, system auto-fills. Manual card picking deferred to future |
| HMAC key rotation | Changing the key invalidates all existing PINs — documented as architectural constraint |
| Automated fraud detection | Manual review via Security Audit report is sufficient at 50-100 libraries |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| BATCH-01 | Phase 33 | Complete |
| BATCH-02 | Phase 34 | Complete |
| BATCH-03 | Phase 34 | Complete |
| BATCH-04 | Phase 34 | Complete |
| BATCH-05 | Phase 34 | Complete |
| BATCH-06 | Phase 34 | Complete |
| BATCH-07 | Phase 34 | Complete |
| BATCH-08 | Phase 34 | Complete |
| BATCH-09 | Phase 33 | Complete |
| CARD-01 | Phase 33 | Complete |
| CARD-02 | Phase 33 | Complete |
| CARD-03 | Phase 33 | Complete |
| CARD-04 | Phase 34 | Complete |
| CARD-05 | Phase 33 | Complete |
| ALLOC-01 | Phase 33 | Complete |
| ALLOC-02 | Phase 35 | Complete |
| ALLOC-03 | Phase 35 | Complete |
| ALLOC-04 | Phase 35 | Complete |
| ALLOC-05 | Phase 35 | Complete |
| ALLOC-06 | Phase 35 | Complete |
| ALLOC-07 | Phase 35 | Complete |
| ALLOC-08 | Phase 33 | Complete |
| REDEEM-01 | Phase 36 | Complete |
| REDEEM-02 | Phase 36 | Complete |
| REDEEM-03 | Phase 36 | Complete |
| REDEEM-04 | Phase 36 | Complete |
| REDEEM-05 | Phase 36 | Complete |
| REDEEM-06 | Phase 36 | Complete |
| REDEEM-07 | Phase 36 | Complete |
| REDEEM-08 | Phase 36 | Complete |
| REDEEM-09 | Phase 36 | Complete |
| SEC-01 | Phase 36 | Complete |
| SEC-02 | Phase 33 | Complete |
| SEC-03 | Phase 33 | Complete |
| SEC-04 | Phase 33 | Complete |
| SEC-05 | Phase 33 | Complete |
| SEC-06 | Phase 33 | Complete |
| FIN-01 | Phase 37 | Pending |
| FIN-02 | Phase 37 | Pending |
| FIN-03 | Phase 37 | Pending |
| FIN-04 | Phase 37 | Pending |
| FIN-05 | Phase 37 | Pending |
| FIN-06 | Phase 37 | Pending |
| FIN-07 | Phase 37 | Pending |
| SCHED-01 | Phase 38 | Pending |
| SCHED-02 | Phase 37 | Pending |
| SCHED-03 | Phase 36 | Complete |
| RPT-01 | Phase 38 | Pending |
| RPT-02 | Phase 38 | Pending |
| RPT-03 | Phase 38 | Pending |
| RPT-04 | Phase 38 | Pending |

**Coverage:**
- v1 requirements: 51 total
- Mapped to phases: 51
- Unmapped: 0

---
*Requirements defined: 2026-02-13*
*Last updated: 2026-02-14 — Phase 36 requirements marked Complete*
