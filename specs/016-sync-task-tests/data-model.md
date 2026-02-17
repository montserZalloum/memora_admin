# Data Model: Sync Task Tests

**Feature**: 016-sync-task-tests
**Date**: 2026-02-17

## Entities Under Test

### 1. Memora Player Wallet (MariaDB)
**Autoname**: `WALT-.#####.`

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| player | Link → Memora Player Profile | Yes (unique) | — | FK to player |
| total_xp | Int | No | 0 | XP balance synced from Redis |
| current_streak | Int | No | 0 | Streak counter synced from Redis |
| dirty_flag | Check | No | 0 | 1 = needs sync from Redis |
| status | Select | No | Active | Active / Suspended / Banned |
| total_lessons | Int | No | 0 | Total lessons completed |
| total_time_min | Int | No | 0 | Total time in minutes |
| last_sync_at | Datetime | No | — | Last sync timestamp |

**Sync flow**: Redis `memora:wallet:{player}` hash → `sync_dirty_wallets()` → UPDATE this record

---

### 2. Memora Structure Progress (MariaDB)
**Autoname**: `PROG-.#####.`

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| player | Link → Memora Player Profile | Yes | — | FK to player |
| subject | Link → Memora Subject | Yes | — | FK to subject |
| passed_lessons_bitset | Long Text | No | — | Hex-encoded bitmap |
| completion_percentage | Float | No | — | Read-only, calculated |

**Sync flow**: Redis bitmap `memora:progress:{user}:{subject}:v{ver}` → `sync_dirty_progress()` → UPSERT this record

---

### 3. Memora Interaction Log (MariaDB)
**Autoname**: `LOG-.#####.`

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| player | Link → Memora Player Profile | Yes | — | FK to player |
| lesson | Link → Memora Lesson | Yes | — | FK to lesson |
| stage_id | Data | Yes | — | Stage identifier |
| item_id | Data | No | — | Item within stage |
| event_type | Select | Yes | — | Started/Completed/Failed/Skipped |
| time_spent | Int | No | 0 | Seconds |
| errors_count | Int | No | 0 | Error count |
| timestamp | Datetime | Yes | — | Event timestamp |
| client_metadata | Code (JSON) | No | — | Client metadata blob |

**Sync flow**: Redis list `memora:buffer:interactions` (JSON strings) → `flush_interaction_buffer()` → INSERT this record

---

### 4. Memora Sync Log (MariaDB)
**Autoname**: `SYNC-.#####.`

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| job_id | Data | Yes | — | Unique job ID (e.g., `wallet-a1b2c3d4`) |
| sync_type | Select | Yes | — | Wallet / Progress / Memory |
| records_processed | Int | No | 0 | Count of synced records |
| status | Select | Yes | — | Success / Failed |

**Created by**: `_log_sync()` at end of each sync run

---

## Redis Structures (Input to Sync Tasks)

### Dirty Wallets Set
- **Key**: `memora:dirty:wallets`
- **Type**: Set of player IDs (strings)
- **Example members**: `"PLAY-00123"`, `"PLAY-00456"`

### Wallet Hash
- **Key**: `memora:wallet:{player_id}`
- **Type**: Hash
- **Fields**: `xp` (int), `streak` (int), `streak_date` (date string)

### Dirty Progress Set
- **Key**: `memora:dirty:progress`
- **Type**: Set of `"user_id:subject_id:v{version}"` strings
- **Example members**: `"PLAY-00123:SUBJ-001:v1"`

### Progress Bitmap
- **Key**: `memora:progress:{user_id}:{subject_id}:v{version}`
- **Type**: String (binary bitmap)
- **Conversion**: `bitmap_bytes.hex()` → hex string stored in MariaDB

### Interaction Buffer
- **Key**: `memora:buffer:interactions`
- **Type**: List of JSON strings
- **Example item**: `{"player": "PLAY-001", "lesson": "LES-001", "stage_id": "STG-1", "event_type": "Completed", "time_spent": 45, "timestamp": "2026-02-17T10:00:00Z"}`

---

## Entity Relationships

```
Memora Player Profile (1) ──→ (0..1) Memora Player Wallet
Memora Player Profile (1) ──→ (0..N) Memora Structure Progress
Memora Player Profile (1) ──→ (0..N) Memora Interaction Log
Memora Lesson (1) ──→ (0..N) Memora Interaction Log
Memora Subject (1) ──→ (0..N) Memora Structure Progress
Sync Run ──→ (1) Memora Sync Log
```

## Test Fixture Dependencies

To create test data for sync tasks, the following chain of dependencies must be satisfied:

```
Memora Season (existing: SEAS-00027)
  └── Memora Grade → Memora Major → Memora Academic Plan
       └── Memora Player Profile
            ├── Memora Player Wallet (created in test)
            └── Memora Structure Progress (created by sync)

Memora Subject → Memora Track → Memora Unit → Memora Topic → Memora Lesson
  └── Referenced by Interaction Log (created by sync)
```

**Shortcut**: Use `make_player(season="SEAS-00027")` from `voucher_fixtures.py` to create a player with all dependencies. Create wallet records manually. For interaction tests, use existing lesson records or create minimal test lessons.
