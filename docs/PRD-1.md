# Memora Platform - Technical PRD
## Part 1: Infrastructure & Data Layer
### Version 1.0 | February 2026

---

# Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Technology Stack](#2-technology-stack)
3. [Complete DocTypes Reference](#3-complete-doctypes-reference)
4. [Redis Data Structures](#4-redis-data-structures)
5. [JSON Files Strategy](#5-json-files-strategy)
6. [Content Hierarchy](#6-content-hierarchy)

---

# 1. System Architecture Overview

## 1.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              STUDENT APP (React)                                │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
                    ▼                 ▼                 ▼
┌───────────────────────┐ ┌───────────────────┐ ┌───────────────────────────┐
│   Cloudflare CDN      │ │    FastAPI        │ │      Frappe               │
│   (Public JSON)       │ │    (Game API)     │ │      (Admin Panel)        │
│                       │ │                   │ │                           │
│ • manifest.json       │ │ • /progress       │ │ • Content Management      │
│ • {subject}_h.json    │ │ • /complete       │ │ • User Management         │
│ • {unit}_c.json       │ │ • /wallet         │ │ • JSON Build Trigger      │
│ • /lessons/*.json     │ │ • /leaderboard    │ │ • Analytics Dashboard     │
│                       │ │                   │ │                           │
│ Cache: 5min - 1month  │ │ Response: <20ms   │ │ Internal Only             │
└───────────────────────┘ └─────────┬─────────┘ └─────────────┬─────────────┘
                                    │                         │
          ┌─────────────────────────┼─────────────────────────┤
          │                         │                         │
          ▼                         ▼                         ▼
┌───────────────────┐    ┌───────────────────┐    ┌───────────────────────┐
│  Private JSON     │    │      Redis        │    │      MariaDB          │
│  (Backend Only)   │    │   (Hot Data)      │    │    (Cold Data)        │
│                   │    │                   │    │                       │
│ • {subject}_b.json│    │ • progress:*      │    │ • DocTypes            │
│   (BitMap Index)  │    │ • wallet:*        │    │ • Interaction Logs    │
│                   │    │ • access:*        │    │ • Aggregated Stats    │
│ Lives on SSD      │    │ • session:*       │    │                       │
└───────────────────┘    └───────────────────┘    └───────────────────────┘
```

## 1.2 Data Flow Patterns

### Pattern A: Content Fetching (95% of traffic)
```
Student → Cloudflare CDN → JSON file (Server sleeps)
Response time: < 50ms globally
```

### Pattern B: Game Interactions (5% of traffic)
```
Student → FastAPI → Redis (update bitmap/wallet)
         → Return 200 OK
         
Background (every 1-2 min):
Worker → Read dirty sets → Batch write to MariaDB
```

### Pattern C: Progress Check
```
Student → FastAPI → Read {subject}_b.json from SSD (cached in memory)
                  → Read bitmap from Redis
                  → Calculate unlock states in-memory
                  → Return states map
Response time: < 20ms
```

### Pattern D: Access Check (Double-Gate)
```
Student → FastAPI → Gate 1: Check Season status in Redis
                  → Gate 2: Check Player access Set in Redis
                  → Allow or Deny
Response time: < 2ms
```

---

# 2. Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Frontend | React | Student App |
| CDN | Cloudflare + R2 | Static JSON delivery |
| Game API | FastAPI (Python) | High-performance endpoints |
| Admin | Frappe v15 | Content management |
| Hot Data | Redis | Progress, Wallet, Sessions, Access |
| Cold Data | MariaDB | Users, Logs, Analytics |
| Queue | Redis Sets | Debounced batch writes |

---

# 3. Complete DocTypes Reference

## 3.1 Summary Table (27 DocTypes)

| # | DocType | Naming | Category | Description |
|---|---------|--------|----------|-------------|
| 1 | Memora Academic Plan | PLAN-.##### | Content | Academic plan grouping subjects |
| 2 | Memora Subject | SUB-.##### | Content | Subject/Course |
| 3 | Memora Track | TRK-.##### | Content | Track/Chapter within subject |
| 4 | Memora Unit | UNT-.##### | Content | Unit within track |
| 5 | Memora Topic | TPC-.##### | Content | Topic within unit |
| 6 | Memora Lesson | LSN-.##### | Content | Lesson with stages |
| 7 | Memora Lesson Stage Settings | autoname | Content | Stage type templates |
| 8 | Memora Grade | autoname | Academic | Grade level (Grade 1, 2, etc.) |
| 9 | Memora Major | autoname | Academic | Major/Specialization |
| 10 | Memora Season | autoname | Academic | Season/Semester with dates |
| 11 | Memora Player Profile | User link | Player | Player profile |
| 12 | Memora Player Wallet | WALLET-{id} | Player | XP, Hearts, Streak |
| 13 | Memora Player Subscription | autoname | Player | Access grants per player |
| 14 | Memora Structure Progress | autoname | Learning | Bitset progress tracking |
| 15 | Memora Memory State | autoname | Learning | FSRS spaced repetition state |
| 16 | Memora Interaction Log | autoname | Analytics | Player interaction events |
| 17 | Memora Analytics Aggregate | autoname | Analytics | Aggregated statistics |
| 18 | Memora Product Grant | autoname | Business | Item-to-content mapping |
| 19 | Memora Plan Overrider | autoname | Business | Plan content customization |
| 20 | Memora Subscription Transaction | autoname | Business | Payment transactions |
| 21 | Memora Build Queue | autoname | System | JSON build queue |
| 22 | Memora Sync Log | autoname | System | Redis-DB sync logs |
| 23 | Memora Settings | Single | System | Global configuration |

### Child Tables (4)

| # | Child Table | Parent DocType | Purpose |
|---|-------------|----------------|---------|
| 1 | Memora Plan Subject | Memora Academic Plan | Links subjects to plan |
| 2 | Memora Lesson Stage | Memora Lesson | Lesson stages |
| 3 | Memora Grant Component | Memora Product Grant | Grant targets |
| 4 | Memora Player Device | Memora Player Profile | Authorized devices |

---

## 3.2 Content Structure DocTypes

### 3.2.1 Memora Academic Plan

An academic plan that groups subjects and defines curriculum structure.

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| plan_name | Data | Plan Name | Display name of the plan |
| grade | Link | Grade | Link to Memora Grade |
| major | Link | Major | Link to Memora Major |
| season | Link | Season | Link to Memora Season |
| is_published | Check | Is Published | Whether plan is active |
| plan_subjects | Table | Plan Subjects | Child table of subjects |

### 3.2.2 Memora Plan Subject (Child Table)

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| subject | Link | Subject | Link to Memora Subject |
| alias_title | Data | Alias Title | Optional display name override |
| notes | Small Text | Notes | Admin notes |
| is_premium | Check | Is Premium | Requires purchase |

### 3.2.3 Memora Subject

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| subject_title | Data | Subject Title | Display name |
| image | Attach Image | Image | Subject icon/image |
| description | Text Editor | Description | Subject description |
| is_linear | Check | Is Linear | Must complete in order |
| is_published | Check | Is Published | Whether active |
| redis_key_prefix | Int | Redis Key Prefix | Storage identifier |
| last_bit_index | Int | Last Bit Index | For bitmap assignment |
| sort_order | Int | Sort Order | Display order |

### 3.2.4 Memora Track

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| track_title | Data | Track Title | Display name |
| subject | Link | Subject | Parent subject |
| sort_order | Int | Sort Order | Display order |
| image | Attach Image | Image | Track image |
| description | Small Text | Description | Track description |
| is_sold_separately | Check | Is Sold Separately | Can be purchased alone |
| is_published | Check | Is Published | Whether active |
| is_linear | Check | Is Linear | Must complete in order |

### 3.2.5 Memora Unit

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| unit_title | Data | Unit Title | Display name |
| track | Link | Track | Parent track |
| sort_order | Int | Sort Order | Display order |
| is_free | Check | Is Free | Available without purchase |
| is_published | Check | Is Published | Whether active |
| is_linear | Check | Is Linear | Must complete in order |

### 3.2.6 Memora Topic

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| topic_title | Data | Topic Title | Display name |
| unit | Link | Unit | Parent unit |
| sort_order | Int | Sort Order | Display order |
| is_free | Check | Is Free | Available without purchase |
| is_linear | Check | Is Linear | Must complete in order |
| is_published | Check | Is Published | Whether active |

### 3.2.7 Memora Lesson

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| lesson_title | Data | Lesson Title | Display name |
| topic | Link | Topic | Parent topic |
| base_xp | Int | Base XP | XP reward for completion |
| max_hearts | Int | Max Hearts | Allowed attempts |
| content_hash | Data | Content Hash | Version control hash |
| stages | Table | Stages | Child table of stages |
| bit_index | Int | Bit Index | Position in progress bitmap (auto-assigned, never changes) |

### 3.2.8 Memora Lesson Stage (Child Table)

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| stage_id | Data | Stage ID | Unique identifier |
| stage_type | Link | Stage Type | Link to Stage Settings |
| is_skippable | Check | Is Skippable | Can be skipped |
| config_json | Code | Config JSON | Stage configuration |

### 3.2.9 Memora Lesson Stage Settings

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| stage_title | Data | Stage Title | Stage type name |
| is_skippable | Check | Is Skippable | Default skippable |
| default_stage_time | Int | Default Stage Time | Time limit in seconds |
| payload | Code | Payload | JSON schema template |

---

## 3.3 Academic Structure DocTypes

### 3.3.1 Memora Grade

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| grade_title | Data | Grade Title | e.g., "Grade 10" |
| majors | Table Multiselect | Majors | e.g., "علمي،ادبي" |
| sort_order | Int | Sort Order | Display order |

### 3.3.2 Memora Major

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| major_title | Data | Major Title | e.g., "Scientific Stream" |

### 3.3.3 Memora Season

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| season_title | Data | Season Title | e.g., "2025-2026 First Semester" |
| start_date | Date | Start Date | Season start |
| end_date | Date | End Date | Season end (used for access expiry) |
| is_published | Check | Is Published | Whether season is active |

---

## 3.4 Player DocTypes

### 3.4.1 Memora Player Profile

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| user | Link | User | Frappe User link |
| display_name | Data | Display Name | Public name |
| plan | Link | Current Plan | Active academic plan |
| avatar | Select | Avatar | Avatar selection |
| grade | Link | Grade | Player's grade |
| major | Link | Major | Player's major |
| season | Link | Season | Current season |
| preferred_lang | Select | Preferred Language | ar/en |
| notifications | Check | Notifications | Push enabled |
| authorized_devices | Table | Authorized Devices | Child table |

### 3.4.2 Memora Player Device (Child Table)

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| device_id | Data | Device ID | Unique device identifier |
| device_name | Data | Device Name | e.g., "iPhone 15" |
| last_login | Datetime | Last Login | Last activity |
| user_agent | Small Text | User Agent | Browser/app info |
| platform | Select | Platform | ios/android/web |

### 3.4.3 Memora Player Wallet

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| player | Link | Player | Link to Player Profile |
| total_xp | Int | Total XP | Cumulative XP |
| current_streak | Int | Current Streak | Days in a row |
| dirty_flag | Check | Dirty Flag | Needs sync |
| status | Select | Status | active/suspended |

### 3.4.4 Memora Player Subscription (NEW)

Records player access grants from purchases.

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| player | Link | Player | Link to Player Profile |
| access_key | Data | Access Key | Granted ID (e.g., SUB-MATH, PLAN-2025) |
| grant | Link | Memora Product Grant
| season | Link | Season | Associated season |
| expires_at | Datetime | Expires At | Auto-set from Season.end_date |
| granted_at | Datetime | Granted At | When access was given |

---

## 3.5 Learning & Progress DocTypes

### 3.5.1 Memora Structure Progress

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| player | Link | Player | Link to Player Profile |
| subject | Link | Subject | Link to Subject |
| passed_lessons_bitset | Long Text | Passed Lessons Bitset | Hex-encoded bitmap |
| completion_percentage | Float | Completion Percentage | 0-100 |

### 3.5.2 Memora Memory State

FSRS spaced repetition state per player-subject-stage.

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| season | Link | Season | Associated season |
| subject | Link | Subject | Link to Subject |
| player | Link | Player | Link to Player Profile |
| stage_id | Data | Stage ID | Stage identifier |
| stability | Float | Stability | FSRS stability value |
| difficulty | Float | Difficulty | FSRS difficulty (0-1) |
| next_review | Datetime | Next Review | When to show again |

---

## 3.6 Analytics DocTypes

### 3.6.1 Memora Interaction Log

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| player | Link | Player | Link to Player Profile |
| lesson | Link | Lesson | Link to Lesson |
| stage_id | Data | Stage ID | Stage identifier |
| event_type | Select | Event Type | started/completed/failed/skipped |
| time_spent | Int | Time Spent (Seconds) | Duration |
| errors_count | Int | Errors Count | Mistakes made |
| timestamp | Datetime | Timestamp | Event time |
| client_metadata | Code | Client Metadata | JSON device/app info |

### 3.6.2 Memora Analytics Aggregate

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| lesson | Link | Lesson | Link to Lesson |
| date | Date | Date | Aggregation date |
| total_attempts | Int | Total Attempts | Count |
| avg_time_spent | Float | Average Time Spent | Seconds |
| success_rate | Float | Success Rate | 0-1 |

---

## 3.7 Business DocTypes

### 3.7.1 Memora Product Grant

Maps ERPNext items to content access.

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| plan | Link | Plan | Associated academic plan |
| item_code | Link | Item Code | ERPNext Item |
| is_published | Check | Is Published | Whether active |
| grant_components | Table | Grant Components | What gets unlocked |

### 3.7.2 Memora Grant Component (Child Table)

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| target_doctype | Link | Target Doctype | Plan/Subject/Track |
| target_name | Dynamic Link | Target Name | Specific item ID |

### 3.7.3 Memora Plan Overrider

Customizes content visibility per plan.

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| plan | Link | Plan | Link to Academic Plan |
| ref_doctype | Link | Ref Doctype | Subject/Track/Unit |
| ref_name | Dynamic Link | Ref Name | Specific item |
| action | Select | Action | show/hide/lock/set_free |

### 3.7.4 Memora Subscription Transaction

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| player | Link | Player | Link to Player Profile |
| payment_method | Select | Payment Method | card/wallet/voucher |
| status | Select | Status | pending/completed/failed/refunded |
| transaction_id | Data | Transaction ID | External payment ID |
| amount_paid | Currency | Amount Paid | Payment amount |
| erpnext_invoice | Link | ERPNext Invoice | Link to Sales Invoice |
| related_grant | Link | Related Grant | Link to Product Grant |

---

## 3.8 System DocTypes

### 3.8.1 Memora Build Queue

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| target_type | Select | Target Type | Plan/Subject/Lesson |
| target_name | Dynamic Link | Target Name | Item to rebuild |
| trigger_reason | Select | Trigger Reason | content_update/override_change/manual |
| triggered_by | Link | Triggered By | User who triggered |
| triggered_at | Datetime | Triggered At | Request time |
| status | Select | Status | pending/processing/completed/failed |
| started_at | Datetime | Started At | Build start |
| completed_at | Datetime | Completed At | Build end |
| duration_sec | Float | Duration (Sec) | Build duration |
| files_generated | Int | Files Generated | Output count |
| error_message | Text | Error Message | If failed |

### 3.8.2 Memora Sync Log

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| job_id | Data | Job ID | Unique job identifier |
| sync_type | Select | Sync Type | progress/wallet/interactions |
| records_processed | Int | Records Processed | Count |
| status | Select | Status | completed/failed |

### 3.8.3 Memora Settings (Single)

| Field Name | Field Type | Label | Description |
|------------|------------|-------|-------------|
| cdn_enabled | Check | CDN Enabled | Use CDN |
| cdn_base_url | Data | CDN Base URL | CDN URL |
| local_fallback_mode | Check | Local Fallback Mode | Fallback enabled |
| storage_provider | Select | Storage Provider | r2/s3/local |
| json_version | Int | JSON Version | Cache bust version |
| access_key | Password | Access Key | CDN access key |
| secret_key | Password | Secret Key | CDN secret key |
| batch_interval_minutes | Int | Batch Interval (minutes) | Sync frequency |
| batch_threshold | Int | Batch Threshold | Max batch size |
| signed_url_expiry_hours | Int | Signed URL Expiry (hours) | URL validity |
| default_max_hearts | Int | Default Max Hearts | New player hearts |
| xp_per_heart | Int | XP Per Heart | Bonus XP calculation |
| base_lesson_xp | Int | Base Lesson XP | Default XP reward |
| replay_xp | Int | Replay XP | XP for replaying |
| max_devices_per_player | Int | Max Devices Per Player | Device limit |
| session_timeout_days | Int | Session Timeout (Days) | JWT refresh expiry |
| fsrs_weights | Small Text | FSRS Weights | Algorithm parameters |
| request_retention_days | Int | Request Retention (Days) | Log cleanup |

---

# 4. Redis Data Structures

## 4.1 Season Control (Global Kill-Switch)

```
Key Pattern: memora:season:{season_id}:meta
Type: Hash
TTL: None (managed by application)

Fields:
  end_ts: 1782654400          # Unix timestamp of season end
  status: "active"            # active/ended/paused

Operations:
  HGETALL memora:season:SEASON-2025:meta
  HSET memora:season:SEASON-2025:meta status "ended"
```

## 4.2 Player Access Set (Key-Ring)

```
Key Pattern: memora:access:{player_id}
Type: Set
TTL: Season end_date + 7 days

Values: Set of granted resource IDs
  - "PLAN-2025-SCI"     # Full plan access
  - "SUB-MATH-101"      # Single subject
  - "TRK-BONUS"         # Single track

Operations:
  SADD memora:access:PLAYER-00001 SUB-MATH-101
  SISMEMBER memora:access:PLAYER-00001 SUB-MATH-101
  SMEMBERS memora:access:PLAYER-00001
  EXPIREAT memora:access:PLAYER-00001 {season_end_ts + 7_days}
```

## 4.3 Plan-Subject Mapping

```
Key Pattern: memora:plan_subjects:{plan_id}
Type: Set
TTL: None

Values: Subject IDs included in plan

Operations:
  SADD memora:plan_subjects:PLAN-2025-SCI SUB-MATH SUB-PHYS SUB-CHEM
  SISMEMBER memora:plan_subjects:PLAN-2025-SCI SUB-MATH
```

## 4.4 Progress Bitmaps

```
Key Pattern: progress:{player_id}:{subject_id}
Type: String (binary bitmap)
TTL: None (persistent)

Structure:
  Bit N = 1 means lesson with bit_index N is completed
  
Operations:
  SETBIT progress:PLAYER-00001:SUB-MATH-101 42 1  # Complete lesson at index 42
  GETBIT progress:PLAYER-00001:SUB-MATH-101 42    # Check if complete
  BITCOUNT progress:PLAYER-00001:SUB-MATH-101     # Count completed lessons
```

## 4.5 Player Wallets

```
Key Pattern: wallet:{player_id}
Type: Hash
TTL: None (persistent)

Fields:
  xp: 15420                          # Total XP
  streak: 7                          # Current streak days
  streak_date: "2026-02-01"          # Last streak update

Operations:
  HINCRBY wallet:PLAYER-00001 xp 10
  HSET wallet:PLAYER-00001 streak 8 streak_date "2026-02-01"
  HGETALL wallet:PLAYER-00001
```

## 4.6 Sessions

```
Key Pattern: session:{player_id}:{device_id}
Type: String (JSON)
TTL: 7 days (604800 seconds)

Value:
{
  "refresh_token_hash": "sha256:...",
  "device_name": "iPhone 15",
  "platform": "ios",
  "created_at": "2026-01-26T10:00:00Z",
  "last_activity": "2026-02-01T14:30:00Z"
}
```

## 4.7 Leaderboards

```
Key Pattern: leaderboard:{metric}:{period}
Type: Sorted Set
TTL: Varies

Examples:
  leaderboard:xp:daily:2026-02-01     (TTL: 48 hours)
  leaderboard:xp:weekly:2026-W05      (TTL: 2 weeks)
  leaderboard:xp:monthly:2026-02      (TTL: 2 months)
  leaderboard:xp:alltime              (No TTL)
  leaderboard:streak:current          (No TTL)

Operations:
  ZINCRBY leaderboard:xp:daily:2026-02-01 10 PLAYER-00001
  ZREVRANGE leaderboard:xp:daily:2026-02-01 0 99 WITHSCORES
  ZREVRANK leaderboard:xp:daily:2026-02-01 PLAYER-00001
```

## 4.8 Dirty Sets (Sync Queue)

```
Key Pattern: dirty:{type}
Type: Set
TTL: None

Types:
  dirty:progress     # Players with unsaved progress
  dirty:wallet       # Players with unsaved wallet changes

Operations:
  SADD dirty:progress PLAYER-00001:SUB-MATH
  SMEMBERS dirty:progress
  SREM dirty:progress PLAYER-00001:SUB-MATH
```

## 4.9 Build Queue

```
Key Pattern: memora:pending_builds
Type: Set
TTL: None

Value: Subject IDs pending rebuild

Key Pattern: memora:pending_lessons:{subject_id}
Type: Set
TTL: None

Value: Lesson IDs changed in this subject
```

## 4.10 Lesson Info Cache

```
Key Pattern: memora:lesson_info
Type: Hash
TTL: None

Note: This cache is populated on-demand (lazy loading).
When FastAPI needs lesson info and its not cached,
it fetches from the unit content JSON and caches it.

Fields: {lesson_id}: {json_data}

Example:
  LSN-00001: {"subject": "SUB-MATH", "bit_index": 0, "xp_reward": 10, "topic": "TPC-00001"}
```

## 4.11 Subject Totals Cache

```
Key Pattern: memora:subject_totals
Type: Hash
TTL: None

Fields: {subject_id}: {total_lessons}

Example:
  SUB-MATH: 450
  SUB-PHYS: 380
```

## 4.12 Rate Limiting

```
Key Pattern: ratelimit:{player_id}:{endpoint}
Type: String (counter)
TTL: 60 seconds

Operations:
  INCR ratelimit:PLAYER-00001:complete_stage
  EXPIRE ratelimit:PLAYER-00001:complete_stage 60
```

## 4.13 Active Lesson Sessions

```
Key Pattern: lesson_session:{session_id}
Type: String (JSON)
TTL: 1 hour (3600 seconds)

Value:
{
  "player_id": "PLAYER-00001",
  "lesson_id": "LSN-00001",
  "subject_id": "SUB-MATH",
  "started_at": "2026-02-01T10:00:00Z",
  "stages_completed": [0, 1, 2],
  "hearts_used": 1,
  "status": "active"
}
```

## 4.14 Interaction Buffer

```
Key Pattern: buffer:interactions
Type: List
TTL: None

Values: JSON interaction records (FIFO)

Operations:
  LPUSH buffer:interactions {json_data}
  RPOP buffer:interactions
```

---

# 5. JSON Files Strategy

## 5.1 File Organization

```
/home/frappe/frappe-bench/sites/site1/
├── public/
│   └── memora_content/              # Public (CDN + Local Fallback)
│       ├── manifest.json            # Global manifest
│       ├── plans/
│       │   └── {plan_id}/
│       │       └── manifest.json    # Plan-specific manifest
│       ├── subjects/
│       │   └── {subject_id}/
│       │       ├── _h.json          # Hierarchy (Tracks > Units)
│       │       └── units/
│       │           └── {unit_id}_c.json  # Unit content
│       └── lessons/
│           └── {lesson_id}.json     # Lesson stages
│
└── private/
    └── memora_bitmaps/              # Private (Backend only)
        └── {subject_id}_b.json      # BitMap index for FastAPI
```

## 5.2 Public JSON Schemas

### 5.2.1 Global Manifest (manifest.json)

```json
{
  "version": 1706275200,
  "generated_at": "2026-02-01T10:00:00Z",
  "plans": [
    {
      "id": "PLAN-00001",
      "title": "High School - Scientific",
      "grade": "high_3",
      "stream": "scientific",
      "season": "2025-2026",
      "subjects_count": 8,
      "manifest_url": "/plans/PLAN-00001/manifest.json?v=1706275200"
    }
  ]
}
```

### 5.2.2 Plan Manifest (plans/{plan_id}/manifest.json)

```json
{
  "version": 1706275200,
  "plan_id": "PLAN-00001",
  "title": "High School - Scientific",
  "subjects": [
    {
      "id": "SUB-MATH-101",
      "title": "Mathematics",
      "total_lessons": 450,
      "total_tracks": 4,
      "is_free_preview": false,
      "hierarchy_url": "/subjects/SUB-MATH-101/_h.json?v=1706275200"
    }
  ]
}
```

### 5.2.3 Subject Hierarchy (subjects/{subject_id}/_h.json)

```json
{
  "version": 1706275200,
  "subject_id": "SUB-MATH-101",
  "title": "Mathematics",
  "is_linear": true,
  "tracks": [
    {
      "id": "TRK-00001",
      "title": "Chapter 1",
      "sort_order": 1,
      "total_lessons": 120,
      "units": [
        {
          "id": "UNT-00001",
          "title": "Real Numbers",
          "content_url": "/subjects/SUB-MATH-101/units/UNT-00001_c.json?v=1706275200"
        }
      ]
    }
  ]
}
```

### 5.2.4 Unit Content (subjects/{subject_id}/units/{unit_id}_c.json)

```json
{
  "version": 1706275200,
  "unit_id": "UNT-00001",
  "title": "Real Numbers",
  "is_linear": true,
  "topics": [
    {
      "id": "TPC-00001",
      "title": "Introduction",
      "sort_order": 1,
      "is_linear": true,
      "is_free": false,
      "lessons": [
        {
          "id": "LSN-00001",
          "title": "What are Real Numbers?",
          "bit_index": 0,
          "content_url": "/lessons/LSN-00001.json"
        }
      ]
    }
  ]
}
```

### 5.2.5 Lesson Content (lessons/{lesson_id}.json)

```json
{
  "version": 1706275200,
  "lesson_id": "LSN-00001",
  "title": "What are Real Numbers?",
  "subject_id": "SUB-MATH-101",
  "xp_reward": 10,
  "hearts_cost": 1,
  "stages": [
    {
      "index": 0,
      "type": "info",
      "is_skippable": true,
      "content": {
        "text": "Real numbers include...",
        "image_url": "/assets/real-numbers.png"
      }
    },
    {
      "index": 1,
      "type": "mcq",
      "is_skippable": false,
      "content": {
        "question": "Which of the following is irrational?",
        "options": [
          {"id": "a", "text": "3/4", "is_correct": false},
          {"id": "b", "text": "√2", "is_correct": true}
        ]
      },
      "explanation": "√2 is irrational because..."
    }
  ]
}
```

## 5.3 Private BitMap JSON ({subject_id}_b.json)

This file is used by FastAPI to calculate unlock states without reading CDN files.

```json
{
  "subject_id": "SUB-MATH-101",
  "version": 1706275200,
  "total_lessons": 99997,
  "structure": {
    "tracks": {
      "TRK-00001": {
        "sort_order": 1,
        "is_linear": true,
        "bit_range": [0, 9999],
        "excluded_bits": []
      }
    },
    "units": {
      "UNT-00001": {
        "track": "TRK-00001",
        "sort_order": 1,
        "is_linear": true,
        "is_free": false,
        "bit_range": [0, 999],
        "excluded_bits": [50, 51]
      }
    },
    "topics": {
      "TPC-00001": {
        "unit": "UNT-00001",
        "sort_order": 1,
        "is_linear": true,
        "is_free": true,
        "bit_range": [0, 99],
        "excluded_bits": [50, 51]
      }
    }
  }
}

# NEW: Check if ALL bits in range are set
def _range_all_set(self, bit_range: List[int], bitmap: bytes) -> bool:
    start, end = bit_range
    for bit in range(start, end + 1):
        if not self._check_bit(bitmap, bit):
            return False
    return True

def _is_range_passed(self, topic_data: dict, bitmap: bytes) -> bool:
    start, end = topic_data["bit_range"]
    excluded = set(topic_data.get("excluded_bits", []))
    
    for bit in range(start, end + 1):
        if bit in excluded:
            continue  # Skip deleted lessons
        if not self._check_bit(bitmap, bit):
            return False
    return True

# NEW: Count completed in range (for percentage)
def _count_in_range(self, bit_range: List[int], bitmap: bytes) -> int:
    start, end = bit_range
    count = 0
    for bit in range(start, end + 1):
        if self._check_bit(bitmap, bit):
            count += 1
    return count
```

## 5.4 Stage Content JSON Schemas

### Info Stage
```json
{
  "type": "info",
  "content": {
    "text": "Educational text here...",
    "image_url": "/files/image.png",
    "highlight": ["keyword1", "keyword2"]
  }
}
```

### Multiple Choice Question (MCQ)
```json
{
  "type": "mcq",
  "question": "What is 2+2?",
  "options": [
    {"id": "a", "text": "3", "is_correct": false},
    {"id": "b", "text": "4", "is_correct": true},
    {"id": "c", "text": "5", "is_correct": false}
  ],
  "shuffle_options": true,
  "multiple_correct": false
}
```

### True/False
```json
{
  "type": "true_false",
  "statement": "The Earth is round",
  "is_true": true
}
```

### Fill in the Blank
```json
{
  "type": "fill_blank",
  "text": "The capital of Jordan is ___",
  "blanks": [
    {
      "position": 0,
      "correct_answers": ["Amman", "amman"],
      "case_sensitive": false
    }
  ]
}
```

### Match Pairs
```json
{
  "type": "match",
  "pairs": [
    {"left": "Cairo", "right": "Egypt"},
    {"left": "Amman", "right": "Jordan"},
    {"left": "Riyadh", "right": "Saudi Arabia"}
  ],
  "shuffle": true
}
```

### Order/Sequence
```json
{
  "type": "order",
  "instruction": "Order the numbers ascending",
  "items": [
    {"id": 1, "text": "5", "correct_position": 2},
    {"id": 2, "text": "3", "correct_position": 1},
    {"id": 3, "text": "9", "correct_position": 3}
  ]
}
```

### Video
```json
{
  "type": "video",
  "video_url": "https://...",
  "duration_sec": 180,
  "require_complete": true,
}
```

## 5.6 Scalability Considerations

### Maximum Recommended Limits

| Level | Soft Limit | Hard Limit | Notes |
|-------|------------|------------|-------|
| Lessons per Subject | 50,000 | 150,000 | Beyond requires sharding |
| Topics per Unit | 50 | 100 | UI/UX consideration |
| Units per Track | 30 | 50 | Semester structure |
| Tracks per Subject | 20 | 30 | Academic year |

### Bitmap Size Calculation
```
Players × Subjects × (Total Lessons ÷ 8) = Redis Memory

Example:
100,000 players × 10 subjects × (100,000 lessons ÷ 8)
= 100,000 × 10 × 12.5 KB
= 12.5 GB Redis memory for progress only
```

### Optimization: Use `bit_range` instead of `bits` array

The `_b.json` file uses `bit_range: [start, end]` instead of listing every bit index.
This reduces file size from O(n) to O(1) where n = number of lessons.

---

# 6. Content Hierarchy

## 6.1 Structure Overview

```
Subject (المادة)
├── Track (الفصل/المسار)
│   ├── Unit (الوحدة)
│   │   ├── Topic (الموضوع)
│   │   │   └── Lesson (الدرس)
│   │   │       └── Stage (المرحلة)
```

## 6.2 Relationships

| Parent | Child | Relationship |
|--------|-------|--------------|
| Academic Plan | Plan Subject | One-to-Many (child table) |
| Subject | Track | One-to-Many |
| Track | Unit | One-to-Many |
| Unit | Topic | One-to-Many |
| Topic | Lesson | One-to-Many |
| Lesson | Lesson Stage | One-to-Many (child table) |

## 6.3 bit_index Assignment Rules

**CRITICAL**: The `bit_index` field in Memora Lesson is:

1. **Auto-assigned** on lesson creation
2. **Never changes** after assignment
3. **Never reused** even if lesson is deleted
4. **Per-subject** (each subject has its own sequence starting from 0)

This ensures bitmap integrity for existing players' progress.

```python
# Assignment Logic (in Memora Lesson before_insert)
def assign_bit_index(self):
    if self.bit_index:
        return  # Already assigned
    
    subject = frappe.get_doc("Memora Subject", self.subject, for_update=True)
    current_last = subject.next_bit_index or 0
    
    self.bit_index = current_last
    subject.next_bit_index = current_last + 1
    subject.save()
```

## 6.4 Linear vs Non-Linear Content

| Field | Effect when True | Effect when False |
|-------|------------------|-------------------|
| `is_linear` | Must complete items in order | Can access any item |

The linearity is checked at each level:
- **Subject**: Tracks must be completed in order
- **Track**: Units must be completed in order
- **Unit**: Topics must be completed in order
- **Topic**: Lessons must be completed in order

---

# End of Part 1