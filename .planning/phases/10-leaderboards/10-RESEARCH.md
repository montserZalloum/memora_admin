# Phase 10: Leaderboards - Research

**Researched:** 2026-02-03
**Domain:** Redis sorted sets (ZSET) for real-time XP leaderboards with tie-breaking and time-based resets
**Confidence:** HIGH

## Summary

Phase 10 implements competitive XP leaderboards via Redis sorted sets (ZSET). Per CONTEXT.md decisions, there are three leaderboard types: daily (resets midnight Asia/Amman), weekly (resets Friday midnight), and all-time. Users can optionally filter by subject_id. The "your rank" feature uses a separate endpoint that returns the user's rank with +/-2 neighbors and distance to next tier.

Redis sorted sets are the industry standard for leaderboards, providing O(log N) insertions and O(log N) rank lookups. The key challenge is tie-breaking: CONTEXT.md specifies "earlier achiever wins," which requires encoding timestamps in the score. The standard approach is composite scoring where the integer part is XP and the fractional part is an inverted timestamp, ensuring higher XP always wins and earlier timestamps break ties.

The codebase already uses the established pattern of updating leaderboard scores when XP is awarded (via `wallet_service.award_xp`). The leaderboard update can be added to this flow with minimal changes. For archival, daily/weekly snapshots use TTL-based key expiry with RENAME operations at reset time.

**Primary recommendation:** Use Redis ZSET with composite scores (XP.inverted_timestamp) for tie-breaking. Add `ZADD` calls to `award_xp()` flow to maintain leaderboards atomically. Use date-suffixed keys for daily/weekly boards with scheduled reset via Lua script.

## Standard Stack

This phase uses the existing codebase stack. No new libraries required.

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py (async) | 5.x | ZADD, ZRANGE, ZREVRANK, ZCARD operations | Already used for all Redis operations in codebase |
| Pydantic | 2.x | LeaderboardEntry, LeaderboardResponse models | Already used for all FastAPI endpoints |
| structlog | Already installed | Structured logging | Already used throughout FastAPI app |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| zoneinfo | stdlib | Asia/Amman timezone for reset timing | Already used in wallet.py for streak dates |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Redis ZSET | PostgreSQL with indexes | SQL is slower for real-time ranking, ZSET is O(log N) |
| Composite score | Lexicographic member | Composite score allows numeric comparisons, cleaner API |
| Hourly sharding | Single key | Single key works at current scale; sharding adds complexity |

**Installation:**
```bash
# No new dependencies - all already in requirements.txt
```

## Architecture Patterns

### Recommended Project Structure
```
fastapi_app/
├── models/
│   └── leaderboard.py        # LeaderboardEntry, LeaderboardResponse, MyRankResponse
├── services/
│   └── leaderboard.py        # LeaderboardService with ZSET operations
└── api/v1/endpoints/
    └── leaderboard.py        # GET /leaderboard/{type}, GET /leaderboard/{type}/me
```

### Pattern 1: Composite Score for Tie-Breaking
**What:** Encode XP in integer part, inverted timestamp in fractional part
**When to use:** "Earlier achiever wins" requirement per CONTEXT.md
**Why:** Redis ZSET sorts by score descending with REV; composite score ensures:
- Higher XP always ranks higher (integer comparison)
- Same XP: earlier timestamp wins (lower fraction = achieved first)

**Formula:** `score = xp + (1.0 - (timestamp % 1_000_000_000) / 1_000_000_000)`

**Example:**
```python
# Source: Redis sorted sets documentation, verified pattern
import time

def compute_composite_score(xp: int, timestamp: float | None = None) -> float:
    """Compute composite score for leaderboard ranking.

    Per CONTEXT.md: "Earlier achiever wins - whoever reached that XP first ranks higher"

    The score encodes:
    - Integer part: XP value (for primary ranking)
    - Fractional part: Inverted timestamp (for tie-breaking)

    Since ZREVRANGE sorts descending, higher scores rank better.
    For same XP, earlier timestamp = smaller fractional part = ranks higher.
    """
    if timestamp is None:
        timestamp = time.time()

    # Use modulo to keep timestamp in manageable range (cycles every ~31 years)
    # Invert so earlier timestamps produce smaller fractions
    inverted = 1.0 - (timestamp % 1_000_000_000) / 1_000_000_000

    return float(xp) + inverted
```

### Pattern 2: Date-Suffixed Keys for Time-Based Leaderboards
**What:** Append date/week identifier to key for automatic scoping
**When to use:** Daily and weekly leaderboards that reset periodically
**Example:**
```python
# Source: Redis leaderboard best practices
# Key patterns:
# - All-time:  memora:lb:alltime
# - All-time (subject):  memora:lb:alltime:subject:{subject_id}
# - Daily:    memora:lb:daily:2026-02-03
# - Daily (subject):    memora:lb:daily:2026-02-03:subject:{subject_id}
# - Weekly:   memora:lb:weekly:2026-W06
# - Weekly (subject):   memora:lb:weekly:2026-W06:subject:{subject_id}

from datetime import datetime
from zoneinfo import ZoneInfo

AMMAN_TZ = ZoneInfo("Asia/Amman")

def get_daily_key(subject_id: str | None = None) -> str:
    """Get current daily leaderboard key."""
    date_str = datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")
    base = f"memora:lb:daily:{date_str}"
    return f"{base}:subject:{subject_id}" if subject_id else base

def get_weekly_key(subject_id: str | None = None) -> str:
    """Get current weekly leaderboard key (ISO week format)."""
    now = datetime.now(AMMAN_TZ)
    # ISO week: 2026-W06 format
    week_str = now.strftime("%G-W%V")
    base = f"memora:lb:weekly:{week_str}"
    return f"{base}:subject:{subject_id}" if subject_id else base

def get_alltime_key(subject_id: str | None = None) -> str:
    """Get all-time leaderboard key."""
    base = "memora:lb:alltime"
    return f"{base}:subject:{subject_id}" if subject_id else base
```

### Pattern 3: ZADD on XP Award
**What:** Update all relevant leaderboards when XP is awarded
**When to use:** Inside `wallet_service.award_xp()` or as a post-award hook
**Example:**
```python
# Source: Codebase pattern from wallet.py award_xp
async def update_leaderboards(
    self,
    player_id: str,
    new_total_xp: int,
    subject_id: str | None = None,
) -> None:
    """Update all relevant leaderboards after XP award.

    Called after HINCRBY in award_xp to maintain consistency.
    Uses composite score for tie-breaking per CONTEXT.md.
    """
    score = compute_composite_score(new_total_xp)

    # All-time leaderboard (always)
    await self.redis.zadd(get_alltime_key(), {player_id: score})

    # Daily leaderboard (today's XP gain tracked separately)
    daily_key = get_daily_key()
    await self.redis.zincrby(daily_key, amount, player_id)

    # Weekly leaderboard (this week's XP gain)
    weekly_key = get_weekly_key()
    await self.redis.zincrby(weekly_key, amount, player_id)

    # Subject-specific leaderboards (if subject context available)
    if subject_id:
        await self.redis.zadd(get_alltime_key(subject_id), {player_id: score})
        await self.redis.zincrby(get_daily_key(subject_id), amount, player_id)
        await self.redis.zincrby(get_weekly_key(subject_id), amount, player_id)
```

### Pattern 4: ZREVRANK with WITHSCORE for User Position
**What:** Get user's rank and score in a single command (Redis 7.2+)
**When to use:** "Your rank" endpoint
**Example:**
```python
# Source: Redis ZREVRANK documentation
async def get_my_rank(
    self,
    leaderboard_key: str,
    player_id: str,
) -> tuple[int | None, float | None]:
    """Get player's rank (0-based) and score.

    Returns (rank, score) or (None, None) if not in leaderboard.
    ZREVRANK WITHSCORE is O(log N).
    """
    # ZREVRANK with WITHSCORE returns [rank, score] or None
    result = await self.redis.zrevrank(leaderboard_key, player_id, withscore=True)

    if result is None:
        return None, None

    rank, score = result
    return int(rank), float(score)
```

### Pattern 5: Neighbors via ZRANGE with REV
**What:** Get players around user's rank for context
**When to use:** "Your rank" endpoint per CONTEXT.md (+/-2 neighbors)
**Example:**
```python
# Source: Redis ZRANGE documentation (replaces ZREVRANGE since 6.2)
async def get_neighbors(
    self,
    leaderboard_key: str,
    player_id: str,
    neighbor_count: int = 2,
) -> list[tuple[str, float]]:
    """Get player's rank context with neighbors.

    Returns list of (player_id, score) tuples around the target player.
    Per CONTEXT.md: +/-2 neighbors for motivation context.
    """
    # First get player's rank
    rank = await self.redis.zrevrank(leaderboard_key, player_id)
    if rank is None:
        return []

    # Calculate range (ensuring non-negative start)
    start = max(0, rank - neighbor_count)
    stop = rank + neighbor_count

    # ZRANGE with REV and WITHSCORES
    # Returns list of (member, score) tuples
    result = await self.redis.zrange(
        leaderboard_key,
        start,
        stop,
        desc=True,  # REV option
        withscores=True,
    )

    return result
```

### Anti-Patterns to Avoid
- **ZRANGEBYSCORE instead of ZRANGE REV:** Deprecated pattern, ZRANGE with REV is canonical since Redis 6.2
- **Separate timestamp storage:** Adds latency and complexity; composite score handles tie-breaking in one field
- **Full scan for rank:** Always use ZREVRANK O(log N), never iterate
- **Eager archival:** Let Redis TTL handle cleanup; archive on-demand if needed
- **Global leaderboard without subject context:** Track subject_id for XP awards to enable subject filtering

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Tie-breaking | Custom sort post-query | Composite score in ZADD | Single Redis operation, no application-side sorting |
| Rank lookup | ZRANGE + iterate | ZREVRANK | O(log N) vs O(N) |
| Total count | Iterate all members | ZCARD | O(1) operation |
| Time-based reset | Manual key deletion | Date-suffixed keys + TTL | Automatic cleanup, no scheduled job required |
| Timezone handling | UTC with client conversion | Asia/Amman in server | Already established pattern in wallet.py |

**Key insight:** Redis ZSET provides all primitives needed for leaderboards. Composite scoring is the standard technique for multi-criteria ranking in a single score field.

## Common Pitfalls

### Pitfall 1: Score Precision Loss
**What goes wrong:** Very large XP values lose precision in composite score
**Why it happens:** IEEE 754 double precision has ~15 significant digits; XP + timestamp fraction exceeds this
**How to avoid:** XP values under 2^53 (~9 quadrillion) are safe. For this app, max XP is realistically under 1 billion.
**Warning signs:** Ranking anomalies at extremely high XP values

### Pitfall 2: Timezone Drift in Resets
**What goes wrong:** Daily/weekly boundaries calculated in UTC instead of Asia/Amman
**Why it happens:** Using `datetime.now()` without timezone, or inconsistent TZ handling
**How to avoid:** Always use `datetime.now(AMMAN_TZ)` pattern from wallet.py
**Warning signs:** Leaderboards resetting at wrong time of day

### Pitfall 3: Missing Subject Context in XP Updates
**What goes wrong:** Subject-filtered leaderboards show stale data
**Why it happens:** XP awarded without subject_id, so subject leaderboards not updated
**How to avoid:** Ensure award_xp path always has subject context from session/progress
**Warning signs:** Global leaderboard shows different XP than subject leaderboard

### Pitfall 4: Dense Rank vs Standard Rank Confusion
**What goes wrong:** Tied players not sharing rank number
**Why it happens:** Using ZREVRANK directly (returns unique positions 0,1,2...)
**How to avoid:** Per CONTEXT.md "tied players share same rank number" - need ZRANGEBYSCORE to find ties
**Warning signs:** Two players with same score showing as #5 and #6 instead of both #5

### Pitfall 5: Unranked Users Breaking Queries
**What goes wrong:** ZREVRANK returns None for users with 0 XP, causing errors
**Why it happens:** Users who never earned XP aren't in the ZSET
**How to avoid:** Per CONTEXT.md "Unranked users treated as tied for last place" - handle None as total_count + 1
**Warning signs:** 500 errors for new users checking their rank

### Pitfall 6: Friday Reset Logic
**What goes wrong:** Weekly leaderboard resets on wrong day
**Why it happens:** ISO week starts Monday; Jordan weekend is Fri-Sat
**How to avoid:** Per CONTEXT.md "Friday midnight Asia/Amman" - check if current day is Friday post-midnight
**Warning signs:** Weekly boards resetting on Monday instead of Friday

## Code Examples

Verified patterns from codebase and official documentation:

### LeaderboardService Core
```python
# Source: Follows codebase pattern from services/wallet.py
from datetime import datetime
from zoneinfo import ZoneInfo

import redis.asyncio as redis
import structlog

logger = structlog.get_logger()

AMMAN_TZ = ZoneInfo("Asia/Amman")

# Key prefixes
LB_PREFIX = "memora:lb"
DAILY_PREFIX = f"{LB_PREFIX}:daily"
WEEKLY_PREFIX = f"{LB_PREFIX}:weekly"
ALLTIME_PREFIX = f"{LB_PREFIX}:alltime"

# Archive retention (30 days in seconds)
ARCHIVE_TTL = 30 * 24 * 60 * 60


class LeaderboardService:
    """Manages XP leaderboards via Redis sorted sets.

    Per CONTEXT.md:
    - Three types: daily, weekly, all-time
    - Optional subject filtering
    - Tie-breaking: earlier achiever wins
    - Dense ranking: tied players share rank

    Key patterns:
    - memora:lb:alltime[:subject:{id}]
    - memora:lb:daily:{YYYY-MM-DD}[:subject:{id}]
    - memora:lb:weekly:{YYYY-Www}[:subject:{id}]
    """

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _get_key(
        self,
        lb_type: str,
        subject_id: str | None = None,
    ) -> str:
        """Build leaderboard key for type and optional subject."""
        now = datetime.now(AMMAN_TZ)

        if lb_type == "alltime":
            base = ALLTIME_PREFIX
        elif lb_type == "daily":
            date_str = now.strftime("%Y-%m-%d")
            base = f"{DAILY_PREFIX}:{date_str}"
        elif lb_type == "weekly":
            # ISO week format: 2026-W06
            week_str = now.strftime("%G-W%V")
            base = f"{WEEKLY_PREFIX}:{week_str}"
        else:
            raise ValueError(f"Invalid leaderboard type: {lb_type}")

        if subject_id:
            return f"{base}:subject:{subject_id}"
        return base
```

### Get Top N Leaderboard
```python
# Source: Redis ZRANGE documentation
async def get_top(
    self,
    lb_type: str,
    limit: int = 10,
    subject_id: str | None = None,
) -> list[dict]:
    """Get top N players from leaderboard.

    Returns list of entries with rank, player_id, xp.
    Per CONTEXT.md: rank, display_name, xp, avatar_url.
    (display_name and avatar_url fetched separately from player profile)
    """
    key = self._get_key(lb_type, subject_id)

    # ZRANGE with REV and WITHSCORES, limit to top N
    # Returns [(member, score), ...]
    results = await self.redis.zrange(
        key,
        0,
        limit - 1,
        desc=True,
        withscores=True,
    )

    entries = []
    current_rank = 1
    prev_score = None

    for idx, (player_id, score) in enumerate(results):
        # Extract XP from composite score (integer part)
        xp = int(score)

        # Dense ranking: same score = same rank
        if prev_score is not None and xp != int(prev_score):
            current_rank = idx + 1

        entries.append({
            "rank": current_rank,
            "player_id": player_id.decode() if isinstance(player_id, bytes) else player_id,
            "xp": xp,
        })
        prev_score = score

    return entries
```

### Get My Rank with Neighbors
```python
# Source: Redis ZREVRANK and ZRANGE documentation
async def get_my_rank(
    self,
    player_id: str,
    lb_type: str,
    subject_id: str | None = None,
    neighbor_count: int = 2,
) -> dict | None:
    """Get player's rank with surrounding context.

    Per CONTEXT.md:
    - Separate endpoint from main leaderboard
    - Include +/-2 neighbors for context
    - Include distance to next tier (XP to pass player above)
    - Unranked users (0 XP) treated as tied for last place
    """
    key = self._get_key(lb_type, subject_id)

    # Get player's position (None if not in leaderboard)
    result = await self.redis.zrevrank(key, player_id, withscore=True)

    # Handle unranked users
    if result is None:
        total = await self.redis.zcard(key)
        return {
            "rank": total + 1,  # Last place
            "xp": 0,
            "xp_to_next": None,  # No one to pass
            "neighbors": [],
        }

    position, score = result
    xp = int(score)

    # Calculate dense rank (count distinct higher scores)
    # This handles tied players sharing rank
    higher_count = await self.redis.zcount(
        key,
        f"({score}",  # Exclusive: scores strictly greater than mine
        "+inf",
    )
    dense_rank = higher_count + 1

    # Get neighbors
    start = max(0, position - neighbor_count)
    stop = position + neighbor_count

    neighbors_raw = await self.redis.zrange(
        key,
        start,
        stop,
        desc=True,
        withscores=True,
    )

    # Build neighbor entries with dense ranks
    neighbors = []
    for neighbor_id, neighbor_score in neighbors_raw:
        neighbor_xp = int(neighbor_score)
        neighbor_higher = await self.redis.zcount(
            key,
            f"({neighbor_score}",
            "+inf",
        )
        neighbors.append({
            "rank": neighbor_higher + 1,
            "player_id": neighbor_id.decode() if isinstance(neighbor_id, bytes) else neighbor_id,
            "xp": neighbor_xp,
            "is_me": (neighbor_id == player_id or
                     (isinstance(neighbor_id, bytes) and neighbor_id.decode() == player_id)),
        })

    # Calculate XP to next tier
    xp_to_next = None
    if position > 0:
        # Get player above
        above = await self.redis.zrange(
            key,
            position - 1,
            position - 1,
            desc=True,
            withscores=True,
        )
        if above:
            above_xp = int(above[0][1])
            xp_to_next = above_xp - xp + 1  # +1 to actually pass them

    return {
        "rank": dense_rank,
        "xp": xp,
        "xp_to_next": xp_to_next,
        "neighbors": neighbors,
    }
```

### Update Leaderboards on XP Award
```python
# Source: Integration with codebase wallet.py pattern
import time

def compute_composite_score(xp: int, timestamp: float | None = None) -> float:
    """Compute composite score for tie-breaking.

    Per CONTEXT.md: Earlier achiever wins.
    Format: XP.inverted_timestamp
    """
    if timestamp is None:
        timestamp = time.time()
    # Invert timestamp so earlier = smaller fraction = ranks higher at same XP
    inverted = 1.0 - (timestamp % 1_000_000_000) / 1_000_000_000
    return float(xp) + inverted


async def update_leaderboards_on_xp(
    self,
    player_id: str,
    xp_amount: int,
    new_total_xp: int,
    subject_id: str | None = None,
) -> None:
    """Update all relevant leaderboards after XP award.

    Called after wallet.award_xp() to maintain consistency.

    Args:
        player_id: Player's user ID
        xp_amount: XP just awarded (for daily/weekly increment)
        new_total_xp: Total XP after award (for all-time composite score)
        subject_id: Optional subject for filtered leaderboards
    """
    timestamp = time.time()
    composite_score = compute_composite_score(new_total_xp, timestamp)

    # All-time: Use composite score (total XP)
    alltime_key = self._get_key("alltime")
    await self.redis.zadd(alltime_key, {player_id: composite_score})

    # Daily: Increment by amount (not total)
    daily_key = self._get_key("daily")
    await self.redis.zincrby(daily_key, xp_amount, player_id)

    # Weekly: Increment by amount (not total)
    weekly_key = self._get_key("weekly")
    await self.redis.zincrby(weekly_key, xp_amount, player_id)

    # Subject-specific leaderboards
    if subject_id:
        await self.redis.zadd(
            self._get_key("alltime", subject_id),
            {player_id: composite_score},
        )
        await self.redis.zincrby(
            self._get_key("daily", subject_id),
            xp_amount,
            player_id,
        )
        await self.redis.zincrby(
            self._get_key("weekly", subject_id),
            xp_amount,
            player_id,
        )

    logger.debug(
        "leaderboards_updated",
        player_id=player_id,
        xp_amount=xp_amount,
        new_total_xp=new_total_xp,
        subject_id=subject_id,
    )
```

### Pydantic Models
```python
# Source: Follows codebase pattern from models/wallet.py
from pydantic import BaseModel


class LeaderboardEntry(BaseModel):
    """Single entry in leaderboard response.

    Per CONTEXT.md:
    - rank, display_name, xp, avatar_url
    """
    rank: int
    player_id: str
    display_name: str
    xp: int
    avatar_url: str | None = None


class LeaderboardResponse(BaseModel):
    """Response for GET /leaderboard/{type}."""
    leaderboard_type: str  # "daily", "weekly", "alltime"
    subject_id: str | None = None
    entries: list[LeaderboardEntry]
    total_players: int


class MyRankResponse(BaseModel):
    """Response for GET /leaderboard/{type}/me.

    Per CONTEXT.md:
    - Separate endpoint from main leaderboard
    - Include +/-2 neighbors for context
    - Include distance to next tier
    """
    rank: int
    xp: int
    xp_to_next: int | None  # XP needed to pass player above (None if #1)
    neighbors: list[LeaderboardEntry]
    total_players: int
```

### Dependency Injection
```python
# Source: Follows codebase pattern from api/deps.py
from typing import Annotated
from fastapi import Depends, Request
import redis.asyncio as redis

from fastapi_app.services.leaderboard import LeaderboardService


async def get_leaderboard_service(request: Request) -> LeaderboardService:
    """Get LeaderboardService with Redis from app state."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    return LeaderboardService(redis_client)


LeaderboardServiceDep = Annotated[LeaderboardService, Depends(get_leaderboard_service)]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| ZREVRANGE | ZRANGE with REV | Redis 6.2 | Single unified command, fewer deprecated APIs |
| Separate timestamp field | Composite score | Industry standard | One field handles both ranking and tie-breaking |
| ZRANK for reverse rank | ZREVRANK | Always available | Correct descending order ranking |
| Manual archive jobs | TTL + date-suffixed keys | Best practice | Automatic cleanup, no cron needed |

**Deprecated/outdated:**
- ZREVRANGE: Superseded by ZRANGE with REV option
- ZRANGEBYSCORE: Superseded by ZRANGE with BYSCORE option
- pytz: Superseded by stdlib zoneinfo in Python 3.9+

## Open Questions

Things that couldn't be fully resolved:

1. **Display name and avatar lookup**
   - What we know: CONTEXT.md requires display_name and avatar_url in response
   - What's unclear: Where this data lives (Frappe DocType? Redis cache?)
   - Recommendation: Add player profile cache or batch lookup via Frappe API

2. **Historical archive access**
   - What we know: Daily leaderboard archived for 30 days per requirements
   - What's unclear: How to query archived leaderboards (date parameter?)
   - Recommendation: Keep date-suffixed keys with 30-day TTL; add optional `date` query param

3. **Hot key mitigation at scale**
   - What we know: STATE.md mentions "hourly sharding strategy" for hot key bottlenecks
   - What's unclear: Current user count and whether sharding is needed now
   - Recommendation: Start with single key per leaderboard; add read replicas before sharding

4. **Weekly reset timing edge cases**
   - What we know: Reset Friday midnight Asia/Amman
   - What's unclear: What happens if server restarts during reset? DST transition?
   - Recommendation: Use ISO week keys (2026-W06) which auto-increment; no explicit reset job needed

## Sources

### Primary (HIGH confidence)
- [Redis ZADD Documentation](https://redis.io/docs/latest/commands/zadd/) - Command syntax, options, time complexity
- [Redis ZRANGE Documentation](https://redis.io/docs/latest/commands/zrange/) - Unified range command with REV, BYSCORE, WITHSCORES
- [Redis ZREVRANK Documentation](https://redis.io/docs/latest/commands/zrevrank/) - Rank lookup with WITHSCORE option
- [Redis ZCARD Documentation](https://redis.io/docs/latest/commands/zcard/) - O(1) cardinality
- [Redis ZCOUNT Documentation](https://redis.io/docs/latest/commands/zcount/) - Score range counting
- Codebase: `services/wallet.py` - Asia/Amman timezone pattern, XP award flow
- Codebase: `api/deps.py` - Service dependency injection pattern

### Secondary (MEDIUM confidence)
- [Redis Leaderboards Solution](https://redis.io/solutions/leaderboards/) - Architecture patterns
- [AWS ElastiCache Gaming Leaderboard](https://aws.amazon.com/blogs/database/building-a-real-time-gaming-leaderboard-with-amazon-elasticache-for-redis/) - Production patterns
- [Redis Sorted Sets Best Practices](https://www.dragonflydb.io/guides/redis-sorted-sets-best-practices) - Composite scoring, tie-breaking
- [Leaderboard System Design](https://systemdesign.one/leaderboard-system-design/) - Scalability patterns

### Tertiary (LOW confidence)
- None - all patterns verified against official Redis documentation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Uses existing codebase libraries only (redis-py, Pydantic, zoneinfo)
- Architecture: HIGH - All patterns verified against Redis official documentation
- Pitfalls: HIGH - Derived from CONTEXT.md decisions and Redis documentation

**Research date:** 2026-02-03
**Valid until:** 30 days (stable domain, Redis ZSET API is stable)
