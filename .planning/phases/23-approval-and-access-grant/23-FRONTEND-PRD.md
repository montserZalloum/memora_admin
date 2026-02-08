# Phase 23: Approval and Access Grant - Player Frontend PRD

**Version:** 1.0
**Date:** 2026-02-08
**Status:** Ready for Frontend Implementation
**Target Audience:** Player App (Client-Side) / Frontend AI Agent

---

## Executive Summary

This PRD defines what the **player-facing frontend app** needs to do when admin approves or rejects a purchase transaction. The player experiences these changes **implicitly** through catalog visibility changes — no notifications, no approval messages, just catalog updates.

- **Approved:** Product disappears from catalog (already owned)
- **Rejected:** Product reappears in catalog (available to buy again)
- **Immediate Access:** On approval, player gets content access without re-login

**Key Role:** Player app must sync with backend state and refresh catalog to show accurate product availability.

---

## What Players Experience in Phase 23

### Scenario 1: Player Submits Purchase, Admin Approves

```
Timeline:
1. Player submits purchase request for Math Bundle
   → Product hidden from catalog (status: pending)
2. Admin approves transaction (status: Completed)
   → Backend creates subscriptions
   → Backend syncs to Redis access set
   → Backend removes from pending set
3. Player refreshes catalog
   → Math Bundle no longer visible (already owned)
   → Player can access Math content immediately
```

### Scenario 2: Player Submits Purchase, Admin Rejects

```
Timeline:
1. Player submits purchase request for Math Bundle
   → Product hidden from catalog (status: pending)
2. Admin rejects transaction (status: Rejected)
   → Backend removes from pending set
3. Player refreshes catalog
   → Math Bundle visible again (can re-submit)
   → No error message shown (implicit failure)
```

### No Notifications Sent

- ✗ No "Your purchase was approved" email/notification
- ✗ No "Your purchase was rejected" email/notification
- ✓ Player discovers status implicitly by checking catalog

---

## Backend APIs Available to Player App

### 1. Get Product Catalog

**Endpoint:** `GET /api/v1/products/{subject_id}/catalog`
**Authentication:** JWT Bearer token
**Response:** List of available products for player's plan

**Response Body:**
```json
{
  "products": [
    {
      "grant_id": "PROD-GRANT-00067",
      "bundle_name": "Mathematics Fundamentals",
      "subject_title": "Mathematics",
      "description": "Basic math concepts",
      "price": 49.99,
      "is_purchased": false,
      "is_pending": false
    },
    {
      "grant_id": "PROD-GRANT-00068",
      "bundle_name": "Advanced Math",
      "subject_title": "Mathematics",
      "description": "Advanced topics",
      "price": 79.99,
      "is_purchased": true,
      "is_pending": false
    }
  ]
}
```

**Field Meanings:**
- `is_purchased: true` — Player has active subscriptions for this product (hide from catalog)
- `is_pending: true` — Player submitted purchase request, waiting for admin approval (hide from catalog)
- `is_pending: false` AND `is_purchased: false` — Show in catalog as "Available to Buy"

### 2. Check Access to Subjects

**Endpoint:** `GET /api/v1/access/check`
**Authentication:** JWT Bearer token

**Response:**
```json
{
  "accessible_subjects": ["SUBJ-00028", "SUBJ-00031"],
  "accessible_tracks": ["TRK-00015"],
  "free_subjects": ["SUBJ-00001"],
  "access_keys": ["SUB-SUBJ-00028", "SUB-SUBJ-00031", "TRK-TRK-00015"]
}
```

Use this after purchase approval to verify access was granted.

### 3. Get Purchase Request Status

**Not Implemented Yet** — Players cannot see their purchase history in Phase 23. Catalog visibility is the only status indicator.

---

## What Frontend Must Do

### 1. Catalog Filtering Logic

The backend returns `is_purchased` and `is_pending` flags for each product. Frontend must:

```pseudocode
FOR each product in catalog:
  IF product.is_purchased == true:
    HIDE (already owned)
  ELSE IF product.is_pending == true:
    HIDE (awaiting approval)
  ELSE:
    SHOW (available to buy)
```

**Example:**
- Math Bundle has `is_pending: true` → Hide from "Available" section
- Later, admin approves → Backend syncs Redis
- Player refreshes catalog → Same Math Bundle now has `is_pending: false` + `is_purchased: true` → Hide from "Available" section

### 2. Refresh Triggers

Catalog should refresh in these situations:

| Trigger | When | Why |
|---------|------|-----|
| App launch | Player opens app | Initial state load |
| Tab focus | Player switches back to app | Catch any background changes |
| Purchase submission | Player clicks "Buy" | Refresh immediately to show pending state |
| Manual refresh | Player pulls-to-refresh (mobile) or clicks refresh | Let player check status |
| Error recovery | After network error | Retry without user action |
| Periodic sync | Every 30-60 seconds (optional) | Catch approval without manual refresh |

### 3. Purchase Flow (From Phase 22 Recap)

When player clicks "Buy Product":

1. **Before Submit:**
   - Show confirmation dialog: "Buy [Product Name] for $X.XX?"

2. **On Submit:**
   - Call: `POST /api/v1/products/purchase`
   - Request body:
     ```json
     {
       "product_grant_id": "PROD-GRANT-00067"
     }
     ```

3. **On Success:**
   - Show success message: "Purchase submitted. Awaiting admin approval."
   - Immediately refresh catalog (product becomes hidden with `is_pending: true`)

4. **On Error:**
   - Show error message: "Could not submit purchase. Please try again."

5. **Waiting State:**
   - Product stays hidden in catalog
   - No progress indicator or countdown
   - Player must manually refresh to check status

### 4. Access Sync on Approval

When admin approves a transaction, the following happens **automatically on backend**:

1. Player Subscription records created
2. Redis access set updated: `SADD memora:access:{user_id} {access_keys}`
3. Pending set cleaned: `SREM memora:pending:{user_id} {grant_id}`

**Frontend Responsibility:**
- On next catalog fetch, access checks will use updated Redis state
- Products that were purchased now show as `is_purchased: true`
- Previously pending products now show as `is_purchased: true`

### 5. Re-Login Not Required

**Key Guarantee:** Player does NOT need to log out and back in for approved access to work.

When backend approves a transaction:
1. Subscriptions created immediately
2. Redis access set updated immediately
3. Next API call to access content checks fresh Redis state
4. Player can access new content **within seconds** (no re-login needed)

**Frontend Implication:** If player tries to access newly approved content before refreshing catalog, it still works (access check is live).

### 6. On Rejection

When admin rejects a transaction:

1. Backend removes from pending set: `SREM memora:pending:{user_id} {grant_id}`
2. Next catalog fetch shows product as `is_pending: false`
3. Product becomes "Available to Buy" again
4. Player can re-submit purchase request

**Frontend Action:** No special handling needed — just refresh catalog and show as available.

---

## Catalog Display States

### Available Products Section
```
Available Products
─────────────────────────────────────────
[Mathematics Fundamentals] $49.99
  Description: Basic math concepts
  [Buy Now]                           ✓ SHOW

[Advanced Math] $79.99
  Description: Advanced topics
  [Buy Now]                           ✓ SHOW
```

### Hidden Products (Not Shown)
```
❌ NOT SHOWN:
   - Is Purchased (already owned)
   - Is Pending (awaiting approval)
```

### Already Owned Section (Separate Tab)
```
My Content
─────────────────────────────────────────
[Mathematics Fundamentals]
  You own this content
  [Access]                            ✓ ALREADY OWNED
  (is_purchased: true)

[Science Bundle]
  You own this content
  [Access]                            ✓ ALREADY OWNED
  (from free plan)
```

---

## API Response Contracts

### Catalog Endpoint Response Fields

| Field | Type | Example | Meaning |
|-------|------|---------|---------|
| `grant_id` | string | "PROD-GRANT-00067" | Unique identifier for product |
| `bundle_name` | string | "Mathematics Fundamentals" | Product name |
| `subject_title` | string | "Mathematics" | Subject area |
| `description` | string | "Basic math concepts" | Product description |
| `price` | number | 49.99 | Price in player's currency |
| `is_purchased` | boolean | true/false | Player has active subscription |
| `is_pending` | boolean | true/false | Player's purchase awaiting approval |

**Filtering Rules (Frontend Job):**
```javascript
const availableProducts = catalog.filter(p =>
  !p.is_purchased && !p.is_pending
);

const ownedProducts = catalog.filter(p =>
  p.is_purchased
);

const pendingProducts = catalog.filter(p =>
  p.is_pending
);
```

---

## User Flows

### Flow 1: Purchase → Approval

```
Player App                    Backend                   Admin
    |                            |                        |
    |-- GET /catalog ----------->|                        |
    |<---- Products list---------|                        |
    |                            |                        |
    |  [Product shown as available]                       |
    |                            |                        |
    |-- POST /purchase --------->|                        |
    |   (grant_id)               |                        |
    |<---- Success --------------|                        |
    |                            |                        |
    |-- GET /catalog ----------->|<-- Sees pending txn---|
    |<---- is_pending: true------|                        |
    |                            |                        |
    | [Product hidden]           |<-- Approves (status-->
    |                            |    Completed)         |
    |                            |                        |
    |                            |-- Creates subscriptions
    |                            |-- Updates Redis       |
    |                            |-- SREM pending        |
    |                            |                        |
    |-- GET /catalog ----------->|                        |
    |<---- is_purchased: true----|                        |
    |                            |                        |
    | [Product hidden, owned]    |                        |
    |-- Can access content ----->|                        |
```

### Flow 2: Purchase → Rejection

```
Player App                    Backend                   Admin
    |                            |                        |
    |-- POST /purchase --------->|                        |
    |   (grant_id)               |                        |
    |<---- Success --------------|                        |
    |                            |                        |
    |-- GET /catalog ----------->|<-- Sees pending txn---|
    |<---- is_pending: true------|                        |
    |                            |                        |
    | [Product hidden]           |<-- Rejects (status-->
    |                            |    Rejected)          |
    |                            |                        |
    |                            |-- SREM pending        |
    |                            |                        |
    |-- GET /catalog ----------->|                        |
    |<---- is_pending: false-----|                        |
    |      is_purchased: false   |                        |
    |                            |                        |
    | [Product shown again]      |                        |
    |-- Can re-submit purchase-->|                        |
```

---

## Frontend Implementation Checklist

✅ **Must Implement:**

- [ ] Catalog fetch endpoint integration
- [ ] Filter products: hide if `is_purchased` OR `is_pending`
- [ ] Show "Available Products" section with available items
- [ ] Show "My Content" section with purchased items
- [ ] On purchase submit, immediately refresh catalog
- [ ] On app launch, fetch catalog
- [ ] On tab focus, refresh catalog (detect background approvals)
- [ ] Manual refresh button (pull-to-refresh or menu)
- [ ] Display product name, description, price
- [ ] "Buy Now" button on available products
- [ ] "Access" button on owned products
- [ ] Error handling for failed catalog fetches
- [ ] Loading state while fetching catalog

✅ **Nice to Have:**

- [ ] Periodic auto-refresh every 30-60 seconds
- [ ] Estimated approval time message (e.g., "Admin usually approves within 24h")
- [ ] Separate "Pending Approval" section showing submitted purchases
- [ ] Retry button on failed purchases
- [ ] Confirmation dialog before purchase
- [ ] Toast notifications for purchase success/error
- [ ] Search/filter products by subject
- [ ] Price discount display (if applicable)

---

## What Backend Does (Hands-Off)

When admin approves a transaction, backend automatically:

✅ Creates Player Subscription records for each subject/track
✅ Syncs to Redis `memora:access:{user_id}` (immediate access)
✅ Cleans up pending set (product status becomes `is_purchased`)
✅ Catalog query returns updated `is_purchased: true` flag

**You don't need to:**
- ❌ Manually sync subscriptions
- ❌ Update access cache
- ❌ Send player notifications
- ❌ Invalidate any frontend caches

Just refresh catalog and let the updated flags tell the story.

---

## Error Handling

### Network Errors

**When:** Catalog fetch fails (timeout, offline, server error)

```javascript
// Show retry UI
showError("Could not load catalog. Check your connection.");
showButton("Retry", () => refreshCatalog());

// Don't show stale data
hideProducts();
```

### Purchase Submission Errors

**When:** Purchase request rejected by backend

**Common Errors:**
- `"You already own this product"` — User bought it elsewhere
- `"Product not available for your plan"` — Shouldn't happen, but safety check
- `"Product not found"` — Grant was deleted

**Frontend Action:**
```
Show error to player
Refresh catalog (in case state changed)
Keep product visible if refresh shows it's available
```

### Catalog State Inconsistency

**When:** Catalog shows product as available, but purchase fails with "already own"

**Likely Cause:** Backend state changed since last fetch, or another device bought it

**Frontend Action:**
1. Show error: "Could not complete purchase. Refreshing your catalog..."
2. Auto-refresh catalog
3. If now shows as owned, show success message
4. If still shows as available, show error

---

## Performance Requirements

### Catalog Load Time
- **Target:** < 500ms on cache hit
- **Acceptable:** < 2s on cache miss
- **Mobile:** < 3s on 4G

### Refresh Time
- **Manual refresh:** < 1s
- **Auto-refresh:** < 2s (non-blocking, in background)

### Memory
- **Catalog cache:** < 1MB (typical: ~100-200KB)
- **Clean up old cache:** Older than 5 minutes

---

## Cache Strategy

### Cache Catalog Response

```javascript
// Cache catalog for 5 minutes or until manual refresh
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes
let lastFetch = null;
let cachedCatalog = null;

async function getCatalog() {
  const now = Date.now();

  // Return cache if fresh
  if (cachedCatalog && now - lastFetch < CACHE_TTL) {
    return cachedCatalog;
  }

  // Fetch fresh data
  const response = await fetch('/api/v1/products/catalog');
  cachedCatalog = response.data;
  lastFetch = now;

  return cachedCatalog;
}

// Clear cache on manual refresh
function refreshCatalog() {
  lastFetch = null;
  cachedCatalog = null;
  return getCatalog();
}
```

### Invalidation Triggers

| Event | Action |
|-------|--------|
| Purchase submitted | Clear cache |
| Manual refresh clicked | Clear cache |
| App comes to focus | Check age, refresh if > 5min |
| Any API error | Keep cache, retry |

---

## Analytics Events (Optional)

Track these events for product insights:

```javascript
// When product is viewed
track("product_viewed", {
  product_grant_id: "PROD-GRANT-00067",
  bundle_name: "Mathematics Fundamentals",
  price: 49.99,
  status: "available" // available, owned, pending
});

// When purchase is attempted
track("purchase_attempted", {
  product_grant_id: "PROD-GRANT-00067",
  price: 49.99
});

// When purchase succeeds
track("purchase_submitted", {
  product_grant_id: "PROD-GRANT-00067",
  price: 49.99
});

// When approval is detected
track("purchase_approved", {
  product_grant_id: "PROD-GRANT-00067",
  price: 49.99,
  days_to_approval: 1.5
});

// When rejection is detected
track("purchase_rejected", {
  product_grant_id: "PROD-GRANT-00067",
  price: 49.99
});
```

---

## Testing Scenarios

### Scenario 1: Happy Path (Approve)

1. Player has 0 products
2. Player submits purchase request for Math Bundle
3. Catalog refreshes → Math Bundle shows `is_pending: true`
4. Admin approves transaction
5. Player refreshes catalog
6. **Expected:** Math Bundle shows `is_purchased: true`, hidden from available section
7. **Verify:** Player can access Math content

### Scenario 2: Rejection

1. Player submits purchase for Science Bundle
2. Catalog refreshes → Science Bundle shows `is_pending: true`
3. Admin rejects transaction
4. Player refreshes catalog
5. **Expected:** Science Bundle shows `is_pending: false`, visible in available section
6. **Verify:** Player can re-submit purchase

### Scenario 3: Multiple Devices

1. Player on Device A submits purchase for Math Bundle
2. Player on Device B refreshes catalog
3. **Expected:** Math Bundle shows `is_pending: true` on Device B too
4. Admin approves
5. **Expected:** Both devices see `is_purchased: true` on next refresh (or within polling interval)

### Scenario 4: Network Disconnection

1. Player submits purchase (succeeds)
2. Network goes down
3. Player can't refresh catalog (offline)
4. Admin approves transaction
5. Player regains connection
6. Player refreshes catalog
7. **Expected:** Math Bundle shows as purchased (backend already approved)

### Scenario 5: Edge Case - Double-Submit Prevention

1. Player clicks "Buy" button
2. Request in flight (loading state)
3. Player clicks "Buy" again
4. **Expected:** Second click ignored or shows already-pending message
5. First request completes successfully

---

## Security Considerations

### Authentication
- All requests require valid JWT token
- Backend validates player ownership before returning `is_purchased` flag

### Data Validation
- Don't trust `is_purchased` flag for access control on content
- Always verify access via `/api/v1/access/check` before playing lessons
- Server-side access check is authoritative (frontend is for display only)

### No Sensitive Data
- Don't log player email, purchase history to client logs
- Catalog data is player-specific (plan-filtered already)

---

## Success Criteria

✅ Player submits purchase request → product hidden from catalog
✅ Admin approves → player refreshes catalog → product shown as owned
✅ Admin rejects → player refreshes catalog → product shown as available
✅ Approved player can access content immediately (no re-login)
✅ Rejected player can re-submit purchase
✅ Catalog response includes `is_purchased` and `is_pending` flags
✅ Frontend filters correctly: hide if either flag is true
✅ No notifications sent (implicit discovery only)
✅ Works on multiple devices (eventually consistent)
✅ Handles network errors gracefully

---

## Appendix: API Reference

### GET /api/v1/products/{subject_id}/catalog

**Purpose:** Fetch available products for player's plan

**Request:**
```
GET /api/v1/products/SUBJ-00028/catalog
Authorization: Bearer {jwt_token}
```

**Response (200):**
```json
{
  "products": [
    {
      "grant_id": "PROD-GRANT-00067",
      "bundle_name": "Mathematics Fundamentals",
      "subject_title": "Mathematics",
      "description": "Basic math concepts and problem-solving",
      "price": 49.99,
      "is_purchased": false,
      "is_pending": false
    },
    {
      "grant_id": "PROD-GRANT-00068",
      "bundle_name": "Advanced Mathematics",
      "subject_title": "Mathematics",
      "description": "Complex topics for advanced learners",
      "price": 79.99,
      "is_purchased": false,
      "is_pending": true
    }
  ]
}
```

### POST /api/v1/products/purchase

**Purpose:** Submit a purchase request

**Request:**
```
POST /api/v1/products/purchase
Authorization: Bearer {jwt_token}
Content-Type: application/json

{
  "product_grant_id": "PROD-GRANT-00067"
}
```

**Response (201):**
```json
{
  "message": "Purchase request submitted. Awaiting admin approval.",
  "transaction_id": "SUB-TXN-00451",
  "status": "Pending Approval"
}
```

**Error (400):**
```json
{
  "error": "You already own this product"
}
```

---

**Document Version:** 1.0
**Last Updated:** 2026-02-08
**For:** Player App Frontend Development
**Backend Status:** Phase 23 Complete
