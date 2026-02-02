# Phase 5: Wallet & Gamification - Research

**Researched:** 2026-02-02
**Domain:** Redis hash operations, XP/streak gamification, timezone handling
**Confidence:** HIGH

## Summary

This phase implements XP wallet and streak tracking for the Memora gamification system. The core pattern is straightforward: use Redis hashes for wallet storage with atomic HINCRBY for XP accumulation, and track streaks using a date field compared against Asia/Amman timezone "today".

The existing codebase provides strong foundations:
- Redis async client already connected via pool (`fastapi_app/core/redis.py`)
- Service pattern established (`ProgressService`, `AccessService`)
- Dependency injection via `Annotated + Depends` (`fastapi_app/api/deps.py`)
- Frappe API integration via `FrappeClient` with caching pattern (`HierarchyService`)
- Lua script pattern for atomic operations (`RateLimiter`)
- Replay detection already exists in `complete_lesson` endpoint (Phase 4)

Key insight: The completion endpoint already detects replays via SETBIT return value. Phase 5 extends this by awarding XP and updating streaks within the same flow.

**Primary recommendation:** Create a `WalletService` that mirrors `AccessService`/`ProgressService` patterns, using Redis hashes with atomic HINCRBY for XP and a Lua script for atomic streak update with date comparison.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis.asyncio | 5.x | Async Redis operations | Already in use, HINCRBY for atomic XP |
| zoneinfo | stdlib | Timezone handling | Python 3.10+ stdlib, no external deps |
| pydantic | 2.x | Request/response models | Already in use, computed_field pattern |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | - | Structured logging | Already in use for all services |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Redis hash | Redis string + JSON | Hash allows atomic field updates, JSON requires read-modify-write |
| zoneinfo | pytz | pytz is legacy, zoneinfo is stdlib and recommended |
| Lua script for streak | Python read-modify-write | Lua guarantees atomicity, Python risks race conditions |

**No additional installation required** - all libraries already in use.

## Architecture Patterns

### Recommended Project Structure

```
fastapi_app/
├── services/
│   └── wallet.py          # NEW: WalletService class
├── models/
│   └── wallet.py          # NEW: WalletResponse, CompletionResult
└── api/v1/endpoints/
    ├── wallet.py          # NEW: GET /wallet, GET /wallet/{player_id}
    └── progress.py        # MODIFY: integrate wallet award on complete
```

### Pattern 1: Redis Hash for Wallet Storage

**What:** Store wallet data as Redis hash fields for atomic updates
**When to use:** Multi-field data requiring atomic increments
**Example:**
```python
# Key pattern: memora:wallet:{player_id}
# Fields: xp (int), streak (int), streak_date (YYYY-MM-DD string)

# Source: /redis/redis-py Context7 documentation
async def award_xp(self, player_id: str, amount: int) -> int:
    """Atomically increment XP and return new total."""
    key = f"{self.prefix}wallet:{player_id}"
    return await self.redis.hincrby(key, "xp", amount)

async def get_wallet(self, player_id: str) -> dict:
    """Get all wallet fields."""
    key = f"{self.prefix}wallet:{player_id}"
    data = await self.redis.hgetall(key)
    return {
        "xp": int(data.get("xp", 0)),
        "streak": int(data.get("streak", 0)),
        "streak_date": data.get("streak_date", ""),
    }
```

### Pattern 2: Lua Script for Atomic Streak Update

**What:** Use Lua script to atomically check date and update streak
**When to use:** Conditional update requiring read-then-write atomicity
**Example:**
```python
# Source: Existing pattern in fastapi_app/services/rate_limit.py
STREAK_UPDATE_SCRIPT = """
local key = KEYS[1]
local today = ARGV[1]
local is_replay = tonumber(ARGV[2])

-- Get current values
local current_streak = tonumber(redis.call('HGET', key, 'streak') or 0)
local streak_date = redis.call('HGET', key, 'streak_date') or ''

-- Replays don't update streak
if is_replay == 1 then
    return {current_streak, 0}  -- {streak, was_updated}
end

-- Same day - no streak change
if streak_date == today then
    return {current_streak, 0}
end

-- Calculate yesterday (passed as ARGV[3] to avoid date math in Lua)
local yesterday = ARGV[3]

-- Consecutive day - increment streak
if streak_date == yesterday then
    current_streak = current_streak + 1
    redis.call('HSET', key, 'streak', current_streak)
    redis.call('HSET', key, 'streak_date', today)
    return {current_streak, 1}
end

-- Missed day(s) - reset to 1
redis.call('HSET', key, 'streak', 1)
redis.call('HSET', key, 'streak_date', today)
return {1, 1}
"""
```

### Pattern 3: Settings Cache Service

**What:** Cache Frappe Memora Settings with TTL for fast XP calculations
**When to use:** Frequently accessed admin-configurable values
**Example:**
```python
# Similar to HierarchyService pattern
class SettingsService:
    CACHE_TTL = 300  # 5 minutes
    CACHE_KEY = "memora:settings:gamification"

    async def get_gamification_settings(self) -> GamificationSettings:
        cached = await self.redis.get(self.CACHE_KEY)
        if cached:
            return GamificationSettings.model_validate_json(cached)

        # Fetch from Frappe
        result = await self.frappe.call(
            "memora_admin.api.settings.get_gamification_settings"
        )
        settings = GamificationSettings.model_validate(result)

        await self.redis.set(
            self.CACHE_KEY,
            settings.model_dump_json(),
            ex=self.CACHE_TTL,
        )
        return settings
```

### Pattern 4: Timezone-Aware Date Calculation

**What:** Convert UTC now to Asia/Amman date for streak comparison
**When to use:** Daily streak boundary checking
**Example:**
```python
# Source: Python docs https://docs.python.org/3/library/zoneinfo.html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

AMMAN_TZ = ZoneInfo("Asia/Amman")

def get_amman_today() -> str:
    """Get today's date in Asia/Amman timezone as YYYY-MM-DD."""
    return datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")

def get_amman_yesterday() -> str:
    """Get yesterday's date in Asia/Amman timezone as YYYY-MM-DD."""
    yesterday = datetime.now(AMMAN_TZ) - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")
```

### Anti-Patterns to Avoid

- **Race condition in streak update:** Never do `get` then `set` for streak - use Lua script
- **Per-request settings fetch:** Cache Memora Settings, don't query Frappe on every completion
- **Storing UTC dates for streak:** Store Asia/Amman date strings, not UTC timestamps
- **Complex Lua date math:** Pass today/yesterday as arguments, don't compute in Lua

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic XP increment | GET + add + SET | HINCRBY | Race conditions with concurrent completions |
| Streak date comparison | Python check then Redis write | Lua script | Race conditions between check and update |
| Timezone handling | Manual offset calculation | zoneinfo.ZoneInfo | DST handling, leap seconds, correctness |
| Settings caching | Global variable | Redis with TTL | Multi-instance deployments, cache invalidation |

**Key insight:** Every read-modify-write operation on wallet data must be atomic. Redis provides HINCRBY for simple increments; use Lua scripts for conditional logic.

## Common Pitfalls

### Pitfall 1: Race Condition on Streak Update

**What goes wrong:** Two concurrent completions both read streak=5, both write streak=6
**Why it happens:** Python code does HGET, increments locally, then HSET
**How to avoid:** Use Lua script that does read-check-write atomically
**Warning signs:** Streaks not incrementing on high activity, inconsistent streak counts

### Pitfall 2: Timezone Boundary Errors

**What goes wrong:** Player completes at 11:59 PM Amman time, streak resets at midnight UTC
**Why it happens:** Comparing UTC dates instead of local timezone dates
**How to avoid:** Always convert to Asia/Amman before extracting date
**Warning signs:** Streaks resetting mid-day for users, complaints around midnight

### Pitfall 3: Settings Cache Stampede

**What goes wrong:** Cache expires, 100 concurrent requests all hit Frappe
**Why it happens:** No lock on cache population
**How to avoid:** Use short TTL (5 min) + stale-while-revalidate pattern OR accept rare Frappe hits
**Warning signs:** Frappe API spikes every 5 minutes

### Pitfall 4: Replay XP Stacking

**What goes wrong:** Player replays same lesson 100 times, gets unlimited XP
**Why it happens:** No daily cap implemented when one was expected
**How to avoid:** Per CONTEXT.md decision: no daily cap on replay XP (this is intentional)
**Warning signs:** N/A - this is accepted behavior per user discussion

### Pitfall 5: Integer Overflow on XP

**What goes wrong:** XP exceeds 64-bit signed integer
**Why it happens:** Redis HINCRBY uses 64-bit signed integers
**How to avoid:** Max is 9,223,372,036,854,775,807 - not a practical concern
**Warning signs:** None expected - would require billions of lesson completions

### Pitfall 6: Missing Wallet Initialization

**What goes wrong:** HGET returns nil for new player, code crashes
**Why it happens:** Wallet hash doesn't exist until first completion
**How to avoid:** Always use default values (xp=0, streak=0, streak_date="")
**Warning signs:** 500 errors for new players on first completion

## Code Examples

### Wallet Service Implementation

```python
# Source: Follows existing AccessService pattern in fastapi_app/services/access.py
class WalletService:
    """Manages player wallet via Redis hash."""

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis_client
        self.prefix = key_prefix
        self._streak_script = None

    def _wallet_key(self, player_id: str) -> str:
        return f"{self.prefix}wallet:{player_id}"

    async def get_wallet(self, player_id: str) -> dict:
        """Get wallet data (xp, streak)."""
        key = self._wallet_key(player_id)
        data = await self.redis.hgetall(key)
        return {
            "xp": int(data.get("xp", 0)),
            "streak": int(data.get("streak", 0)),
        }

    async def award_xp(self, player_id: str, amount: int) -> int:
        """Atomically add XP. Returns new total."""
        key = self._wallet_key(player_id)
        return await self.redis.hincrby(key, "xp", amount)
```

### XP Calculation Logic

```python
# Per CONTEXT.md decisions
async def calculate_xp_award(
    settings: GamificationSettings,
    lesson_base_xp: int,
    current_streak: int,
    is_replay: bool,
) -> int:
    """Calculate XP to award for completion."""
    if is_replay:
        # Replay: fixed amount from settings
        base_xp = settings.replay_xp
    else:
        # Fresh completion: lesson XP or fallback to settings default
        base_xp = lesson_base_xp if lesson_base_xp > 0 else settings.base_lesson_xp

    # Apply streak multiplier (linear +1% per day, capped)
    multiplier = 1.0 + min(current_streak, settings.max_streak_multiplier) * 0.01

    # Floor the result (per Claude's discretion)
    return int(base_xp * multiplier)
```

### Completion Endpoint Integration

```python
# Extend existing POST /progress/complete endpoint
# After marking lesson complete:

is_replay = await progress_service.complete_lesson(...)

# Award XP and update streak
settings = await settings_service.get_gamification_settings()
streak, streak_updated = await wallet_service.update_streak(
    player_id=user.sub,
    is_replay=is_replay,
)

xp_awarded = calculate_xp_award(
    settings=settings,
    lesson_base_xp=lesson_info.xp,
    current_streak=streak,
    is_replay=is_replay,
)

new_xp = await wallet_service.award_xp(user.sub, xp_awarded)

logger.info(
    "lesson_completed",
    user_id=user.sub,
    is_replay=is_replay,
    xp_awarded=xp_awarded,
    new_xp=new_xp,
    streak=streak,
)
```

### Admin Wallet Access

```python
# Per CONTEXT.md: GET /wallet/{player_id} for admin
async def get_current_user_or_admin(
    player_id: str | None,
    user: CurrentUser,
) -> str:
    """Return player_id for admin, or user.sub for self-access."""
    if player_id:
        # Admin accessing another player
        if user.role != "System Manager":
            raise HTTPException(403, "Admin access required")
        return player_id
    return user.sub
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pytz for timezones | zoneinfo (stdlib) | Python 3.9+ | No external dependency, better DST handling |
| redis-py sync | redis.asyncio | redis-py 4.2+ | Proper async support, better performance |
| String + JSON wallet | Hash fields | - | Atomic field updates without read-modify-write |

**Deprecated/outdated:**
- pytz: Use zoneinfo instead (stdlib in Python 3.9+)
- Separate Redis calls for conditional writes: Use Lua scripts

## Open Questions

1. **Completion endpoint response**
   - What we know: User said Claude's discretion on whether to return wallet snapshot
   - What's unclear: Performance impact of including wallet in completion response
   - Recommendation: Return `{success: true, xp_awarded: N, is_replay: bool}` - minimal but useful

2. **Streak multiplier cap field name**
   - What we know: Stored in Memora Settings, admin-adjustable
   - What's unclear: Exact field name (not in current doctype JSON)
   - Recommendation: Add `max_streak_multiplier_percent` field to Memora Settings (e.g., 50 = 50% max bonus)

3. **Rounding for fractional XP**
   - What we know: Claude's discretion on floor vs round
   - Recommendation: Use `int()` (floor) for predictability - players always know minimum XP

## Sources

### Primary (HIGH confidence)
- `/redis/redis-py` Context7 - Hash operations, HINCRBY, Lua scripts
- Python docs zoneinfo - https://docs.python.org/3/library/zoneinfo.html
- Existing codebase patterns:
  - `fastapi_app/services/progress.py` - Service pattern
  - `fastapi_app/services/rate_limit.py` - Lua script pattern
  - `fastapi_app/services/hierarchy.py` - Settings cache pattern
  - `fastapi_app/api/deps.py` - Dependency injection pattern

### Secondary (MEDIUM confidence)
- Redis HINCRBY docs - https://redis.io/docs/latest/commands/hincrby/
- Trophy streak design - https://trophy.so/blog/how-to-build-a-streaks-feature

### Tertiary (LOW confidence)
- General gamification patterns from web search (design principles, not implementation)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries already in use
- Architecture: HIGH - Follows existing codebase patterns exactly
- Pitfalls: HIGH - Based on atomic operation requirements and existing race condition patterns

**Research date:** 2026-02-02
**Valid until:** 2026-03-02 (stable domain, no fast-moving dependencies)
