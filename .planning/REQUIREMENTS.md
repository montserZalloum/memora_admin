# Requirements: Memora Platform — v1.4 Product Store

**Defined:** 2026-02-07
**Core Value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.

## v1.4 Requirements

Requirements for Product Store milestone. Players can discover and purchase available products from their plan.

### Catalog (Discovery)

- [x] **CTLG-01**: Player can view list of available Product Grants for their plan
- [x] **CTLG-02**: Already-purchased products are excluded from the catalog
- [x] **CTLG-03**: Each product displays bundle name, subject titles (from Plan Subject alias_title), descriptions (from notes), and price (from Item Price price_list_rate)
- [ ] **CTLG-04**: Products with pending transactions show a "pending approval" status badge
- [x] **CTLG-05**: Product catalog is cached in Redis per plan with sub-100ms response times
- [x] **CTLG-06**: Cache is invalidated when Product Grant is created, updated, or deleted

### Purchase

- [ ] **PRCHS-01**: Player can submit a purchase request for a Product Grant
- [ ] **PRCHS-02**: Purchase request creates a Memora Subscription Transaction with status "Pending Approval"
- [ ] **PRCHS-03**: Payment gateway transactions are auto-approved and access is granted immediately
- [ ] **PRCHS-04**: Manual payment transactions require admin approval in Frappe Desk before access is granted
- [x] **PRCHS-05**: On approval, Memora Player Subscription records are created and access is synced to Redis

## Future Requirements

### Payment Integration

- **PAY-01**: Integration with payment gateway for real-time payment processing
- **PAY-02**: Automatic receipt generation after purchase
- **PAY-03**: Refund flow for cancelled subscriptions

### Store Enhancements

- **STORE-01**: Product images/thumbnails on catalog cards
- **STORE-02**: Discount codes and promotional pricing
- **STORE-03**: Transaction history page for players
- **STORE-04**: Product recommendations based on academic plan

## Out of Scope

| Feature | Reason |
|---------|--------|
| Transaction history page | Not needed for v1.4 — store discovery only |
| In-app payment gateway integration | No gateway selected yet — purchase request only |
| Product images/thumbnails | Plan Subject doesn't have image fields currently |
| Product reviews/ratings | Not relevant for educational content purchases |
| Discount codes/coupons | Future feature — pricing is straightforward for v1.4 |
| Refund flow | Admin can manually revoke access; automated refunds deferred |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CTLG-01 | Phase 21 | Complete |
| CTLG-02 | Phase 21 | Complete |
| CTLG-03 | Phase 21 | Complete |
| CTLG-04 | Phase 22 | Pending |
| CTLG-05 | Phase 21 | Complete |
| CTLG-06 | Phase 21 | Complete |
| PRCHS-01 | Phase 22 | Pending |
| PRCHS-02 | Phase 22 | Pending |
| PRCHS-03 | Phase 22 | Pending |
| PRCHS-04 | Phase 22 | Pending |
| PRCHS-05 | Phase 23 | Complete |

**Coverage:**
- v1.4 requirements: 11 total
- Mapped to phases: 11
- Unmapped: 0

---
*Requirements defined: 2026-02-07*
*Last updated: 2026-02-08 after Phase 21 completion*
