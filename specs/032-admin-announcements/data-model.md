# Data Model: Admin Announcement System

**Feature Branch**: `032-admin-announcements`
**Date**: 2026-02-28

## Entities

### Memora Announcement (Parent DocType)

| Field | Frappe Type | Required | Default | Notes |
|-------|------------|----------|---------|-------|
| `name` | (auto) | — | `ANN-.#####.` | Auto-generated ID |
| `title_ar` | Data | Yes | — | Arabic title (max 140 chars via validate) |
| `title_en` | Data | Yes | — | English title (max 140 chars via validate) |
| `body_ar` | Small Text | Yes | — | Arabic body (plain text) |
| `body_en` | Small Text | Yes | — | English body (plain text) |
| `target_audience` | Select | Yes | `All Players` | Options: `All Players`, `Specific Plans` |
| `target_plans` | Table | No | — | Child table: `Memora Announcement Target Plan`. `depends_on: eval:doc.target_audience=='Specific Plans'` |
| `duration_type` | Select | Yes | `Date Range` | Options: `Date Range`, `Fixed Duration` |
| `start_date` | Date | No | — | `depends_on: eval:doc.duration_type=='Date Range'`. Required when duration_type is Date Range. |
| `end_date` | Date | No | — | `depends_on: eval:doc.duration_type=='Date Range'`. Required when duration_type is Date Range. |
| `duration_days` | Int | No | — | `depends_on: eval:doc.duration_type=='Fixed Duration'`. Required when duration_type is Fixed Duration. Min 1. |
| `effective_start_date` | Date | No | — | Computed. Read-only. Set by `validate()`. |
| `effective_end_date` | Date | No | — | Computed. Read-only. Set by `validate()`. |
| `display_frequency` | Select | Yes | `Always` | Options: `Always`, `Once`, `Once Per Day`, `Once Per Session` |
| `is_published` | Check | No | `0` | Toggle to make announcement visible via API |

**Permissions**: System Manager (full CRUD)

**Lifecycle**: No formal state machine. `is_published` is a simple boolean toggle. Deletion removes the record entirely.

### Memora Announcement Target Plan (Child Table DocType)

| Field | Frappe Type | Required | Notes |
|-------|------------|----------|-------|
| `plan` | Link | Yes | Links to `Memora Academic Plan` |

`istable: 1` — used inline on the parent `Memora Announcement` form.

## Validation Rules

### Python `validate()` (on `MemoraAnnouncement` class)

1. **Target plans required**: If `target_audience == "Specific Plans"` and `len(target_plans) == 0`, throw error.
2. **Date Range fields required**: If `duration_type == "Date Range"`, require `start_date` and `end_date`.
3. **End date after start date**: If `duration_type == "Date Range"` and `end_date <= start_date`, throw error.
4. **Duration days minimum**: If `duration_type == "Fixed Duration"` and `duration_days < 1`, throw error.
5. **Fixed Duration fields required**: If `duration_type == "Fixed Duration"`, require `duration_days`.
6. **Compute effective dates**:
   - If `duration_type == "Date Range"`: `effective_start_date = start_date`, `effective_end_date = end_date`
   - If `duration_type == "Fixed Duration"` and `is_published` is being set to true (first time):
     - `effective_start_date = today()`
     - `effective_end_date = today() + timedelta(days=duration_days)`
   - If `duration_type == "Fixed Duration"` and already published (re-save): preserve existing effective dates.
7. **Title length**: `title_ar` and `title_en` max 140 characters.

## Relationships

```
Memora Announcement (1) ──── (N) Memora Announcement Target Plan
                                        │
                                        └── plan ──► Memora Academic Plan (existing)

Memora Player Profile (existing)
    ├── plan ──► Memora Academic Plan (existing)
    └── preferred_lang: "ar" | "en"
```

## Redis Cache Structure

### Key: `memora:announcements:active`

- **Type**: STRING (JSON-encoded array)
- **TTL**: 300 seconds (5 minutes) — defined as `ANNOUNCEMENTS_CACHE_TTL` in `redis_keys.py`
- **Producers**: `AnnouncementService.get_active_announcements()` on cache miss; Frappe API hydration
- **Consumers**: `AnnouncementService.get_for_player()`
- **Invalidation**: Frappe hook on Announcement DocType → DEL + pubsub

### Cached Data Shape

```json
[
  {
    "id": "ANN-00001",
    "title_ar": "عنوان الإعلان",
    "title_en": "Announcement Title",
    "body_ar": "نص الإعلان",
    "body_en": "Announcement body text",
    "target_audience": "all",
    "target_plans": [],
    "display_frequency": "once_per_day",
    "effective_start_date": "2026-03-01",
    "effective_end_date": "2026-03-10",
    "created_at": "2026-03-01 10:00:00"
  },
  {
    "id": "ANN-00002",
    "title_ar": "إعلان خاص",
    "title_en": "Special Announcement",
    "body_ar": "للطلاب في الخطة أ",
    "body_en": "For students on Plan A",
    "target_audience": "specific_plans",
    "target_plans": ["PLAN-00001", "PLAN-00003"],
    "display_frequency": "always",
    "effective_start_date": "2026-03-01",
    "effective_end_date": "2026-03-15",
    "created_at": "2026-03-02 14:30:00"
  }
]
```

### Filtering Logic (at read time in AnnouncementService)

```
For each cached announcement:
  1. Date filter: today >= effective_start_date AND today <= effective_end_date
  2. Plan filter:
     - If target_audience == "all" → include
     - If target_audience == "specific_plans" → include only if player.plan in target_plans
  3. Language select: pick title_{lang} and body_{lang} based on player's lang param
```

## Index Considerations

No custom indexes needed. `is_published` is the primary filter, and with < 100 total announcements, the default Frappe indexes are sufficient. The Frappe API query runs only on cache miss (every 5 min at worst).
