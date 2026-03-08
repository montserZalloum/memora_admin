# Data Model: Live Challenges

**Feature**: `037-live-challenges` | **Date**: 2026-03-07

## Entity Relationship Diagram

```
Memora Live Challenge Event (1)
├── has many ──► Memora Live Challenge Question (child table, ordered by idx)
├── has many ──► Memora Live Challenge Eligible Plan (child table)
└── has many ──► Memora Live Challenge Participation (standalone, linked)

Memora Live Challenge Participation (1)
└── belongs to ──► Memora Live Challenge Event (via event field)
└── belongs to ──► Memora Player Profile (via player field)

Memora Live Challenge Eligible Plan (child table)
└── links to ──► Memora Academic Plan (via plan field)

Memora Live Challenge Question (child table)
└── optionally links to ──► Memora Review Item (via source_review_item field)
```

## Entities

### 1. Memora Live Challenge Event

**Naming**: `autoname = "LC-.#####."` (e.g., LC-00001)
**Module**: Memora Admin

| Field | Fieldtype | Options/Default | Required | Notes |
|-------|-----------|----------------|----------|-------|
| event_name | Data | | Yes | Display name |
| description | Text Editor | | No | Rich text description shown to students |
| status | Select | Draft/Waiting/Active/Ended | Yes | Default: Draft |
| scheduled_start | Datetime | | Yes | When waiting room opens |
| waiting_room_duration | Int | Default: 180 | Yes | Seconds (min: 30, max: 600) |
| exam_duration | Int | Default: 10 | Yes | Minutes (min: 1, max: 180) |
| exam_start_ts | Datetime | | No | Computed: scheduled_start + waiting_room_duration. Read-only. |
| exam_end_ts | Datetime | | No | Computed: exam_start_ts + exam_duration. Read-only. |
| enable_question_timer | Check | Default: 0 | No | Per-question countdown |
| question_time_limit | Int | Default: 30 | No | Seconds. Visible only when enable_question_timer=1 |
| capacity | Int | Default: 100 | Yes | Max participants (min: 1, max: 10000) |
| is_paid | Check | Default: 0 | No | Deferred — flag stored, not enforced |
| show_correct_answers | Check | Default: 0 | No | Show answers after submission |
| show_student_rank | Check | Default: 0 | No | Show rank after event ends |
| participation_xp | Int | Default: 0 | No | XP for all submitters |
| first_place_xp | Int | Default: 0 | No | Bonus for rank 1 |
| second_place_xp | Int | Default: 0 | No | Bonus for rank 2 |
| third_place_xp | Int | Default: 0 | No | Bonus for rank 3 |
| default_xp | Int | Default: 0 | No | Bonus for rank 4+ |
| questions | Table | Memora Live Challenge Question | Yes | Child table |
| eligible_plans | Table | Memora Live Challenge Eligible Plan | No | Empty = all plans |
| leaderboard_json | JSON | | No | Top 20 entries, populated after event ends |
| participant_count | Int | Default: 0 | No | Read-only counter, updated during event |
| submitted_count | Int | Default: 0 | No | Read-only counter, updated during event |

**State transitions**:

| From | To | Trigger |
|------|----|---------|
| Draft | Waiting | Scheduled job: `scheduled_start <= now()` |
| Waiting | Active | Scheduled job: `exam_start_ts <= now()` |
| Active | Ended | Scheduled job: `exam_end_ts <= now()` |

**Validation rules**:
- `scheduled_start` must be in the future (only when status=Draft)
- `waiting_room_duration` must be between 30 and 600 seconds
- `exam_duration` must be between 1 and 180 minutes
- `capacity` must be between 1 and 10000
- `questions` must have at least 1 row before leaving Draft
- `question_time_limit` is only relevant when `enable_question_timer=1`
- All XP fields must be >= 0
- Schedule overlap validation: reserved slot must not conflict with any existing non-Draft event

**Computed fields (set in `validate()`)**:
- `exam_start_ts = scheduled_start + timedelta(seconds=waiting_room_duration)`
- `exam_end_ts = exam_start_ts + timedelta(minutes=exam_duration)`

**Permissions**: System Manager only (admin DocType)

---

### 2. Memora Live Challenge Question (Child Table)

**istable**: 1
**Module**: Memora Admin

| Field | Fieldtype | Options/Default | Required | Notes |
|-------|-----------|----------------|----------|-------|
| question_text | Small Text | | Yes | The question body |
| option_a | Data | | Yes | Choice A |
| option_b | Data | | Yes | Choice B |
| option_c | Data | | Yes | Choice C |
| option_d | Data | | Yes | Choice D |
| correct_answer | Select | A/B/C/D | Yes | The correct option |
| source_review_item | Link | Memora Review Item | No | Optional source reference |

**Notes**:
- `idx` (Frappe built-in) provides ordering
- When importing from Review Item: map `choice_1..4` to `option_a..d`, map `correct_choice` (1-4) to `correct_answer` (A/B/C/D)
- Changing questions is only allowed in Draft status (enforced by parent's status validation)

---

### 3. Memora Live Challenge Eligible Plan (Child Table)

**istable**: 1
**Module**: Memora Admin

| Field | Fieldtype | Options/Default | Required | Notes |
|-------|-----------|----------------|----------|-------|
| plan | Link | Memora Academic Plan | Yes | Eligible plan |

**Notes**:
- If this child table is empty, all registered students can join (no plan restriction)
- Follows same pattern as `Memora Announcement Target Plan`

---

### 4. Memora Live Challenge Participation

**Naming**: `autoname = "hash"` (Frappe auto-generated hash)
**Module**: Memora Admin

| Field | Fieldtype | Options/Default | Required | Notes |
|-------|-----------|----------------|----------|-------|
| event | Link | Memora Live Challenge Event | Yes | FK to event |
| player | Link | Memora Player Profile | Yes | FK to student |
| score | Float | | No | Score out of 100 (set after submission) |
| rank | Int | | No | Dense rank (set after event ends) |
| joined_at | Datetime | | Yes | When student joined waiting room |
| submitted_at | Datetime | | No | When student submitted answers |
| answers_json | JSON | | No | Detailed answer record |
| xp_awarded | Int | Default: 0 | No | Total XP awarded (participation + rank bonus) |

**Unique constraint**: `(event, player)` — one participation per student per event

**answers_json structure**:
```json
{
  "answers": [
    {"question_idx": 0, "selected": "A", "correct": true},
    {"question_idx": 1, "selected": "C", "correct": false},
    {"question_idx": 2, "selected": null, "correct": false}
  ]
}
```

**Permissions**: System Manager for admin access. Students access their own record via FastAPI endpoint only.

---

## Redis Keys (Hot Data During Event)

| Key | Type | Purpose | TTL |
|-----|------|---------|-----|
| `memora:lc:{event_id}:status` | STRING | Current state: waiting, active, ended | 24h |
| `memora:lc:{event_id}:questions` | STRING | JSON array with correct answers | 24h |
| `memora:lc:{event_id}:count` | STRING | Atomic participant counter (INCR/DECR) | 24h |
| `memora:lc:{event_id}:submitted` | SET | Player IDs who submitted | 24h |
| `memora:lc:{event_id}:meta` | HASH | exam_start_ts, exam_end_ts, capacity, show_correct_answers, show_student_rank, enable_question_timer, question_time_limit, eligible_plans (JSON array of plan IDs, empty array if unrestricted) | 24h |

**Notes**:
- Keys are created when event transitions to Waiting Room
- 24h TTL serves as automatic cleanup safety net
- Questions key contains correct answers — NEVER served to client directly
- `count` key uses INCR for atomic capacity enforcement
- `submitted` set prevents duplicate submissions via SISMEMBER + SADD atomicity
