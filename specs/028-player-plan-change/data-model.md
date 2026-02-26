# Data Model: Player Plan Change

**Feature**: 028-player-plan-change
**Date**: 2026-02-26

## New Entities

### Memora Player Plan History (DocType)

Insert-only audit record capturing complete pre-change state.

**Autoname**: `PLHIST-.#####.`
**Is Submittable**: No
**Track Changes**: No (immutable by design)

| Field | Fieldtype | Required | Options/Default | Notes |
|-------|-----------|----------|-----------------|-------|
| `player` | Link | Yes | Memora Player Profile | Indexed |
| `previous_plan` | Link | Yes | Memora Academic Plan | |
| `previous_grade` | Link | Yes | Memora Grade | |
| `previous_major` | Link | No | Memora Major | Nullable (some plans have no major) |
| `previous_season` | Link | Yes | Memora Season | |
| `new_plan` | Link | Yes | Memora Academic Plan | |
| `new_grade` | Link | Yes | Memora Grade | |
| `new_major` | Link | No | Memora Major | |
| `new_season` | Link | Yes | Memora Season | |
| `trigger_reason` | Select | Yes | "Season Expired\nVoluntary Change" | Auto-detected by backend (FR-018) |
| `snapshot_total_xp` | Int | No | 0 | XP at time of change |
| `snapshot_current_streak` | Int | No | 0 | Streak at time of change |
| `snapshot_total_lessons` | Int | No | 0 | Lessons completed at time of change |
| `snapshot_total_time_min` | Int | No | 0 | Total time in minutes |
| `snapshot_subscriptions_json` | Long Text | No | | JSON array of `{access_key, expires_at, is_active}` |
| `snapshot_progress_json` | Long Text | No | | JSON array of `{subject, passed_lessons_bitset, completion_percentage}` |
| `changed_at` | Datetime | Yes | | Timestamp of plan change |

**Permissions**: System Manager (read-only after creation). Players cannot access this DocType directly.

**Indexes**: `player` (for cooldown check and history queries).

---

## Modified Entities

### Memora Player Profile (existing)

**Fields modified during plan change** (FR-009, FR-023):

| Field | Old Value | New Value | Source |
|-------|-----------|-----------|--------|
| `plan` | Current plan ID | `new_plan_id` | Request parameter |
| `grade` | Current grade | New plan's `grade` | Derived from plan |
| `major` | Current major | New plan's `major` | Derived from plan |
| `season` | Current season | New plan's `season` | Derived from plan |

**Fields NOT modified**: `mobile`, `password`, `display_name`, `avatar`, `gender`, `preferred_lang`, `notifications`, `authorized_devices`.

### Memora Player Wallet (existing)

**Fields reset to zero** (FR-006, FR-010):

| Field | Reset Value |
|-------|-------------|
| `total_xp` | 0 |
| `current_streak` | 0 |
| `total_lessons` | 0 |
| `total_time_min` | 0 |
| `daily_xp_json` | `"{}"` (empty JSON) |
| `dirty_flag` | 0 |
| `last_sync_at` | NULL |

### Memora Player Subscription (existing)

**Action**: DELETE all records where `player = {player_id}` (FR-007).

### Memora Structure Progress (existing)

**Action**: DELETE all records where `player = {player_id}` (FR-008).

---

## New Redis Keys

All keys defined in `fastapi_app/core/redis_keys.py` following existing patterns.

### `freeze_key(player_id: str) -> str`

```python
def freeze_key(player_id: str) -> str:
    """Per-player freeze during plan change.

    Type: STRING (value: Unix timestamp of freeze start)
    Producers: PlanChangeService.execute()
    Consumers: sync_dirty_wallets(), sync_dirty_progress(),
               require_not_frozen() dependency
    TTL: 30s (safety net auto-expire)
    """
    return f"memora:freeze:{player_id}"

FREEZE_KEY_TTL = 30  # seconds
```

### `plan_change_ts_key(player_id: str) -> str`

```python
def plan_change_ts_key(player_id: str) -> str:
    """Cooldown timestamp for plan change rate limiting.

    Type: STRING (value: Unix timestamp of last plan change)
    Producers: PlanChangeService.execute() (after successful change)
    Consumers: PlanChangeService._check_cooldown()
    TTL: 24h (matches cooldown window)
    """
    return f"memora:plan_change_ts:{player_id}"

PLAN_CHANGE_COOLDOWN_TTL = 86400  # 24 hours in seconds
```

---

## Redis Keys Affected by Plan Change

### Keys Deleted (DEL)

| Key Pattern | Builder | Type | Notes |
|-------------|---------|------|-------|
| `memora:session:{player_id}` | `session_key()` | STRING | Auth session invalidation |
| `memora:gamesession:{player_id}` | `game_session_key()` | HASH | Force-close active game |
| `memora:wallet:{player_id}` | `wallet_key()` | HASH | Wallet cache |
| `memora:access:{player_id}` | `access_key()` | SET | Access grants |
| `memora:daily_xp:{player_id}` | `daily_xp_key()` | HASH | Daily XP history |
| `memora:player_plan:{player_id}` | `player_plan_key()` | STRING | Plan cache |
| `memora:profile:{player_id}` | `profile_key()` | STRING | Profile display cache |
| `memora:reviews_overview:{player_id}` | `reviews_overview_key()` | STRING | Review due count |
| `memora:practice:{player_id}` | `practice_session_key()` | HASH | Active practice session |
| `memora:pending:{player_id}` | `pending_key()` | SET | Pending purchases |

### Keys Deleted via SCAN Pattern (DEL)

| Pattern | Builder | Type | Notes |
|---------|---------|------|-------|
| `memora:progress:{player_id}:*` | `progress_key()` | BITMAP | All subject progress |
| `memora:stats:{player_id}:*` | `stats_key()` | HASH | All subject stats |
| `memora:items_learned:{player_id}:*` | `items_learned_key()` | STRING | Per-subject learned count |
| `memora:mastery:{player_id}:*` | `mastery_key()` | HASH | Per-subject mastery |
| `memora:fsrs:{player_id}:*` | `fsrs_card_state_key()` | STRING | FSRS card states |
| `memora:fsrs:processed:{player_id}:*` | `fsrs_processed_key()` | STRING | FSRS idempotency keys |

### Keys Modified via ZREM (Leaderboards)

| Pattern | Builder | Action |
|---------|---------|--------|
| `memora:lb:alltime*` | `lb_alltime_key()` | ZREM player_id |
| `memora:lb:daily:*` | `lb_daily_key()` | ZREM player_id |
| `memora:lb:weekly:*` | `lb_weekly_key()` | ZREM player_id |
| `memora:lb:archive:*` | `lb_archive_daily_key()` | ZREM player_id |

### Dirty Sets (SREM before DB writes)

| Key | Builder | Member to Remove |
|-----|---------|------------------|
| `memora:dirty:wallets` | `dirty_wallets_key()` | `{player_id}` |
| `memora:dirty:progress` | `dirty_progress_key()` | `{player_id}:{subject}:v{version}` (per subject) |

### Frappe Cache (Redis 13000)

| Key | Action |
|-----|--------|
| `player_season_seq:{player_id}` | `frappe.cache().delete_value()` — handled by existing `plan_change_sync.py` hook |

### Keys NOT Cleaned (per spec)

| Key | Reason |
|-----|--------|
| `memora:buffer:interactions` | Accepted edge case — pending interactions may attribute to old season |
| `memora:voucher_fail:player:{player_id}` | Short TTL (1h), expires naturally |
| `memora:report_cooldown:{player_id}` | Short TTL (60s), expires naturally |
| `memora:devices:{player_id}` | Devices persist across plan changes |
| Hydration locks (`memora:hydrating:*`) | Short TTL (30s), expires naturally |
| Hydration sentinels (`*:_hydrated`) | Short TTL (60s), expires naturally |

---

## Entity Relationship Diagram

```
                    ┌──────────────────────┐
                    │   Memora Season      │
                    │ (SEAS-#####)         │
                    │ season_title         │
                    │ start_date, end_date │
                    │ is_published         │
                    │ season_seq           │
                    └────────▲─────────────┘
                             │ season
                    ┌────────┴─────────────┐
                    │ Memora Academic Plan  │
                    │ (PLAN-#####)          │
                    │ plan_name             │
                    │ grade, major, season  │
                    │ is_published          │
                    └────────▲─────────────┘
                             │ plan
                    ┌────────┴─────────────┐
                    │ Memora Player Profile │
                    │ (PLAYER-#####)        │
                    │ plan, grade, major    │
                    │ season                │
                    └────────┬─────────────┘
                             │ player
          ┌──────────────────┼──────────────────┐
          │                  │                  │
  ┌───────▼────────┐ ┌──────▼───────┐ ┌───────▼────────────────┐
  │ Player Wallet  │ │ Player Sub   │ │ Structure Progress     │
  │ (WALT-#####)   │ │ (PSUB-#####) │ │ (PROG-#####)           │
  │ total_xp       │ │ access_key   │ │ subject                │
  │ current_streak │ │ expires_at   │ │ passed_lessons_bitset   │
  │ total_lessons  │ │ is_active    │ │ completion_percentage   │
  │ total_time_min │ │ DELETE ALL   │ │ DELETE ALL              │
  │ daily_xp_json  │ └──────────────┘ └────────────────────────┘
  │ RESET TO ZERO  │
  └────────────────┘

                    ┌────────────────────────────┐
                    │ Memora Player Plan History  │
                    │ (PLHIST-#####)    [NEW]     │
                    │ player                      │
                    │ previous_plan/grade/major/  │
                    │   season                    │
                    │ new_plan/grade/major/season │
                    │ trigger_reason              │
                    │ snapshot_* (wallet fields)  │
                    │ snapshot_*_json (subs/prog) │
                    │ changed_at                  │
                    │ INSERT (immutable)           │
                    └────────────────────────────┘
```
