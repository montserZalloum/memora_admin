# Mobile App Integration Guide: Announcements

**Feature**: Admin Announcement System
**Last Updated**: 2026-03-01

---

## Overview

The announcements system delivers bilingual (Arabic/English) admin-created announcements to players on the Home screen. The backend handles content storage, plan-based targeting, and date filtering. The mobile app is responsible for **fetching, displaying, and enforcing display frequency rules**.

Key points:
- **One endpoint** — single GET call returns all relevant announcements
- **No write operations** — the endpoint is purely read-only, no state tracking on the backend
- **Display frequency is client-side only** — the backend tells you *how often* to show each announcement, but does NOT track whether the player has seen it
- **Body is HTML** — the `body` field contains HTML content from a rich text editor. Titles remain plain text.

---

## Endpoint

### `GET /api/v1/announcements/`

Fetches all active announcements for the authenticated player.

**Base URL**: `https://<api-host>/api/v1/announcements/`

**Authentication**: Bearer JWT token (same token used across all game API endpoints).

**Query Parameters**:

| Parameter | Type   | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `lang`    | string | No       | `ar`    | Content language. Allowed values: `ar`, `en` |

**Request Example**:

```http
GET /api/v1/announcements/?lang=ar
Authorization: Bearer <player_jwt_token>
```

**Response** (`200 OK`):

```json
{
  "announcements": [
    {
      "id": "ANN-00001",
      "title": "عيد سعيد!",
      "body": "<p>نتمنى لكم عيداً مباركاً وسعيداً</p>",
      "display_frequency": "once_per_day",
      "created_at": "2026-03-01T10:00:00"
    },
    {
      "id": "ANN-00002",
      "title": "ميزة جديدة",
      "body": "<p>تم إضافة ميزة <strong>المراجعة المتباعدة</strong></p>",
      "display_frequency": "once",
      "created_at": "2026-02-28T14:30:00"
    }
  ]
}
```

**Empty Response** (no active announcements):

```json
{
  "announcements": []
}
```

**Sort Order**: Announcements are always sorted by `created_at` descending (newest first).

---

## Response Schema

### `AnnouncementsResponse`

| Field           | Type                   | Description                     |
|-----------------|------------------------|---------------------------------|
| `announcements` | `AnnouncementItem[]`   | Array of active announcements (may be empty) |

### `AnnouncementItem`

| Field               | Type     | Required | Description |
|---------------------|----------|----------|-------------|
| `id`                | `string` | Yes      | Unique announcement ID (e.g., `"ANN-00001"`) |
| `title`             | `string` | Yes      | Title text in the requested language |
| `body`              | `string` | Yes      | Body content in the requested language (**HTML** — render with a WebView or HTML renderer) |
| `display_frequency` | `string` | Yes      | One of: `"always"`, `"once"`, `"once_per_day"`, `"once_per_session"` |
| `created_at`        | `string` | Yes      | ISO 8601 datetime (e.g., `"2026-03-01T10:00:00"`) |

---

## Error Responses

| Status | Body | When |
|--------|------|------|
| `401`  | `{"detail": "TOKEN_EXPIRED"}` | JWT token is invalid or expired |
| `422`  | Validation error array | Invalid `lang` value (not `ar` or `en`) |
| `503`  | `{"detail": "Service temporarily unavailable"}` | Redis is down (rare) |

---

## Display Frequency — Client-Side Enforcement

The backend returns `display_frequency` but does **NOT** track whether the player has seen an announcement. The mobile app must enforce this locally.

### Frequency Values

| Value              | Behavior |
|--------------------|----------|
| `always`           | Show every time the Home screen loads. No local tracking needed. |
| `once`             | Show only once ever. After the player dismisses it, never show again. |
| `once_per_day`     | Show once per calendar day. After shown today, don't show again until tomorrow. |
| `once_per_session` | Show once per app session. After shown, don't show again until the app is restarted/reopened. |

### Implementation Guidance

1. **Track by `id`**: Use the announcement `id` as the key for local tracking (e.g., `"ANN-00001"`).

2. **Local storage structure** (suggested):
   ```json
   {
     "ANN-00001": {
       "seen": true,
       "last_shown_date": "2026-03-01"
     },
     "ANN-00002": {
       "seen": true,
       "last_shown_date": "2026-02-28"
     }
   }
   ```

3. **Filtering logic** (after receiving the API response):
   ```
   for each announcement in response.announcements:
     if display_frequency == "always":
       show it

     if display_frequency == "once":
       if NOT in local storage as "seen":
         show it, then mark as "seen"

     if display_frequency == "once_per_day":
       if local storage last_shown_date != today:
         show it, then update last_shown_date to today

     if display_frequency == "once_per_session":
       if NOT shown in this session (in-memory flag):
         show it, then set in-memory flag
   ```

4. **Edge case — local storage cleared**: If the player clears app data or reinstalls, they may re-see `"once"` announcements. This is an accepted tradeoff for keeping the backend stateless.

5. **Cleanup**: Periodically clean up local tracking entries for announcement IDs that no longer appear in API responses (the announcement was deleted or expired).

---

## When to Call the Endpoint

- **On Home screen load** — fetch announcements every time the player opens/navigates to the Home screen.
- **No polling needed** — announcements change infrequently (admin-driven). A fresh call on each Home screen visit is sufficient.
- **Caching on client is optional** — the backend response is already served from cache (< 10ms). However, if you want to avoid a network call on every Home screen visit, you can cache locally for a short period (e.g., 5 minutes).

---

## Language Handling

- Pass the player's current language preference as the `lang` query parameter.
- The backend returns `title` and `body` already localized — no need to handle multiple language fields on the client.
- If the player changes their language preference in the app, re-fetch announcements with the new `lang` value.
- Fallback: if the requested language content is empty on the backend, it falls back to Arabic (`ar`).

---

## Body Content — HTML Rendering

The `body` field contains HTML produced by Frappe's rich text editor (Quill-based). The `title` field is plain text.

### Expected HTML Tags

The admin editor produces standard formatting tags:
- `<p>`, `<br>` — paragraphs and line breaks
- `<strong>`, `<b>` — bold
- `<em>`, `<i>` — italic
- `<u>` — underline
- `<ol>`, `<ul>`, `<li>` — ordered/unordered lists
- `<h1>` through `<h6>` — headings (unlikely but possible)
- `<a href="...">` — links (if admin pastes a URL)
- `<blockquote>` — block quotes

No `<img>`, `<video>`, `<iframe>`, or `<script>` tags are expected.

### Rendering Recommendations

- Use a lightweight HTML renderer (e.g., `flutter_html`, `flutter_widget_from_html`, or native `WebView` for complex cases)
- Apply your app's typography styles to the rendered HTML (font family, sizes, colors)
- Ensure RTL rendering works correctly for Arabic HTML content
- Handle `<a>` tags by opening links in an external browser

---

## Plan-Based Targeting

The backend automatically filters announcements based on the player's current plan:
- **"All Players"** announcements are returned to everyone.
- **"Specific Plans"** announcements are returned only if the player's plan matches one of the targeted plans.

The mobile app does **NOT** need to handle targeting logic — it's already applied server-side. The response only contains announcements the player should see.

**Plan change**: If a player changes their plan, the next API call will automatically return the correct announcements for their new plan.

---

## UI/UX Recommendations

These are suggestions — the mobile team has final say on design:

1. **Home screen placement**: Show announcements as compact banners/cards at the top of the Home screen, above the main content.

2. **Tap to expand**: Show only the `title` in the banner. Tapping reveals the full `body` text (e.g., bottom sheet or expanded card).

3. **Dismiss action**: Allow the player to dismiss an announcement (swipe or X button). Use this to mark `"once"` announcements as seen.

4. **Multiple announcements**: If multiple announcements are active, stack them vertically. The API returns them newest-first, so display in that order.

5. **Empty state**: If `announcements` array is empty, show nothing — don't display a "no announcements" placeholder.

6. **RTL support**: Arabic content (`lang=ar`) should render right-to-left. English content (`lang=en`) should render left-to-right.

---

## Integration Checklist

- [ ] Call `GET /api/v1/announcements/?lang={player_lang}` on Home screen load
- [ ] Pass Bearer JWT token in the `Authorization` header
- [ ] Handle empty `announcements` array (show nothing)
- [ ] Render `title` as plain text
- [ ] Render `body` as HTML (use a WebView, `flutter_html`, or native HTML renderer)
- [ ] Implement local display frequency tracking by announcement `id`
- [ ] Handle `always` frequency (always show)
- [ ] Handle `once` frequency (show once, persist in local storage)
- [ ] Handle `once_per_day` frequency (track last shown date)
- [ ] Handle `once_per_session` frequency (in-memory flag per session)
- [ ] Support RTL for Arabic content
- [ ] Handle 401 errors (token refresh or re-login)
- [ ] Handle 503 errors gracefully (skip announcements, don't block Home screen)
- [ ] Clean up local tracking for expired/deleted announcements

---

## FAQ

**Q: Does the backend track if a player has seen an announcement?**
A: No. The backend is completely stateless regarding announcement views. Display frequency is enforced client-side only.

**Q: What happens if I don't pass the `lang` parameter?**
A: It defaults to `"ar"` (Arabic).

**Q: What format is the body content?**
A: The `body` field contains **HTML** from a rich text editor (bold, italic, lists, paragraphs, etc.). Titles are plain text. No embedded images — HTML formatting only.

**Q: How quickly do admin changes appear in the API?**
A: Within 2 seconds. The backend invalidates the cache immediately when an admin creates, edits, or deletes an announcement.

**Q: What's the maximum number of announcements returned?**
A: There's no hard limit, but typically < 20 active announcements at any time. The response is always a flat array.

**Q: Should I paginate?**
A: No. All active announcements are returned in a single response. The count is small enough that pagination is unnecessary.

**Q: What if the player's language preference changes mid-session?**
A: Re-fetch with the new `lang` value. The backend will return content in the new language.

**Q: Is there a WebSocket or push notification for new announcements?**
A: No. Announcements are fetched on-demand via the GET endpoint. No real-time push.
