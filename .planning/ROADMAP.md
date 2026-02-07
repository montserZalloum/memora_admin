# Roadmap: Memora Platform

## Milestones

- SHIPPED **v1.0 MVP** — Phases 1-7 (shipped 2026-02-02)
- SHIPPED **v1.1 Feature Expansion** — Phases 8-11 (shipped 2026-02-03)
- SHIPPED **v1.2 Plan System Enhancement** — Phase 12 (shipped 2026-02-03)
- SHIPPED **v1.2.1 Gap Closure** — Phase 13 (shipped 2026-02-03)
- SHIPPED **v1.3 Leaderboard Profiles & Admin Device Management** — Phases 14-20 (shipped 2026-02-07)
- ACTIVE **v1.4 Product Store** — Phases 21-23

## Phases

<details>
<summary>v1.0 MVP (Phases 1-7) — SHIPPED 2026-02-02</summary>

- [x] Phase 1: Project Foundation (3/3 plans) — completed 2026-02-01
- [x] Phase 2: Authentication (4/4 plans) — completed 2026-02-01
- [x] Phase 3: Access Control (4/4 plans) — completed 2026-02-01
- [x] Phase 4: Progress Tracking (3/3 plans) — completed 2026-02-02
- [x] Phase 5: Gamification (4/4 plans) — completed 2026-02-02
- [x] Phase 6: Content Pipeline (4/4 plans) — completed 2026-02-02
- [x] Phase 7: Sync Mechanisms (4/4 plans) — completed 2026-02-02

See: `.planning/milestones/v1.0-ROADMAP.md` for full details

</details>

<details>
<summary>v1.1 Feature Expansion (Phases 8-11) — SHIPPED 2026-02-03</summary>

- [x] Phase 8: Device Management (2/2 plans) — completed 2026-02-03
- [x] Phase 9: Game Sessions (4/4 plans) — completed 2026-02-03
- [x] Phase 10: Leaderboards (3/3 plans) — completed 2026-02-03
- [x] Phase 11: Scheduled Tasks (4/4 plans) — completed 2026-02-03

See: `.planning/milestones/v1.1-ROADMAP.md` for full details

</details>

<details>
<summary>v1.2 Plan System Enhancement (Phase 12) — SHIPPED 2026-02-03</summary>

- [x] Phase 12: Plan System Enhancement (4/4 plans) — completed 2026-02-03

See: `.planning/milestones/v1.2-ROADMAP.md` for full details

</details>

<details>
<summary>v1.2.1 Gap Closure (Phase 13) — SHIPPED 2026-02-03</summary>

- [x] Phase 13: Plan Cache Invalidation Fix (1/1 plans) — completed 2026-02-03

See: `.planning/phases/13-plan-cache-invalidation-fix/13-VERIFICATION.md` for details

</details>

<details>
<summary>v1.3 Leaderboard Profiles & Admin Device Management (Phases 14-20) — SHIPPED 2026-02-07</summary>

- [x] Phase 14: Profile Display Names (3/3 plans) — completed 2026-02-05
- [x] Phase 15: JWT Simplification (2/2 plans) — completed 2026-02-05
- [x] Phase 16: Admin Device Management (2/2 plans) — completed 2026-02-07
- [x] Phase 17: Progress API Optimization (2/2 plans) — completed 2026-02-05
- [x] Phase 18: Lesson Completion Status API (1/1 plan) — completed 2026-02-06
- [x] Phase 19: Stage Content Editor (2/2 plans) — completed 2026-02-07
- [x] Phase 20: Lesson Complete Pipeline Overhaul (4/4 plans) — completed 2026-02-07

See: `.planning/milestones/v1.3-ROADMAP.md` for full details

</details>

### v1.4 Product Store (Phases 21-23)

**Milestone Goal:** Players can discover available products for their plan and submit purchase requests, with admin approval flow granting content access.

- [ ] **Phase 21: Product Catalog API** — Players can browse available products for their plan with fast cached responses
- [ ] **Phase 22: Purchase Request Flow** — Players can submit purchase requests that create trackable transactions
- [ ] **Phase 23: Approval and Access Grant** — Approved transactions automatically create subscriptions and grant content access

## Phase Details

### Phase 21: Product Catalog API
**Goal**: Players can discover available products for their plan with rich product details and sub-100ms cached responses
**Depends on**: Nothing (first phase of v1.4; builds on existing plan infrastructure from v1.2)
**Requirements**: CTLG-01, CTLG-02, CTLG-03, CTLG-04, CTLG-05, CTLG-06
**Success Criteria** (what must be TRUE):
  1. Player hits the catalog endpoint and receives a list of Product Grants available for their plan, excluding any products they have already purchased
  2. Each product in the response includes bundle name, subject titles (alias_title), descriptions (notes), and price (price_list_rate)
  3. Products with pending purchase transactions display a "pending approval" status instead of a buy action
  4. Catalog response returns in under 100ms on subsequent requests (Redis cache hit)
  5. When a Product Grant is created, updated, or deleted in Frappe, the cached catalog for that plan refreshes on the next request
**Plans:** 2 plans
Plans:
- [ ] 21-01-PLAN.md — Catalog endpoint, service, models, and Frappe data API
- [ ] 21-02-PLAN.md — Cache invalidation wiring (Frappe hooks + pubsub + lifespan)

### Phase 22: Purchase Request Flow
**Goal**: Players can submit a purchase request for a product, creating a trackable Subscription Transaction with appropriate approval routing
**Depends on**: Phase 21 (catalog must exist so players know what to buy; pending status feeds back into catalog)
**Requirements**: PRCHS-01, PRCHS-02, PRCHS-03, PRCHS-04
**Success Criteria** (what must be TRUE):
  1. Player can submit a purchase request for a specific Product Grant and receive confirmation that the request was created
  2. The purchase request creates a Memora Subscription Transaction DocType record with status "Pending Approval"
  3. A purchase submitted via payment gateway is auto-approved (transaction status moves to "Approved" without admin intervention)
  4. A purchase submitted via manual payment method stays in "Pending Approval" until an admin approves it in Frappe Desk
  5. After submitting a purchase, the product shows "pending approval" in the catalog (no duplicate purchases)
**Plans**: TBD

### Phase 23: Approval and Access Grant
**Goal**: When a transaction is approved, the player automatically receives content access through subscription records and Redis access sync
**Depends on**: Phase 22 (transactions must exist to be approved)
**Requirements**: PRCHS-05
**Success Criteria** (what must be TRUE):
  1. When an admin approves a Subscription Transaction (or it is auto-approved), Memora Player Subscription records are created for each subject in the Product Grant
  2. On subscription creation, the player's access set in Redis is updated (subjects become accessible without re-login)
  3. The approved product no longer appears in the player's catalog (excluded as already purchased)
**Plans**: TBD

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Project Foundation | v1.0 | 3/3 | Complete | 2026-02-01 |
| 2. Authentication | v1.0 | 4/4 | Complete | 2026-02-01 |
| 3. Access Control | v1.0 | 4/4 | Complete | 2026-02-01 |
| 4. Progress Tracking | v1.0 | 3/3 | Complete | 2026-02-02 |
| 5. Gamification | v1.0 | 4/4 | Complete | 2026-02-02 |
| 6. Content Pipeline | v1.0 | 4/4 | Complete | 2026-02-02 |
| 7. Sync Mechanisms | v1.0 | 4/4 | Complete | 2026-02-02 |
| 8. Device Management | v1.1 | 2/2 | Complete | 2026-02-03 |
| 9. Game Sessions | v1.1 | 4/4 | Complete | 2026-02-03 |
| 10. Leaderboards | v1.1 | 3/3 | Complete | 2026-02-03 |
| 11. Scheduled Tasks | v1.1 | 4/4 | Complete | 2026-02-03 |
| 12. Plan System Enhancement | v1.2 | 4/4 | Complete | 2026-02-03 |
| 13. Plan Cache Invalidation Fix | v1.2.1 | 1/1 | Complete | 2026-02-03 |
| 14. Profile Display Names | v1.3 | 3/3 | Complete | 2026-02-05 |
| 15. JWT Simplification | v1.3 | 2/2 | Complete | 2026-02-05 |
| 16. Admin Device Management | v1.3 | 2/2 | Complete | 2026-02-07 |
| 17. Progress API Optimization | v1.3 | 2/2 | Complete | 2026-02-05 |
| 18. Lesson Completion Status API | v1.3 | 1/1 | Complete | 2026-02-06 |
| 19. Stage Content Editor | v1.3 | 2/2 | Complete | 2026-02-07 |
| 20. Lesson Complete Pipeline Overhaul | v1.3 | 4/4 | Complete | 2026-02-07 |
| 21. Product Catalog API | v1.4 | 0/2 | Not started | - |
| 22. Purchase Request Flow | v1.4 | 0/TBD | Not started | - |
| 23. Approval and Access Grant | v1.4 | 0/TBD | Not started | - |

**Total:** 20 phases complete (64 plans), 3 phases planned for v1.4
