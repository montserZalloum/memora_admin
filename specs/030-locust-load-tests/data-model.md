# Data Model: Locust Load Test Suite

**Phase 1 Output** | **Date**: 2026-02-27

## Entities

This feature has no database entities. All "models" are Python configuration structures and Locust user classes that exist only at test runtime.

### Configuration Entities

#### TestPlayer
A pre-created player account used by simulated users for authentication.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mobile` | `str` | Yes | Phone number in E.164 format (e.g., `"+201000000001"`) |
| `password` | `str` | Yes | Plain-text password for login |

**Source**: `config.py` → `TEST_PLAYERS` list
**Validation**: At least 3 players required for basic testing; 100-500 recommended for 100k simulation.
**Relationship**: Each Locust virtual user picks a random TestPlayer on startup. Multiple virtual users may share the same TestPlayer (account reuse is expected).

#### TestLesson
A pre-existing lesson used by LessonPlayer profile to simulate session flows.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `lesson_id` | `str` | Yes | Lesson DocType name (e.g., `"LESSON-00001"`) |
| `subject_id` | `str` | Yes | Parent subject DocType name (e.g., `"SUBJ-00001"`) |
| `topic_id` | `str` | Yes | Parent topic DocType name (e.g., `"TOPIC-00001"`) — used for browsing simulation |
| `stages` | `list[dict]` | Yes | Stage result templates for session-end payload |

**Source**: `config.py` → `TEST_LESSONS` list
**Validation**: At least 1 lesson required. Lessons must be accessible to all test players (proper access grants).

#### StageTemplate
Template for generating realistic stage results in session-end requests.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `stage_id` | `str` | Yes | Stage identifier |
| `min_time_ms` | `int` | No | Minimum simulated time spent (default: 3000) |
| `max_time_ms` | `int` | No | Maximum simulated time spent (default: 10000) |
| `max_fail_count` | `int` | No | Maximum random fail count (default: 2) |

**Source**: Nested within `TEST_LESSONS[].stages`

#### TestSubject
A subject used by BrowserUser to drill down the content hierarchy.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `subject_id` | `str` | Yes | Subject DocType name |

**Source**: `config.py` → `TEST_SUBJECTS` list
**Validation**: At least 1 subject required. Subjects must have tracks/units/topics for meaningful browsing.

#### ScalingStage
Reference data for the 5-stage scaling ladder.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `stage` | `int` | Yes | Stage number (1-5) |
| `users` | `int` | Yes | Target user count |
| `spawn_rate` | `int` | Yes | Users spawned per second |
| `duration` | `str` | Yes | Test duration (e.g., `"5m"`) |

**Source**: `config.py` → `SCALING_LADDER` list (documentation/reference only)

---

### Locust User Classes (Runtime Entities)

#### DashboardUser
Simulates a student who primarily checks their dashboard stats.

| Attribute | Value | Description |
|-----------|-------|-------------|
| `weight` | 40 | 40% of spawned users |
| `wait_time` | `between(3, 8)` | 3-8 seconds between tasks |
| `token` | `str \| None` | JWT access token from login |
| `device_id` | `str` | Unique device ID per virtual user |

**Tasks**: `check_profile` (weight 3), `check_stats` (weight 2), `check_activity` (weight 2), `check_mastery` (weight 1), `check_progress` (weight 1), `check_wallet` (weight 1)

#### LessonPlayer
Simulates a student completing lessons.

| Attribute | Value | Description |
|-----------|-------|-------------|
| `weight` | 35 | 35% of spawned users |
| `wait_time` | `between(5, 15)` | 5-15 seconds between lesson attempts |
| `token` | `str \| None` | JWT access token |
| `device_id` | `str` | Unique device ID |

**Tasks**: `play_lesson` (weight 1) — full session lifecycle with in-task think time

#### BrowserUser
Simulates a student browsing content hierarchy.

| Attribute | Value | Description |
|-----------|-------|-------------|
| `weight` | 15 | 15% of spawned users |
| `wait_time` | `between(2, 6)` | 2-6 seconds between drill-downs |
| `token` | `str \| None` | JWT access token |
| `device_id` | `str` | Unique device ID |

**Tasks**: `browse_hierarchy` (weight 1) — drills down Subject → Tracks → Units

#### LeaderboardChecker
Simulates a student checking leaderboard rankings.

| Attribute | Value | Description |
|-----------|-------|-------------|
| `weight` | 10 | 10% of spawned users |
| `wait_time` | `between(5, 10)` | 5-10 seconds between checks |
| `token` | `str \| None` | JWT access token |
| `device_id` | `str` | Unique device ID |

**Tasks**: `check_daily` (weight 2), `check_weekly` (weight 1), `check_my_rank` (weight 2)

---

### State Transitions

No state machines — all Locust user classes are stateless between task iterations (except the stored `token` from login). The `LessonPlayer` has a within-task flow (start → think → end) but no cross-task state.

### Relationships

```
config.TEST_PLAYERS ──1:N──► Virtual Users (any profile type)
config.TEST_LESSONS ──1:N──► LessonPlayer.play_lesson()
config.TEST_SUBJECTS ──1:N──► BrowserUser.browse_hierarchy()
```
