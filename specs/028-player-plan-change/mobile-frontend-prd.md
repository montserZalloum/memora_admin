# Mobile App PRD: Player Plan Change

**Feature**: 028-player-plan-change
**Date**: 2026-02-26
**Audience**: Mobile App AI Agent / Frontend Developer
**Backend Status**: Fully implemented and deployed
**Base URL**: `https://<domain>/api/v1`

---

## Overview

Players need to change their academic plan in two scenarios:
1. **Season Expired** (P1): Their current season ended, they're blocked from all content, and must pick a new plan to continue learning.
2. **Voluntary Change** (P2): They want to switch grade/major/track while their season is still active.

**Critical UX fact**: Plan change is a **destructive, irreversible operation**. ALL player data is wiped clean (XP, streak, progress, leaderboards, subscriptions, purchases). The player starts completely fresh. The backend auto-detects whether it's a season expiry or voluntary change -- the client only sends `new_plan_id`.

After a successful plan change, the **JWT is invalidated server-side** and the player **must re-login**.

---

## Authentication

All endpoints require a valid JWT Bearer token.

```
Authorization: Bearer <access_token>
```

The JWT contains these relevant claims:
```json
{
  "sub": "PLAYER-00001",      // Player ID
  "plan": "PLAN-00015",       // Current plan ID
  "season": "SEAS-00027",     // Current season ID
  "name": "Ahmed",            // Display name
  "fid": "abc123",            // Session family ID
  "exp": 1740000000           // Expiration
}
```

After plan change, the backend deletes the session from Redis, so any subsequent API call with the old token returns:

```json
// HTTP 401
{
  "code": "SESSION_SUPERSEDED",
  "message": "Session invalidated by new login"
}
```

---

## API Endpoints

### 1. Browse Available Plans

**`GET /api/v1/plans/available`**

Returns all eligible plans the player can switch to, grouped by grade. Automatically excludes the player's current plan.

**Headers**: `Authorization: Bearer <token>`

**Success Response (200)**:
```json
{
  "grades": [
    {
      "grade_id": "GRD-00001",
      "grade_name": "الصف العاشر",
      "plans": [
        {
          "plan_id": "PLAN-00042",
          "plan_name": "الصف العاشر - علمي",
          "grade_id": "GRD-00001",
          "grade_name": "الصف العاشر",
          "major_id": "MJR-00002",
          "major_name": "علمي",
          "season_id": "SEAS-00030",
          "season_title": "ربيع 2026"
        },
        {
          "plan_id": "PLAN-00043",
          "plan_name": "الصف العاشر - أدبي",
          "grade_id": "GRD-00001",
          "grade_name": "الصف العاشر",
          "major_id": "MJR-00003",
          "major_name": "أدبي",
          "season_id": "SEAS-00030",
          "season_title": "ربيع 2026"
        }
      ]
    },
    {
      "grade_id": "GRD-00002",
      "grade_name": "الصف الحادي عشر",
      "plans": [
        {
          "plan_id": "PLAN-00050",
          "plan_name": "الصف الحادي عشر - علمي",
          "grade_id": "GRD-00002",
          "grade_name": "الصف الحادي عشر",
          "major_id": "MJR-00002",
          "major_name": "علمي",
          "season_id": "SEAS-00030",
          "season_title": "ربيع 2026"
        }
      ]
    }
  ],
  "total": 3
}
```

**Notes**:
- `major_id` and `major_name` can be `null` for plans without a major (e.g., lower grades)
- Plans are pre-filtered: only published plans with active seasons (end_date >= today) are returned
- Player's current plan is automatically excluded
- Plans are sorted by grade_name, then major_name, then plan_name
- If `total` is 0, show an empty state (no plans available)

**Error Responses**:
| Status | When |
|--------|------|
| 401 | Invalid/expired token |
| 500 | Server error |

---

### 2. Execute Plan Change

**`POST /api/v1/plans/change`**

Executes the plan change. This is the destructive operation.

**Headers**: `Authorization: Bearer <token>`, `Content-Type: application/json`

**Request Body**:
```json
{
  "new_plan_id": "PLAN-00042"
}
```

**Success Response (200)**:
```json
{
  "success": true,
  "message": "Plan changed successfully. Please log in again.",
  "history_id": "PLHIST-00001",
  "previous_plan_id": "PLAN-00015",
  "new_plan_id": "PLAN-00042"
}
```

**Error Responses**:

| Status | Error Code | When | Action |
|--------|-----------|------|--------|
| 400 | `SAME_PLAN` | Player selected their current plan | Show message, shouldn't happen if UI filters correctly |
| 400 | `INVALID_PLAN` | Plan doesn't exist, not published, or season expired | Show message, refresh available plans list |
| 400 | `INVALID_PLAYER` | Player not found (shouldn't happen) | Log error, force logout |
| 401 | — | Token invalid/expired | Redirect to login |
| 409 | `PLAN_CHANGE_IN_PROGRESS` | Another plan change request is running | Show "please wait", auto-retry after 5 seconds |
| 429 | `COOLDOWN_ACTIVE` | Changed plan less than 24h ago | Show cooldown timer with `retry_after` timestamp |
| 500 | — | Server error | Show generic error, allow retry |

**Error response body format**:
```json
{
  "detail": {
    "error": "COOLDOWN_ACTIVE",
    "message": "You can change your plan again after the cooldown period.",
    "retry_after": "2026-02-27T14:30:00+00:00"
  }
}
```

> **Note**: FastAPI wraps HTTPException detail in a `detail` key. Parse errors from `response.detail.error`.

**`retry_after`** is an ISO 8601 timestamp (UTC). Only present for `COOLDOWN_ACTIVE` errors. Use it to show a countdown timer.

---

## User Flows

### Flow 1: Season Expired (Forced Plan Change) — P1

This is the critical path. The player opens the app and can't do anything because their season ended.

```
┌─────────────────────────────────────────────────────┐
│ Player opens app → makes any API call               │
│                                                     │
│ Backend returns 403:                                │
│ {"code": "SEASON_EXPIRED", "message": "..."}        │
│                                                     │
│ ┌─────────────────────────────────────┐             │
│ │     🔒 Season Expired Screen       │             │
│ │                                     │             │
│ │  "انتهى الموسم الدراسي الحالي"     │             │
│ │  "اختر خطة جديدة للمتابعة"         │             │
│ │                                     │             │
│ │  [اختيار خطة جديدة]  ← CTA Button │             │
│ └─────────────────────────────────────┘             │
│                    │                                │
│                    ▼                                │
│   GET /api/v1/plans/available                       │
│                    │                                │
│                    ▼                                │
│ ┌─────────────────────────────────────┐             │
│ │     📋 Plan Selection Screen       │             │
│ │                                     │             │
│ │  ── الصف العاشر ──                 │             │
│ │  ┌─────────────────────────┐       │             │
│ │  │ علمي - ربيع 2026       │       │             │
│ │  └─────────────────────────┘       │             │
│ │  ┌─────────────────────────┐       │             │
│ │  │ أدبي - ربيع 2026       │       │             │
│ │  └─────────────────────────┘       │             │
│ │                                     │             │
│ │  ── الصف الحادي عشر ──            │             │
│ │  ┌─────────────────────────┐       │             │
│ │  │ علمي - ربيع 2026       │       │             │
│ │  └─────────────────────────┘       │             │
│ └─────────────────────────────────────┘             │
│                    │                                │
│              Player selects plan                    │
│                    │                                │
│                    ▼                                │
│ ┌─────────────────────────────────────┐             │
│ │   ⚠️ Confirmation Dialog           │             │
│ │                                     │             │
│ │  "هل أنت متأكد؟"                   │             │
│ │  "سيتم حذف جميع بياناتك:"          │             │
│ │  • نقاط الخبرة (XP)                │             │
│ │  • سلسلة الإنجاز (Streak)          │             │
│ │  • تقدم الدروس                     │             │
│ │  • ترتيب المتصدرين                  │             │
│ │  • المشتريات                       │             │
│ │                                     │             │
│ │  "لا يمكن التراجع عن هذا الإجراء"  │             │
│ │                                     │             │
│ │  [إلغاء]        [تأكيد التغيير]    │             │
│ └─────────────────────────────────────┘             │
│                    │                                │
│              Player confirms                        │
│                    │                                │
│                    ▼                                │
│   POST /api/v1/plans/change                         │
│   {"new_plan_id": "PLAN-00042"}                     │
│                    │                                │
│              success: true                          │
│                    │                                │
│                    ▼                                │
│ ┌─────────────────────────────────────┐             │
│ │   ✅ Success Screen                │             │
│ │                                     │             │
│ │  "تم تغيير الخطة بنجاح!"           │             │
│ │  "يرجى تسجيل الدخول مجدداً"        │             │
│ │                                     │             │
│ │  [تسجيل الدخول]                    │             │
│ └─────────────────────────────────────┘             │
│                    │                                │
│     Clear local state + redirect to login           │
│                    │                                │
│                    ▼                                │
│   Player logs in → new JWT with new plan/season     │
│   App shows clean slate (0 XP, 0 streak, etc.)     │
└─────────────────────────────────────────────────────┘
```

### Flow 2: Voluntary Plan Change — P2

Player is on an active season but wants to switch (e.g., from Settings or Profile).

```
┌──────────────────────────────────────────────────────┐
│ Player navigates to Settings / Profile               │
│                                                      │
│ ┌──────────────────────────────────────┐             │
│ │   ⚙️ Settings Screen                │             │
│ │                                      │             │
│ │  الخطة الحالية: الصف العاشر - علمي  │             │
│ │  الموسم: ربيع 2026                  │             │
│ │                                      │             │
│ │  [تغيير الخطة الدراسية]             │             │
│ └──────────────────────────────────────┘             │
│                    │                                 │
│                    ▼                                 │
│   Same flow as above (Plan Selection → Confirm →    │
│   Execute → Success → Re-login)                     │
└──────────────────────────────────────────────────────┘
```

### Flow 3: Cooldown Active — P2

Player already changed plan in the last 24 hours.

```
┌──────────────────────────────────────────────────────┐
│ Player taps "Change Plan" → selects plan → confirms  │
│                                                      │
│ POST /api/v1/plans/change → 429                      │
│ {                                                    │
│   "detail": {                                        │
│     "error": "COOLDOWN_ACTIVE",                      │
│     "retry_after": "2026-02-27T14:30:00+00:00"       │
│   }                                                  │
│ }                                                    │
│                                                      │
│ ┌──────────────────────────────────────┐             │
│ │   ⏰ Cooldown Screen                │             │
│ │                                      │             │
│ │  "يمكنك تغيير خطتك مرة أخرى بعد"   │             │
│ │                                      │             │
│ │         12:45:30                      │             │
│ │     (countdown timer)                │             │
│ │                                      │             │
│ │  [حسناً]                            │             │
│ └──────────────────────────────────────┘             │
└──────────────────────────────────────────────────────┘
```

### Flow 4: No Available Plans

```
┌──────────────────────────────────────────────────────┐
│ GET /api/v1/plans/available → 200                    │
│ {"grades": [], "total": 0}                           │
│                                                      │
│ ┌──────────────────────────────────────┐             │
│ │   😔 Empty State                    │             │
│ │                                      │             │
│ │  "لا توجد خطط متاحة حالياً"         │             │
│ │  "يرجى التواصل مع الدعم"            │             │
│ │                                      │             │
│ │  [إعادة المحاولة]  [تواصل معنا]     │             │
│ └──────────────────────────────────────┘             │
└──────────────────────────────────────────────────────┘
```

---

## Scenarios & Edge Cases the Client Must Handle

### S1: Season Expired Detection (CRITICAL)

**Trigger**: Any API call returns HTTP 403 with `code: "SEASON_EXPIRED"`.

**Client behavior**:
1. Intercept this globally (HTTP interceptor / middleware)
2. Block ALL navigation to content screens
3. Show the Season Expired Screen with a CTA to change plan
4. The plan change flow is the ONLY way out (no "dismiss" option)

**Important**: The player's current JWT is still valid for calling `/plans/available` and `/plans/change` even though content endpoints return 403. The season gate only blocks gameplay endpoints, not plan change endpoints.

### S2: Session Invalidated After Plan Change

**Trigger**: After successful plan change, the backend deletes the session key from Redis.

**Client behavior**:
1. On success response from `/plans/change`:
   - **Clear ALL local storage** (cached profile, progress, XP, leaderboard, etc.)
   - **Clear ALL in-memory state** (any provider/store/bloc data)
   - **Delete stored tokens** (access_token + refresh_token)
   - Navigate to Login screen
2. Do NOT attempt to refresh the token -- it will fail with 401
3. After re-login, the new JWT will have the new `plan` and `season` claims
4. All screens should show clean-slate data from the server (0 XP, 0 streak, etc.)

### S3: Concurrent Request (409)

**Trigger**: Player double-taps the confirm button, or network retry sends duplicate.

**Client behavior**:
1. **Prevent double-tap**: Disable the confirm button immediately on first tap, show loading spinner
2. If 409 `PLAN_CHANGE_IN_PROGRESS` received despite protection:
   - Show a "please wait" message
   - Auto-retry once after 5 seconds
   - If still 409, show error and let user retry manually

### S4: Mid-Lesson Plan Change (Edge Case)

**Trigger**: Player has a game session open when they navigate to plan change (unlikely but possible in multi-tab/multi-window).

**Client behavior**:
- No special handling needed. The backend force-closes the game session.
- If the player somehow returns to the game screen after plan change, the next API call will return 401 (session gone), which should redirect to login.

### S5: Token Refresh During Plan Change (Race Condition)

**Trigger**: Token expires during the plan change flow (between loading available plans and confirming).

**Client behavior**:
1. If `/plans/change` returns 401, attempt a token refresh ONCE
2. If refresh succeeds, retry the plan change
3. If refresh fails, redirect to login
4. After login, the player should be able to initiate plan change again (if still needed)

### S6: Network Failure During Plan Change

**Trigger**: Connection lost after tapping confirm.

**Client behavior**:
1. Show loading state with timeout (recommend 15 seconds)
2. If timeout: show error message with retry button
3. **Important**: The plan change may have succeeded server-side even if the client didn't get the response
4. On retry: if the backend returns `SAME_PLAN` (400), it means the change already went through -- treat this as success and redirect to login
5. On retry: if the backend returns `COOLDOWN_ACTIVE` (429), it also means the change succeeded -- redirect to login

### S7: App Kill/Crash During Plan Change

**Client behavior on next app launch**:
1. Try any authenticated API call
2. If 401 → session was invalidated → redirect to login (change succeeded)
3. If 403 SEASON_EXPIRED → change didn't happen → show season expired flow
4. If normal response → nothing happened, continue normally

### S8: Deep Link / Push Notification After Plan Change

**Client behavior**:
- Any deep link that requires authentication should check token validity first
- If 401, redirect to login
- After re-login, evaluate whether the deep link target is still valid in the new plan context

---

## State Management Requirements

### What to Clear on Plan Change Success

The client MUST clear **all** of the following local data:

| Data | Storage Type | Why |
|------|-------------|-----|
| Access token | Secure storage | Session invalidated server-side |
| Refresh token | Secure storage | Session invalidated server-side |
| Cached player profile | Local storage / memory | Plan, grade, major, season all changed |
| XP / Streak / Level | Memory / local cache | Reset to zero |
| Progress data | Local storage / memory | All deleted server-side |
| Leaderboard data | Memory | Player removed from all boards |
| Subscription / access grants | Memory | All deleted server-side |
| Catalog / store data | Memory | Different plan = different catalog |
| Activity / daily XP history | Local cache | Cleared server-side |
| Review / practice data | Memory | Reset for new season |
| Any "has seen" flags related to content | Local storage | Fresh start |

### What to KEEP

| Data | Why |
|------|-----|
| Device registration / FCM token | Devices persist across plan changes |
| App preferences (language, theme, notifications) | Personal settings, not plan-specific |
| Login credentials (if "remember me" is enabled) | Convenience for re-login |

---

## UI/UX Requirements

### Confirmation Dialog (MANDATORY)

Before executing the plan change, the client **MUST** show a confirmation dialog that clearly communicates:

1. This action is **irreversible**
2. **Everything** will be deleted (list the items)
3. They will need to **log in again**

**Do NOT allow the plan change to happen with a single tap.** Two deliberate actions minimum: select plan + confirm dialog.

### Loading State

The plan change operation takes up to 5 seconds. Show:
- Full-screen loading overlay (prevent any interaction)
- Progress indicator (spinner or progress bar)
- Message: "جاري تغيير الخطة..."
- Do NOT allow back navigation during this state

### Error Messages (Arabic)

| Error Code | Arabic Message |
|-----------|---------------|
| `SAME_PLAN` | "أنت بالفعل على هذه الخطة" |
| `INVALID_PLAN` | "الخطة المختارة غير متاحة. يرجى اختيار خطة أخرى" |
| `PLAN_CHANGE_IN_PROGRESS` | "جاري تغيير الخطة، يرجى الانتظار" |
| `COOLDOWN_ACTIVE` | "يمكنك تغيير خطتك مرة أخرى بعد {countdown}" |
| Network error | "حدث خطأ في الاتصال. يرجى المحاولة مرة أخرى" |
| 500 / Unknown | "حدث خطأ غير متوقع. يرجى المحاولة لاحقاً" |

### Plan Selection UI

- Group plans by grade (use `grades` array from response)
- Each plan card should show: `plan_name`, `major_name` (if present), `season_title`
- Highlight the selected plan before confirmation
- If a grade has only one plan, still show it under the grade header
- Plans without a major (lower grades) should not show a major field

---

## Integration Checklist

### HTTP Interceptor Updates

- [ ] Handle 403 `SEASON_EXPIRED` globally — redirect to plan change flow
- [ ] Handle 401 `SESSION_SUPERSEDED` globally — clear state and redirect to login
- [ ] Do NOT intercept 403 on `/plans/available` and `/plans/change` (these should work even with expired season)

### Navigation Guards

- [ ] When season is expired, block ALL content screens
- [ ] Allow navigation ONLY to: plan change screens, settings, support/contact
- [ ] After plan change success, prevent any back navigation (no going back to old state screens)

### API Client

- [ ] `GET /api/v1/plans/available` — no request body, just auth header
- [ ] `POST /api/v1/plans/change` — request body `{"new_plan_id": "PLAN-XXXXX"}`
- [ ] Parse error responses from `response.detail` (FastAPI convention)
- [ ] Handle `retry_after` field as ISO 8601 UTC timestamp

### Testing Scenarios

- [ ] **Happy path (expired season)**: Login → 403 on any content call → see season expired screen → browse plans → select → confirm → success → re-login → clean slate
- [ ] **Happy path (voluntary)**: Settings → change plan → browse plans → select → confirm → success → re-login → clean slate
- [ ] **Cooldown**: Attempt second plan change within 24h → see countdown timer
- [ ] **No plans available**: All seasons expired → empty state with contact support
- [ ] **Double tap prevention**: Tap confirm twice rapidly → only one request fires
- [ ] **Network failure**: Kill network mid-request → error shown → retry works
- [ ] **App restart after change**: Force-kill during plan change → reopen → correct state detected
- [ ] **Old token after change**: Verify no API call succeeds with old token
- [ ] **Clean slate verification**: After re-login, verify ALL of: XP=0, streak=0, progress=0%, no leaderboard rank, empty activity, empty purchases

---

## Summary of Endpoints

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| GET | `/api/v1/plans/available` | Browse eligible plans (grouped by grade) | Bearer JWT |
| POST | `/api/v1/plans/change` | Execute plan change (destructive) | Bearer JWT |

No other new endpoints are needed. Existing endpoints (`/auth/player/login`, `/profile/hero`, etc.) work as before -- they will just return fresh/zeroed data after re-login.
