# PRD: Practice-Standalone Subscription

## 1. Overview

The Practice feature (spaced-repetition review arena) currently requires a full content subscription
(`SUB-{subject_id}` or `TRK-{track_id}`) to access. This means a student must purchase full subject
access to use Practice, even if they only want to review items they already know.

This feature introduces **practice-only subscriptions** — a separate, lower-priced tier that grants
access to Practice for a specific subject without granting access to the full lesson content.

## 2. Problem Statement

- Practice is currently bundled with full content access. There is no way to sell it independently.
- Some students or parents want to pay only for the review/practice tool, not the full curriculum.
- Admins cannot create voucher batches that grant practice-only access per subject.

## 3. Goals

- Allow admins to create `Memora Product Grant` entries that grant practice-only access to a subject.
- Allow voucher batches to be tied to practice-only grants.
- When a student redeems such a voucher, they get practice access for that subject — not full content.
- A full `SUB-{subject_id}` subscription continues to implicitly include practice access (no regression).
- The change must be per-subject (not global).

## 4. Non-Goals

- No new UI screens for students — access control is transparent at the API level.
- No changes to the spaced-repetition logic (FSRS), review items, or practice session mechanics.
- No changes to the voucher redemption flow, subscription transaction lifecycle, or Redis sync hooks.
- No global "practice pass" (all subjects at once) — out of scope for this iteration.
- No plan-level practice entitlement — only explicit grant-based access is added here.

## 5. Access Key Design

### New key prefix

```
PRAC-SUB-{subject_id}
```

Examples:
- `PRAC-SUB-SUBJ-00001`
- `PRAC-SUB-MATH-GRADE-5`

This key lives in the same Redis set as existing access keys:
`memora:access:{player_id}` (type: SET).

### Access priority order for Practice (updated)

1. **Full subject grant**: player has `SUB-{subject_id}` → full access, includes practice ✓
2. **Track grant**: player has `TRK-{track_id}` for any track in the subject → includes practice ✓
3. **Practice-only grant**: player has `PRAC-SUB-{subject_id}` → practice access only ✓
4. **Plan free subjects**: subject is free on the player's plan → practice access allowed ✓
5. **Free content fallback**: subject has free units/topics → practice scoped to free content only ✓
6. **No grant**: 403 Forbidden

Note: Steps 1–2 already exist. Step 3 is new. Steps 4–5 are unchanged.

## 6. Architecture Changes

### 6.1 `Memora Grant Component` DocType

**File:** `memora_admin/memora_admin/doctype/memora_grant_component/memora_grant_component.json`

Add a new field `key_type` (Select) with options:
- `full` (default) — grants `SUB-*` or `TRK-*` as today
- `practice` — grants `PRAC-SUB-*` (only valid when `target_doctype = "Memora Subject"`)

Add a server-side validation: if `key_type = practice`, then `target_doctype` must be
`"Memora Subject"` (practice is per-subject, not per-track).

### 6.2 `get_grant_keys()` — `memora_admin/memora_admin/api/products.py`

Extend the key generation logic:

```python
# Current logic:
if component.target_doctype == "Memora Subject":
    keys.append(f"SUB-{component.target_name}")
elif component.target_doctype == "Memora Track":
    keys.append(f"TRK-{component.target_name}")

# New logic:
if component.target_doctype == "Memora Subject":
    if component.key_type == "practice":
        keys.append(f"PRAC-SUB-{component.target_name}")
    else:
        keys.append(f"SUB-{component.target_name}")
elif component.target_doctype == "Memora Track":
    keys.append(f"TRK-{component.target_name}")
```

No other changes to this function. The rest of the redemption chain is untouched.

### 6.3 Practice endpoint — `fastapi_app/api/v1/endpoints/practice.py`

The current access check (lines 53–81) checks subject → track → free content.

Add a third check after the track check and before the free content fallback:

```python
# New: check practice-only grant
has_practice_grant = await access_service.check_access(
    player_id, f"PRAC-SUB-{body.subject_id}"
)
if has_practice_grant:
    # Allow full practice scope (all review items for this subject)
    scope = PracticeScope.full(subject_id=body.subject_id)
    # continue to practice session creation
```

If the player only has `PRAC-SUB-*` (no full subject/track grant), they can practice all items
for the subject — but they cannot access lesson content (separate endpoints, no change needed).

### 6.4 Redis Keys

No new Redis key types. `PRAC-SUB-*` strings live inside the existing
`memora:access:{player_id}` SET. The existing sync hooks, hydration, and TTL handling all work
automatically — they are key-value agnostic.

**Reference:** `fastapi_app/core/redis_keys.py`
- No new key builder function needed (key is constructed inline in `products.py` and `practice.py`).
- Add a comment documenting `PRAC-SUB-{subject_id}` as an access key convention alongside
  the existing `SUB-*` / `TRK-*` documentation.

## 7. Data Model Changes

| Entity | Change | File |
|--------|--------|------|
| `Memora Grant Component` | Add `key_type` Select field (`full`/`practice`) | `memora_grant_component.json` |
| `Memora Grant Component` | Add server-side validation for practice+track combo | `memora_grant_component.py` |
| `Memora Grant Component` | Add JS form handler to hide `key_type` when target is Track | `memora_grant_component.js` |

No new DocTypes. No new database tables. No new Redis key shapes.

## 8. Backward Compatibility

- All existing `Memora Grant Component` rows have no `key_type` field → default to `full` →
  `get_grant_keys()` emits `SUB-*`/`TRK-*` as before. Zero regression.
- All existing subscriptions with `SUB-*` keys continue to grant practice access (step 1 in the
  priority order above).
- Existing practice endpoint logic for full subscribers is untouched.

## 9. Admin Workflow (How Admins Use This)

1. Create a `Memora Product Grant` titled e.g. "Practice Pass — Mathematics Grade 5".
2. Add a `grant_components` row:
   - `target_doctype` = `Memora Subject`
   - `target_name` = `SUBJ-MATH-G5`
   - `key_type` = `practice`
3. Create a `Memora Voucher Batch` linked to this grant.
4. Generate and distribute voucher cards.
5. Student redeems voucher → gets `PRAC-SUB-SUBJ-MATH-G5` subscription → can access Practice
   for that subject immediately.

## 10. Implementation Tasks

| # | Task | File(s) | Effort |
|---|------|---------|--------|
| T1 | Add `key_type` field to `Memora Grant Component` JSON schema | `memora_grant_component.json` | Small |
| T2 | Add server-side validation (practice + track = error) | `memora_grant_component.py` | Small |
| T3 | Add JS form handler to hide/show `key_type` field conditionally | `memora_grant_component.js` | Small |
| T4 | Update `get_grant_keys()` to emit `PRAC-SUB-*` for practice components | `products.py` | Small |
| T5 | Update practice endpoint to check `PRAC-SUB-*` as access path | `practice.py` | Small |
| T6 | Document `PRAC-SUB-*` convention in `redis_keys.py` comments | `redis_keys.py` | Trivial |
| T7 | Write tests: grant key generation for practice components | `test_products.py` | Small |
| T8 | Write tests: practice endpoint with practice-only grant | `test_practice.py` | Small |
| T9 | Write tests: full subscriber still gets practice access (regression) | `test_practice.py` | Small |
| T10 | Write tests: no-grant player is denied practice | `test_practice.py` | Small |

**Total estimated effort: 2–3 days.**

## 11. Decisions

1. **Scope of practice-only grant**: `PRAC-SUB-*` grants access to ALL review items in the
   subject — not gated by lesson progress. Practice-only users can review any item in the subject
   immediately upon subscription.

2. **Plan entitlement**: Should some academic plans implicitly include practice for all their
   subjects (without an explicit grant)? — Out of scope for this PRD; can be added later via the
   plan free subjects mechanism.

3. **Expiry**: Practice-only subscriptions expire at season end (same as full subscriptions)?
   — Yes, same lifecycle, no special handling needed.
