# PRD: Web Push Notification Service

**Version:** 1.0
**Date:** 2026-04-06
**Status:** Draft

---

## Problem

There is no way to reach players when they are not actively using the app. Announcements, ads, and time-sensitive alerts (challenge reminders, subscription expiry) only appear inside the app — if the player doesn't open it, they never see them.

---

## Solution

Build a **generic Web Push notification service** using the W3C Push API standard. No third-party services required — uses VAPID (Voluntary Application Server Identification) keys and the browser's built-in push service.

**First use case**: Push announcements to all players (or plan-targeted subsets) when an admin publishes a `Memora Announcement`.

Future use cases (wired later, zero service changes needed): challenge start reminders, subscription expiry warnings, ad campaigns, achievement unlocks.

---

## How Web Push Works

```
┌──────────────┐      ┌──────────────────┐      ┌─────────────────┐
│  Admin Panel  │      │  Memora Backend   │      │  Browser Push    │
│  (Frappe)     │─────▶│  (Frappe worker)  │─────▶│  Service         │
│               │      │  pywebpush + VAPID│      │  (Chrome/Firefox)│
└──────────────┘      └──────────────────┘      └────────┬────────┘
                                                          │
                                                          ▼
                                                 ┌─────────────────┐
                                                 │  Player Browser   │
                                                 │  (Service Worker) │
                                                 │  → OS Notification│
                                                 └─────────────────┘
```

1. Player's browser registers a Service Worker and calls `pushManager.subscribe()` with the server's VAPID public key
2. Browser returns a **subscription object** (endpoint URL + encryption keys) — stored in `push_token` field on `Memora Player Device`
3. When admin publishes an announcement, a Frappe background job reads all subscriptions and sends encrypted payloads via `pywebpush`
4. Browser push service (run by Google/Mozilla/Apple as part of the web standard) delivers to the Service Worker
5. Service Worker shows an OS-level notification — works even when the tab is closed

**No Firebase, no APNs certificates, no third-party accounts needed.**

---

## Existing Infrastructure (Already Built)

| Component | Status | Location |
|-----------|--------|----------|
| `push_token` field on Player Device | Exists (Text field) | `memora_player_device.json` |
| `notifications` preference on Player Profile | Exists (Check field, default=enabled) | `memora_player_profile.json` |
| Device registration with platform detection | Exists | `fastapi_app/services/device.py` |
| Push token stored in Redis per device | Exists | `memora:devices:{user_id}` hash |
| Device sync (Redis → Frappe) | Exists | `memora_admin/events/device_sync.py` |
| Announcement DocType with targeting | Exists | `memora_announcement.json` |
| WebSocket real-time notifications | Exists | `fastapi_app/api/v1/endpoints/notifications.py` |

---

## What Needs to Be Built

### Phase 1: Push Infrastructure (Generic Service)

#### 1.1 VAPID Key Management

**Store in**: `Memora Settings` DocType (existing singleton)

| Field | Type | Description |
|-------|------|-------------|
| `vapid_public_key` | Small Text | Base64url-encoded VAPID public key |
| `vapid_private_key` | Password | Base64url-encoded VAPID private key (encrypted at rest) |
| `vapid_contact_email` | Data | Required by VAPID spec — admin contact email |

**Generation**: Add a button "Generate VAPID Keys" on Memora Settings form that generates a key pair using `py_vapid` (bundled with `pywebpush`). Keys are generated once and persist forever.

#### 1.2 Push Subscription Endpoint (FastAPI)

New endpoint to receive and store browser push subscriptions:

```
POST /api/v1/push/subscribe
```

**Request body:**
```json
{
  "subscription": {
    "endpoint": "https://fcm.googleapis.com/fcm/send/...",
    "keys": {
      "p256dh": "base64url-encoded-key",
      "auth": "base64url-encoded-key"
    }
  }
}
```

**Logic:**
1. Extract `user_id` from JWT (existing auth)
2. Extract `device_id` from session (existing device tracking)
3. Serialize the subscription object as JSON string
4. Store in Redis: `HSET memora:devices:{user_id} device:{device_id}:push_sub <json>`
5. Also update `push_token` field on the Player Device child row (via device sync, already exists)

**Unsubscribe:**
```
DELETE /api/v1/push/subscribe
```
Removes the push subscription from Redis for the current device.

#### 1.3 VAPID Public Key Endpoint (FastAPI)

Frontend needs the public key to subscribe:

```
GET /api/v1/push/vapid-key
```

**Response:**
```json
{
  "public_key": "base64url-encoded-vapid-public-key"
}
```

This is a public endpoint (no auth required) — the public key is not secret.

#### 1.4 Push Notification Service (Frappe-side)

New file: `memora_admin/services/push_service.py`

Core function:

```python
def send_push_notification(
    title: str,
    body: str,
    url: str | None = None,
    icon: str | None = None,
    target_players: list[str] | None = None,  # None = all players
    target_plans: list[str] | None = None,     # filter by plan
) -> dict:
    """Send web push notification to targeted players.

    Returns: {"sent": int, "failed": int, "stale_removed": int}
    """
```

**Implementation:**
1. Load VAPID keys from `Memora Settings`
2. Build player list:
   - If `target_players` is provided: use that list
   - If `target_plans` is provided: query players in those plans
   - If neither: all players with `notifications=1`
3. For each player, read push subscriptions from Redis (`memora:devices:{user_id}`)
4. Filter to devices that have a `push_sub` value
5. Build the notification payload:
   ```json
   {
     "title": "...",
     "body": "...",
     "url": "/announcements/ANN-00123",
     "icon": "/assets/memora_admin/images/icon-192.png"
   }
   ```
6. Send in batches (500 per batch, 1-second pause between batches) using `pywebpush.webpush()`
7. Handle errors:
   - **410 Gone** or **404 Not Found**: subscription expired — delete from Redis + mark stale
   - **429 Too Many Requests**: back off and retry
   - Other errors: log and continue
8. Return summary stats

**Concurrency**: Use `concurrent.futures.ThreadPoolExecutor` (max 10 workers) within each batch — `pywebpush` is an HTTP call, parallelism helps.

#### 1.5 Admin API for Manual Push

New file: `memora_admin/api/push.py`

```python
@frappe.whitelist(methods=["POST"])
def send_push(title, body, url=None, icon=None, target_plans=None):
    """Admin-triggered push notification. Enqueues as background job."""
    frappe.enqueue(
        "memora_admin.services.push_service.send_push_notification",
        title=title,
        body=body,
        url=url,
        icon=icon,
        target_plans=frappe.parse_json(target_plans) if target_plans else None,
        queue="long",
    )
    return {"status": "queued"}
```

Permission: `Memora Admin` or `System Manager` role only.

---

### Phase 2: Announcement Integration

#### 2.1 Auto-Push on Announcement Publish

Add a `doc_event` hook in `hooks.py`:

```python
# hooks.py
doc_events = {
    "Memora Announcement": {
        "on_update": "memora_admin.events.push_events.on_announcement_updated",
    },
}
```

New file: `memora_admin/events/push_events.py`

```python
def on_announcement_updated(doc, method):
    """Send push notification when an announcement is published for the first time."""
    if not doc.is_published:
        return
    was_published = doc._doc_before_save and doc._doc_before_save.is_published
    if was_published:
        return  # Already published before — don't re-push

    target_plans = None
    if doc.target_audience == "Specific Plans":
        target_plans = [row.plan for row in doc.target_plans]

    frappe.enqueue(
        "memora_admin.services.push_service.send_push_notification",
        title=doc.title_ar,  # Arabic-first for target audience
        body=frappe.utils.strip_html(doc.body_ar)[:200],
        url=f"/announcements/{doc.name}",
        target_plans=target_plans,
        queue="long",
    )
```

#### 2.2 Add `send_push` Field to Announcement DocType

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `send_push` | Check | 1 | Whether to send a push notification when published |

This gives admins control — some announcements may be in-app only.

Update the hook to check `doc.send_push` before enqueueing.

---

### Phase 3: Frontend Requirements (for web team)

These are the frontend changes needed. The backend PRD defines the contracts — frontend implementation is out of scope for the backend agent.

#### 3.1 Service Worker

A `sw.js` file at the web app root that handles `push` events:

```javascript
// sw.js
self.addEventListener('push', (event) => {
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: data.icon || '/icon-192.png',
      data: { url: data.url },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.notification.data.url) {
    event.waitUntil(clients.openWindow(event.notification.data.url));
  }
});
```

#### 3.2 Subscription Flow

On login (or on app load if already authenticated):

1. Check if notifications are supported: `'Notification' in window && 'serviceWorker' in navigator`
2. Register the Service Worker: `navigator.serviceWorker.register('/sw.js')`
3. Request permission: `Notification.requestPermission()`
4. If granted, fetch VAPID public key from `GET /api/v1/push/vapid-key`
5. Subscribe: `registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidPublicKey })`
6. Send subscription to backend: `POST /api/v1/push/subscribe` with the subscription object

#### 3.3 Permission UX

- Show a custom in-app prompt BEFORE the browser prompt (better acceptance rates)
- Arabic text explaining what notifications they'll receive
- "Enable Notifications" button triggers the actual browser permission request
- If denied, respect it — show a settings hint but don't re-prompt

---

## New Dependency

| Package | Version | Purpose |
|---------|---------|---------|
| `pywebpush` | `>=2.0,<3.0` | VAPID signing + Web Push protocol. Includes `py_vapid` for key generation. Pure Python, no native deps. |

Add to `requirements.txt`.

---

## Data Flow

```
Admin clicks "Publish" on Announcement
  │
  ▼
Frappe doc_event fires (on_update)
  │
  ├─ Check: is_published changed to 1? send_push enabled?
  │
  ▼
frappe.enqueue() → background worker (long queue)
  │
  ▼
push_service.send_push_notification()
  │
  ├─ Load VAPID keys from Memora Settings
  ├─ Build player list (all or by plan)
  ├─ Read push subscriptions from Redis
  ├─ Batch send (500/batch, 10 threads)
  │   ├─ pywebpush.webpush() → Browser Push Service
  │   ├─ 410/404 → remove stale subscription
  │   └─ 429 → backoff retry
  │
  ▼
Return: {sent: N, failed: N, stale_removed: N}
```

---

## Performance Impact

| Concern | Impact |
|---------|--------|
| **FastAPI hot path** | Zero — push runs in Frappe background workers |
| **Redis (13001)** | Negligible — subscription data is small (~300 bytes per device) |
| **CPU** | VAPID signing ~0.1ms per message |
| **Bulk send (100k players)** | ~200 seconds in background (500/batch, 10 threads, 1s pause). No user-facing latency. |
| **Player API latency** | Unchanged — sub-20ms targets unaffected |

---

## Redis Key Design

Add to `fastapi_app/core/redis_keys.py`:

```python
# Push subscription is stored as a field within the existing device hash
# Key: memora:devices:{user_id}
# Field: device:{device_id}:push_sub
# Value: JSON-serialized PushSubscription object
# TTL: Inherits from device hash TTL (no separate TTL)
```

No new top-level Redis keys — push subscriptions live inside the existing device hash.

---

## Files to Create

| File | Purpose |
|------|---------|
| `memora_admin/services/push_service.py` | Core send logic (batched, threaded, error handling) |
| `memora_admin/api/push.py` | Admin API for manual push sends |
| `memora_admin/events/push_events.py` | Hooks for auto-push on announcement publish |
| `fastapi_app/api/v1/endpoints/push.py` | Subscribe/unsubscribe + VAPID key endpoints |

## Files to Modify

| File | Change |
|------|--------|
| `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json` | Add VAPID key fields + generate button |
| `memora_admin/memora_admin/doctype/memora_settings/memora_settings.py` | Key generation logic |
| `memora_admin/memora_admin/doctype/memora_settings/memora_settings.js` | Generate keys button handler |
| `memora_admin/memora_admin/doctype/memora_announcement/memora_announcement.json` | Add `send_push` check field |
| `memora_admin/hooks.py` | Add announcement doc_event for push |
| `fastapi_app/api/v1/router.py` | Register push endpoints |
| `fastapi_app/core/redis_keys.py` | Document push subscription field pattern |
| `requirements.txt` | Add `pywebpush>=2.0,<3.0` |

---

## Validation & Error Handling

- **Invalid subscription**: `pywebpush` raises `WebPushException` — catch and log
- **Expired subscription (410/404)**: Auto-remove from Redis, increment `stale_removed` counter
- **Rate limited (429)**: Exponential backoff (1s, 2s, 4s), max 3 retries per subscription
- **VAPID keys not configured**: `send_push_notification()` logs warning and returns early
- **No subscriptions found**: Return `{sent: 0}` — not an error

---

## Out of Scope

- Mobile push (iOS/Android) — web only
- Rich notifications (images, action buttons) — v2 enhancement
- Notification history/inbox in the app — handled by existing WebSocket system
- Scheduled push (send at specific time) — use Frappe's scheduled jobs if needed later
- Analytics (open rates, click rates) — v2 enhancement
- Per-player notification preferences beyond the existing `notifications` toggle
- Frontend implementation (Service Worker, subscription UI) — separate frontend task

---

## Security

- VAPID private key stored in Frappe's `Password` field type (encrypted at rest in `__Auth` table)
- Push subscription endpoint requires valid JWT (authenticated players only)
- Admin send API restricted to `Memora Admin` / `System Manager` roles
- Notification payload is encrypted end-to-end by the Web Push protocol (browser push service cannot read content)
