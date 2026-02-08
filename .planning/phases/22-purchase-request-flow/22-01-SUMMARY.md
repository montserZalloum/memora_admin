# Phase 22 Plan 01: Purchase Request Frappe Infrastructure Summary

**One-liner:** Subscription Transaction DocType updated with payment_proof field + Rejected status, Frappe whitelisted API for validated purchase request creation, admin email/desk notifications via doc_event hook.

## What Was Done

### Task 1: Update DocType schema and create Frappe whitelisted API
- Added `payment_proof` (Attach Image) field to Memora Subscription Transaction DocType
- Added `Rejected` status option to the status Select field
- Created `memora_admin/api/purchase.py` with `create_purchase_request` whitelisted API
- API validates: product grant exists + published, plan match, player profile exists, no duplicate pending
- Gets price from Item Price list, creates transaction with Pending Approval status
- Ran `bench migrate` successfully to apply schema changes
- **Commit:** `bdcbf1f`

### Task 2: Add admin notification hook and wire in hooks.py
- Created `memora_admin/events/purchase_sync.py` with `on_purchase_request_created` handler
- Sends desk realtime alert to Administrator on new purchase request
- Sends email to all enabled System Manager users with transaction details and Desk link
- Wired `after_insert` doc_event for Memora Subscription Transaction in hooks.py
- **Commit:** `0ddba6f`

## Deviations from Plan

None - plan executed exactly as written.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Product not found AND unpublished both return DoesNotExistError | Don't reveal existence of unpublished products to callers |
| payment_proof field is optional (not required) | Payment proof may not always be applicable |
| Email sent with `now=True` | Immediate delivery for time-sensitive admin review |

## Key Files

### Created
- `memora_admin/api/purchase.py` - Frappe whitelisted API for purchase request creation
- `memora_admin/events/purchase_sync.py` - Admin notification doc_event handler

### Modified
- `memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.json` - Added payment_proof field, Rejected status
- `memora_admin/hooks.py` - Wired Subscription Transaction after_insert doc_event

## Verification Results

- bench migrate: SUCCESS
- API importable via bench console: YES
- DocType has payment_proof field: YES (Attach Image)
- DocType has Rejected status: YES
- purchase_sync.py compiles: YES
- hooks.py has Subscription Transaction entry: YES

## Next Phase Readiness

Phase 22-02 (FastAPI purchase endpoint + Redis pending set) can proceed. It will:
- Call `memora_admin.api.purchase.create_purchase_request` via FrappeClient
- Write to `memora:pending:{player_id}` Redis set after successful Frappe call
- The Frappe API created here handles all validation and DocType creation

## Duration

~3.5 minutes (2026-02-08T11:06:25Z to 2026-02-08T11:09:52Z)
