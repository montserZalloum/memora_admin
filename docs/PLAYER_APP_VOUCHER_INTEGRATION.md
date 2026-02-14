# Player App - Voucher System Integration Guide

**Target Audience:** Player App AI Agent (Frontend Developer)
**Backend Version:** v3.0 Voucher Management System
**Phases Covered:** 33-38
**Date:** 2026-02-14

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [API Endpoints](#api-endpoints)
4. [Authentication](#authentication)
5. [Request/Response Schemas](#requestresponse-schemas)
6. [Error Handling](#error-handling)
7. [Rate Limiting](#rate-limiting)
8. [Integration Flow](#integration-flow)
9. [Testing Guide](#testing-guide)
10. [Arabic Localization](#arabic-localization)

---

## Overview

### What is the Voucher System?

The Voucher Management System allows students to purchase physical voucher cards from libraries and unlock educational content by entering a PIN code in the mobile app. This enables offline sales and instant digital content delivery.

### Phases 33-38 Summary

| Phase | Name | Relevant to Frontend? |
|-------|------|----------------------|
| **36** | Redemption API | ✅ **YES - Core Integration** |

**Frontend Integration Focus:** Phase 36 only - the redemption API endpoints.

---

## System Architecture

```
┌─────────────────┐
│   Player App    │
│   (Frontend)    │
└────────┬────────┘
         │ JWT Auth + PIN
         │
         ▼
┌─────────────────────────┐
│   FastAPI Backend       │
│   http://API_URL:8002   │
│   /api/v1/voucher/*     │
└────────┬────────────────┘
         │ HMAC(PIN) + Player ID
         │
         ▼
┌─────────────────────────┐
│   Frappe Backend        │
│   (Transactional Layer) │
└─────────────────────────┘
         │
         ▼
┌─────────────────────────┐
│   Access Grant Pipeline │
│   (Existing Phase 23)   │
└─────────────────────────┘
```

**Key Points:**
- PIN is sent in plaintext to FastAPI (over HTTPS)
- FastAPI computes HMAC-SHA256 hash before forwarding to Frappe
- Content unlocks instantly via existing subscription pipeline
- All operations are logged for audit trail

---

### Endpoint Summary

| Method | Endpoint | Auth Required | Rate Limited | Purpose |
|--------|----------|---------------|--------------|---------|
| POST | `/voucher/preview` | ✅ Yes (JWT) | ❌ No | Preview available grants |
| POST | `/voucher/redeem` | ✅ Yes (JWT) | ✅ Yes (on failure) | Redeem voucher |

---

## Authentication

All voucher endpoints require JWT authentication.

### Headers

```http
Authorization: Bearer <jwt_token>
Content-Type: application/json
```

---

## Request/Response Schemas

### 1. Preview Voucher

**Endpoint:** `POST /api/v1/voucher/preview`

**Purpose:** Check what content a voucher card unlocks WITHOUT redeeming it. Students can verify the card before deciding to redeem.

#### Request

```typescript
interface VoucherPreviewRequest {
  pin: string;  // 6-20 characters
}
```

**Example:**
```json
{
  "pin": "ABC123DEF456"
}
```

#### Success Response (200 OK)

```typescript
interface VoucherPreviewResponse {
  face_value: string;      // e.g., "50.00" (in local currency 'JOD')
  grants: VoucherGrant[];  // List of available grants
}

interface VoucherGrant {
  grant_id: string;  // Backend grant identifier
  name: string;      // Display name (Arabic)
}
```

**Example:**
```json
{
  "face_value": "50.00",
  "grants": [
    {
      "grant_id": "GRANT-2024-001",
      "name": "الرياضيات - الصف الأول الابتدائي"
    },
    {
      "grant_id": "GRANT-2024-002",
      "name": "العلوم - الصف الأول الابتدائي"
    }
  ]
}
```

**Notes:**
- `grants` array only includes grants the student **doesn't already own**
- If student owns all grants in the card, returns error `ALL_GRANTS_OWNED` (see error section)
- `face_value` is informational only (shows card's purchase price)

#### Error Response

See [Error Handling](#error-handling) section for all possible error codes and HTTP status codes.

---

### 2. Redeem Voucher

**Endpoint:** `POST /api/v1/voucher/redeem`

**Purpose:** Redeem a voucher card for a specific product grant. This is a **one-time, irreversible operation**.

#### Request

```typescript
interface VoucherRedeemRequest {
  pin: string;      // 6-20 characters
  grant_id: string; // Must be from preview response
}
```

**Example:**
```json
{
  "pin": "ABC123DEF456",
  "grant_id": "GRANT-2024-001"
}
```

#### Success Response (200 OK)

```typescript
interface VoucherRedeemResponse {
  status: "success";
  transaction_id: string;  // Backend transaction reference
}
```

**Example:**
```json
{
  "status": "success",
  "transaction_id": "SUB-TXN-2024-001234"
}
```

**What Happens After Success:**
1. Voucher card is marked as "Redeemed" (cannot be used again)
2. Subscription Transaction is created in the backend
3. Content is **instantly unlocked** via existing access grant pipeline
4. Student can immediately access the unlocked content in the app

#### Error Response

See [Error Handling](#error-handling) section.

---

## Error Handling

All endpoints return errors in the following format:

```typescript
interface VoucherErrorResponse {
  error: string;           // Machine-readable error code
  retry_after?: number;    // Seconds until retry allowed (RATE_LIMITED only)
}
```

### Error Codes Reference

| Error Code | HTTP Status | Meaning | User Action |
|------------|-------------|---------|-------------|
| `INVALID_PIN` | 404 | PIN not found or incorrect | Check PIN and try again |
| `NOT_ALLOCATED` | 422 | Card not allocated to a library yet | Contact library/admin |
| `ALREADY_REDEEMED` | 409 | Card already used | Cannot reuse, contact support |
| `EXPIRED` | 410 | Card expired (season ended) | Cannot use, contact support |
| `VOID` | 410 | Card voided by admin | Cannot use, contact support |
| `BATCH_INACTIVE` | 422 | Card batch not active | Contact admin |
| `SEASON_INACTIVE` | 422 | Season ended or not published | Content no longer available |
| `ALL_GRANTS_OWNED` | 409 | Student already owns all grants | Card has no new content |
| `GRANT_NOT_IN_BATCH` | 422 | Invalid grant_id for this card | Use grant from preview response |
| `ALREADY_OWNED` | 409 | Student already owns this grant | **Card NOT consumed** - choose different grant |
| `RATE_LIMITED` | 429 | Too many failed attempts | Wait `retry_after` seconds |
| `SERVICE_UNAVAILABLE` | 503 | Redis/backend unavailable | Retry later |
| `INTERNAL_ERROR` | 500 | Unexpected server error | Report to support |

### Special Cases

#### ALREADY_OWNED (Important!)

This error is **special** - it does NOT consume the voucher card. The student can retry with a different grant.

**Flow:**
1. Student calls `/redeem` with `grant_id: "GRANT-A"`
2. Backend checks: student already owns GRANT-A
3. Returns `409 ALREADY_OWNED` - card remains "Allocated" (not redeemed)
4. Student can call `/redeem` again with `grant_id: "GRANT-B"`

**UI Recommendation:** Show a friendly message like "You already own this content. Please choose a different item from the list."

#### RATE_LIMITED

When a student makes too many **failed** attempts, they'll receive:

```json
{
  "error": "RATE_LIMITED",
  "retry_after": 2847  // seconds
}
```

**UI Recommendation:** Show countdown timer: "Too many attempts. Please try again in 47 minutes."

### Example Error Responses

```json
// Invalid PIN
{
  "error": "INVALID_PIN"
}

// Already redeemed
{
  "error": "ALREADY_REDEEMED"
}

// Rate limited
{
  "error": "RATE_LIMITED",
  "retry_after": 3600
}
```

---

## Rate Limiting

### Overview

Rate limiting protects against PIN brute-forcing. It applies **only to failed redemption attempts**.

### Rules

| Limit Type | Threshold | Window | Applies To |
|------------|-----------|--------|------------|
| Per Player | 5 failed attempts | 1 hour | Individual student |
| Per IP | 20 failed attempts | 1 hour | Shared devices/networks |
| Preview | Unlimited | - | No rate limit on preview |

### Behavior

**What Counts as a Failure:**
- `INVALID_PIN`
- `NOT_ALLOCATED`
- `ALREADY_REDEEMED`
- `EXPIRED`
- `VOID`
- `BATCH_INACTIVE`
- `SEASON_INACTIVE`
- `ALL_GRANTS_OWNED`
- `GRANT_NOT_IN_BATCH`
- `ALREADY_OWNED`

**What Does NOT Count:**
- ✅ Successful redemptions
- ✅ Preview requests (unlimited)
- ✅ `RATE_LIMITED` responses (doesn't increment counter)

### Frontend Implementation

```typescript
async function redeemVoucher(pin: string, grantId: string): Promise<RedeemResult> {
  try {
    const response = await fetch('/api/v1/voucher/redeem', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ pin, grant_id: grantId })
    });

    const data = await response.json();

    if (response.status === 429) {
      // Rate limited - show countdown
      const minutes = Math.ceil(data.retry_after / 60);
      showError(`Too many attempts. Try again in ${minutes} minutes.`);
      return { success: false, error: 'RATE_LIMITED', retryAfter: data.retry_after };
    }

    if (!response.ok) {
      // Other error - map to user message
      showError(getErrorMessage(data.error));
      return { success: false, error: data.error };
    }

    // Success!
    return { success: true, transactionId: data.transaction_id };

  } catch (err) {
    showError('Network error. Please check your connection.');
    return { success: false, error: 'NETWORK_ERROR' };
  }
}
```

---

## Integration Flow

### Recommended User Flow

```
1. Student purchases physical card from library
   ↓
2. Student opens app → "Redeem Voucher" screen
   ↓
3. Student enters PIN (text input or QR scan if available)
   ↓
4. App calls POST /voucher/preview
   ↓
5. App shows: "This card unlocks:" + list of grants + face value
   ↓
6. Student selects which grant to redeem (if multiple)
   ↓
7. App calls POST /voucher/redeem with PIN + grant_id
   ↓
8. Success → Show confirmation + navigate to unlocked content
   OR
   Error → Show user-friendly error message
```

### Code Example (React Native / TypeScript)

```typescript
import { useState } from 'react';

interface Grant {
  grant_id: string;
  name: string;
}

function VoucherRedeemScreen() {
  const [pin, setPin] = useState('');
  const [step, setStep] = useState<'enter_pin' | 'select_grant' | 'redeeming'>('enter_pin');
  const [grants, setGrants] = useState<Grant[]>([]);
  const [faceValue, setFaceValue] = useState('');

  // Step 1: Preview
  async function handlePreview() {
    try {
      const response = await fetch('/api/v1/voucher/preview', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${getJwtToken()}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ pin })
      });

      if (!response.ok) {
        const error = await response.json();
        showError(getArabicErrorMessage(error.error));
        return;
      }

      const data = await response.json();
      setGrants(data.grants);
      setFaceValue(data.face_value);
      setStep('select_grant');

    } catch (err) {
      showError('حدث خطأ في الاتصال. يرجى المحاولة مرة أخرى.');
    }
  }

  // Step 2: Redeem
  async function handleRedeem(grantId: string) {
    setStep('redeeming');

    try {
      const response = await fetch('/api/v1/voucher/redeem', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${getJwtToken()}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          pin,
          grant_id: grantId
        })
      });

      const data = await response.json();

      if (response.status === 429) {
        // Rate limited
        const minutes = Math.ceil(data.retry_after / 60);
        showError(`محاولات كثيرة جداً. حاول مرة أخرى بعد ${minutes} دقيقة.`);
        setStep('select_grant');
        return;
      }

      if (response.status === 409 && data.error === 'ALREADY_OWNED') {
        // Special case: can try different grant
        showWarning('أنت تملك هذا المحتوى بالفعل. اختر محتوى آخر.');
        setStep('select_grant');
        return;
      }

      if (!response.ok) {
        showError(getArabicErrorMessage(data.error));
        setStep('enter_pin');
        return;
      }

      // Success!
      showSuccess('تم تفعيل البطاقة بنجاح! 🎉');
      navigateToUnlockedContent();

    } catch (err) {
      showError('حدث خطأ في الاتصال. يرجى المحاولة مرة أخرى.');
      setStep('select_grant');
    }
  }

  // Render based on step...
}
```

---

## Testing Guide

### Prerequisites

1. Valid JWT token (authenticate via `/api/v1/auth/login` first)
2. Test voucher cards (ask admin to create a test batch)
3. Backend running on correct port (8002 for FastAPI)

### Test Cases

#### 1. Happy Path - Preview

```bash
curl -X POST /api/v1/voucher/preview \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pin": "TEST123456"}'
```

**Expected Response (200):**
```json
{
  "face_value": "50.00",
  "grants": [
    {
      "grant_id": "GRANT-2024-001",
      "name": "الرياضيات - الصف الأول"
    }
  ]
}
```

#### 2. Happy Path - Redeem

```bash
curl -X POST /api/v1/voucher/redeem \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "pin": "TEST123456",
    "grant_id": "GRANT-2024-001"
  }'
```

**Expected Response (200):**
```json
{
  "status": "success",
  "transaction_id": "SUB-TXN-2024-001234"
}
```

#### 3. Invalid PIN

```bash
curl -X POST /api/v1/voucher/preview \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pin": "WRONG_PIN"}'
```

**Expected Response (404):**
```json
{
  "error": "INVALID_PIN"
}
```

#### 4. Rate Limiting Test

Make 6 failed redemption attempts rapidly:

```bash
for i in {1..6}; do
  curl -X POST /api/v1/voucher/redeem \
    -H "Authorization: Bearer YOUR_JWT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"pin": "WRONG_PIN", "grant_id": "GRANT-001"}' \
    -w "\nStatus: %{http_code}\n\n"
  sleep 1
done
```

**Expected:** First 5 return 404, 6th returns 429 with `retry_after`.

#### 5. Authentication Test (No JWT)

```bash
curl -X POST /api/v1/voucher/preview \
  -H "Content-Type: application/json" \
  -d '{"pin": "TEST123456"}' \
  -w "\nStatus: %{http_code}\n"
```

**Expected Response (403):**
```json
{
  "detail": "Not authenticated"
}
```

### Integration Test Checklist

- [ ] Preview endpoint returns grants for valid allocated card
- [ ] Preview endpoint returns INVALID_PIN for wrong PIN
- [ ] Redeem endpoint successfully redeems and unlocks content
- [ ] Redeem endpoint returns ALREADY_REDEEMED on second attempt
- [ ] Rate limiting kicks in after 5 failed attempts
- [ ] Rate limit resets after 1 hour
- [ ] ALREADY_OWNED doesn't consume the card
- [ ] Unauthenticated requests return 403
- [ ] Content appears in app after successful redemption

---

## Arabic Localization

All error messages should be displayed in Arabic to students. The API returns machine-readable error codes - your app must map them to user-friendly Arabic messages.

### Error Message Mapping (Arabic)

```typescript
const ERROR_MESSAGES_AR: Record<string, string> = {
  INVALID_PIN: 'الرمز غير صحيح. يرجى التحقق والمحاولة مرة أخرى.',
  NOT_ALLOCATED: 'البطاقة غير مفعلة بعد. يرجى التواصل مع المكتبة.',
  ALREADY_REDEEMED: 'تم استخدام هذه البطاقة من قبل. لا يمكن استخدامها مرة أخرى.',
  EXPIRED: 'انتهت صلاحية البطاقة. يرجى التواصل مع الدعم الفني.',
  VOID: 'البطاقة ملغاة. يرجى التواصل مع المكتبة.',
  BATCH_INACTIVE: 'البطاقة غير متاحة حالياً. يرجى التواصل مع المسؤول.',
  SEASON_INACTIVE: 'انتهى موسم هذا المحتوى. لم يعد متاحاً.',
  ALL_GRANTS_OWNED: 'أنت تملك جميع محتويات هذه البطاقة بالفعل.',
  GRANT_NOT_IN_BATCH: 'المحتوى المختار غير صحيح. يرجى اختيار محتوى من القائمة.',
  ALREADY_OWNED: 'أنت تملك هذا المحتوى بالفعل. اختر محتوى آخر من القائمة.',
  RATE_LIMITED: 'محاولات كثيرة جداً. يرجى المحاولة لاحقاً.',
  SERVICE_UNAVAILABLE: 'الخدمة غير متاحة حالياً. يرجى المحاولة لاحقاً.',
  INTERNAL_ERROR: 'حدث خطأ. يرجى المحاولة لاحقاً أو التواصل مع الدعم الفني.',
};

function getArabicErrorMessage(errorCode: string): string {
  return ERROR_MESSAGES_AR[errorCode] || 'حدث خطأ غير متوقع.';
}
```

### UI Text Suggestions (Arabic)

```typescript
const UI_TEXT_AR = {
  // Screen titles
  redeemVoucherTitle: 'تفعيل بطاقة الشحن',

  // Input labels
  enterPinLabel: 'أدخل رمز البطاقة',
  pinPlaceholder: 'ABC123DEF456',

  // Buttons
  previewButton: 'معاينة محتويات البطاقة',
  redeemButton: 'تفعيل البطاقة',
  cancelButton: 'إلغاء',

  // Preview screen
  cardValueLabel: 'قيمة البطاقة',
  availableContentLabel: 'المحتوى المتاح',
  selectContentLabel: 'اختر المحتوى الذي تريد تفعيله:',

  // Success
  successTitle: 'تم التفعيل بنجاح! 🎉',
  successMessage: 'يمكنك الآن الوصول إلى المحتوى الجديد.',
  goToContentButton: 'انتقل إلى المحتوى',

  // Loading
  checkingCard: 'جارٍ التحقق من البطاقة...',
  activatingCard: 'جارٍ تفعيل البطاقة...',

  // Warnings
  alreadyOwnedWarning: 'أنت تملك هذا المحتوى. اختر محتوى آخر.',
  noGrantsAvailable: 'جميع محتويات هذه البطاقة لديك بالفعل.',
};
```

---

## Advanced Topics

### Content Unlock Verification

After successful redemption, the content is **instantly unlocked**. To verify:

1. Call existing access check endpoint (from Phase 3)
2. Refresh user's subscription list
3. Show unlocked content in UI

```typescript
async function verifyContentUnlocked(grantId: string) {
  // Call your existing access/subscription endpoint
  const subscriptions = await fetchUserSubscriptions();
  const isUnlocked = subscriptions.some(sub => sub.grant_id === grantId);

  if (isUnlocked) {
    showUnlockedContent(grantId);
  } else {
    // Edge case: wait a moment for cache to update
    setTimeout(() => verifyContentUnlocked(grantId), 2000);
  }
}
```

### Handling Network Errors

```typescript
async function callVoucherAPI(endpoint: string, body: any) {
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${getJwtToken()}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body),
      // Add timeout
      signal: AbortSignal.timeout(15000) // 15 seconds
    });

    return { ok: response.ok, status: response.status, data: await response.json() };

  } catch (err) {
    if (err.name === 'TimeoutError') {
      throw new Error('REQUEST_TIMEOUT');
    }
    if (err.name === 'AbortError') {
      throw new Error('REQUEST_CANCELLED');
    }
    throw new Error('NETWORK_ERROR');
  }
}
```

### QR Code Support (Optional)

If voucher cards have QR codes:

```typescript
import { Camera } from 'expo-camera'; // or react-native-camera

async function scanVoucherQR() {
  const { status } = await Camera.requestCameraPermissionsAsync();

  if (status !== 'granted') {
    showError('يرجى السماح بالوصول إلى الكاميرا لمسح رمز QR.');
    return;
  }

  // Show QR scanner
  const scannedData = await showQRScanner();

  // Extract PIN from QR data (format depends on your QR design)
  const pin = extractPinFromQR(scannedData);

  setPin(pin);
  handlePreview();
}
```

---

## Security Notes

### Important Security Requirements

1. **Always use HTTPS in production** - PIN is sent in plaintext over the wire
2. **Store JWT securely** - use device keychain/keystore, not localStorage
3. **Never log PINs** - not in analytics, crash reports, or console logs
4. **Validate input locally** - PIN should be 6-20 characters alphanumeric
5. **Handle rate limiting gracefully** - don't allow users to spam the endpoint

### Example Security Implementation

```typescript
// Bad ❌
console.log('Redeeming PIN:', pin);

// Good ✅
console.log('Redeeming voucher...');
```

```typescript
// Bad ❌
localStorage.setItem('jwt', token);

// Good ✅ (React Native)
import * as SecureStore from 'expo-secure-store';
await SecureStore.setItemAsync('jwt', token);
```

---

## Support & Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| 403 Forbidden | Missing/invalid JWT | Re-authenticate user |
| 503 Service Unavailable | Redis down | Retry after 30s |
| Network timeout | Slow connection | Implement retry logic |
| 429 Rate Limited | Too many failures | Show countdown timer |
| Content not appearing | Cache delay | Poll access endpoint |

### Debug Checklist

- [ ] JWT token is valid and not expired
- [ ] Authorization header is correctly formatted
- [ ] Content-Type header is set to `application/json`
- [ ] Request body is valid JSON
- [ ] PIN is exactly as printed on physical card (no spaces/dashes)
- [ ] Backend is running on correct port (8002)
- [ ] HTTPS is used in production
- [ ] Client IP is correctly detected for rate limiting

### Contact Information

For backend issues or questions:
- Check backend logs at `/var/log/memora/`
- Report bugs at GitHub issues (if applicable)
- Contact backend team via [your communication channel]

---

## Appendix

### Full TypeScript Definitions

```typescript
// Request types
interface VoucherPreviewRequest {
  pin: string;
}

interface VoucherRedeemRequest {
  pin: string;
  grant_id: string;
}

// Response types
interface VoucherGrant {
  grant_id: string;
  name: string;
}

interface VoucherPreviewResponse {
  face_value: string;
  grants: VoucherGrant[];
}

interface VoucherRedeemResponse {
  status: 'success';
  transaction_id: string;
}

interface VoucherErrorResponse {
  error: string;
  retry_after?: number;
}

// Error codes enum
enum VoucherErrorCode {
  INVALID_PIN = 'INVALID_PIN',
  NOT_ALLOCATED = 'NOT_ALLOCATED',
  ALREADY_REDEEMED = 'ALREADY_REDEEMED',
  EXPIRED = 'EXPIRED',
  VOID = 'VOID',
  BATCH_INACTIVE = 'BATCH_INACTIVE',
  SEASON_INACTIVE = 'SEASON_INACTIVE',
  ALL_GRANTS_OWNED = 'ALL_GRANTS_OWNED',
  GRANT_NOT_IN_BATCH = 'GRANT_NOT_IN_BATCH',
  ALREADY_OWNED = 'ALREADY_OWNED',
  RATE_LIMITED = 'RATE_LIMITED',
  SERVICE_UNAVAILABLE = 'SERVICE_UNAVAILABLE',
  INTERNAL_ERROR = 'INTERNAL_ERROR',
}

// API client
class VoucherAPIClient {
  constructor(
    private baseURL: string,
    private getJWT: () => Promise<string>
  ) {}

  async preview(pin: string): Promise<VoucherPreviewResponse> {
    const jwt = await this.getJWT();
    const response = await fetch(`${this.baseURL}/api/v1/voucher/preview`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${jwt}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ pin }),
    });

    if (!response.ok) {
      const error: VoucherErrorResponse = await response.json();
      throw new VoucherError(error.error, response.status, error.retry_after);
    }

    return response.json();
  }

  async redeem(pin: string, grantId: string): Promise<VoucherRedeemResponse> {
    const jwt = await this.getJWT();
    const response = await fetch(`${this.baseURL}/api/v1/voucher/redeem`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${jwt}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ pin, grant_id: grantId }),
    });

    if (!response.ok) {
      const error: VoucherErrorResponse = await response.json();
      throw new VoucherError(error.error, response.status, error.retry_after);
    }

    return response.json();
  }
}

// Custom error class
class VoucherError extends Error {
  constructor(
    public code: string,
    public httpStatus: number,
    public retryAfter?: number
  ) {
    super(`Voucher error: ${code}`);
    this.name = 'VoucherError';
  }
}
```

### Example React Hook

```typescript
import { useState, useCallback } from 'react';

interface UseVoucherResult {
  preview: (pin: string) => Promise<VoucherPreviewResponse | null>;
  redeem: (pin: string, grantId: string) => Promise<boolean>;
  isLoading: boolean;
  error: string | null;
}

export function useVoucher(): UseVoucherResult {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const client = new VoucherAPIClient(API_BASE_URL, getJWTToken);

  const preview = useCallback(async (pin: string) => {
    setIsLoading(true);
    setError(null);

    try {
      const result = await client.preview(pin);
      return result;
    } catch (err) {
      if (err instanceof VoucherError) {
        setError(getArabicErrorMessage(err.code));
      } else {
        setError('حدث خطأ في الاتصال');
      }
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [client]);

  const redeem = useCallback(async (pin: string, grantId: string) => {
    setIsLoading(true);
    setError(null);

    try {
      await client.redeem(pin, grantId);
      return true;
    } catch (err) {
      if (err instanceof VoucherError) {
        setError(getArabicErrorMessage(err.code));
      } else {
        setError('حدث خطأ في الاتصال');
      }
      return false;
    } finally {
      setIsLoading(false);
    }
  }, [client]);

  return { preview, redeem, isLoading, error };
}
```

---

## Document Version

- **Version:** 1.0
- **Last Updated:** 2026-02-14
- **Backend Version:** v3.0 (Phases 33-38)
- **Maintained By:** Backend Team

---

## Quick Reference Card

### Endpoints
```
POST /api/v1/voucher/preview   → Preview card (no rate limit)
POST /api/v1/voucher/redeem    → Redeem card (rate limited on failure)
```

### Key Error Codes
```
404 INVALID_PIN       → Wrong PIN
409 ALREADY_REDEEMED  → Card used
409 ALREADY_OWNED     → Try different grant (card NOT consumed)
429 RATE_LIMITED      → Too many attempts (5/hour)
```

### Rate Limits
```
5 failed attempts/hour per student
20 failed attempts/hour per IP
Unlimited previews
```

### Authentication
```
Header: Authorization: Bearer <jwt_token>
```
