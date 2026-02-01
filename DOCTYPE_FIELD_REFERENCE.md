# Memora Admin - DocType Fields Summary

**Date Generated:** 2026-02-01  
**Total DocTypes:** 28  
**Status:** New Doctypes (from latest commit)

---

## Overview

The memora_admin app contains 28 doctypes organized into 6 main categories. Below is a comprehensive breakdown of all fields added to each doctype.

---

## 1. Academic Structure Layer

### 1.1 Memora Academic Plan
**Type:** Master | **Fields:** 13 | **Purpose:** Maps curriculum to grades/majors

```
Field Order:
  1. plan_name (Text, Required)
  2. grade (Link → Memora Grade, Required)
  3. major (Link → Memora Major)
  4. season (Link → Memora Season, Required)
  5. is_published (Checkbox)
  6. plan_subjects (Table → Memora Plan Subject)
  7. sb_stats (Section Header)
  8. total_subjects (Integer)
  9. total_lessons (Integer)
  10. sb_json (Section Header)
  11. json_version (Integer)
  12. json_hash (Text)
  13. json_generated_at (DateTime)
```

### 1.2 Memora Subject
**Type:** Master | **Fields:** 16 | **Purpose:** Top-level content category with media

```
Field Order:
  1. subject_title (Text, Required)
  2. image (Attach Image)
  3. description (Text)
  4. in_linear (Checkbox)
  5. is_published (Checkbox)
  6. redis_key_prefix (Text)
  7. last_bit_index (Integer)
  8. sort_order (Integer)
  9. json_version (Integer)
  10. json_generated_at (DateTime)
  11. sb_stats (Section Header)
  12. total_tracks (Integer)
  13. total_lessons (Integer)
  14. sb_json (Section Header)
  15. json_hash (Text)
  16. cdn_url (Text)
```

### 1.3 Memora Track
**Type:** Master | **Fields:** 11 | **Purpose:** Skill groupings within subjects

```
Field Order:
  1. track_title (Text, Required)
  2. subject (Link → Memora Subject, Required)
  3. sort_order (Integer)
  4. image (Attach Image)
  5. description (Text)
  6. is_sold_separately (Checkbox)
  7. is_published (Checkbox)
  8. is_linear (Checkbox)
  9. sb_stats (Section Header)
  10. total_units (Integer)
  11. total_lessons (Integer)
```

### 1.4 Memora Unit
**Type:** Master | **Fields:** 9 | **Purpose:** Content modules within tracks

```
Field Order:
  1. unit_title (Text, Required)
  2. track (Link → Memora Track, Required)
  3. sort_order (Integer)
  4. is_free (Checkbox)
  5. is_published (Checkbox)
  6. is_linear (Checkbox)
  7. sb_stats (Section Header)
  8. total_topics (Integer)
  9. total_lessons (Integer)
```

### 1.5 Memora Topic
**Type:** Master | **Fields:** 10 | **Purpose:** Learning units within units

```
Field Order:
  1. topic_title (Text, Required)
  2. unit (Link → Memora Unit, Required)
  3. sort_order (Integer)
  4. is_free (Checkbox)
  5. is_linear (Checkbox)
  6. is_published (Checkbox)
  7. sb_hierarchy (Section Header)
  8. track (Link → Memora Track)
  9. subject (Link → Memora Subject)
  10. total_lessons (Integer)
```

### 1.6 Memora Lesson
**Type:** Master | **Fields:** 12 | **Purpose:** Atomic learning units with stages

```
Field Order:
  1. lesson_title (Text, Required)
  2. topic (Link → Memora Topic, Required)
  3. base_xp (Integer) - XP earned on first completion
  4. max_hearts (Integer) - Allowed attempts (default 3)
  5. content_hash (Text) - For version control
  6. stages (Table → Memora Lesson Stage) - Questions/exercises
  7. bit_index (Integer) - Position in bitset tracking
  8. sb_hierarchy (Section Header)
  9. unit (Link → Memora Unit)
  10. track (Link → Memora Track)
  11. subject (Link → Memora Subject)
  12. is_reviewable (Checkbox) - FSRS-eligible for spaced repetition
```

---

## 2. Player & Profile Layer

### 2.1 Memora Player Profile
**Type:** Master | **Fields:** 10 | **Purpose:** Central player identity and preferences

```
Field Order:
  1. user (Link → User, Required, Unique)
  2. display_name (Text, Required)
  3. plan (Link → Memora Academic Plan, Required) - Current curriculum
  4. avatar (Select, Required) - Cosmetic choice
  5. grade (Link → Memora Grade, Required)
  6. major (Link → Memora Major, Required)
  7. season (Link → Memora Season, Required)
  8. preferred_lang (Select) - ar or en
  9. notifications (Checkbox) - Toggle push notifications
  10. authorized_devices (Table → Memora Player Device)
```

### 2.2 Memora Player Wallet
**Type:** Master | **Fields:** 9 | **Purpose:** Gamification metrics and sync status

```
Field Order:
  1. player (Link → Memora Player Profile, Required, Unique)
  2. total_xp (Integer) - Cumulative experience points
  3. current_streak (Integer) - Days of consecutive activity
  4. dirty_flag (Checkbox) - Redis ↔ DB sync indicator
  5. status (Select) - Active/Suspended/Banned
  6. sb_stats (Section Header)
  7. total_lessons (Integer) - Lessons completed
  8. total_time_min (Integer) - Total study time in minutes
  9. last_sync_at (DateTime) - Last Redis sync timestamp
```

### 2.3 Memora Player Device
**Type:** Child Table | **Fields:** 6 | **Parent:** Memora Player Profile | **Purpose:** Multi-device authentication

```
Field Order:
  1. device_id (Text, Required) - UUID of device
  2. device_name (Text) - User-friendly name
  3. last_login (DateTime)
  4. user_agent (Text) - Browser/app info
  5. platform (Text) - iOS/Android/Web
  6. push_token (Text) - FCM/APNS token
```

---

## 3. Content & Lessons Layer

### 3.1 Memora Lesson Stage
**Type:** Child Table | **Fields:** 4 | **Parent:** Memora Lesson | **Purpose:** Individual exercises/questions

```
Field Order:
  1. stage_id (Text, Required) - Unique identifier
  2. stage_type (Link → Memora Lesson Stage Settings, Required)
  3. is_skippable (Checkbox) - Can bypass without counting time
  4. config_json (Code/JSON) - Stage-specific configuration
```

### 3.2 Memora Lesson Stage Settings
**Type:** Master | **Fields:** 4 | **Purpose:** Template for stage types

```
Field Order:
  1. stage_title (Text, Required)
  2. is_skippable (Checkbox) - Skip timeout rule
  3. default_stage_time (Integer) - Default duration in seconds
  4. payload (Code/JSON) - Default configuration payload
```

### 3.3 Memora Interaction Log
**Type:** Master | **Fields:** 8 | **Purpose:** Granular event tracking for analytics

```
Field Order:
  1. player (Link → Memora Player Profile, Required)
  2. lesson (Link → Memora Lesson, Required)
  3. stage_id (Text, Required)
  4. event_type (Select, Required) - Started/Completed/Failed/Skipped
  5. time_spent (Integer) - Seconds
  6. errors_count (Integer) - Mistakes made
  7. timestamp (DateTime, Required)
  8. client_metadata (Code/JSON) - IP, browser, device info
```

---

## 4. Learning & Progress Layer

### 4.1 Memora Memory State
**Type:** Master | **Fields:** 8 | **Purpose:** FSRS spaced repetition tracking

```
Field Order:
  1. season (Link → Memora Season, Required)
  2. subject (Link → Memora Subject, Required)
  3. player (Link → Memora Player Profile, Required)
  4. stage_id (Text, Required)
  5. stability (Float) - Memory stability parameter
  6. difficulty (Float) - Question difficulty estimate
  7. next_review (DateTime) - When to review next
  8. lesson (Link → Memora Lesson, Required)
```

**Note:** Implements Free Spaced Repetition Scheduler (FSRS) algorithm

### 4.2 Memora Structure Progress
**Type:** Master | **Fields:** 4 | **Purpose:** Lesson completion tracking per subject

```
Field Order:
  1. player (Link → Memora Player Profile, Required)
  2. subject (Link → Memora Subject, Required)
  3. passed_lessons_bitset (Long Text) - Binary completion map
  4. completion_percentage (Float)
```

**Note:** Uses bitset for efficient lesson completion tracking

### 4.3 Memora Analytics Aggregate
**Type:** Master | **Fields:** 5 | **Purpose:** Lesson-level performance metrics

```
Field Order:
  1. lesson (Link → Memora Lesson, Required)
  2. date (Date, Required)
  3. total_attempts (Integer)
  4. avg_time_spent (Float) - Average seconds per attempt
  5. success_rate (Float) - Percentage of successful attempts
```

### 4.4 Memora Content Report
**Type:** Master | **Fields:** 7 | **Purpose:** Player-submitted feedback and bug reports

```
Field Order:
  1. player (Link → Memora Player Profile, Required)
  2. subject (Link → Memora Subject)
  3. lesson (Link → Memora Lesson)
  4. screen_shot (Attach) - Evidence image
  5. report_type (Select) - BugReport/Feedback/Typo
  6. description (Long Text)
  7. status (Select) - Open/InProgress/Resolved/Closed
```

---

## 5. Gamification Layer

### 5.1 Memora Achievement
**Type:** Master | **Fields:** 11 | **Purpose:** Badge unlock conditions

```
Field Order:
  1. achievement_title (Text, Required)
  2. description (Small Text)
  3. badge_image (Attach Image)
  4. sb_unlock (Section Header)
  5. achievement_type (Select) - Milestone/Challenge/Streak
  6. threshold (Integer) - XP/lessons/streak to unlock
  7. subject (Link → Memora Subject) - Optional scope
  8. sb_rewards (Section Header)
  9. xp_reward (Integer) - Bonus XP for achieving
  10. is_active (Checkbox)
  11. sort_order (Integer)
```

### 5.2 Memora Grade
**Type:** Master | **Fields:** 2 | **Purpose:** Academic levels (1st, 2nd grade, etc.)

```
Field Order:
  1. grade_title (Text, Required)
  2. sort_order (Integer)
```

### 5.3 Memora Major
**Type:** Master | **Fields:** 1 | **Purpose:** Specializations (Science, Arts, etc.)

```
Field Order:
  1. major_title (Text, Required)
```

### 5.4 Memora Season
**Type:** Master | **Fields:** 4 | **Purpose:** Academic calendar periods

```
Field Order:
  1. season_title (Text, Required)
  2. start_date (Date, Required)
  3. end_date (Date, Required)
  4. is_published (Checkbox)
```

---

## 6. Platform & Admin Layer

### 6.1 Memora Settings
**Type:** Singleton | **Fields:** 24 | **Purpose:** Global configuration

```
Field Order (Organized by Section):

CDN & STORAGE:
  1. cdn_section (Section Break)
  2. cdn_enabled (Checkbox)
  3. cdn_base_url (Text) - https://cdn.example.com
  4. local_fallback_mode (Checkbox) - Fallback to server if CDN down
  5. storage_provider (Select) - AWS S3 or Cloudflare R2
  6. column_break_cdn (Column Break)
  7. json_version (Integer) - Cache bust number
  8. access_key (Password)
  9. secret_key (Password)
  10. batch_interval_minutes (Integer)
  11. batch_threshold (Integer)
  12. signed_url_expiry_hours (Integer)

GAMIFICATION:
  13. gamification_section (Section Break)
  14. default_max_hearts (Integer) - Default attempts per lesson
  15. xp_per_heart (Integer) - Bonus XP per remaining heart
  16. column_break_game (Column Break)
  17. base_lesson_xp (Integer) - Base XP for lesson completion
  18. replay_xp (Integer) - XP for replaying completed lesson

SECURITY:
  19. security_section (Section Break)
  20. max_devices_per_player (Integer) - Multi-device limit
  21. session_timeout_days (Integer)

FSRS ENGINE:
  22. fsrs_section (Section Break)
  23. fsrs_weights (Small Text) - Algorithm weights (JSON)
  24. request_retention_days (Integer) - Log retention period
```

### 6.2 Memora Product Grant
**Type:** Master | **Fields:** 4 | **Purpose:** Map products to curriculum access

```
Field Order:
  1. plan (Link → Memora Academic Plan, Required)
  2. item_code (Link → Item, Required) - ERPNext inventory item
  3. is_published (Checkbox, Default: true)
  4. grant_components (Table → Memora Grant Component)
```

### 6.3 Memora Grant Component
**Type:** Child Table | **Fields:** 2 | **Parent:** Memora Product Grant | **Purpose:** Dynamic content grants

```
Field Order:
  1. target_doctype (Link → DocType, Required) - Subject/Unit/Track/Lesson
  2. target_name (Dynamic Link, Required) - Specific content to grant
```

### 6.4 Memora Plan Overrider
**Type:** Master | **Fields:** 4 | **Purpose:** Custom access rules per player

```
Field Order:
  1. plan (Link → Memora Academic Plan, Required)
  2. ref_doctype (Link → DocType, Required) - Subject/Track/Unit/Lesson
  3. ref_name (Dynamic Link, Required) - Specific content
  4. action (Select) - Grant/Revoke/Override
```

### 6.5 Memora Plan Subject
**Type:** Child Table | **Fields:** 4 | **Parent:** Memora Academic Plan | **Purpose:** Subject customization per plan

```
Field Order:
  1. subject (Link → Memora Subject, Required)
  2. alias_title (Text) - Alternative name for this plan
  3. notes (Small Text) - Plan-specific notes
  4. is_premium (Checkbox, Default: true)
```

### 6.6 Memora Subscription Transaction
**Type:** Master | **Fields:** 7 | **Purpose:** Payment tracking

```
Field Order:
  1. player (Link → Memora Player Profile, Required)
  2. payment_method (Select, Required) - Payment Gateway/Manual-Admin/Voucher
  3. status (Select) - Pending Approval/Approved/Failed
  4. transaction_id (Text)
  5. amount_paid (Float)
  6. erpnext_invoice (Link → Sales Invoice)
  7. related_grant (Link → Memora Product Grant)
```

### 6.7 Memora Build Queue
**Type:** Master | **Fields:** 13 | **Purpose:** Async content generation pipeline

```
Field Order:
  1. target_type (Select, Required) - Plan/Subject/Unit/Lesson
  2. target_name (Dynamic Link, Required)
  3. trigger_reason (Text)
  4. triggered_by (Link → User)
  5. triggered_at (DateTime)
  6. status_section (Section Break)
  7. status (Select) - Pending/Processing/Completed/Failed
  8. started_at (DateTime)
  9. completed_at (DateTime)
  10. duration_sec (Integer)
  11. results_section (Section Break)
  12. files_generated (Integer)
  13. error_message (Long Text)
```

### 6.8 Memora Sync Log
**Type:** Master | **Fields:** 4 | **Purpose:** Batch synchronization audit trail

```
Field Order:
  1. job_id (Text, Required) - Queue job identifier
  2. sync_type (Select, Required) - Wallet/Progress/Memory
  3. records_processed (Integer)
  4. status (Select) - Pending/Processing/Completed/Failed
```

---

## Key Architectural Patterns

### 1. Content Hierarchy (Top-Down)
```
Academic Plan
  ├── Plan Subject
  └── Grade + Major + Season
  
Subject (Master)
  ├── Track
  │   ├── Unit
  │   │   └── Topic
  │   │       └── Lesson
  │   │           └── Stage (Lesson Stage)
  │   │               └── Stage Type (Lesson Stage Settings)
```

### 2. Player Ecosystem
```
Player Profile (User identity)
  ├── Player Wallet (XP, streaks, stats)
  │   └── synced via dirty_flag
  ├── Player Device (Multi-device auth)
  ├── Interaction Log (Event tracking)
  ├── Memory State (Spaced repetition)
  └── Structure Progress (Completion bitset)
```

### 3. Gamification Engine
```
Lesson rewards:
  base_xp (configurable in Settings)
  + (max_hearts - errors_count) × xp_per_heart
  
Achievement tracking:
  type (Milestone/Challenge/Streak)
  threshold → xp_reward

Player progression:
  current_streak (days active)
  total_xp (cumulative)
  completion_percentage (per subject)
```

### 4. Spaced Repetition (FSRS)
```
Memory State fields:
  stability (increases on success)
  difficulty (adjusts on mistakes)
  next_review (recalculated after each attempt)
  
Configured via Settings:
  fsrs_weights (algorithm parameters)
  is_reviewable (lesson flag)
```

### 5. Admin Control & Versioning
```
JSON Generation:
  json_version (cache bust)
  json_hash (integrity check)
  json_generated_at (track recency)
  redis_key_prefix (storage location)

Build Queue:
  Async generation for Plans/Subjects
  Track progress and errors
  Support replay on failure
```

### 6. Multi-Tenancy Support
```
Per-Player Customization:
  Plan Overrider (content access)
  Subscription Transaction (purchase history)
  Product Grant (what they own)
  
Per-Plan Variants:
  Plan Subject (renamed subjects)
  Grade + Major + Season (cohort grouping)
```

---

## Field Type Reference

| Type | Usage | Examples |
|------|-------|----------|
| **Data** | Short text | titles, IDs, names |
| **Link** | Foreign key | player, lesson, subject |
| **Dynamic Link** | Conditional FK | target_name (depends on target_doctype) |
| **Text/Small Text** | Long text | descriptions, notes |
| **Checkbox** | Boolean | is_published, is_free |
| **Select** | Enum | status, event_type, platform |
| **Integer** | Whole numbers | XP, attempts, sort_order |
| **Float** | Decimals | success_rate, stability, difficulty |
| **Date/DateTime** | Timestamps | next_review, triggered_at |
| **Attach/Attach Image** | File uploads | avatar, badge_image |
| **Code** | JSON/HTML | config_json, payload |
| **Table** | Child records | stages, authorized_devices |
| **Section Break** | UI organization | sb_stats, security_section |
| **Column Break** | Layout helper | column_break_cdn |

---

## Summary Statistics

- **Total Doctypes:** 28
- **Master Doctypes:** 20
- **Child Tables:** 5
- **Singleton:** 1
- **Total Fields (across all):** ~210
- **Common Field Names:**
  - `is_published` → 9 occurrences
  - `sort_order` → 8 occurrences
  - `player` → 7 occurrences
  - `subject` → 8 occurrences

---

## Recent Additions (Key New Fields)

1. **FSRS Support**
   - `Memora Memory State` - stability, difficulty, next_review
   - `Memora Lesson.is_reviewable` - eligibility flag
   - `Memora Settings.fsrs_weights` - algorithm configuration

2. **Content Generation Pipeline**
   - `Memora Build Queue` - async generation tracking
   - `Memora Academic Plan.json_*` - versioning fields
   - `Memora Subject.json_*` - content caching fields

3. **Advanced Analytics**
   - `Memora Interaction Log.client_metadata` - detailed tracking
   - `Memora Analytics Aggregate` - lesson-level metrics
   - `Memora Structure Progress.passed_lessons_bitset` - efficient tracking

4. **Multi-Device Support**
   - `Memora Player Device` - table for devices
   - `Memora Settings.max_devices_per_player` - rate limiting
   - `push_token` field for notifications

5. **Gamification Enhancement**
   - `Memora Achievement` - badge system
   - `xp_per_heart` configuration in Settings
   - `replay_xp` for lesson revisits

---

## Next Steps for Implementation

1. **Database Migrations** - Create tables for all 28 doctypes
2. **Indexes** - Add on frequently-filtered fields (player, season, status)
3. **Permissions** - Set role-based access control
4. **Frontend Forms** - Generate form UI from field definitions
5. **APIs** - Create REST/GraphQL endpoints for mobile apps
6. **Background Jobs** - Implement build queue and sync processes

