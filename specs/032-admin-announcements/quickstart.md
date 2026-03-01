# Quickstart: Admin Announcement System

**Feature Branch**: `032-admin-announcements`

## Prerequisites

- Frappe v15 bench with `memora_admin` app installed
- FastAPI sidecar running on port 8002
- Redis (Memora) on port 13001
- A valid player JWT token (for testing the API)

## What Gets Built

| Component | Location | Purpose |
|-----------|----------|---------|
| `Memora Announcement` DocType | `memora_admin/doctype/memora_announcement/` | Admin form for creating announcements |
| `Memora Announcement Target Plan` DocType | `memora_admin/doctype/memora_announcement_target_plan/` | Child table for plan targeting |
| Frappe API | `memora_admin/api/announcements.py` | Hydration source (MariaDB → Redis) |
| Frappe event handler | `memora_admin/events/announcement_sync.py` | Cache invalidation on admin actions |
| Redis key builder | `fastapi_app/core/redis_keys.py` | `announcements_active_key()` + TTL constant |
| FastAPI service | `fastapi_app/services/announcements.py` | Cache read + hydration + filtering |
| FastAPI endpoint | `fastapi_app/api/v1/endpoints/announcements.py` | `GET /api/v1/announcements/?lang=ar` |
| Pydantic models | `fastapi_app/models/announcements.py` | Response schemas |
| Dependency injection | `fastapi_app/api/deps.py` | `AnnouncementServiceDep` |

## Implementation Order

### Phase 1: Frappe DocTypes (admin-side)

1. Create `Memora Announcement Target Plan` child DocType (istable=1)
2. Create `Memora Announcement` parent DocType with all fields
3. Add Python class with `validate()` for computed dates and field validation
4. Add JS form script for conditional field visibility
5. Register doc_events in `hooks.py`

### Phase 2: Frappe API + Cache Invalidation

1. Create `memora_admin/api/announcements.py` with `get_active_announcements()` whitelist method
2. Create `memora_admin/events/announcement_sync.py` with invalidation handler
3. Register hooks in `hooks.py` (doc_events for Memora Announcement)

### Phase 3: FastAPI Endpoint (player-side)

1. Add `announcements_active_key()` and `ANNOUNCEMENTS_CACHE_TTL` to `redis_keys.py`
2. Create `fastapi_app/models/announcements.py` with Pydantic response models
3. Create `fastapi_app/services/announcements.py` with cache-read + hydration + filtering
4. Create `fastapi_app/api/v1/endpoints/announcements.py` with GET endpoint
5. Add `AnnouncementServiceDep` to `deps.py`
6. Register router in `fastapi_app/api/v1/router.py`
7. Add pubsub handler for `"announcements"` type in FastAPI pubsub listener

## Quick Validation

### 1. Create an announcement (Frappe Desk)

Navigate to `/app/memora-announcement/new` and fill in:
- Title AR: `اختبار`
- Title EN: `Test`
- Body AR: `هذا إعلان تجريبي`
- Body EN: `This is a test announcement`
- Target Audience: `All Players`
- Duration Type: `Date Range`
- Start Date: today
- End Date: tomorrow
- Display Frequency: `Always`
- Check `Is Published`
- Save

### 2. Verify API response

```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  "http://127.0.0.1:8002/api/v1/announcements/?lang=ar"
```

Expected:
```json
{
  "announcements": [
    {
      "id": "ANN-00001",
      "title": "اختبار",
      "body": "هذا إعلان تجريبي",
      "display_frequency": "always",
      "created_at": "2026-02-28T12:00:00"
    }
  ]
}
```

### 3. Verify cache invalidation

Edit the announcement body and save. The next API call should return the updated body.

### 4. Verify plan targeting

Create a second announcement with `Specific Plans` targeting a plan the test player is NOT on. Verify it does not appear in the API response.

## Performance Target

- Announcement retrieval: **< 10ms** from cache (single Redis GET + JSON parse + in-memory filter)
- Cache invalidation: **< 2 seconds** after admin action (Frappe hook → Redis DEL + pubsub)
- 50K concurrent users: served from single cached Redis key — no per-user state
