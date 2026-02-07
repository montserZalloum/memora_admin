# Phase 20: Lesson Complete Pipeline Overhaul - Research

**Researched:** 2026-02-07
**Domain:** Redis performance optimization, FSRS spaced repetition, XP calculation, FastAPI endpoint lifecycle
**Confidence:** HIGH

## Summary

This phase overhuals the lesson completion pipeline (the `POST /sessions/end` hot path) for 100K concurrent users. It involves six interlocking workstreams: (1) enriching the hierarchy API with `base_xp`/`max_hearts` per lesson with fallback to Memora Settings defaults, (2) implementing hearts bonus XP calculation, (3) optimizing Redis operations from the current ~12+ round-trips down to ~4 via Lua scripts and pipelining, (4) integrating FSRS spaced repetition for non-skippable stages as a background task, (5) changing `StageResult.time_spent` from seconds to milliseconds, and (6) removing the legacy `POST /progress/complete` endpoint.

The codebase already has strong patterns for Lua scripts (game session start), Redis pipelining (stats service, progress service), and scheduled tasks (sync.py pattern). The `fsrs` package v6.3.0 is already installed. The Memora Settings DocType already has `default_max_hearts`, `xp_per_heart`, `fsrs_weights`, and `fsrs_section` fields. The Memora Memory State DocType already has the exact schema needed (season, subject, player, stage_id, stability, difficulty, next_review, lesson). The infrastructure is largely in place -- this phase is about wiring it together and optimizing the hot path.

**Primary recommendation:** Restructure the `end_session` endpoint into a Lua-script-powered pipeline that performs session deletion, progress SETBIT, dirty-set SADD, and interaction RPUSH in 1-2 Lua scripts, then uses a pipeline for wallet/leaderboard/stats updates, achieving ~4 total Redis round-trips. FSRS processing runs as a background scheduled task (every minute), not in the hot path.

## Standard Stack

### Core (Already Installed)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fsrs | 6.3.0 | FSRS spaced repetition scheduler | Official py-fsrs package, MIT license, 21-parameter model |
| redis | 7.1.0 | Async Redis client with Lua script support | Already in use, supports `register_script()` and `pipeline()` |
| pydantic | 2.x | Request/response models | Already in use throughout FastAPI app |

### Supporting (Already Available)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | 24.x | Structured logging | All service/endpoint logging |
| prometheus_client | 0.20.x | Task metrics | FSRS background task monitoring |

### No New Dependencies
All required libraries are already installed. The `fsrs` package is already at v6.3.0 (latest). Add `fsrs>=6.0.0` to `requirements.txt` to formalize the dependency.

**Installation:**
```bash
# Already installed, just add to requirements.txt:
echo "fsrs>=6.0.0" >> requirements.txt
```

## Architecture Patterns

### Current End-Session Redis Round-Trip Analysis

The current `POST /sessions/end` handler in `sessions.py` makes the following Redis calls (counted from code review):

| # | Operation | Service | Redis Command | Round-trips |
|---|-----------|---------|---------------|-------------|
| 1 | Get active session | GameSessionService | HGETALL | 1 |
| 2 | Get hierarchy (cached) | HierarchyService | GET | 1 |
| 3 | Push stage analytics | Direct redis | RPUSH x N stages | N (currently N separate calls!) |
| 4 | End session | GameSessionService | HGETALL + DEL | 2 |
| 5 | Complete lesson | ProgressService | SETBIT + SADD | 2 |
| 6 | Find lesson path | In-memory | N/A | 0 |
| 7 | Increment stats | StatsService | Pipeline (5 ops) | 1 |
| 8 | Get settings (cached) | SettingsService | GET | 1 |
| 9 | Update streak | WalletService | Lua script | 1 |
| 10 | Award XP | WalletService | HINCRBY + SADD | 2 |
| 11 | Update leaderboards | LeaderboardService | 6x ZADD/ZINCRBY | 6 |
| **Total** | | | | **17 + N** |

For a lesson with 5 stages: **22 round-trips**. This is the baseline to optimize.

### Target Architecture: ~4 Redis Round-Trips

```
Round-trip 1: Lua Script "session_complete"
  - HGETALL session (read)
  - DEL session
  - SETBIT progress
  - SADD dirty:progress
  - RPUSH interactions (all stages in one call)
  Returns: session data, is_replay (previous bit value)

Round-trip 2: GET hierarchy (cache hit, already loaded by endpoint)
  - Already fetched before end_session logic

Round-trip 3: GET settings (cache hit)
  - Already cached with 5min TTL

Round-trip 4: Pipeline for wallet/stats/leaderboard
  - Lua: streak update (within pipeline)
  - HINCRBY xp
  - SADD dirty:wallets
  - HINCRBY stats x 4 + EXPIRE
  - ZADD alltime + ZINCRBY daily + ZINCRBY weekly (x2 if subject)
```

### Recommended Project Structure Changes

```
fastapi_app/
├── services/
│   ├── game_session.py        # MODIFY: Add completion Lua script
│   ├── progress.py            # MINOR: complete_lesson stays but used in Lua
│   ├── wallet.py              # MINOR: pipeline-friendly methods
│   ├── leaderboard.py         # MINOR: pipeline-friendly methods
│   ├── settings.py            # MODIFY: Add hearts/FSRS settings
│   └── fsrs_service.py        # NEW: FSRS processing service
├── models/
│   ├── game_session.py        # MODIFY: time_spent ms, hearts fields
│   ├── settings.py            # MODIFY: Add hearts/FSRS fields
│   └── progress.py            # MODIFY: Add base_xp, max_hearts to LessonInfo
├── api/v1/endpoints/
│   ├── sessions.py            # MODIFY: Overhaul end_session
│   └── progress.py            # MODIFY: Remove POST /complete
└── lua/                       # NEW: Lua script files (optional, can stay inline)

memora_admin/
├── tasks/
│   └── fsrs_processor.py      # NEW: FSRS background task
├── memora_admin/api/
│   ├── hierarchy.py           # MODIFY: Return base_xp, max_hearts per lesson
│   └── settings.py            # MODIFY: Return hearts/FSRS settings
└── hooks.py                   # MODIFY: Add FSRS task schedule
```

### Pattern 1: Lua Script for Atomic Session Completion

**What:** Single Lua script that performs session read + delete + progress SETBIT + dirty SADD + interaction RPUSH atomically
**When to use:** The session/end hot path
**Example:**
```lua
-- KEYS[1] = memora:gamesession:{user_id}
-- KEYS[2] = memora:progress:{user_id}:{subject_id}:v{version}
-- KEYS[3] = memora:dirty:progress
-- KEYS[4] = memora:buffer:interactions
-- ARGV[1] = bit_index
-- ARGV[2] = dirty_member (user_id:subject_id:v{version})
-- ARGV[3..N] = JSON interaction strings

-- Read session
local session = redis.call('HGETALL', KEYS[1])
if #session == 0 then
    return {0}  -- No active session
end

-- Delete session
redis.call('DEL', KEYS[1])

-- Set progress bit, get previous value (0=first, 1=replay)
local prev = redis.call('SETBIT', KEYS[2], ARGV[1], 1)

-- Mark dirty
redis.call('SADD', KEYS[3], ARGV[2])

-- Batch RPUSH all interactions
if #ARGV > 2 then
    local interactions = {}
    for i = 3, #ARGV do
        interactions[#interactions + 1] = ARGV[i]
    end
    redis.call('RPUSH', KEYS[4], unpack(interactions))
end

-- Return: {1, is_replay, session_id, lesson_id, subject_id, device_id, started_at}
-- session is flat array: [field1, value1, field2, value2, ...]
return {1, prev, session}
```

### Pattern 2: Pipeline for Post-Completion Updates

**What:** After Lua script completes, a single pipeline handles all remaining updates
**When to use:** After the atomic completion Lua script returns
**Example:**
```python
# Source: Existing pattern in StatsService.increment_completion_stats
pipe = redis.pipeline()

# Streak update via Lua (can embed in pipeline per redis-py docs)
# Actually: streak Lua must run separately since it returns data we need
streak_script = await self._get_streak_script()
streak, was_updated = await streak_script(keys=[wallet_key], args=[today, yesterday, is_replay_int])

# Now pipeline the rest
pipe = redis.pipeline()
pipe.hincrby(wallet_key, "xp", xp_awarded)
pipe.sadd(DIRTY_WALLETS_KEY, player_id)
# Stats updates
pipe.hincrby(stats_key, "completed", 1)
pipe.hincrby(stats_key, f"{track_id}:completed", 1)
pipe.hincrby(stats_key, f"{unit_id}:completed", 1)
pipe.hincrby(stats_key, f"{topic_id}:completed", 1)
pipe.expire(stats_key, 3600)
# Leaderboard updates
pipe.zadd(alltime_key, {player_id: composite_score})
pipe.zincrby(daily_key, xp_awarded, player_id)
pipe.zincrby(weekly_key, xp_awarded, player_id)
if subject_id:
    pipe.zadd(alltime_subject_key, {player_id: composite_score})
    pipe.zincrby(daily_subject_key, xp_awarded, player_id)
    pipe.zincrby(weekly_subject_key, xp_awarded, player_id)
await pipe.execute()
```

### Pattern 3: FSRS Background Processing

**What:** Scheduled Frappe task that processes stage results from interaction buffer and updates FSRS memory state
**When to use:** Every minute, processes stages where `is_skippable=False`
**Example:**
```python
from fsrs import Scheduler, Card, Rating

# Initialize scheduler with weights from Memora Settings
weights_str = settings.fsrs_weights  # "0.212, 1.2931, ..."
weights = tuple(float(w.strip()) for w in weights_str.split(","))
scheduler = Scheduler(parameters=weights)

# For each non-skippable stage result:
# 1. Load or create Card from Redis/Memory State
card_json = r.get(f"memora:fsrs:{player}:{stage_id}")
if card_json:
    card = Card.from_json(card_json)
else:
    card = Card()

# 2. Determine rating from performance (fail_count based)
rating = Rating.Good if fail_count == 0 else Rating.Again

# 3. Review card
card, review_log = scheduler.review_card(card, rating)

# 4. Persist to Redis (hot) + mark dirty for MariaDB sync
r.set(f"memora:fsrs:{player}:{stage_id}", card.to_json())
# 5. Update Memora Memory State DocType (cold)
```

### Pattern 4: Hearts Bonus XP Calculation

**What:** Hearts bonus added before streak multiplier
**When to use:** In the XP calculation flow
**Formula:** `remaining_hearts = max_hearts - fail_count_total; hearts_xp = remaining_hearts * xp_per_heart; total = (base_xp + hearts_xp) * streak_multiplier`
**Example:**
```python
def _calculate_xp_award(
    base_xp: int,
    lesson_xp: int,
    current_streak: int,
    max_multiplier_percent: int,
    is_replay: bool,
    replay_xp: int,
    remaining_hearts: int,  # NEW
    xp_per_heart: int,      # NEW
) -> int:
    if is_replay:
        base = replay_xp
    else:
        base = lesson_xp if lesson_xp > 0 else base_xp
        # Add hearts bonus BEFORE streak multiplier
        hearts_bonus = max(0, remaining_hearts) * xp_per_heart
        base += hearts_bonus

    # Apply streak multiplier
    capped_streak = min(current_streak, max_multiplier_percent)
    multiplier = 1.0 + (capped_streak * 0.01)
    return int(base * multiplier)
```

### Anti-Patterns to Avoid
- **N separate RPUSH calls for N stages:** Use single `RPUSH key val1 val2 ... valN` -- this is already a Redis feature, the current code just loops
- **Lua script returning large data:** Keep Lua return values minimal (flags, IDs), not full JSON blobs
- **FSRS in hot path:** FSRS card review is CPU-bound; never put it in the <10ms session/end path
- **Reading hierarchy twice:** The hierarchy is already loaded for validation in start_session; reuse the cached version

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Spaced repetition scheduling | Custom interval calculator | `fsrs` package (v6.3.0) | 21-parameter model backed by academic research, handles edge cases (same-day reviews, fuzzing, relearning) |
| Card state serialization | Custom JSON format | `Card.to_json()` / `Card.from_json()` | Built-in serialization handles all fields including datetime, State enum |
| Optimal review weights | Manual tuning | `fsrs` default parameters or admin-configured weights | Default weights are trained on millions of real review logs |
| Atomic multi-key operations | Manual locking/transactions | Redis Lua scripts | Lua executes atomically on Redis server, no race conditions |
| Batch Redis operations | Sequential awaits | `redis.pipeline()` | Single network round-trip for N operations |

**Key insight:** The FSRS package handles ALL scheduling complexity internally. We only need to: (1) map stage results to FSRS Ratings, (2) persist Card state, (3) use `next_review` date for scheduling. Don't try to interpret or modify the stability/difficulty values -- they are internal to the FSRS model.

## Common Pitfalls

### Pitfall 1: Lua Script Key Count Mismatch
**What goes wrong:** Lua scripts in Redis require explicit declaration of all keys accessed via KEYS[], but forgetting a key or accessing keys directly by name (not via KEYS[]) can cause issues in Redis Cluster.
**Why it happens:** Developer accesses `redis.call('GET', 'memora:some:key')` instead of `redis.call('GET', KEYS[n])`.
**How to avoid:** All Redis keys accessed in Lua MUST be passed via KEYS array. Pass data (user_id, etc.) via ARGV.
**Warning signs:** Works in development but fails in Redis Cluster.

### Pitfall 2: Hierarchy API Hardcoded XP
**What goes wrong:** The current hierarchy API on line 139 has `"xp": lesson.xp if lesson.xp else 10` -- hardcoding 10 XP as default instead of using Memora Settings `base_lesson_xp`.
**Why it happens:** Legacy default from Phase 4 before Settings were implemented.
**How to avoid:** Return `base_xp` and `max_hearts` directly from Memora Lesson fields; let FastAPI apply Settings defaults at runtime when values are 0.
**Warning signs:** All lessons show 10 XP regardless of Memora Settings.

### Pitfall 3: Hearts Calculation Requires max_hearts in Hierarchy
**What goes wrong:** The hierarchy API currently does NOT return `max_hearts` per lesson -- it only returns `xp`. The client/server cannot calculate hearts bonus without knowing `max_hearts`.
**Why it happens:** `max_hearts` field exists on Memora Lesson DocType (default 3) but was never included in the hierarchy response.
**How to avoid:** Add `max_hearts` to the lesson info in the hierarchy API response and `LessonInfo` Pydantic model.
**Warning signs:** Hearts bonus XP always 0 because max_hearts is unknown.

### Pitfall 4: Stage time_spent Unit Change is Breaking
**What goes wrong:** Changing `StageResult.time_spent` from seconds to milliseconds without versioning breaks existing clients.
**Why it happens:** Client sends seconds, server expects milliseconds (or vice versa).
**How to avoid:** (1) Update the Pydantic model docstring, (2) update interaction buffer storage, (3) coordinate with client team. Since this is an internal API, document the change clearly.
**Warning signs:** Time values are 1000x too large or too small in analytics.

### Pitfall 5: FSRS Card State Key Conflicts
**What goes wrong:** Two concurrent lesson completions for the same stage_id could create conflicting FSRS card state.
**Why it happens:** Background FSRS task reads interaction buffer, processes cards, but another completion arrives between read and write.
**How to avoid:** Use LRANGE + LTRIM pattern (already used in `flush_interaction_buffer`) to process batches atomically. Group interactions by (player, stage_id) and process only the latest.
**Warning signs:** Card stability/difficulty values oscillate unexpectedly.

### Pitfall 6: Removing Legacy Endpoint Without Client Coordination
**What goes wrong:** Removing `POST /progress/complete` breaks any client still using it.
**Why it happens:** The endpoint has been superseded by `POST /sessions/end` but may still be called.
**How to avoid:** Check if any client is still using it. If uncertain, deprecate first (return 410 Gone) before full removal.
**Warning signs:** 404 errors in client logs after deployment.

### Pitfall 7: Leaderboard Pipeline ZADD Score Type
**What goes wrong:** Redis pipeline ZADD expects `{member: score}` dict but pipelining changes calling convention.
**Why it happens:** When moving from individual calls to pipeline, the API is the same but error handling differs (errors collected, not raised).
**How to avoid:** Verify pipeline results after `execute()` -- check for Redis errors in the response list.
**Warning signs:** Leaderboard scores silently not updated.

## Code Examples

### Enriched Hierarchy API Response (Frappe Side)
```python
# Source: Modify memora_admin/memora_admin/api/hierarchy.py line 128-142
# BEFORE (current):
lessons = frappe.get_all(
    "Memora Lesson",
    filters={"topic": topic.name},
    fields=["name", "xp"],
    order_by="idx asc",
)
for lesson in lessons:
    lesson_info = {
        "lesson_id": lesson.name,
        "bit_index": bit_index,
        "xp": lesson.xp if lesson.xp else 10,  # BUG: hardcoded default
    }

# AFTER (Phase 20):
lessons = frappe.get_all(
    "Memora Lesson",
    filters={"topic": topic.name},
    fields=["name", "base_xp", "max_hearts"],
    order_by="idx asc",
)
for lesson in lessons:
    lesson_info = {
        "lesson_id": lesson.name,
        "bit_index": bit_index,
        "base_xp": lesson.base_xp or 0,      # 0 means "use Settings default"
        "max_hearts": lesson.max_hearts or 0,  # 0 means "use Settings default"
    }
```

### Enriched LessonInfo Model
```python
# Source: Modify fastapi_app/models/progress.py
class LessonInfo(BaseModel):
    lesson_id: str
    bit_index: int
    base_xp: int = 0      # Renamed from xp; 0 = use Settings default
    max_hearts: int = 0    # NEW; 0 = use Settings default
```

### Enriched GamificationSettings Model
```python
# Source: Modify fastapi_app/models/settings.py
class GamificationSettings(BaseModel):
    base_lesson_xp: int = 100
    replay_xp: int = 25
    max_streak_multiplier_percent: int = 50
    max_devices_per_player: int = 3
    default_max_hearts: int = 5   # NEW: from Memora Settings
    xp_per_heart: int = 0         # NEW: from Memora Settings
```

### Enriched Settings API (Frappe Side)
```python
# Source: Modify memora_admin/memora_admin/api/settings.py
def get_gamification_settings() -> dict:
    settings = frappe.get_single("Memora Settings")
    return {
        "base_lesson_xp": settings.base_lesson_xp or 100,
        "replay_xp": settings.replay_xp or 25,
        "max_streak_multiplier_percent": settings.max_streak_multiplier_percent or 50,
        "default_max_hearts": settings.default_max_hearts or 5,   # NEW
        "xp_per_heart": settings.xp_per_heart or 0,               # NEW
    }
```

### FSRS Card State Redis Key Pattern
```python
# Key: memora:fsrs:{player_id}:{stage_id}
# Value: Card JSON from fsrs.Card.to_json()
# TTL: None (persistent until season end or explicit cleanup)

# Example stored value:
# {"card_id": 1770455237838, "state": 2, "step": 0,
#  "stability": 5.2, "difficulty": 3.1,
#  "due": "2026-02-10T09:00:00+00:00",
#  "last_review": "2026-02-07T09:07:17+00:00"}
```

### FSRS Background Task Pattern
```python
# Source: New file memora_admin/tasks/fsrs_processor.py
# Follows existing pattern from tasks/sync.py

def process_fsrs_reviews():
    """Process non-skippable stage results for FSRS scheduling.

    Scheduled: every 1 minute via hooks.py

    Steps:
    1. Read batch from interaction buffer (already flushed to Memora Interaction Log)
    2. Filter to non-skippable stages (check Memora Lesson Stage Settings.is_skippable)
    3. For each (player, stage_id) pair:
       a. Load Card from Redis or create new
       b. Map fail_count to Rating (0 fails = Good, 1+ = Again)
       c. Call scheduler.review_card(card, rating)
       d. Save updated Card to Redis
       e. Upsert Memora Memory State DocType
    """
```

### StageResult time_spent Milliseconds
```python
# Source: Modify fastapi_app/models/game_session.py
class StageResult(BaseModel):
    stage_id: str
    time_spent: int  # milliseconds (was seconds, changed in Phase 20)
    fail_count: int = 0
    completed_at: str  # ISO timestamp
    metadata: dict = {}
```

### Skippable Stage Detection Pattern
```python
# The is_skippable flag exists in TWO places:
# 1. Memora Lesson Stage Settings (DocType) - stage TYPE default
# 2. Memora Lesson Stage (child table) - per-stage override
#
# The child table's is_skippable can override the type default.
# For FSRS, check the child table value first (stage-level override),
# fall back to Lesson Stage Settings (type-level default).
#
# In the FSRS background task:
# Query: Get all non-skippable stage types from Memora Lesson Stage Settings
skippable_types = set(
    frappe.get_all(
        "Memora Lesson Stage Settings",
        filters={"is_skippable": 1},
        pluck="name",
    )
)
# Then when processing interaction log entries:
# Skip if stage's type is in skippable_types
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| N separate RPUSH calls per stage | Single RPUSH with multiple values | Redis has always supported this | Reduces N round-trips to 1 |
| Sequential Redis calls in endpoint | Lua script + pipeline batching | redis-py has supported this since v4+ | ~17 round-trips -> ~4 |
| Hardcoded XP default (10) in hierarchy API | Return actual base_xp from Lesson, 0 = use Settings | Phase 20 | Correct per-lesson XP customization |
| No hearts bonus | remaining_hearts * xp_per_heart added pre-streak | Phase 20 | Incentivizes perfect play |
| No FSRS / spaced repetition | fsrs package v6.3.0 with 21-parameter model | FSRS 6 (Oct 2025) | Research-backed review scheduling |
| time_spent in seconds | time_spent in milliseconds | Phase 20 | Finer-grained analytics |

**Deprecated/outdated:**
- `POST /progress/complete` endpoint: Superseded by `POST /sessions/end` in Phase 9. Will be removed.
- `calculate_xp_award()` in `progress.py`: Duplicate of `_calculate_xp_award()` in `sessions.py`. The sessions.py version is canonical.

## Key Data Structures Already In Place

### Memora Settings DocType (Singleton)
Already has all required fields:
- `default_max_hearts` (Int, default 5) -- for fallback when lesson max_hearts is 0
- `xp_per_heart` (Int) -- bonus XP per remaining heart
- `base_lesson_xp` (Int) -- fallback when lesson base_xp is 0
- `fsrs_weights` (Small Text) -- comma-separated float weights for FSRS Scheduler
- `request_retention_days` (Int) -- retention period

### Memora Memory State DocType
Already has all required fields:
- `season` (Link to Memora Season, required)
- `subject` (Link to Memora Subject, required)
- `player` (Link to Memora Player Profile, required)
- `stage_id` (Data, required)
- `stability` (Float, default 0)
- `difficulty` (Float, default 0)
- `next_review` (Datetime)
- `lesson` (Link to Memora Lesson, required)
- Autoname: `format:{season}-{subject}-{player}-{stage_id}`

### Memora Lesson DocType
Already has all required fields:
- `base_xp` (Int, default 0) -- per-lesson XP override
- `max_hearts` (Int, default 3) -- per-lesson max hearts
- `is_reviewable` (Check, default 0) -- FSRS eligibility flag

### Memora Lesson Stage Settings DocType
Already has:
- `stage_title` (Data, unique, autonaming)
- `is_skippable` (Check, default 0) -- exclude from FSRS

### Memora Lesson Stage (Child Table)
Already has:
- `stage_type` (Link to Memora Lesson Stage Settings)
- `is_skippable` (Check, default 0) -- per-stage override

## Optimization Details: Round-Trip Reduction

### Strategy: 2 Lua scripts + 1 pipeline = ~4 round-trips

**Lua Script 1: "Complete Session"** (1 round-trip)
- Read + delete session hash
- SETBIT progress (returns previous = is_replay)
- SADD dirty:progress
- RPUSH all interactions (batched)
- Returns: session fields + is_replay flag

**Lua Script 2: "Streak Update"** (1 round-trip) -- already exists in wallet.py
- Read/check/write streak atomically
- Returns: current streak + was_updated flag

**Pipeline 1: "Post-Completion Updates"** (1 round-trip)
- HINCRBY wallet:xp
- SADD dirty:wallets (conditional on streak update)
- HINCRBY stats x4 + EXPIRE
- ZADD/ZINCRBY leaderboards (3-6 commands)
- Total: 8-15 commands in single pipeline

**Hierarchy + Settings: pre-fetched** (already in-memory)
- GET hierarchy: fetched in endpoint validation (before end_session logic)
- GET settings: fetched with 5-min TTL cache

**Total: 3-4 round-trips** (down from 17+N)

### Why Not a Single Giant Lua Script?

1. **Key count:** Redis Cluster requires all keys declared upfront; a script touching session + progress + dirty + interactions + wallet + stats + 6 leaderboard keys = 10+ keys, which is fragile
2. **Lua timeout:** Large scripts risk hitting the `lua-time-limit` (default 5000ms)
3. **Debugging:** Smaller scripts are easier to test and debug
4. **Flexibility:** Wallet/leaderboard updates can be fire-and-forget if needed

## FSRS Integration Details

### Card Fields Mapping to Memora Memory State
| FSRS Card Field | Memora Memory State Field | Notes |
|-----------------|---------------------------|-------|
| stability | stability (Float) | Exact match |
| difficulty | difficulty (Float) | Exact match |
| due | next_review (Datetime) | Rename: `due` -> `next_review` |
| state | Not stored separately | Derivable from stability/due |
| step | Not stored separately | Only relevant during learning phase |

### FSRS Rating Mapping
| Condition | FSRS Rating | Rationale |
|-----------|-------------|-----------|
| fail_count = 0 | Rating.Good (3) | Perfect play |
| fail_count = 1 | Rating.Hard (2) | Minor struggle |
| fail_count >= 2 | Rating.Again (1) | Significant difficulty |

### FSRS Redis Key Pattern
- Hot state: `memora:fsrs:{player_id}:{stage_id}` = Card JSON
- No TTL (persists until explicit cleanup or season rollover)
- Cold state: Memora Memory State DocType (synced by background task)

### FSRS Scheduler Configuration
```python
from fsrs import Scheduler
from datetime import timedelta

# Parse weights from Memora Settings
weights_str = "0.212, 1.2931, 2.3065, ..."  # 21 floats
weights = tuple(float(w.strip()) for w in weights_str.split(","))

scheduler = Scheduler(
    parameters=weights,           # 21 weights from Memora Settings
    desired_retention=0.9,        # 90% target retention
    learning_steps=(timedelta(minutes=1), timedelta(minutes=10)),
    relearning_steps=(timedelta(minutes=10),),
    maximum_interval=365,         # 1 year max (educational context, not flashcards)
    enable_fuzzing=False,         # Deterministic for reproducibility
)
```

## Open Questions

1. **Hearts remaining calculation from stages:**
   - What we know: Each stage can have a fail_count. max_hearts is per-lesson.
   - What's unclear: How to derive "remaining hearts" from individual stage fail_counts. Is it `max_hearts - sum(all stage fail_counts)` or `max_hearts - max(any stage fail_count)`?
   - Recommendation: Use `remaining_hearts = max_hearts - total_fail_count` where `total_fail_count = sum(stage.fail_count for stage in stages)`. Clamp to minimum 0. This matches Duolingo-style heart mechanics.

2. **FSRS Rating granularity:**
   - What we know: FSRS has 4 ratings (Again, Hard, Good, Easy). Stage results have fail_count.
   - What's unclear: Should we use Easy rating? Should time_spent factor into rating?
   - Recommendation: Start with simple fail_count mapping (0=Good, 1=Hard, 2+=Again). Add time_spent-based rating later if data shows need. Keep Easy unused for now (avoids FSRS interval inflation).

3. **Season field in Memory State:**
   - What we know: Memora Memory State requires `season` (Link). The session endpoint doesn't currently track season.
   - What's unclear: How to determine current season in FSRS background task.
   - Recommendation: Query active season from Redis cache (SeasonService) in the background task. Use the currently published season.

4. **Legacy endpoint removal timing:**
   - What we know: `POST /progress/complete` is superseded by `POST /sessions/end`.
   - What's unclear: Whether any client is still calling the legacy endpoint.
   - Recommendation: Remove it in this phase as specified in success criteria. If rollback is needed, the git history preserves it.

## Sources

### Primary (HIGH confidence)
- Codebase: `fastapi_app/api/v1/endpoints/sessions.py` -- current end_session implementation
- Codebase: `fastapi_app/services/game_session.py` -- existing Lua script pattern for session start
- Codebase: `fastapi_app/services/wallet.py` -- existing Lua script pattern for streak update
- Codebase: `fastapi_app/services/stats.py` -- existing pipeline pattern for stats increment
- Codebase: `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json` -- Settings DocType with FSRS/hearts fields
- Codebase: `memora_admin/memora_admin/doctype/memora_memory_state/memora_memory_state.json` -- Memory State DocType schema
- Codebase: `memora_admin/memora_admin/doctype/memora_lesson/memora_lesson.json` -- Lesson DocType with base_xp, max_hearts
- Codebase: `memora_admin/memora_admin/api/hierarchy.py` -- current hierarchy API (shows hardcoded xp=10 bug)
- Runtime: `fsrs` v6.3.0 installed, API verified via Python introspection
- Runtime: `redis` v7.1.0 installed (supports Lua, pipeline, async)

### Secondary (MEDIUM confidence)
- [py-fsrs GitHub](https://github.com/open-spaced-repetition/py-fsrs) -- API documentation, usage examples
- [fsrs PyPI](https://pypi.org/project/fsrs/) -- v6.3.0 release info
- [Redis Lua scripting](https://redis.io/docs/latest/develop/programmability/eval-intro/) -- Official Lua script documentation
- [Redis pipelining](https://redis.io/docs/latest/develop/using-commands/pipelining/) -- Official pipeline documentation
- [redis-py Lua scripting docs](https://redis.readthedocs.io/en/stable/lua_scripting.html) -- Python client Lua integration

### Tertiary (LOW confidence)
- None -- all findings verified against codebase or official documentation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already installed and verified
- Architecture patterns: HIGH -- based on existing codebase patterns (Lua scripts, pipelines, scheduled tasks)
- Pitfalls: HIGH -- identified from actual code review (e.g., hardcoded XP bug found in hierarchy.py:139)
- FSRS integration: HIGH -- API verified via runtime introspection of installed package
- Performance estimates: MEDIUM -- round-trip count is exact from code, but <10ms target depends on Redis latency

**Research date:** 2026-02-07
**Valid until:** 2026-03-07 (30 days -- stable domain, no fast-moving dependencies)
