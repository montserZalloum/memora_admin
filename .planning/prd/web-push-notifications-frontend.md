# PRD: Web Push Notifications — Frontend

**Version:** 1.0
**Date:** 2026-04-06
**Status:** Draft
**Depends on:** [Backend PRD](web-push-notifications.md) (implemented)

---

## Context

The backend Web Push infrastructure is live — VAPID key management, push subscription storage, and bulk-send service are deployed. This PRD covers the **frontend changes** needed to complete the feature: Service Worker registration, push subscription flow, notification display, and permission UX.

**Stack**: React + TypeScript + Zustand + Vite
**Backend**: FastAPI sidecar at `https://x.conanacademy.com`
**Target audience**: Arabic-speaking students on mobile web browsers

---

## What the Backend Provides

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/v1/push/vapid-key` | GET | None | Returns VAPID public key for `pushManager.subscribe()` |
| `/api/v1/push/subscribe` | POST | JWT + `X-Device-ID` | Stores browser push subscription on the server |
| `/api/v1/push/subscribe` | DELETE | JWT + `X-Device-ID` | Removes push subscription from the server |

**Push payload format** (received by Service Worker):

```json
{
  "title": "عنوان الإشعار",
  "body": "نص الإشعار (حتى 200 حرف)",
  "url": "/announcements/ANN-00123",
  "icon": "/assets/memora_admin/images/icon-192.png"
}
```

---

## Scope

### In scope
- Service Worker for receiving and displaying push notifications
- Push subscription lifecycle (subscribe, unsubscribe, re-subscribe)
- Permission request UX (custom pre-prompt in Arabic)
- Notification click handling (open URL)
- Notification preference toggle (sync with existing `notifications` profile field)

### Out of scope
- Notification history / inbox UI (handled by existing WebSocket system)
- Rich notifications (images, action buttons) — v2
- Offline page / PWA install prompt — separate feature
- iOS Safari quirks (Safari supports Web Push since iOS 16.4 but requires the app to be added to Home Screen first — documented as a known limitation, not solved here)

---

## 1. Service Worker

### 1.1 File: `/sw.js`

Must be served from the web app root (same origin) so it can control all pages.

```javascript
// sw.js — Web Push Service Worker

self.addEventListener("push", (event) => {
  if (!event.data) return;

  let data;
  try {
    data = event.data.json();
  } catch {
    // Fallback for plain text payloads
    data = { title: "كونان أكاديمي", body: event.data.text() };
  }

  const options = {
    body: data.body,
    icon: data.icon || "/icon-192.png",
    badge: "/badge-72.png",
    dir: "rtl",
    lang: "ar",
    data: { url: data.url },
    tag: data.url || "default",      // Collapse duplicate notifications
    renotify: true,                   // Vibrate even if replacing
  };

  event.waitUntil(self.registration.showNotification(data.title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = event.notification.data?.url;
  if (!url) return;

  // Focus existing tab or open new one
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes(url) && "focus" in client) {
          return client.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
```

**Key decisions:**
- `dir: "rtl"` and `lang: "ar"` — Arabic-first for target audience
- `tag` based on URL — prevents duplicate notifications for the same announcement
- `renotify: true` — still vibrates when replacing a tagged notification
- `notificationclick` focuses an existing tab if one matches the URL, otherwise opens new

### 1.2 Registration

Register the Service Worker on app load (not on login — SW must be active before push subscription):

```typescript
// services/serviceWorker.ts

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) {
    console.warn("Service Worker not supported");
    return null;
  }

  try {
    const registration = await navigator.serviceWorker.register("/sw.js", {
      scope: "/",
    });
    console.log("SW registered, scope:", registration.scope);
    return registration;
  } catch (error) {
    console.error("SW registration failed:", error);
    return null;
  }
}
```

Call `registerServiceWorker()` once in the app root component (`App.tsx`) on mount. Store the registration in a module-level variable or Zustand store for later use by the subscription flow.

---

## 2. Push Subscription Flow

### 2.1 Core Logic

```typescript
// services/pushNotifications.ts

import { getDeviceId } from "./deviceId";
import { api } from "./api";

/** Convert base64url string to Uint8Array (for applicationServerKey). */
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from(rawData, (char) => char.charCodeAt(0));
}

/** Fetch VAPID public key from backend (cached after first call). */
let cachedVapidKey: string | null = null;

async function getVapidKey(): Promise<string> {
  if (cachedVapidKey) return cachedVapidKey;

  const response = await fetch("/api/v1/push/vapid-key");
  if (!response.ok) throw new Error("Failed to fetch VAPID key");

  const data = await response.json();
  cachedVapidKey = data.public_key;
  return data.public_key;
}

/**
 * Subscribe the current browser to push notifications.
 *
 * Call AFTER:
 * 1. User has granted notification permission
 * 2. Service Worker is registered
 * 3. User is authenticated (has valid JWT)
 */
export async function subscribeToPush(
  registration: ServiceWorkerRegistration
): Promise<boolean> {
  try {
    const vapidKey = await getVapidKey();
    const applicationServerKey = urlBase64ToUint8Array(vapidKey);

    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey,
    });

    // Send subscription to backend
    await api.post("/api/v1/push/subscribe", {
      subscription: subscription.toJSON(),
    }, {
      headers: { "X-Device-ID": getDeviceId() },
    });

    return true;
  } catch (error) {
    console.error("Push subscription failed:", error);
    return false;
  }
}

/**
 * Unsubscribe from push notifications.
 * Removes both the browser subscription and the server-side record.
 */
export async function unsubscribeFromPush(
  registration: ServiceWorkerRegistration
): Promise<boolean> {
  try {
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      await subscription.unsubscribe();
    }

    await api.delete("/api/v1/push/subscribe", {
      headers: { "X-Device-ID": getDeviceId() },
    });

    return true;
  } catch (error) {
    console.error("Push unsubscribe failed:", error);
    return false;
  }
}

/**
 * Check current push subscription state.
 * Returns "granted", "denied", "default" (not asked), or "unsupported".
 */
export function getPushPermissionState(): string {
  if (!("Notification" in window) || !("serviceWorker" in navigator)) {
    return "unsupported";
  }
  return Notification.permission; // "granted" | "denied" | "default"
}

/**
 * Check if the current browser has an active push subscription.
 */
export async function hasActiveSubscription(
  registration: ServiceWorkerRegistration
): Promise<boolean> {
  const subscription = await registration.pushManager.getSubscription();
  return subscription !== null;
}
```

### 2.2 Subscription Lifecycle

```
App loads
  │
  ├─ Register Service Worker (always, even before login)
  │
  ▼
User logs in successfully
  │
  ├─ Check: Notification.permission
  │
  ├─ "granted" ──────────────────┐
  │                               │
  ├─ "default" (never asked) ──► Show custom pre-prompt (Section 3)
  │                               │ User taps "Enable" ──► Notification.requestPermission()
  │                               │   ├─ granted ─────────┤
  │                               │   └─ denied ──► Done (respect it)
  │                               │
  ├─ "denied" ──► Done            │
  │                               ▼
  │                        subscribeToPush(registration)
  │                               │
  │                        POST /api/v1/push/subscribe
  │                               │
  │                        ✓ Subscription stored on server
  │
  ▼
User toggles "Notifications" OFF in settings
  │
  ├─ unsubscribeFromPush(registration)
  ├─ DELETE /api/v1/push/subscribe
  └─ Update profile: notifications = 0
```

### 2.3 Re-subscription on Token Refresh

Browser push subscriptions can silently expire or change. On each app load (after login), check and re-subscribe if needed:

```typescript
// In the post-login or app-load hook
async function ensurePushSubscription(registration: ServiceWorkerRegistration) {
  if (Notification.permission !== "granted") return;

  const existing = await registration.pushManager.getSubscription();
  if (existing) {
    // Already subscribed — send to backend in case it was lost (Redis flush, etc.)
    try {
      await api.post("/api/v1/push/subscribe", {
        subscription: existing.toJSON(),
      }, {
        headers: { "X-Device-ID": getDeviceId() },
      });
    } catch {
      // Non-critical — subscription may already exist
    }
    return;
  }

  // No subscription — re-subscribe
  await subscribeToPush(registration);
}
```

This handles:
- Redis data loss (cache flush) — re-sends subscription to backend
- Browser subscription expiry — re-subscribes with fresh PushSubscription
- Device switch — new device gets its own subscription

---

## 3. Permission Request UX

### 3.1 Design Principle

**Never trigger the browser permission prompt without context.** Browsers penalize sites that show the native prompt immediately — and users who click "Block" cannot easily undo it.

Instead, show a **custom in-app pre-prompt** first. If the user says yes, then trigger the native browser prompt.

### 3.2 Pre-Prompt Component

Show this component **once** after the first successful login, or when the user navigates to a section where notifications add value (e.g., announcements page).

```
┌─────────────────────────────────────────────┐
│                                             │
│    🔔                                       │
│                                             │
│    تفعيل الإشعارات                          │
│                                             │
│    احصل على تنبيهات فورية عند وجود          │
│    إعلانات جديدة أو تحديات قادمة            │
│                                             │
│    ┌───────────────────────────────────┐    │
│    │         تفعيل الإشعارات           │    │
│    └───────────────────────────────────┘    │
│                                             │
│              ليس الآن                       │
│                                             │
└─────────────────────────────────────────────┘
```

**Translations:**
- Title: "تفعيل الإشعارات" (Enable Notifications)
- Body: "احصل على تنبيهات فورية عند وجود إعلانات جديدة أو تحديات قادمة" (Get instant alerts for new announcements and upcoming challenges)
- Button: "تفعيل الإشعارات" (Enable Notifications)
- Dismiss: "ليس الآن" (Not now)

### 3.3 Component Behavior

```typescript
// components/PushPermissionPrompt.tsx

interface Props {
  onDismiss: () => void;
}

export function PushPermissionPrompt({ onDismiss }: Props) {
  const [loading, setLoading] = useState(false);

  async function handleEnable() {
    setLoading(true);

    const permission = await Notification.requestPermission();
    if (permission === "granted") {
      const registration = await navigator.serviceWorker.ready;
      await subscribeToPush(registration);
    }

    setLoading(false);
    onDismiss(); // Close prompt regardless of outcome
  }

  function handleNotNow() {
    // Record dismissal so we don't show again this session
    sessionStorage.setItem("push_prompt_dismissed", "1");
    onDismiss();
  }

  // ... render UI
}
```

### 3.4 When to Show

| Trigger | Condition | Show? |
|---------|-----------|-------|
| First login ever | `Notification.permission === "default"` AND no `push_prompt_dismissed` in sessionStorage | Yes |
| Subsequent app loads | Permission is `"default"` AND last dismissal was >7 days ago | Yes |
| Permission is `"granted"` | Already subscribed | No |
| Permission is `"denied"` | Browser blocked — nothing we can do | No |
| User toggled notifications OFF in settings | Explicit opt-out | No |

**Persistence**: Store the last dismissal timestamp in `localStorage` as `push_prompt_dismissed_at`. Re-show after 7 days.

### 3.5 Denied State Guidance

If the user previously blocked notifications (`Notification.permission === "denied"`), show a subtle hint in the notification settings page:

```
الإشعارات محظورة من المتصفح.
لتفعيلها، اضغط على رمز القفل بجانب عنوان الموقع واسمح بالإشعارات.
```

(Notifications are blocked by the browser. To enable them, tap the lock icon next to the site address and allow notifications.)

Do not show a button — there is no programmatic way to undo a browser-level denial.

---

## 4. Notification Settings Integration

### 4.1 Existing Field

The `Memora Player Profile` already has a `notifications` checkbox (default: enabled). This field controls whether the backend includes the player in push sends.

### 4.2 Settings UI

In the player's settings/profile page, add a toggle:

```
┌─────────────────────────────────────────┐
│  الإشعارات                              │
│                                         │
│  ┌─────┐  تلقي إشعارات عند وجود        │
│  │ ON  │  إعلانات أو تحديات جديدة      │
│  └─────┘                                │
└─────────────────────────────────────────┘
```

**Toggle behavior:**

```typescript
async function handleNotificationToggle(enabled: boolean) {
  const registration = await navigator.serviceWorker.ready;

  if (enabled) {
    // Check browser permission first
    if (Notification.permission === "default") {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") return; // Don't enable if browser denied
    } else if (Notification.permission === "denied") {
      // Show browser settings hint
      return;
    }

    await subscribeToPush(registration);
    await api.patch("/api/v1/profile", { notifications: true });
  } else {
    await unsubscribeFromPush(registration);
    await api.patch("/api/v1/profile", { notifications: false });
  }
}
```

**Toggle state** reflects three sources:
1. Backend `notifications` field (server-side opt-out)
2. `Notification.permission` (browser-level permission)
3. Active `PushSubscription` (subscription exists)

The toggle should be **ON** only if all three are true. If the browser permission is `"denied"`, the toggle should be disabled with the hint text from Section 3.5.

---

## 5. Zustand Store

### 5.1 Push Store

```typescript
// stores/pushStore.ts

interface PushState {
  /** Service Worker registration (null if not supported or not registered). */
  swRegistration: ServiceWorkerRegistration | null;

  /** Browser notification permission: "granted" | "denied" | "default" | "unsupported". */
  permission: string;

  /** Whether an active PushSubscription exists on this browser. */
  isSubscribed: boolean;

  /** Whether the pre-prompt has been shown and dismissed this session. */
  promptDismissed: boolean;

  /** Initialize SW registration and check current state. */
  init: () => Promise<void>;

  /** Subscribe to push (after permission granted). */
  subscribe: () => Promise<boolean>;

  /** Unsubscribe from push. */
  unsubscribe: () => Promise<boolean>;

  /** Mark the pre-prompt as dismissed. */
  dismissPrompt: () => void;

  /** Re-check permission and subscription state. */
  refresh: () => Promise<void>;
}
```

### 5.2 Integration with Auth Store

After successful login, trigger push check:

```typescript
// In the login success handler (authStore or login page)
async function onLoginSuccess() {
  // ... existing logic (store tokens, redirect) ...

  // Check and restore push subscription
  const pushStore = usePushStore.getState();
  await pushStore.init();

  if (pushStore.permission === "granted" && pushStore.swRegistration) {
    await ensurePushSubscription(pushStore.swRegistration);
  }
}
```

---

## 6. API Integration Details

### 6.1 GET /api/v1/push/vapid-key

**No authentication required.** Can be called before login.

```typescript
const response = await fetch("/api/v1/push/vapid-key");
// 200: { "public_key": "BJ..." }
// 404: VAPID keys not configured (admin hasn't generated them yet)
```

Cache the response — the key never changes.

### 6.2 POST /api/v1/push/subscribe

**Requires:** JWT `Authorization` header + `X-Device-ID` header.

```typescript
const subscription = await registration.pushManager.subscribe({
  userVisibleOnly: true,
  applicationServerKey: urlBase64ToUint8Array(vapidKey),
});

await api.post("/api/v1/push/subscribe", {
  subscription: subscription.toJSON(),
  // toJSON() returns: { endpoint, keys: { p256dh, auth } }
}, {
  headers: { "X-Device-ID": getDeviceId() },
});

// 200: { "status": "subscribed" }
// 400: Missing X-Device-ID
// 401: Not authenticated
// 404: Device not registered
```

**Important:** The `subscription.toJSON()` output matches the backend's expected `PushSubscribeRequest` schema exactly. No transformation needed.

### 6.3 DELETE /api/v1/push/subscribe

**Requires:** JWT `Authorization` header + `X-Device-ID` header.

```typescript
await api.delete("/api/v1/push/subscribe", {
  headers: { "X-Device-ID": getDeviceId() },
});

// 200: { "status": "unsubscribed" }
```

### 6.4 Error Handling

| Status | Meaning | Frontend Action |
|--------|---------|-----------------|
| 200 | Success | Update push store state |
| 400 | Missing `X-Device-ID` | Bug — should never happen if `getDeviceId()` is wired |
| 401 | Token expired | Trigger token refresh, retry |
| 404 (vapid-key) | VAPID keys not configured | Silently skip push setup |
| 404 (subscribe) | Device not registered | Re-login required |
| 503 | Backend cannot reach Frappe | Retry with exponential backoff (max 3) |

---

## 7. Push Notification Payload Handling

### 7.1 Payload Schema

All push payloads follow this structure:

```typescript
interface PushPayload {
  title: string;     // Arabic notification title
  body: string;      // Arabic body text (max 200 chars)
  url?: string;      // In-app route to navigate to (e.g., "/announcements/ANN-00123")
  icon?: string;     // Icon URL (defaults to app icon)
}
```

### 7.2 Click Navigation

When the user taps a notification:
1. Service Worker's `notificationclick` handler fires
2. If the app is open in a tab, focus that tab and navigate to `url`
3. If no tab is open, `clients.openWindow(url)` opens the app at that route

The `url` value is a **relative in-app path** (e.g., `/announcements/ANN-00123`), not a full URL. The Service Worker's `openWindow` resolves it relative to the app origin.

### 7.3 Future Payload Extensions (v2)

These fields may be added later — the Service Worker should gracefully ignore unknown fields:
- `image`: Large image URL for rich notifications
- `actions`: Array of `{ action, title, icon }` for action buttons
- `badge_count`: Number to display on app badge

---

## 8. Browser Compatibility

| Browser | Push Support | Notes |
|---------|-------------|-------|
| Chrome (Android) | Full | Primary target. Works with tab closed. |
| Chrome (Desktop) | Full | Works even when browser is closed (OS-level). |
| Firefox | Full | Same as Chrome. |
| Safari (macOS 13+) | Full | Supported since macOS Ventura. |
| Safari (iOS 16.4+) | Partial | **Only works if app is added to Home Screen.** Standard browser tab does NOT support push. |
| Samsung Internet | Full | Uses Chrome's push service (FCM). |

### 8.1 iOS Safari Limitation

iOS Safari (16.4+) supports Web Push, but **only for web apps added to the Home Screen** (PWA mode). Standard Safari tabs do not support push.

**Frontend handling:**
- Detect iOS Safari: `navigator.userAgent` check for `iPhone` or `iPad` + no `CriOS`/`FxiOS`
- If push is unsupported, do not show the permission prompt
- Optionally show a hint: "أضف التطبيق إلى الشاشة الرئيسية لتلقي الإشعارات" (Add the app to the Home Screen to receive notifications)

```typescript
function isIOSSafari(): boolean {
  const ua = navigator.userAgent;
  const isIOS = /iPhone|iPad/.test(ua) && !("MSStream" in window);
  const isSafari = /Safari/.test(ua) && !/CriOS|FxiOS/.test(ua);
  return isIOS && isSafari;
}

function isPWAMode(): boolean {
  return window.matchMedia("(display-mode: standalone)").matches
    || (navigator as any).standalone === true;
}

function canUsePush(): boolean {
  if (!("Notification" in window) || !("serviceWorker" in navigator)) return false;
  if (isIOSSafari() && !isPWAMode()) return false;
  return true;
}
```

---

## 9. Testing Checklist

### Manual QA

- [ ] **SW registration**: Open DevTools → Application → Service Workers → `sw.js` shown as "activated and running"
- [ ] **Permission prompt**: Clear site data → login → custom Arabic prompt appears (not the browser prompt)
- [ ] **Browser prompt**: Tap "تفعيل الإشعارات" → native browser permission dialog appears
- [ ] **Grant permission**: Accept → subscription sent to backend (`POST /push/subscribe` in Network tab)
- [ ] **Receive notification**: Admin publishes announcement → OS notification appears with Arabic text, RTL layout
- [ ] **Notification click**: Tap notification → app opens to the announcement URL
- [ ] **Tab focus**: If app is already open, tapping notification focuses the existing tab
- [ ] **Collapse duplicates**: Two pushes with same URL → only one notification shown (tag-based)
- [ ] **Settings toggle OFF**: Disable notifications in settings → `DELETE /push/subscribe` sent → no more pushes
- [ ] **Settings toggle ON**: Re-enable → permission re-checked → subscription re-sent
- [ ] **Denied state**: Block in browser settings → toggle is disabled → hint text shown
- [ ] **Re-subscription on load**: Clear Redis (`HDEL` the push_sub field) → reload app → subscription re-sent automatically
- [ ] **iOS Safari**: Open in Safari (not PWA) → push prompt is NOT shown → hint about Home Screen is shown
- [ ] **iOS PWA**: Add to Home Screen → open PWA → push prompt works normally

### Automated (if applicable)

- [ ] Unit test: `urlBase64ToUint8Array` converts correctly
- [ ] Unit test: `canUsePush` returns false on unsupported browsers
- [ ] Unit test: `getPushPermissionState` returns correct values
- [ ] Integration test: `subscribeToPush` calls correct API with correct payload

---

## 10. Files to Create

| File | Purpose |
|------|---------|
| `/sw.js` | Service Worker (push handler + click handler) — served from app root |
| `src/services/pushNotifications.ts` | Subscribe/unsubscribe/permission logic |
| `src/services/serviceWorker.ts` | SW registration helper |
| `src/stores/pushStore.ts` | Zustand store for push state |
| `src/components/PushPermissionPrompt.tsx` | Arabic pre-prompt UI component |

## Files to Modify

| File | Change |
|------|--------|
| `src/App.tsx` | Register Service Worker on mount |
| `src/pages/Login.tsx` (or auth flow) | Call `ensurePushSubscription` after successful login |
| `src/pages/Settings.tsx` (or profile) | Add notification toggle with push integration |
| `vite.config.ts` | Ensure `/sw.js` is copied to build output root |

---

## 11. Sequence Diagram

```
Player opens app
  │
  ├─ navigator.serviceWorker.register("/sw.js")
  │
  ▼
Player logs in
  │
  ├─ POST /api/v1/auth/login → JWT tokens
  │
  ├─ Check Notification.permission
  │   ├─ "default" → Show PushPermissionPrompt
  │   │               User taps "تفعيل" → Notification.requestPermission()
  │   │                 ├─ "granted" ──┐
  │   │                 └─ "denied" ──► Done
  │   │                                │
  │   ├─ "granted" ────────────────────┤
  │   │                                │
  │   └─ "denied" ──► Done            │
  │                                    ▼
  │                    GET /api/v1/push/vapid-key
  │                                    │
  │                    pushManager.subscribe({ applicationServerKey })
  │                                    │
  │                    POST /api/v1/push/subscribe
  │                    Headers: Authorization, X-Device-ID
  │                    Body: { subscription: { endpoint, keys } }
  │                                    │
  │                    ✓ Server stores subscription in Redis
  │
  ▼
Admin publishes announcement
  │
  ├─ Backend sends push via pywebpush
  │
  ▼
Browser Push Service delivers to SW
  │
  ├─ SW: self.addEventListener("push") fires
  ├─ SW: showNotification(title, { body, icon, dir: "rtl" })
  │
  ▼
Player taps notification
  │
  ├─ SW: notificationclick → clients.openWindow(url)
  └─ App navigates to /announcements/ANN-00123
```
