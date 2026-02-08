# Product Requirements Document: Real-Time Subscription Notifications (Player App)

## Document Information

**Version:** 1.0
**Date:** 2026-02-08
**Author:** Memora Platform Team
**Target:** Player App AI Agent
**Related Phase:** Phase 24 - Real-Time Subscription Notifications

---

## Executive Summary

The Memora backend now supports **real-time WebSocket notifications** for subscription status changes. When an admin approves or rejects a player's purchase request, the player receives an instant notification without polling.

**Your mission:** Integrate WebSocket notifications into the Player App so users get immediate feedback when their subscription requests are processed.

---

## Background & Context

### Current User Flow (Before This Feature)

1. Player browses product catalog
2. Player submits purchase request → transaction goes to "Pending Approval"
3. Player sees pending product disappear from catalog (prevents duplicate purchases)
4. Admin approves/rejects transaction in Frappe Desk
5. **Player must refresh app or re-login** to see new content access ❌

### New User Flow (With WebSocket Notifications)

1. Player browses product catalog
2. Player submits purchase request → transaction goes to "Pending Approval"
3. Player sees pending product disappear from catalog
4. Admin approves/rejects transaction in Frappe Desk
5. **Player receives WebSocket notification within 20ms** ✅
6. App automatically updates UI (shows new content or rejection message)

### Why This Matters

- **Instant feedback:** No more waiting or refreshing
- **Better UX:** Player knows immediately if their purchase was approved
- **Scalability:** WebSocket connections are lightweight (~14 KiB/connection)
- **Real-time:** <20ms propagation from admin action to client notification

---

## Technical Architecture

### Backend Components (Already Built)

```
┌─────────────────────────────────────────────────────────────┐
│ Frappe Desk (Admin Panel)                                   │
│  ↓ Admin clicks "Approve" or "Reject"                       │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ Frappe: Subscription Transaction Handler                     │
│  - Creates Player Subscription records (approval only)       │
│  - Publishes notification to Redis pub/sub                   │
│    Channel: memora:notify:{player_id}                        │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ Redis Pub/Sub                                                │
│  - Broadcasts notification to all FastAPI instances          │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ FastAPI: Notification Listener                               │
│  - Receives pub/sub message                                  │
│  - Forwards to ConnectionManager                             │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ ConnectionManager                                            │
│  - Sends JSON message to all WebSocket connections           │
│    for this player (multi-device support)                    │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ Player App (WebSocket Client) ← YOUR WORK STARTS HERE       │
└─────────────────────────────────────────────────────────────┘
```

---

## Requirements

### 1. WebSocket Connection Management

#### 1.1 Connection Establishment

**Endpoint:**
```
ws://127.0.0.1:8002/api/v1/notifications/ws?token={JWT_ACCESS_TOKEN}
```
(Replace with production URL: `wss://api.memora.com/api/v1/notifications/ws?token={JWT}`)

**Authentication:**
- **Method:** JWT token passed as query parameter `?token=...`
- **Token Type:** Access token (NOT refresh token)
- **Token Source:** Use the same access token from login response
- **Security:** Token is validated BEFORE WebSocket handshake
- **Invalid Token Behavior:** Server closes connection with code `1008` (Policy Violation)

**Connection Lifecycle:**

```javascript
// Pseudocode for connection lifecycle
function connectWebSocket() {
  const accessToken = getAccessTokenFromStorage();

  if (!accessToken) {
    console.warn("No access token - skip WebSocket connection");
    return;
  }

  const ws = new WebSocket(
    `wss://api.memora.com/api/v1/notifications/ws?token=${accessToken}`
  );

  ws.onopen = () => {
    console.log("WebSocket connected");
    // Ready to receive notifications
  };

  ws.onmessage = (event) => {
    handleNotification(JSON.parse(event.data));
  };

  ws.onerror = (error) => {
    console.error("WebSocket error:", error);
  };

  ws.onclose = (event) => {
    console.log("WebSocket closed:", event.code, event.reason);

    if (event.code === 1008) {
      // Token invalid - refresh token and reconnect
      refreshAccessToken().then(() => connectWebSocket());
    } else {
      // Network issue - exponential backoff retry
      setTimeout(() => connectWebSocket(), calculateBackoff());
    }
  };

  return ws;
}
```

#### 1.2 When to Connect

**Connect WebSocket:**
- ✅ After successful login (token available)
- ✅ After token refresh (new access token)
- ✅ When app comes to foreground (if disconnected)

**Do NOT connect:**
- ❌ Before login (no token)
- ❌ During guest/preview mode (no authenticated user)
- ❌ When user explicitly logged out

#### 1.3 Reconnection Strategy

**Exponential Backoff:**
```javascript
const BACKOFF_BASE = 1000; // 1 second
const BACKOFF_MAX = 30000; // 30 seconds
let reconnectAttempts = 0;

function calculateBackoff() {
  const delay = Math.min(
    BACKOFF_BASE * Math.pow(2, reconnectAttempts),
    BACKOFF_MAX
  );
  reconnectAttempts++;
  return delay;
}

function resetBackoff() {
  reconnectAttempts = 0;
}
```

**Reset backoff counter:**
- On successful connection (`onopen` event)
- After 5 minutes of stable connection

#### 1.4 Multi-Device Support

**Behavior:** Server sends notifications to ALL connected devices for a player.

**Your job:**
- Each device maintains its own WebSocket connection
- Each device receives the same notification independently
- Each device updates its own UI independently

**No coordination needed** between devices - the backend handles multi-device broadcast.

---

### 2. Notification Message Format

#### 2.1 Message Schema

**Approval Notification:**
```json
{
  "type": "subscription_update",
  "status": "approved",
  "transaction_id": "TXSUB-00123",
  "product_name": "Advanced Biology Bundle",
  "subject_ids": ["SUBJ-00028", "SUBJ-00029", "SUBJ-00030"],
  "timestamp": "2026-02-08T14:23:45.123456"
}
```

**Rejection Notification:**
```json
{
  "type": "subscription_update",
  "status": "rejected",
  "transaction_id": "TXSUB-00123",
  "product_name": "Advanced Biology Bundle",
  "subject_ids": ["SUBJ-00028", "SUBJ-00029", "SUBJ-00030"],
  "timestamp": "2026-02-08T14:23:45.123456"
}
```

#### 2.2 Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"subscription_update"` for subscription notifications |
| `status` | string | `"approved"` or `"rejected"` |
| `transaction_id` | string | Subscription transaction ID (e.g., `TXSUB-00123`) |
| `product_name` | string | Human-readable product name (e.g., "Advanced Biology Bundle") |
| `subject_ids` | array[string] | List of subject IDs included in the product (e.g., `["SUBJ-00028"]`) |
| `timestamp` | string | ISO 8601 timestamp when admin took action |

#### 2.3 Future Message Types (Not Implemented Yet)

The `type` field is designed for extensibility. In the future, you might receive:
- `"lesson_comment"` - Teacher commented on your work
- `"achievement_unlocked"` - New badge earned
- `"leaderboard_update"` - Your rank changed

**For now:** Only handle `type === "subscription_update"`.

---

### 3. UI/UX Requirements

#### 3.1 Approval Flow (Happy Path)

**Trigger:** Receive notification with `status: "approved"`

**Actions:**
1. **Show success notification:**
   - Title: "Purchase Approved! 🎉"
   - Message: "You now have access to {product_name}"
   - Duration: 3-5 seconds
   - Style: Green/success theme

2. **Update local state:**
   - Invalidate catalog cache (remove pending indicator)
   - Refresh subject list (new subjects now accessible)
   - Update user's active subscriptions list

3. **Navigate to content (optional):**
   - If user is on catalog/home screen, show a "Start Learning" button
   - Button navigates to first subject in `subject_ids`

**Example UI:**
```
┌─────────────────────────────────────────┐
│  ✅ Purchase Approved!                   │
│                                          │
│  You now have access to:                 │
│  Advanced Biology Bundle                 │
│                                          │
│  [Start Learning] [Dismiss]              │
└─────────────────────────────────────────┘
```

#### 3.2 Rejection Flow (Sad Path)

**Trigger:** Receive notification with `status: "rejected"`

**Actions:**
1. **Show rejection notification:**
   - Title: "Purchase Request Declined"
   - Message: "Your request for {product_name} was not approved. Contact support for details."
   - Duration: 5-7 seconds (longer than approval)
   - Style: Orange/warning theme (NOT red/error - it's not an error)

2. **Update local state:**
   - Invalidate catalog cache (product should reappear since it's no longer pending)
   - Clear any pending transaction indicators

3. **Provide support link:**
   - Show "Contact Support" button that opens email/chat
   - Pre-fill with transaction ID for easy reference

**Example UI:**
```
┌─────────────────────────────────────────┐
│  ⚠️  Purchase Request Declined           │
│                                          │
│  Your request for:                       │
│  Advanced Biology Bundle                 │
│  was not approved.                       │
│                                          │
│  [Contact Support] [Dismiss]             │
└─────────────────────────────────────────┘
```

#### 3.3 Background Notification Handling

**If app is in background:**
- Store notification in local queue
- Show OS-level push notification (if enabled)
- When app returns to foreground, process queued notifications

**If app is in foreground:**
- Show in-app banner/toast immediately
- No need for OS push notification (user is already in app)

---

### 4. Data Synchronization

#### 4.1 What to Refresh After Notification

**On Approval (`status: "approved"`):**

1. **Refetch Player Access:**
   ```
   GET /api/v1/access/{player_id}
   ```
   This returns updated `subject_ids` array with newly granted subjects.

2. **Refetch Catalog (if on catalog screen):**
   ```
   GET /api/v1/catalog/{plan_id}
   ```
   Purchased product should now be hidden (no longer appears in catalog).

3. **Refetch Subject Hierarchy (for new subjects):**
   ```
   GET /api/v1/progress/{subject_id}
   ```
   Load the hierarchy for each new subject in `subject_ids`.

**On Rejection (`status: "rejected"`):**

1. **Refetch Catalog:**
   ```
   GET /api/v1/catalog/{plan_id}
   ```
   Product should reappear (no longer pending, available for re-purchase).

#### 4.2 Cache Invalidation Strategy

**Simple approach (recommended):**
- Clear all cached catalog/access data on any notification
- Refetch on-demand when user navigates to affected screens

**Optimized approach (advanced):**
- Selectively invalidate only affected resources:
  - Catalog cache: `catalog:{plan_id}`
  - Access cache: `access:{player_id}`
  - Subject hierarchies: `progress:{subject_id}` for each new subject

---

### 5. Error Handling

#### 5.1 Connection Errors

| Error | Cause | Action |
|-------|-------|--------|
| Close code `1008` | Invalid/expired JWT token | Refresh access token, reconnect |
| Close code `1006` | Network disconnected | Exponential backoff retry |
| Close code `1000` | Normal close (server restart) | Immediate reconnect |
| Connection timeout | Network slow/unreliable | Exponential backoff retry |

#### 5.2 Message Parsing Errors

**If received message is invalid JSON:**
```javascript
ws.onmessage = (event) => {
  try {
    const notification = JSON.parse(event.data);
    handleNotification(notification);
  } catch (error) {
    console.error("Invalid JSON from WebSocket:", event.data);
    // Log to analytics but don't crash app
  }
};
```

**If message has unexpected structure:**
```javascript
function handleNotification(data) {
  if (data.type !== "subscription_update") {
    console.warn("Unknown notification type:", data.type);
    return; // Ignore unknown types
  }

  if (!data.status || !data.product_name || !data.subject_ids) {
    console.error("Malformed notification:", data);
    return; // Ignore malformed messages
  }

  // Process valid notification
  if (data.status === "approved") {
    handleApproval(data);
  } else if (data.status === "rejected") {
    handleRejection(data);
  }
}
```

#### 5.3 Race Conditions

**Scenario:** Notification arrives before API response from purchase submission.

**Solution:** Use optimistic updates + reconciliation:
```javascript
// When user submits purchase request
async function submitPurchaseRequest(productId) {
  // 1. Optimistic update: mark product as pending locally
  markProductAsPending(productId);

  // 2. Submit to backend
  const response = await api.post("/api/v1/purchases", { product_id: productId });

  // 3. If submission fails, revert optimistic update
  if (!response.ok) {
    unmarkProductAsPending(productId);
    throw new Error("Purchase submission failed");
  }

  // 4. WebSocket notification will arrive later (minutes/hours/days)
  // When it arrives, reconcile state
}
```

---

### 6. Testing Requirements

#### 6.1 Manual Testing Checklist

**Prerequisites:**
- Test user account with access to product catalog
- Admin access to Frappe Desk

**Test Cases:**

1. **✅ Approval Flow**
   - [ ] Open Player App, log in
   - [ ] Submit purchase request for a product
   - [ ] In Frappe Desk, approve the transaction
   - [ ] Verify: Success notification appears within 3 seconds
   - [ ] Verify: New subject appears in subject list
   - [ ] Verify: Product disappears from catalog

2. **✅ Rejection Flow**
   - [ ] Submit purchase request for a product
   - [ ] In Frappe Desk, reject the transaction
   - [ ] Verify: Rejection notification appears within 3 seconds
   - [ ] Verify: Product reappears in catalog

3. **✅ Multi-Device**
   - [ ] Log in on Device A (phone)
   - [ ] Log in on Device B (tablet) with same account
   - [ ] Approve transaction in Frappe Desk
   - [ ] Verify: BOTH devices receive notification

4. **✅ Background Handling**
   - [ ] Submit purchase request
   - [ ] Put app in background (press home button)
   - [ ] Approve transaction in Frappe Desk
   - [ ] Verify: OS push notification appears
   - [ ] Open app from notification
   - [ ] Verify: In-app notification shown, content updated

5. **✅ Token Expiry**
   - [ ] Connect WebSocket with valid token
   - [ ] Wait for access token to expire (60 minutes)
   - [ ] Verify: WebSocket closes with code 1008
   - [ ] Verify: App refreshes token automatically
   - [ ] Verify: WebSocket reconnects with new token

6. **✅ Network Interruption**
   - [ ] Connect WebSocket
   - [ ] Disable WiFi/mobile data
   - [ ] Wait 5 seconds
   - [ ] Re-enable network
   - [ ] Verify: WebSocket reconnects automatically
   - [ ] Submit test notification - verify it's received

#### 6.2 Automated Testing (Recommended)

**Unit Tests:**
- WebSocket connection manager (connect, disconnect, reconnect)
- Notification message parser
- Backoff calculation logic

**Integration Tests:**
- End-to-end flow: purchase request → approval → notification → UI update
- Mock WebSocket server for testing reconnection logic

**Performance Tests:**
- Verify notification received within 20ms (measure from backend publish to client receive)
- Verify app remains responsive during reconnection storms

---

### 7. Implementation Guidance

#### 7.1 Step-by-Step Implementation Plan

**Phase 1: Basic Connection (Week 1)**
1. Create WebSocket connection manager class
2. Connect to WebSocket endpoint with JWT token
3. Log received messages to console
4. Handle token expiry (1008 close code)
5. Implement exponential backoff reconnection

**Phase 2: Notification Handling (Week 2)**
6. Parse JSON messages into typed objects
7. Implement approval flow (show success notification)
8. Implement rejection flow (show rejection notification)
9. Invalidate caches and refetch access/catalog data

**Phase 3: UI Polish (Week 3)**
10. Design approval/rejection notification banners
11. Add "Start Learning" / "Contact Support" action buttons
12. Handle background notifications (OS push integration)
13. Add analytics events (notification_received, notification_tapped)

**Phase 4: Testing & Edge Cases (Week 4)**
14. Multi-device testing
15. Network interruption testing
16. Race condition handling
17. Performance measurement (20ms target)

#### 7.2 Code Examples (Reference Architecture)

**WebSocket Manager (TypeScript/React Native):**
```typescript
// websocket-manager.ts
import { EventEmitter } from 'events';

export type NotificationMessage = {
  type: 'subscription_update';
  status: 'approved' | 'rejected';
  transaction_id: string;
  product_name: string;
  subject_ids: string[];
  timestamp: string;
};

export class WebSocketManager extends EventEmitter {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private readonly maxBackoff = 30000; // 30 seconds

  constructor(private baseUrl: string) {
    super();
  }

  connect(accessToken: string) {
    const url = `${this.baseUrl}/api/v1/notifications/ws?token=${accessToken}`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log('[WS] Connected');
      this.reconnectAttempts = 0;
      this.emit('connected');
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as NotificationMessage;
        this.emit('notification', data);
      } catch (error) {
        console.error('[WS] Invalid JSON:', event.data);
      }
    };

    this.ws.onerror = (error) => {
      console.error('[WS] Error:', error);
      this.emit('error', error);
    };

    this.ws.onclose = (event) => {
      console.log('[WS] Closed:', event.code);
      this.emit('disconnected', event.code);

      if (event.code === 1008) {
        // Token expired - caller should refresh and reconnect
        this.emit('token_expired');
      } else {
        // Network issue - reconnect with backoff
        this.scheduleReconnect(accessToken);
      }
    };
  }

  disconnect() {
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
  }

  private scheduleReconnect(accessToken: string) {
    const backoff = Math.min(
      1000 * Math.pow(2, this.reconnectAttempts),
      this.maxBackoff
    );
    this.reconnectAttempts++;

    console.log(`[WS] Reconnecting in ${backoff}ms (attempt ${this.reconnectAttempts})`);
    setTimeout(() => this.connect(accessToken), backoff);
  }
}
```

**Usage in App (React Native):**
```typescript
// App.tsx
import { useEffect, useState } from 'react';
import { WebSocketManager, NotificationMessage } from './websocket-manager';
import { showNotification } from './notifications';

function App() {
  const [wsManager] = useState(() => new WebSocketManager('wss://api.memora.com'));

  useEffect(() => {
    const accessToken = getAccessToken();
    if (!accessToken) return;

    wsManager.connect(accessToken);

    const handleNotification = (data: NotificationMessage) => {
      if (data.type === 'subscription_update') {
        if (data.status === 'approved') {
          showNotification({
            title: 'Purchase Approved! 🎉',
            message: `You now have access to ${data.product_name}`,
            type: 'success',
            action: {
              label: 'Start Learning',
              onPress: () => navigateToSubject(data.subject_ids[0])
            }
          });

          // Invalidate caches
          invalidateCache('catalog');
          invalidateCache('access');

          // Refetch access
          refetchUserAccess();
        } else if (data.status === 'rejected') {
          showNotification({
            title: 'Purchase Request Declined',
            message: `Your request for ${data.product_name} was not approved.`,
            type: 'warning',
            action: {
              label: 'Contact Support',
              onPress: () => openSupport(data.transaction_id)
            }
          });

          // Invalidate catalog (product should reappear)
          invalidateCache('catalog');
        }
      }
    };

    const handleTokenExpired = async () => {
      const newToken = await refreshAccessToken();
      wsManager.connect(newToken);
    };

    wsManager.on('notification', handleNotification);
    wsManager.on('token_expired', handleTokenExpired);

    return () => {
      wsManager.off('notification', handleNotification);
      wsManager.off('token_expired', handleTokenExpired);
      wsManager.disconnect();
    };
  }, []);

  return <YourApp />;
}
```

---

### 8. Analytics & Monitoring

#### 8.1 Analytics Events to Track

**Connection Events:**
- `websocket_connected` - WebSocket established successfully
- `websocket_disconnected` - WebSocket closed (include close code)
- `websocket_reconnected` - Successfully reconnected after failure

**Notification Events:**
- `notification_received` - Notification arrived (include type, status)
- `notification_displayed` - User saw the notification UI
- `notification_action_tapped` - User tapped action button (Start Learning / Contact Support)
- `notification_dismissed` - User dismissed notification without action

**Performance Events:**
- `notification_latency` - Time from timestamp to client receipt (target: <20ms)

**Error Events:**
- `websocket_error` - Connection error occurred
- `notification_parse_error` - Invalid message received

#### 8.2 Monitoring Dashboards

**Key Metrics:**
- **Connection Rate:** WebSocket connections per minute
- **Reconnection Rate:** Failed connections per minute
- **Notification Delivery Rate:** Notifications received per minute
- **Average Latency:** Mean time from publish to receipt
- **Error Rate:** WebSocket errors per minute

**Alerts:**
- If notification latency > 50ms (2.5x target) for 5 minutes
- If WebSocket error rate > 10% for 5 minutes
- If reconnection rate > 50% of connection rate for 5 minutes

---

### 9. Security Considerations

#### 9.1 Token Security

**DO:**
- ✅ Store access token in secure storage (Keychain on iOS, Keystore on Android)
- ✅ Pass token as query parameter (WebSocket doesn't support headers)
- ✅ Use WSS (WebSocket Secure) in production, never WS

**DON'T:**
- ❌ Log full access token to console
- ❌ Store token in AsyncStorage (not secure)
- ❌ Send token over unencrypted WS connection

#### 9.2 Message Validation

**Always validate received messages:**
- Check `type` field matches expected values
- Verify all required fields exist before processing
- Ignore unknown message types (forward compatibility)
- Don't trust `subject_ids` - always verify with backend API

---

### 10. API Reference

### WebSocket Endpoint

**URL:** `wss://api.memora.com/api/v1/notifications/ws`

**Authentication:** JWT access token as query parameter

**Request:**
```
GET /api/v1/notifications/ws?token={JWT_ACCESS_TOKEN}
Connection: Upgrade
Upgrade: websocket
```

**Response (on success):**
```
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
```

**Response (on auth failure):**
```
WebSocket Close Frame
Code: 1008 (Policy Violation)
Reason: Invalid or expired token
```

**Message Format (server → client):**
```json
{
  "type": "subscription_update",
  "status": "approved" | "rejected",
  "transaction_id": "string",
  "product_name": "string",
  "subject_ids": ["string"],
  "timestamp": "ISO 8601 string"
}
```

### Related REST Endpoints

**Refetch User Access (after approval):**
```http
GET /api/v1/access/{player_id}
Authorization: Bearer {JWT_ACCESS_TOKEN}
```

**Refetch Catalog (after rejection):**
```http
GET /api/v1/catalog/{plan_id}
Authorization: Bearer {JWT_ACCESS_TOKEN}
```

**Refetch Subject Hierarchy (for new subjects):**
```http
GET /api/v1/progress/{subject_id}
Authorization: Bearer {JWT_ACCESS_TOKEN}
```

---

### 11. FAQ

**Q: What if the app is closed when notification arrives?**
A: The notification is lost. The backend doesn't queue missed notifications. When the user opens the app next time, they'll see updated content access via normal API calls. Consider implementing OS-level push notifications as a fallback.

**Q: Can I send messages from client to server?**
A: Technically yes (WebSocket is bidirectional), but the backend doesn't handle client messages. The connection is server → client only.

**Q: What if multiple notifications arrive while app is in background?**
A: Queue them locally and process in order when app returns to foreground. Show the most recent one as OS notification.

**Q: How do I test without waiting for admin approval?**
A: Use Frappe Desk to manually approve/reject test transactions. Or ask backend team to create a test endpoint that publishes fake notifications.

**Q: Does WebSocket work on all platforms (iOS, Android, Web)?**
A: Yes. All modern platforms support WebSocket protocol. Use platform-appropriate libraries (WebSocket API on web, react-native-websocket on React Native, etc.).

**Q: What about battery drain?**
A: WebSocket connections are lightweight and use minimal battery. Modern mobile OS optimizes WebSocket connections. If battery is a concern, implement connection throttling (disconnect after 5 minutes idle, reconnect on user activity).

---

### 12. Success Criteria

**This feature is complete when:**

✅ Player receives approval notification within 3 seconds of admin action
✅ Player receives rejection notification within 3 seconds of admin action
✅ Approved purchases grant immediate content access (no app restart needed)
✅ Rejected purchases reappear in catalog automatically
✅ WebSocket reconnects automatically after network interruption
✅ Multi-device support works (same notification to all devices)
✅ Token expiry handled gracefully (auto-refresh + reconnect)
✅ Background notifications trigger OS push (if app is closed)
✅ All manual test cases pass
✅ Average notification latency < 50ms (measured over 100 samples)

---

### 13. Support & Resources

**Backend Documentation:**
- Phase 24 SUMMARY: `.planning/phases/24-real-time-subscription-notifications/24-01-SUMMARY.md`
- WebSocket endpoint code: `fastapi_app/api/v1/endpoints/notifications.py`
- Notification models: `fastapi_app/models/notification.py`

**Backend Team Contacts:**
- Technical lead: [Your contact info]
- API questions: [Backend team channel]

**Test Environment:**
- API base URL: `http://127.0.0.1:8002` (local) or `https://api.memora.com` (staging)
- WebSocket URL: `ws://127.0.0.1:8002` (local) or `wss://api.memora.com` (staging)
- Admin panel: `https://x.conanacademy.com/desk`

---

## Appendix: Example Notification Flows

### Flow 1: Happy Path (Approval)

```
Timeline:
T+0s    : Admin clicks "Approve" in Frappe Desk
T+10ms  : Frappe publishes to Redis pub/sub
T+15ms  : FastAPI notification listener receives message
T+18ms  : ConnectionManager sends to WebSocket client
T+20ms  : Player App receives notification
T+100ms : Player App shows success banner
T+200ms : Player App refetches access data
T+300ms : Player App updates subject list

User sees: "Purchase Approved! 🎉" banner
User action: Taps "Start Learning" button
Result: Navigates to first new subject
```

### Flow 2: Sad Path (Rejection)

```
Timeline:
T+0s    : Admin clicks "Reject" in Frappe Desk
T+10ms  : Frappe publishes to Redis pub/sub
T+15ms  : FastAPI notification listener receives message
T+18ms  : ConnectionManager sends to WebSocket client
T+20ms  : Player App receives notification
T+100ms : Player App shows rejection banner
T+200ms : Player App refetches catalog (product reappears)

User sees: "Purchase Request Declined" banner
User action: Taps "Contact Support" button
Result: Opens email with pre-filled transaction ID
```

### Flow 3: Background Notification

```
Timeline:
T+0s    : Admin clicks "Approve"
T+20ms  : Player App (in background) receives WebSocket message
T+50ms  : Player App enqueues notification in local storage
T+100ms : Player App triggers OS push notification
T+5s    : User sees banner: "Purchase Approved!"
T+6s    : User taps banner
T+7s    : App opens to foreground
T+8s    : App processes queued notification
T+200ms : App refetches data and updates UI

User sees: OS notification first, then in-app update
```

---

**END OF DOCUMENT**

For questions or clarifications, contact the backend team or refer to Phase 24 technical documentation in `.planning/phases/24-real-time-subscription-notifications/`.
