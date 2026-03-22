# Data Model: Live Challenge Mode — Last Stand

**Feature Branch**: `053-last-stand-mode`
**Date**: 2026-03-22

## Entity Changes

### 1. Memora Live Challenge Event (Extended)

**New fields** added to existing DocType:

| Field | Type | Default | Constraints | Notes |
|-------|------|---------|-------------|-------|
| `mode` | Select | `exam` | Options: `exam`, `last_stand`. Required. | Immutable after creation (enforced in `validate()`). Placed in new "Mode" section before Schedule. |
| `starting_hearts` | Int | `3` | Range 1-10. Mandatory when `mode=last_stand`. | `depends_on: eval:doc.mode=='last_stand'` + `mandatory_depends_on` |
| `result_window_duration` | Int | `3` | Range 1-10 (seconds). Mandatory when `mode=last_stand`. | Duration of result display phase between rounds. |

**Modified field behavior**:

| Field | Exam Mode | Last Stand Mode |
|-------|-----------|-----------------|
| `enable_question_timer` | Optional | **Must be enabled** (validated in `validate()`) |
| `question_time_limit` | Per-question hint (client-side) | **Server-enforced** answer window duration per round |
| `exam_duration` | Auto-calculated from timer × questions | Auto-calculated: `ceil(question_count * (question_time_limit + result_window_duration) / 60)` |
| `exam_end_ts` | Hard deadline for submissions | **Safety ceiling** — round engine may end event earlier (all eliminated / all questions done) |
| `capacity` | Optional (0 = unlimited) | Optional (0 = unlimited), but recommended for Last Stand |

**Validation rules** (in `memora_live_challenge_event.py`):
```python
# In validate():
if self.mode == "last_stand":
    if not self.enable_question_timer:
        frappe.throw("Question timer must be enabled for Last Stand mode")
    if not self.starting_hearts or not (1 <= self.starting_hearts <= 10):
        frappe.throw("Starting hearts must be between 1 and 10")
    if not self.result_window_duration or not (1 <= self.result_window_duration <= 10):
        frappe.throw("Result window duration must be between 1 and 10 seconds")

# In before_save():
if not self.is_new() and self.has_value_changed("mode"):
    frappe.throw("Mode cannot be changed after creation")
```

**exam_duration auto-calculation for Last Stand**:
```python
if self.mode == "last_stand" and self.enable_question_timer:
    total_seconds = len(self.questions) * (self.question_time_limit + self.result_window_duration)
    # Add 30s buffer for transitions and final reconciliation
    self.exam_duration = math.ceil(total_seconds / 60) + 1
```

---

### 2. Memora Live Challenge Participation (Extended)

**New fields** added to existing DocType:

| Field | Type | Default | Constraints | Notes |
|-------|------|---------|-------------|-------|
| `final_hearts` | Int | `0` | >= 0 | Hearts remaining when event ended. 0 if eliminated. |
| `is_eliminated` | Check | `0` | | 1 if player was eliminated (hearts reached 0). |
| `eliminated_at_question` | Int | `0` | >= 0 | Question index (0-based) where elimination occurred. 0 if not eliminated. |
| `avg_response_time_ms` | Int | `0` | >= 0 | Average response time in milliseconds across answered questions. |

**Note**: These fields are nullable/defaulted so existing exam Participation records remain valid. Exam mode ignores them (always 0).

**Modified ranking for Last Stand** (in `compute_ranking`):
```python
if mode == "last_stand":
    # Sort by: score DESC, final_hearts DESC, avg_response_time_ms ASC
    participants.sort(key=lambda p: (-p.score, -p.final_hearts, p.avg_response_time_ms))
else:
    # Existing exam ranking: score DESC only
    participants.sort(key=lambda p: -p.score)
```

---

### 3. Redis State — Runtime (Active Gameplay Only)

All keys use TTL = `LC_KEY_TTL` (86400s). Keys are created when Waiting → Active and cleaned after reconciliation.

#### Existing Keys (unchanged, used by both modes)

| Key | Type | Purpose |
|-----|------|---------|
| `memora:lc:{event_id}:status` | STRING | "waiting" / "active" / "ended" |
| `memora:lc:{event_id}:questions` | STRING (JSON) | Questions array WITH correct answers (server-only) |
| `memora:lc:{event_id}:meta` | HASH | Event config: timestamps, capacity, XP, timer settings |
| `memora:lc:{event_id}:count` | STRING (INCR) | Participant counter |
| `memora:lc:{event_id}:joined` | SET | Player IDs who joined |
| `memora:lc:{event_id}:join_times` | HASH | player_id → ISO datetime |
| `memora:lc:{event_id}:reconcile_lock` | STRING | Distributed lock for reconciliation |
| `memora:lc:{event_id}:reconciled` | STRING | "1" after successful reconciliation |

#### New Keys (Last Stand only)

| Key | Type | Purpose |
|-----|------|---------|
| `memora:lc:{event_id}:mode` | STRING | "exam" or "last_stand" — set on hydration, used for mode branching |
| `memora:lc:{event_id}:round` | HASH | Current round state (see schema below) |
| `memora:lc:{event_id}:hearts` | HASH | player_id → remaining hearts (integer string) |
| `memora:lc:{event_id}:alive` | SET | Player IDs still alive |
| `memora:lc:{event_id}:eliminated` | SET | Player IDs eliminated |
| `memora:lc:{event_id}:eliminated_at` | HASH | player_id → question_idx (0-based) where eliminated |
| `memora:lc:{event_id}:round_answers:{round_id}` | HASH | player_id → JSON `{selected, ts}` |
| `memora:lc:{event_id}:response_times` | HASH | player_id → JSON array of response times in ms `[120, 340, ...]` |
| `memora:lc:{event_id}:correct_counts` | HASH | player_id → correct answer count (integer string) |
| `memora:lc:{event_id}:answered_counts` | HASH | player_id → total answered count (excludes timeouts) |

#### Round State HASH Schema

`memora:lc:{event_id}:round` fields:

| Field | Type | Description |
|-------|------|-------------|
| `round_id` | STRING | `"{event_id}-R{question_idx}"` — unique per round |
| `question_idx` | STRING (int) | 0-based index into questions array |
| `phase` | STRING | `"answer"` or `"result"` |
| `phase_end_ts` | STRING (float) | Unix timestamp when current phase ends |
| `alive_count` | STRING (int) | Alive players at round start (cached for early-close check) |

#### Meta HASH Extensions

New fields added to `memora:lc:{event_id}:meta`:

| Field | Type | Description |
|-------|------|-------------|
| `mode` | STRING | "exam" or "last_stand" |
| `starting_hearts` | STRING (int) | Hearts per player at start |
| `result_window_duration` | STRING (int) | Seconds for result display phase |

---

## State Transitions

### Event Lifecycle (unchanged)

```
Draft ──→ Waiting ──→ Active ──→ Ended
         (time)      (time)     (round engine OR time ceiling)
```

For Last Stand, the Active → Ended transition is triggered by:
1. Round engine: all questions answered OR all players eliminated
2. Safety ceiling: `exam_end_ts` reached (cron fallback)

### Round Lifecycle (new — Last Stand only)

```
                    ┌──────────────────────────────────────────┐
                    │                                          │
                    ▼                                          │
              ┌──────────┐    timeout OR     ┌──────────┐     │
  (start) ──→ │  Answer   │ ──all answered──→ │  Result  │ ────┘
              │  Window   │                  │  Window   │  (next round if
              └──────────┘                  └──────────┘   questions remain
                                                 │          AND alive > 0)
                                                 │
                                                 ▼
                                          ┌──────────┐
                                          │  Event    │  (all questions done
                                          │  Ended    │   OR alive == 0)
                                          └──────────┘
```

**Answer Window**:
- Duration: `question_time_limit` seconds (from event config)
- Players submit answers via `POST /answer`
- Ends early if all alive players have answered (FR-010)
- Unanswered players lose a heart (FR-005)

**Result Window**:
- Duration: `result_window_duration` seconds (from event config)
- Server evaluates answers, updates hearts, detects eliminations
- Broadcasts `round_result` with personalized per-player state
- No player actions accepted during this phase

### Player State Machine (new — Last Stand only)

```
              ┌─────────┐
  (join) ──→  │  Alive   │ ──hearts reach 0──→ ┌────────────┐
              │          │                      │ Eliminated  │
              │ (answers │                      │ (spectator) │
              │  rounds) │                      └────────────┘
              └─────────┘                             │
                   │                                  │
                   ▼                                  ▼
              Event Ends                        Event Ends
              (with hearts)                     (hearts = 0)
```

---

## Entity Relationship Diagram

```
┌─────────────────────────────────┐
│   Memora Live Challenge Event   │
│─────────────────────────────────│
│ + mode: exam | last_stand       │  NEW
│ + starting_hearts: 1-10         │  NEW
│ + result_window_duration: 1-10  │  NEW
│ + status: Draft/Waiting/...     │
│ + questions: [Question]         │
│ + eligible_plans: [Plan]        │
│ + scheduled_start, exam_start_ts│
│ + exam_end_ts, capacity         │
│ + XP config fields              │
├─────────────────────────────────┤
│ 1                               │
│ │                               │
│ ├──< Memora LC Question (child) │
│ │                               │
│ ├──< Memora LC Eligible Plan    │
│ │                               │
│ └──< Memora LC Participation    │
│                                 │
└─────────────────────────────────┘

┌──────────────────────────────────┐
│ Memora LC Participation          │
│──────────────────────────────────│
│ + event (FK → Event)             │
│ + player (FK → Player Profile)   │
│ + joined_at, submitted_at        │
│ + score, rank, xp_awarded        │
│ + answers_json                   │
│ + final_hearts: int              │  NEW
│ + is_eliminated: bool            │  NEW
│ + eliminated_at_question: int    │  NEW
│ + avg_response_time_ms: int      │  NEW
├──────────────────────────────────┤
│ UNIQUE(event, player)            │
└──────────────────────────────────┘
```

---

## Data Flow: Last Stand Active Gameplay

```
Mobile Client                    FastAPI (Redis-only)              Redis
     │                                │                              │
     │──POST /join─────────────────→│──Lua: atomic_join───────────→│
     │←─{position, hearts}──────────│←─{SADD joined, HSET hearts}─│
     │                                │                              │
     │──WS connect─────────────────→│──register_connection────────→│
     │←─player_state {hearts, ...}──│                              │
     │                                │                              │
     │  ┌─── Round Engine Loop ──────────────────────────────────────┐
     │  │                                                            │
     │←─│─round_start {question}────│──HSET round state────────────→│
     │  │                           │                                │
     │──│─POST /answer──────────────│──Lua: atomic_answer──────────→│
     │←─│─{accepted: true}──────────│←─{HSET round_answers}────────│
     │  │                           │                                │
     │  │  (time limit OR all answered)                              │
     │  │                           │──evaluate: HGET answers───────→│
     │  │                           │──HINCRBY hearts -1────────────→│
     │  │                           │──SMOVE alive→eliminated───────→│
     │  │                           │                                │
     │←─│─round_result {personal}───│                                │
     │  │                           │                                │
     │  │  (result_window_duration seconds)                          │
     │  │                           │                                │
     │  └─── Next Round OR End ──────────────────────────────────────┘
     │                                │                              │
     │  (Event ends)                  │                              │
     │←─event_ended────────────────│──SET status "ended"───────────→│
     │                                │                              │
     │                                │──reconcile: Redis → DB───────│
     │──GET /result────────────────→│──query MariaDB─────────────────│
     │←─{score, rank, hearts, ...}──│                                │
```

---

## Migration Notes

- **No data migration required**: `mode` defaults to `exam` for all existing events.
- New Participation fields default to 0, so existing records are unaffected.
- Schema changes are additive — no columns removed or renamed.
- Frappe handles schema migration automatically via DocType JSON sync (`bench migrate`).
