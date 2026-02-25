# Data Model: Plan-Scoped Leaderboard

**Date**: 2026-02-24 | **Branch**: `026-plan-leaderboard`

## Entities

### 1. Plan-Scoped Leaderboard ZSET (Redis)

**Description**: A Redis sorted set containing player XP rankings scoped to a specific academic plan, time period, and optionally a subject.

**Key Patterns**:

| Variant | Key | TTL |
|---------|-----|-----|
| Daily (plan-wide) | `memora:lb:daily:{YYYY-MM-DD}:plan:{plan_id}` | 48h |
| Daily (subject) | `memora:lb:daily:{YYYY-MM-DD}:plan:{plan_id}:subject:{subject_id}` | 48h |
| Weekly (plan-wide) | `memora:lb:weekly:{YYYY-MM-DD}:plan:{plan_id}` | 8d |
| Weekly (subject) | `memora:lb:weekly:{YYYY-MM-DD}:plan:{plan_id}:subject:{subject_id}` | 8d |

**Members**: `player_id` (string, e.g., `PLAYER-00100`)
**Scores**: XP earned in the period (integer via ZINCRBY accumulation)

**Producers**: `LeaderboardService.update_leaderboards()`
**Consumers**: `LeaderboardService.get_top()`, `LeaderboardService.get_my_rank()`

---

### 2. Global Leaderboard ZSET (Redis) — Write-Only

**Description**: Existing global leaderboard ZSETs. Kept for future use but no longer read by any endpoint.

**Key Patterns** (unchanged):

| Variant | Key | TTL |
|---------|-----|-----|
| All-time | `memora:lb:alltime` | None |
| All-time (subject) | `memora:lb:alltime:subject:{subject_id}` | None |
| Daily | `memora:lb:daily:{YYYY-MM-DD}` | 30d |
| Daily (subject) | `memora:lb:daily:{YYYY-MM-DD}:subject:{subject_id}` | 30d |
| Weekly | `memora:lb:weekly:{YYYY-MM-DD}` | 90d |
| Weekly (subject) | `memora:lb:weekly:{YYYY-MM-DD}:subject:{subject_id}` | 90d |

---

### 3. LeaderboardEntry (Pydantic Response Model)

| Field | Type | Description |
|-------|------|-------------|
| `rank` | int | Dense rank within plan (1-indexed) |
| `player_id` | str | Player identifier |
| `display_name` | str | From ProfileService cache |
| `xp` | int | XP earned in the period |
| `avatar` | str \| None | Avatar URL from ProfileService cache |
| `is_me` | bool | True if this entry is the requesting player |

---

### 4. LeaderboardResponse (Pydantic Response Model)

| Field | Type | Description |
|-------|------|-------------|
| `leaderboard_type` | `"daily" \| "weekly"` | Selected time period |
| `subject_id` | str \| None | Subject filter if applied |
| `entries` | list[LeaderboardEntry] | Top 20 (max) entries |
| `total_players` | int | Total ranked players in plan for this period |

---

### 5. MyRankResponse (Pydantic Response Model)

| Field | Type | Description |
|-------|------|-------------|
| `rank` | int \| None | Player's dense rank (None if unranked) |
| `xp` | int | Player's XP in the period |
| `xp_to_next` | int \| None | XP gap to next higher rank (None if #1 or unranked) |
| `neighbors` | list[LeaderboardEntry] | ±2 neighbors around player |
| `total_players` | int | Total ranked players in plan for this period |

---

## Existing Entities (Unchanged)

### Player Profile (MariaDB — Memora Player Profile)

| Field | Type | Relevance |
|-------|------|-----------|
| `name` | str (PK) | Player ID (`PLAYER-#####`) |
| `plan` | Link | Reference to Memora Academic Plan — **source of truth for plan membership** |
| `display_name` | str | Shown on leaderboard entries |
| `avatar` | str | Shown on leaderboard entries |

### Academic Plan (MariaDB — Memora Academic Plan)

| Field | Type | Relevance |
|-------|------|-----------|
| `name` | str (PK) | Plan ID (`PLAN-#####`) |
| `plan_name` | str | Human-readable name |
| `grade` | Link | Grade reference |
| `major` | Link | Major reference |
| `season` | Link | Season reference |

### Plan Subject (MariaDB — Memora Plan Subject, child of Academic Plan)

| Field | Type | Relevance |
|-------|------|-----------|
| `subject` | Link | Subject reference — used for subject filter dropdown |
| `alias_title` | str | Display name override for subject in plan |
| `is_premium` | bool | Whether subject requires paid subscription |

---

## State Transitions

None — leaderboard ZSETs are stateless accumulators. XP is added via ZINCRBY and expires via TTL. No lifecycle states.

## Relationships

```
Player Profile (1) --belongs to--> (1) Academic Plan
Academic Plan (1) --has many--> (N) Plan Subjects
Plan Subject (1) --references--> (1) Subject

Player (1) --has score in--> (N) Plan-Scoped Leaderboard ZSETs
Plan-Scoped Leaderboard ZSET --scoped by--> (1) Academic Plan
Plan-Scoped Leaderboard ZSET --optionally filtered by--> (0..1) Subject
```
