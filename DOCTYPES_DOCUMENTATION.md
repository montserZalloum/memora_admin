# Memora Admin Doctypes Documentation

This document provides comprehensive documentation for all DocTypes in the Memora Admin module, including their fields, field types, and descriptions.

## Table of Contents

1. [Memora Academic Plan](#memora-academic-plan)
2. [Memora Analytics Aggregate](#memora-analytics-aggregate)
3. [Memora Grade](#memora-grade)
4. [Memora Grant Component](#memora-grant-component)
5. [Memora Interaction Log](#memora-interaction-log)
6. [Memora Lesson](#memora-lesson)
7. [Memora Lesson Stage](#memora-lesson-stage)
8. [Memora Lesson Stage Settings](#memora-lesson-stage-settings)
9. [Memora Major](#memora-major)
10. [Memora Memory State](#memora-memory-state)
11. [Memora Plan Overrider](#memora-plan-overrider)
12. [Memora Plan Subject](#memora-plan-subject)
13. [Memora Player Device](#memora-player-device)
14. [Memora Player Profile](#memora-player-profile)
15. [Memora Player Wallet](#memora-player-wallet)
16. [Memora Product Grant](#memora-product-grant)
17. [Memora Season](#memora-season)
18. [Memora Settings](#memora-settings)
19. [Memora Structure Progress](#memora-structure-progress)
20. [Memora Subject](#memora-subject)
21. [Memora Subscription Transaction](#memora-subscription-transaction)
22. [Memora Sync Log](#memora-sync-log)
23. [Memora Topic](#memora-topic)
24. [Memora Track](#memora-track)
25. [Memora Unit](#memora-unit)
25. [Memora Build Queue](#memora-build-queue)

---

## Memora Academic Plan

An academic plan that groups subjects and defines curriculum structure for different grades and majors.

| Field Name | Field Type | Label |
|---|---|---|
| plan_name | Data | Plan Name |
| grade | Link | Grade |
| major | Link | Major |
| season | Link | Season |
| is_published | Check | Is Published |
| plan_subjects | Table | Plan Subjects |

---

## Memora Analytics Aggregate

Analytics data aggregated for lessons, tracking attempts, time spent, and success rates.

| Field Name | Field Type | Label |
|---|---|---|
| lesson | Link | Lesson |
| date | Date | Date |
| total_attempts | Int | Total Attempts |
| avg_time_spent | Float | Average Time Spent |
| success_rate | Float | Success Rate |

---

## Memora Grade

Represents a grade level in the academic hierarchy (e.g., Grade 1, Grade 2, etc.).

| Field Name | Field Type | Label |
|---|---|---|
| grade_title | Data | Grade Title |
| sort_order | Int | Sort Order |

---

## Memora Grant Component

A child table for Memora Product Grant that links to target doctypes and names.

| Field Name | Field Type | Label |
|---|---|---|
| target_doctype | Link | Target Doctype |
| target_name | Dynamic Link | Target Name |

---

## Memora Interaction Log

Logs player interactions with lessons, tracking events like starting, completing, failing, and skipping.

| Field Name | Field Type | Label |
|---|---|---|
| player | Link | Player |
| lesson | Link | Lesson |
| stage_id | Data | Stage ID |
| event_type | Select | Event Type |
| time_spent | Int | Time Spent (Seconds) |
| errors_count | Int | Errors Count |
| timestamp | Datetime | Timestamp |
| client_metadata | Code | Client Metadata |

---

## Memora Lesson

A lesson contains multiple stages and is organized under topics within units.

| Field Name | Field Type | Label |
|---|---|---|
| lesson_title | Data | Lesson Title |
| topic | Link | Topic |
| base_xp | Int | Base XP |
| max_hearts | Int | Max Hearts |
| content_hash | Data | Content Hash |
| stages | Table | Stages |

---

## Memora Lesson Stage

A child table for Memora Lesson that defines individual stages within a lesson.

| Field Name | Field Type | Label |
|---|---|---|
| stage_id | Data | Stage ID |
| stage_type | Link | Stage Type |
| is_skippable | Check | Is Skippable |
| config_json | Code | Config JSON |

---

## Memora Lesson Stage Settings

Settings template for lesson stages, defining default configurations and payload schemas.

| Field Name | Field Type | Label |
|---|---|---|
| stage_title | Data | Stage Title |
| is_skippable | Check | Is Skippable |
| default_stage_time | Int | Default Stage Time |
| payload | Code | Payload |

---

## Memora Major

Represents a major or specialization field in a grade.

| Field Name | Field Type | Label |
|---|---|---|
| major_title | Data | Major Title |

---

## Memora Memory State

Tracks the spaced repetition memory state for each player-subject-stage combination using FSRS algorithm.

| Field Name | Field Type | Label |
|---|---|---|
| season | Link | Season |
| subject | Link | Subject |
| player | Link | Player |
| stage_id | Data | Stage ID |
| stability | Float | Stability |
| difficulty | Float | Difficulty |
| next_review | Datetime | Next Review |

---

## Memora Plan Overrider

Allows overriding the visibility or accessibility of subjects, tracks, or units in an academic plan.

| Field Name | Field Type | Label |
|---|---|---|
| plan | Link | Plan |
| ref_doctype | Link | Ref Doctype |
| ref_name | Dynamic Link | Ref Name |
| action | Select | Action |

---

## Memora Plan Subject

A child table for Memora Academic Plan that links subjects to a plan with custom notes.

| Field Name | Field Type | Label |
|---|---|---|
| subject | Link | Subject |
| alias_title | Data | Alias Title |
| notes | Small Text | Notes |
| is_premium | Check | is Premium |

---

## Memora Player Device

A child table for Memora Player Profile that tracks authorized devices for a player.

| Field Name | Field Type | Label |
|---|---|---|
| device_id | Data | Device ID |
| device_name | Data | Device Name |
| last_login | Datetime | Last Login |
| user_agent | Small Text | User Agent |
| platform | Select | Platform |

---

## Memora Player Profile

Represents a player profile with personal information, preferences, and authorized devices.

| Field Name | Field Type | Label |
|---|---|---|
| user | Link | User |
| display_name | Data | Display Name |
| plan | Link | Current Plan |
| avatar | Select | Avatar |
| grade | Link | Grade |
| major | Link | Major |
| season | Link | Season |
| preferred_lang | Select | Preferred Language |
| notifications | Check | Notifications |
| authorized_devices | Table | Authorized Devices |

---

## Memora Player Wallet

Tracks a player's experience points, streaks, and account status.

| Field Name | Field Type | Label |
|---|---|---|
| player | Link | Player |
| total_xp | Int | Total XP |
| current_streak | Int | Current Streak |
| dirty_flag | Check | Dirty Flag |
| status | Select | Status |

---

## Memora Product Grant

Defines a product/premium content item with components that unlock specific content.

| Field Name | Field Type | Label |
|---|---|---|
| plan | Link | Plan |
| item_code | Link | Item Code |
| is_published | Check | Is Published |
| grant_components | Table | Grant Components |

---

## Memora Season

Represents a season or semester with start and end dates.

| Field Name | Field Type | Label |
|---|---|---|
| season_title | Data | Season Title |
| start_date | Date | Start Date |
| end_date | Date | End Date |
| is_published | Check | Is Published |

---

## Memora Settings

Global configuration settings for the Memora Admin module including CDN, gamification, security, and FSRS engine settings.

| Field Name | Field Type | Label |
|---|---|---|
| cdn_enabled | Check | CDN Enabled |
| cdn_base_url | Data | CDN Base URL |
| local_fallback_mode | Check | Local Fallback Mode |
| storage_provider | Select | Storage Provider |
| json_version | Int | JSON Version |
| access_key | Password | Access Key |
| secret_key | Password | Secret Key |
| batch_interval_minutes | Int | Batch Interval (minutes) |
| batch_threshold | Int | Batch Threshold |
| signed_url_expiry_hours | Int | Signed URL Expiry (hours) |
| default_max_hearts | Int | Default Max Hearts |
| xp_per_heart | Int | XP Per Heart |
| base_lesson_xp | Int | Base Lesson XP |
| replay_xp | Int | Replay XP |
| max_devices_per_player | Int | Max Devices Per Player |
| session_timeout_days | Int | Session Timeout (Days) |
| fsrs_weights | Small Text | FSRS Weights |
| request_retention_days | Int | Request Retention (Days) |

---

## Memora Structure Progress

Tracks a player's progress through a subject's lesson structure using a bitset to mark completed lessons.

| Field Name | Field Type | Label |
|---|---|---|
| player | Link | Player |
| subject | Link | Subject |
| passed_lessons_bitset | Long Text | Passed Lessons Bitset |
| completion_percentage | Float | Completion Percentage |

---

## Memora Subject

Represents a subject area that contains tracks, units, topics, and lessons.

| Field Name | Field Type | Label |
|---|---|---|
| subject_title | Data | Subject Title |
| image | Attach Image | Image |
| description | Text Editor | Description |
| in_linear | Check | Is Linear |
| is_published | Check | Is Published |
| redis_key_prefix | Int | Redis Key Prefix |
| next_bit_index | Int | Next Bit Index |
| sort_order | Int | Sort Order |

---

## Memora Subscription Transaction

Records player subscription transactions, payment methods, and links to invoices and grants.

| Field Name | Field Type | Label |
|---|---|---|
| player | Link | Player |
| payment_method | Select | Payment Method |
| status | Select | Status |
| transaction_id | Data | Transaction ID |
| amount_paid | Currency | Amount Paid |
| erpnext_invoice | Link | ERPNext Invoice |
| related_grant | Link | Related Grant |

---

## Memora Sync Log

Tracks synchronization jobs between Redis and the database for wallets, progress, and memory states.

| Field Name | Field Type | Label |
|---|---|---|
| job_id | Data | Job ID |
| sync_type | Select | Sync Type |
| records_processed | Int | Records Processed |
| status | Select | Status |

---

## Memora Topic

A topic is a collection of lessons within a unit, organized hierarchically.

| Field Name | Field Type | Label |
|---|---|---|
| topic_title | Data | Topic Title |
| unit | Link | Unit |
| sort_order | Int | Sort Order |
| is_free | Check | Is Free |
| is_linear | Check | Is Linear |
| is_published | Check | Is Published |

---

## Memora Track

A track is a collection of units within a subject, representing a learning path.

| Field Name | Field Type | Label |
|---|---|---|
| track_title | Data | Track Title |
| subject | Link | Subject |
| sort_order | Int | Sort Order |
| image | Attach Image | Image |
| description | Small Text | Description |
| is_sold_separately | Check | Is Sold Separately |
| is_published | Check | Is Published |
| is_linear | Check | Is Linear |

---

## Memora Unit

A unit is a collection of topics within a track, forming the middle tier of the learning hierarchy.

| Field Name | Field Type | Label |
|---|---|---|
| unit_title | Data | Unit Title |
| track | Link | Track |
| sort_order | Int | Sort Order |
| is_free | Check | Is Free |
| is_published | Check | Is Published |
| is_linear | Check | Is Linear |

---

## Hierarchy Overview

The content hierarchy in Memora Admin follows this structure:

```
Subject
├── Track
│   ├── Unit
│   │   ├── Topic
│   │   │   └── Lesson
│   │   │       └── Stage
```

## Key Relationships

- **Memora Academic Plan**: Links Grade, Major, and Season; contains Plan Subjects
- **Memora Player Profile**: Links to User, Grade, Major, Season, and Academic Plan
- **Memora Subject**: Contains Tracks
- **Memora Track**: Contains Units
- **Memora Unit**: Contains Topics
- **Memora Topic**: Contains Lessons
- **Memora Lesson**: Contains Stages
- **Memora Lesson Stage Settings**: Template for lesson stages

## Special Doctypes

- **Memora Settings**: Single document for global configuration
- **Memora Player Wallet**: One per player, tracks XP and streaks
- **Memora Interaction Log**: Records all player interactions with lessons
- **Memora Memory State**: Implements spaced repetition algorithm (FSRS)
- **Memora Structure Progress**: Tracks player's lesson completion using bitsets


## Memora Build Queue

```
DocType: Memora Build Queue
Naming Rule: autoname

Purpose: تتبع طلبات توليد JSON

┌──────────────────┬─────────────┬─────────┬───────────────────────────────────┐
│ Field Name       │ Type        │ Required│ Description                       │
├──────────────────┼─────────────┼─────────┼───────────────────────────────────┤
│ target_type      │ Select      │ ✓       │ Plan/Subject/Lesson               │
│ target_name      │ Dynamic Link│ ✓       │ العنصر المطلوب بناؤه              │
│ trigger_reason   │ Select      │         │ content_update/override_change/manual│
│ triggered_by     │ Link        │ RO      │ User                              │
│ triggered_at     │ Datetime    │ RO      │ وقت الطلب                         │
│ ─────────────────│─────────────│─────────│───────────────────────────────────│
│ Build Status     │             │         │                                   │
│ ─────────────────│─────────────│─────────│───────────────────────────────────│
│ status           │ Select      │         │ pending/processing/completed/failed│
│ started_at       │ Datetime    │         │ بداية البناء                      │
│ completed_at     │ Datetime    │         │ نهاية البناء                      │
│ duration_sec     │ Float       │ RO      │ مدة البناء (ثواني)                │
│ ─────────────────│─────────────│─────────│───────────────────────────────────│
│ Results          │             │         │                                   │
│ ─────────────────│─────────────│─────────│───────────────────────────────────│
│ files_generated  │ Int         │         │ عدد الملفات                       │
│ error_message    │ Text        │         │ رسالة الخطأ (إن وجد)              │
└──────────────────┴─────────────┴─────────┴───────────────────────────────────┘
```