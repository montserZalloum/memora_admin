# Data Model: Challenge Hub (038)

**Date**: 2026-03-08
**Status**: Complete

## Entities

### 1. Memora Challenge Progress (Frappe DocType)

One record per student per topic. Tracks the current state that drives unlock logic and XP.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | Auto | - | - | Primary key (autoname) |
| `player` | Link → Memora Player Profile | Yes | - | Student reference |
| `topic` | Link → Memora Topic | Yes | - | Topic reference |
| `subject` | Link → Memora Subject | Yes | - | Denormalized for fast queries |
| `season` | Link → Memora Season | Yes | - | Season scope (for reset) |
| `stamped` | Check | Yes | 0 | Whether topic is stamped (passed ≥ threshold) |
| `best_correct` | Int | Yes | 0 | Best correct count across all attempts (for XP delta) |
| `best_score_pct` | Percent | Yes | 0 | Best score % across all attempts (for XP calc) |
| `best_passing_pct` | Percent | Yes | 0 | Best passing score % (≥ threshold, for display) |
| `total_xp_earned` | Int | Yes | 0 | Cumulative Challenge XP for this topic |
| `attempt_count` | Int | Yes | 0 | Total completed attempts |

**Unique constraint**: `(player, topic, season)`
**Autoname**: `hash` (deterministic from player + topic + season)

### 2. Memora Challenge Attempt (Frappe DocType)

One record per completed attempt. Analytics and audit trail.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | Auto | - | - | Primary key |
| `player` | Link → Memora Player Profile | Yes | - | Student reference |
| `topic` | Link → Memora Topic | Yes | - | Topic reference |
| `subject` | Link → Memora Subject | Yes | - | Denormalized |
| `season` | Link → Memora Season | Yes | - | Season scope |
| `attempt_number` | Int | Yes | - | Sequential per player per topic (1, 2, 3...) |
| `total_questions` | Int | Yes | - | Total MCQ questions in this attempt |
| `correct_count` | Int | Yes | - | Number of correct answers |
| `score_pct` | Percent | Yes | - | `correct_count / total_questions * 100` |
| `passed` | Check | Yes | 0 | `score_pct >= pass_threshold` |
| `time_spent` | Int | Yes | 0 | Total seconds for the attempt |
| `xp_earned` | Int | Yes | 0 | Challenge XP delta earned this attempt |
| `submitted_at` | Datetime | Yes | - | Submission timestamp |
| `details` | Table → Memora Challenge Attempt Detail | No | - | Per-question results |

**Autoname**: `naming_series` (`CHA-.#####`)

### 3. Memora Challenge Attempt Detail (Child Table)

Per-question results within an attempt.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `item_id` | Data | Yes | Review Item UUID |
| `correct` | Check | Yes | Answered correctly |
| `time_spent` | Int | Yes | Seconds on this question |
| `chosen_answer` | Int | Yes | 1-based index of chosen option |

**Parent**: `Memora Challenge Attempt` (via `details` field)

### 4. Memora Settings Extensions

New fields added to existing `Memora Settings` singleton:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `challenge_section` | Section Break | - | "Challenge Hub" section |
| `challenge_xp_per_question` | Int | 5 | XP per correct answer |
| `challenge_pass_threshold` | Int | 50 | Min score % to stamp |
| `column_break_challenge` | Column Break | - | - |
| `challenge_lb_top_count` | Int | 20 | Leaderboard top N |
| `challenge_lb_refresh_interval` | Int | 300 | LB refresh seconds |

## Redis Data Structures

### Challenge Progress Cache

**Key**: `memora:ch:progress:{player_id}:{subject_id}`
**Type**: HASH
**TTL**: 48h (same as main progress)

```
field: {topic_id}
value: JSON string
{
  "stamped": true,
  "best_correct": 14,
  "best_score_pct": 70.0,
  "best_passing_pct": 70.0,
  "total_xp": 70,
  "attempt_count": 3
}
```

**Producers**: Challenge attempt submission endpoint
**Consumers**: Challenge hierarchy endpoint (unlock logic)
**Self-heals**: Yes — on cache miss, hydrate from `Memora Challenge Progress` records for that player + subject

### Challenge Leaderboard

**Key**: `memora:lb:ch:{season_id}:plan:{plan_id}`
**Type**: ZSET (member = player_id, score = total Challenge XP)
**TTL**: None (protected within season, cleaned on season reset)

**Key**: `memora:lb:ch:{season_id}:plan:{plan_id}:subject:{subject_id}`
**Type**: ZSET (member = player_id, score = subject-specific Challenge XP)
**TTL**: None (same lifecycle)

**Tier metadata** (same pattern as main leaderboard):
- `memora:lbmeta:ch:{season_id}:plan:{plan_id}:tieridx` (ZSET)
- `memora:lbmeta:ch:{season_id}:plan:{plan_id}:tiercnt` (HASH)
- Subject-scoped variants follow same pattern

### Attempt Idempotency

**Key**: `memora:ch:idem:{player_id}:{attempt_key}`
**Type**: STRING (JSON response body)
**TTL**: 300s (5 minutes)

**Producers**: Attempt submission endpoint (SET NX EX)
**Consumers**: Attempt submission endpoint (GET before processing)

### Attempt Buffer

**Key**: `memora:ch:attempt_buffer`
**Type**: LIST (JSON-serialized attempt payloads)
**TTL**: None (protected — data loss = permanent attempt loss)

Each entry:
```json
{
  "player": "PLAYER-00001",
  "topic": "TOPIC-00001",
  "subject": "SUBJ-00001",
  "season": "SEAS-00027",
  "attempt_number": 3,
  "total_questions": 25,
  "correct_count": 18,
  "score_pct": 72.0,
  "passed": true,
  "time_spent": 420,
  "xp_earned": 15,
  "submitted_at": "2026-03-08 14:30:00",
  "details": [
    {"item_id": "...", "correct": true, "time_spent": 12, "chosen_answer": 2}
  ]
}
```

**Producers**: Challenge attempt submission endpoint (RPUSH after Redis progress update)
**Consumers**: `sync_dirty_challenge_progress()` background task (LPOP batch, creates MariaDB records)

### Challenge Settings Cache

**Key**: `memora:settings:challenge`
**Type**: STRING (JSON)
**TTL**: 300s (5 minutes)

```json
{
  "xp_per_question": 5,
  "pass_threshold": 50,
  "lb_top_count": 20,
  "lb_refresh_interval": 300
}
```

**Note**: Extends existing `SettingsService` cache pattern — may reuse `memora:settings` key with additional fields.

### Dirty Set for Progress Sync

**Key**: `memora:dirty:ch_progress`
**Type**: SET (members = `{player_id}:{subject_id}`)
**TTL**: None (protected)

**Producers**: Attempt submission endpoint (SADD after Redis update)
**Consumers**: Background sync task (flush to MariaDB)

## State Transitions

### Topic State (Challenge Hub)

```
 ┌──────────┐
 │  locked  │ ← Missing access OR normal path incomplete OR predecessor not stamped
 └────┬─────┘
      │ All 3 conditions met
      ▼
 ┌──────────┐
 │   open   │ ← Can start challenge. Fail keeps it open.
 └────┬─────┘
      │ Score ≥ pass_threshold
      ▼
 ┌──────────┐
 │ stamped  │ ← Can still replay (unlimited retries)
 └──────────┘
```

**Note**: `stamped` is permanent within a season. Once stamped, a topic cannot become unstamped (except season reset).

### Empty Topic (Auto-Stamp)

```
 ┌──────────────┐
 │ hidden/empty  │ ← 0 MCQ questions
 └──────┬───────┘
        │ Predecessor stamped (computed at read time)
        ▼
 ┌──────────────┐
 │ auto-stamped  │ ← Inherits predecessor state, not stored
 └──────────────┘
```

## Relationships

```
Memora Player Profile
  │
  ├── 1:N → Memora Challenge Progress (per topic per season)
  │           │
  │           └── links to → Memora Topic, Memora Subject, Memora Season
  │
  └── 1:N → Memora Challenge Attempt (per attempt)
              │
              ├── links to → Memora Topic, Memora Subject, Memora Season
              │
              └── 1:N → Memora Challenge Attempt Detail (per question)
                          │
                          └── links to → Memora Review Item (via item_id)

Memora Season
  │
  ├── scopes → Challenge Progress records
  ├── scopes → Challenge Attempt records
  └── scopes → Challenge Leaderboard Redis keys
```

## Validation Rules

### Challenge Progress
- `best_score_pct` is always `≥ best_passing_pct` (best overall ≥ best passing)
- `total_xp_earned` is monotonically non-decreasing (XP delta is always ≥ 0)
- `stamped` transitions from 0 → 1 only (never reverted within season)
- `attempt_count` is monotonically increasing

### Challenge Attempt
- `correct_count ≤ total_questions`
- `score_pct = round(correct_count / total_questions * 100, 2)`
- `passed = score_pct >= pass_threshold`
- `xp_earned ≥ 0` (delta, never negative)
- `attempt_number = previous max + 1` for same player + topic
- `len(details) == total_questions` (every question must have a result)

### Challenge Attempt Detail
- `chosen_answer` in range 1-4
- `time_spent ≥ 0`
