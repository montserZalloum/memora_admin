# Implementation Plan: Single Live Event Purchase

**Branch**: `052-live-event-purchase` | **Date**: 2026-03-18 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/052-live-event-purchase/spec.md`

## Summary

Complete the single live event purchase system by implementing three remaining gaps in the monetized-access foundation (051): purchase auto-cancellation after 30-minute expiry, atomic refund with credit note creation, and idempotent ERPNext Item auto-creation for paid events. The core purchase, payment confirmation, join-time gating, access state query, webhook handler, and Redis caching were delivered in 051 and require no changes.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 server-side)
**Primary Dependencies**: Frappe v15 ORM, ERPNext (Item, Sales Invoice, Credit Note), Redis (distributed locking, caching)
**Storage**: MariaDB (via Frappe ORM for all financial and DocType records)
**Testing**: pytest (unit), FrappeTestCase (integration), httpx.AsyncClient (FastAPI)
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Frappe app (dual architecture: Frappe admin + FastAPI sidecar)
**Performance Goals**: Access check < 2ms (via Redis), purchase operations < 30s end-to-end
**Constraints**: All financial operations via Frappe ORM (no raw SQL for invoices), Decimal for monetary math, JOD 3 decimal places
**Scale/Scope**: Single-event single-student purchases, ~10K concurrent events max

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevant | Compliant | Notes |
|-----------|----------|-----------|-------|
| I. Self-Healing Cache Architecture | Yes | PASS | Event-driven Redis invalidation for access/premium already in place (051). No new cache keys needed. Auto-cancel job operates on MariaDB only. |
| II. Sub-20ms Game API Performance | Yes | PASS | Join-time access check reads Redis only (051). All new work is server-side Frappe (scheduled jobs, hooks) — no hot-path changes. |
| III. Content Hierarchy Integrity | No | N/A | Not touching content hierarchy. |
| IV. Double-Gate Access Control | Yes | PASS | Paid event gate extends existing double-gate (051). No changes to gate logic needed. |
| V. Cryptographic Voucher Security | No | N/A | Not touching voucher PIN generation or verification. |
| VI. Financial Precision | Yes | PASS | Credit note creation uses Frappe ORM exclusively (constitution-mandated). Invoice amounts sourced from purchase record (already Decimal). ERPNext Item creation via Frappe ORM. |
| VII. Auditable State Machines | Yes | PASS | Purchase states: pending -> paid -> refunded / cancelled. Access states: active -> refunded / revoked. Both defined in 051. Auto-cancel adds the pending -> cancelled transition (already a valid state). No new states. |
| VIII. Test-First Coverage | Yes | PASS | TDD mandatory. Unit tests for expiry logic and item code generation, integration tests for refund + credit note lifecycle, doc event tests for idempotent item creation. |

**Gate result**: PASS (pre-design) | PASS (post-design) — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/052-live-event-purchase/
├── plan.md                            # This file
├── research.md                        # Phase 0: gap analysis + design decisions
├── data-model.md                      # Phase 1: delta data model (new fields, behaviors)
├── quickstart.md                      # Phase 1: getting started guide
├── contracts/                         # Phase 1: internal service contracts
│   ├── purchase-expiry.yaml           # Auto-cancel scheduled job contract
│   ├── refund-credit-note.yaml        # Refund + credit note service contract
│   └── item-auto-creation.yaml        # ERPNext Item doc event contract
└── tasks.md                           # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/
├── memora_admin/
│   ├── doctype/
│   │   ├── memora_live_event_purchase/
│   │   │   ├── memora_live_event_purchase.json  # MODIFY: add expires_at Datetime field
│   │   │   └── memora_live_event_purchase.py    # MODIFY: set expires_at on insert
│   │   └── memora_live_challenge_event/
│   │       └── memora_live_challenge_event.py   # MODIFY: remove erpnext_item_code from Redis meta
│   ├── services/
│   │   └── premium/
│   │       ├── event_purchase.py                # MODIFY: set expires_at = now + 30 min during creation
│   │       └── refund.py                        # MODIFY: add Credit Note creation to refund flow
│   ├── events/
│   │   └── item_sync.py                         # NEW: LIVE_EVENT_ITEM_CODE constant + ensure_shared_live_event_item()
│   └── tasks/
│       └── purchase_expiry.py                   # NEW: cancel_expired_purchases() scheduled job
├── fastapi_app/                                  # NO CHANGES (051 code sufficient)
├── setup.py                                       # MODIFY: add _ensure_live_event_service_item() in after_migrate
└── hooks.py                                      # MODIFY: add scheduler_events entry (removed before_save doc_event)
```

**Structure Decision**: All new work is Frappe-side (server hooks, scheduled jobs, ORM operations). The FastAPI sidecar requires no changes — the join-time access check, access state query, and webhook handler already work correctly with the existing data model. Adding `expires_at` to the purchase DocType is transparent to FastAPI.

## Existing Infrastructure (from 051, commit 378d022)

The following components were delivered in 051 and require **NO changes**:

| Component | Location | Spec Requirement | Status |
|-----------|----------|------------------|--------|
| Live Event Purchase DocType | `doctype/memora_live_event_purchase/` | FR-001 (partial) | Complete |
| Live Event Access DocType | `doctype/memora_live_event_access/` | FR-007 | Complete |
| Purchase creation API | `fastapi_app/api/v1/endpoints/event_access.py` | FR-001, FR-002, FR-003, FR-004 | Complete |
| Duplicate purchase prevention | `event_purchase.py` validates no active access + no pending purchase | FR-002, FR-003 | Complete |
| Atomic payment confirmation | `services/premium/event_purchase.py` | FR-005, FR-006, FR-015 | Complete |
| Join-time access check | `fastapi_app/services/live_challenge.py` | FR-008, FR-009 | Complete |
| Access state query | `GET /events/{event_id}/access-state` | FR-016 | Complete |
| Redis 3-tier caching | `fastapi_app/services/event_access.py` | SC-002 | Complete |
| Redis distributed locking (10s TTL) | `event_access_lock_key` | FR-012, FR-017 | Complete |
| Event-driven cache invalidation | `events/event_access_sync.py` | Principle I | Complete |
| Payment webhook (idempotent) | `fastapi_app/api/v1/endpoints/monetized_webhooks.py` | SC-003 | Complete |
| Refund (purchase + access status) | `services/premium/refund.py` | FR-011 (partial) | Partial |

## Implementation Gaps (this feature)

| ID | Gap | Spec Requirement | Priority | Approach |
|----|-----|-----------------|----------|----------|
| G1 | Purchase `expires_at` field | FR-001 (30-min expiry) | P1 | Add Datetime field to DocType JSON, set `now + 30 min` in `create_event_purchase()` |
| G2 | Auto-cancel expired purchases | FR-010 | P2 | New scheduled job `cancel_expired_purchases()` every 5 min, batch SQL UPDATE |
| G3 | Refund Credit Note | FR-011 (atomic cascade) | P2 | Extend `refund_event_purchase()` to create Credit Note via Frappe ORM |
| G4 | Shared ERPNext Item for invoices | FR-013 | P3 | Single `LIVE-EVENT-ACCESS` item, ensured at after_migrate and lazily before invoice creation |

## Complexity Tracking

No violations to justify — all changes follow existing patterns established in 051.
