# Pitfalls Research: v1.1 Features

**Domain:** Gamified educational platform - game sessions, leaderboards, device management, scheduled tasks
**Project:** Memora Admin (FastAPI + Redis + MariaDB)
**Researched:** 2026-02-02
**Confidence:** HIGH

## Executive Summary

Adding game sessions, leaderboards, device management, and scheduled tasks to Memora introduces **critical integration risks** with the existing high-performance Redis architecture. The most dangerous pitfalls relate to:

1. **Session state memory leaks** - Missing TTL on session keys causes Redis OOM
2. **Leaderboard hot key bottlenecks** - Single global leaderboard creates performance chokepoint at 100K users
3. **Race conditions in device limit enforcement** - Concurrent logins bypass 3-device limit
4. **Redis-MariaDB consistency gaps** - Background sync loses data during failures
5. **Timezone-naive streak resets** - UTC vs Asia/Amman misalignment breaks user trust

These aren't generic pitfalls - they're **integration-specific risks** when adding stateful features to a platform designed for sub-20ms stateless responses with 1-minute eventual consistency.

**Critical recommendation:** Phase 1 must establish session cleanup + leaderboard sharding patterns before Phase 2 adds complexity.

---

## Critical Pitfalls

### Pitfall 1: Session State Memory Leaks via Missing TTL

**Risk:** Session keys without proper TTL accumulation causing Redis memory exhaustion
**Impact:** Redis OOM kills, cascading failures, data loss for active users
**Likelihood:** HIGH (production incidents documented in 2026)

**What goes wrong:**
- Start lesson creates `memora:game_session:{session_id}` with data but forgets TTL
- Player never completes lesson (app crash, network loss, abandonment)
- Session remains in Redis forever
- At 100K concurrent users with 20% abandonment: 20K zombie sessions/day = memory exhaustion in weeks

**Production evidence:**
Recent go-redis v9.17.2 memory leak with high concurrency showed massive queuedNewConn accumulation leading to memory growth ([GitHub Issue #3678](https://github.com/redis/redis-py/issues/3678)). Spring Session experienced similar issue where indexed keys lacked TTL, causing indefinite persistence ([Spring Session Issue #3183](https://github.com/spring-projects/spring-session/issues/3183)). In one production case, only 2% of items had TTL when they should have ([Redis Memory Mystery](https://www.nfq.com/blog/redis-the-memory-usage-mystery)).

**Warning signs:**
- Redis memory usage grows linearly over days
- `redis-cli --bigkeys` shows large session key count
- `MEMORY USAGE memora:game_session:*` reveals accumulated sessions
- Monitoring shows session creation rate > completion rate long-term

**Prevention:**
```python
# WRONG - no TTL
await redis.hset(f"memora:game_session:{session_id}", mapping=data)

# RIGHT - TTL on creation
await redis.hset(f"memora:game_session:{session_id}", mapping=data)
await redis.expire(f"memora:game_session:{session_id}", 3600)  # 1 hour

# BETTER - atomic with pipeline
pipeline = redis.pipeline()
pipeline.hset(f"memora:game_session:{session_id}", mapping=data)
pipeline.expire(f"memora:game_session:{session_id}", 3600)
await pipeline.execute()
```

**Additional safeguards:**
1. **Hourly cleanup task** - SCAN for sessions older than TTL threshold, delete explicitly
2. **Monitoring alert** - Track session key count, alert if growth > 10% daily
3. **Defensive expiry** - Set longer backup TTL (24h) even with manual cleanup
4. **Test coverage** - Assert TTL exists after session creation in integration tests

**Phase:** Phase 1 (Game Sessions) - Must be correct from day 1

**Memora-specific note:**
Existing `SessionService` uses TTL correctly (`ex=ttl_days * 24 * 3600`). Game sessions must follow same pattern. Verify FastAPI lifespan doesn't prevent TTL registration on startup.

---

### Pitfall 2: Leaderboard Hot Key Bottleneck

**Risk:** Single global leaderboard sorted set creates performance chokepoint under high concurrency
**Impact:** Response times degrade from <20ms to >200ms, single Redis node overwhelmed, uneven cluster load
**Likelihood:** HIGH (documented at 100K+ user scale)

**What goes wrong:**
- Daily XP leaderboard uses single key: `memora:leaderboard:daily_xp`
- Every lesson completion triggers `ZADD memora:leaderboard:daily_xp {player_id} {xp}`
- At 100K concurrent users with 10 lessons/hour average = 1M ZADD/hour = 278 writes/second
- In Redis Cluster, key hash determines shard - all requests hit ONE node
- Other 98 nodes in cluster sit idle while one node maxes out CPU
- Reads (`ZREVRANGE` for top 100) compete with writes on same node

**Production evidence:**
"When you centralize access to a few pieces of data, you create a hot-key problem. In a cluster of 99 nodes, if a single key gets a million requests per second, all million requests go to a single node" ([Redis Hot Key Performance](https://master-spring-ter.medium.com/understanding-redis-hotkeys-bigkeys-and-other-performance-bottlenecks-optimization-strategies-in-7ae47eaa2706)). "HotKeys usually have long CPU run time which deteriorates Redis performance and affects other requests" ([BigKey and HotKey Issues](https://dev.to/mrboogiej/deep-dive-of-bigkey-and-hotkey-issues-in-redis-what-they-are-how-to-discover-how-to-handle-4ldl)).

**Warning signs:**
- `redis-cli --hotkeys` shows leaderboard key dominates access
- `MONITOR` output (run 2-3 seconds) shows repeated ZADD to same key
- Redis `slowlog` shows ZREVRANGE taking >10ms
- Single Redis node CPU at 90%+ while others <20%
- Response time percentiles: p50 OK, p99 terrible (queuing behind hot key)

**Prevention strategies:**

**Strategy 1: Time-based sharding (recommended for Memora)**
```python
# Shard by hour within day
hour = datetime.now(AMMAN_TZ).hour
key = f"memora:leaderboard:daily_xp:{hour}"
await redis.zadd(key, {player_id: xp})

# Aggregate on read (ZUNIONSTORE for top 100 across 24 shards)
temp_key = f"memora:leaderboard:daily_xp:merged:{uuid4()}"
await redis.zunionstore(temp_key, [f"memora:leaderboard:daily_xp:{h}" for h in range(24)])
top_100 = await redis.zrevrange(temp_key, 0, 99, withscores=True)
await redis.delete(temp_key)
```

**Strategy 2: Write sharding with periodic merge**
```python
# Shard writes by player_id hash
shard = hash(player_id) % 10  # 10 shards
key = f"memora:leaderboard:daily_xp:shard_{shard}"
await redis.zadd(key, {player_id: xp})

# Background task merges shards every 5 minutes into read-optimized master
```

**Recommendation for Memora:**
Use **Strategy 1** (hourly sharding) for v1.1 - simple, no background merge complexity, acceptable 24-key aggregation cost for top 100 queries.

**Phase:** Phase 2 (Leaderboards) - Critical for architecture, not bolt-on

**Performance target verification:**
Load test with 1000 concurrent lesson completions. Measure p99 latency for both ZADD (write) and leaderboard read. Must stay <20ms.

---

### Pitfall 3: Device Limit Race Condition on Concurrent Login

**Risk:** Two devices login simultaneously, both bypass 3-device limit, player ends up with 4+ devices
**Impact:** Business rule violation, revenue loss (device sharing), user confusion
**Likelihood:** MEDIUM (concurrent login rare but possible, especially on app update day)

**What goes wrong:**
```python
# NON-ATOMIC check-then-set pattern
devices = await get_authorized_devices(player_id)  # Returns 2 devices
if len(devices) < 3:  # Both requests see 2 devices, both pass check
    await add_device(player_id, new_device_id)  # Both add, result = 4 devices
```

**Scenario timeline:**
```
T0: Player has 2 devices registered
T1: Device A login starts - reads 2 devices
T2: Device B login starts - reads 2 devices (A's write not committed yet)
T3: Device A adds itself (total = 3)
T4: Device B adds itself (total = 4) ❌
```

**Production evidence:**
CVE-2026-20921 documented race condition in SMB Server due to improper synchronization ([Windows CVE](https://windowsforum.com/threads/cve-2026-20921-smb-server-race-condition-privilege-escalation-and-mitigation.396784/)). General guidance: "Use a mutex to ensure only one thread can access shared resources at a time" and "ensure sensitive endpoints make state changes atomic using datastore's concurrency features" ([Race Condition Best Practices](https://www.techtarget.com/searchstorage/definition/race-condition)).

**Warning signs:**
- Player support tickets: "I can't add my new phone, but I only have 2 devices registered"
- Device count audits show 3.2% of players exceed 3 devices
- Logs show simultaneous `device_registered` events with same timestamp
- Redis `SCARD memora:devices:{player_id}` returns 4+ for some players

**Prevention (recommended for Memora):**
```python
# Use SET instead of LIST for O(1) membership check
key = f"memora:devices:{player_id}"

# Atomic add with cardinality enforcement
pipeline = redis.pipeline()
pipeline.sadd(key, device_id)  # Idempotent - duplicate = no-op
pipeline.scard(key)
results = await pipeline.execute()

device_count = results[1]
if device_count > 3:
    # Rollback - remove the device we just added
    await redis.srem(key, device_id)
    raise DeviceLimitExceeded(f"Maximum 3 devices allowed, found {device_count}")
```

**Phase:** Phase 3 (Device Management) - Must be atomic from start

**Testing strategy:**
```python
# Integration test - simulate concurrent logins
async def test_concurrent_device_registration():
    player_id = "test_player"
    await add_device(player_id, "device_1")
    await add_device(player_id, "device_2")

    # Attempt to register 2 devices concurrently (should only allow 1)
    tasks = [
        add_device(player_id, "device_3"),
        add_device(player_id, "device_4"),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Verify exactly one succeeded, one failed
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert await redis.scard(f"memora:devices:{player_id}") == 3
```

---

### Pitfall 4: Redis Persistence Gap Causing Session Data Loss

**Risk:** Redis restart loses all session state because AOF/RDB not configured correctly for ephemeral data
**Impact:** All active game sessions lost, players return to stale state, streak data inconsistent
**Likelihood:** MEDIUM (depends on Redis deployment config, not code)

**What goes wrong:**
- Game sessions stored in Redis with `memora:game_session:{id}` keys
- Redis crashes or maintenance restart occurs
- **If using RDB only:** Snapshots every 5 minutes, lose last 5 minutes of sessions
- **If using AOF with fsync=everysec:** Lose last 1 second of writes
- **If no persistence:** Lose everything, fallback to MariaDB but 1-minute sync lag means stale data

**Production evidence:**
"RDB is NOT good if you need to minimize chance of data loss - you'll usually create RDB snapshot every 5 minutes or more, so you should be prepared to lose the latest minutes of data if Redis stops working" ([Redis Persistence Guide](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)). "AOF re-executes commands to rebuild state upon restart" but "asynchronous replication between leader and replica means outage before data reaches replica results in stale or missing data" ([Redis Persistence Explained](https://leapcell.medium.com/redis-persistence-explained-aof-rdb-f2c37a7b197b)).

**Memora-specific impact:**
1. **Game sessions:** Players in middle of lesson lose progress since last stage completion
2. **Wallets (XP/streak):** Dirty set exists (`memora:dirty:wallets`) but if Redis dies before sync, XP lost
3. **Leaderboards:** Daily XP leaderboard resets to last RDB snapshot (unfair rankings)
4. **Device sessions:** JWT tokens still valid but `memora:session:{user_id}` gone → 401 errors

**Warning signs:**
- Redis config shows `save ""` (RDB disabled) and `appendonly no` (AOF disabled)
- Redis info shows `rdb_last_save_time` more than 5 minutes ago
- After Redis restart, users report losing XP or streak increments
- Error logs spike with "session not found" after Redis maintenance

**Prevention (recommended for Memora):**
```conf
# redis.conf
appendonly yes
appendfsync everysec  # Compromise: lose max 1 second, good performance
aof-use-rdb-preamble yes  # Faster restarts: RDB snapshot + AOF since snapshot
```

**Tiered data classification:**
```python
# Critical data: MariaDB source of truth, Redis is cache
# - Player wallet: Sync every 1 minute (existing), accept 1-min loss window
# - Progress bitmaps: Sync every 1 minute (existing)

# Ephemeral data: Redis only, acceptable loss
# - Game sessions: TTL 1 hour, user can restart lesson
# - Rate limits: Reset on Redis restart is acceptable

# Leaderboards: Hybrid
# - Daily leaderboard: Rebuild from MariaDB interaction logs if Redis lost
# - All-time leaderboard: Backed by MariaDB analytics aggregate (existing)
```

**Recommendation for Memora:**
1. **Phase 1:** Document Redis persistence requirements in deployment guide
2. **Phase 2:** Add leaderboard rebuild from MariaDB interaction logs (scheduled task)
3. **Phase 4:** Implement daily task to snapshot critical Redis keys to MariaDB backup table

**Phase:** Phase 0 (Pre-Development) - DevOps requirement, not code fix

---

### Pitfall 5: Timezone-Naive Daily Streak Reset

**Risk:** Daily streak reset runs at midnight UTC instead of midnight Asia/Amman, breaking streaks unfairly
**Impact:** User trust destroyed, engagement drops, support tickets surge
**Likelihood:** HIGH (default cron/scheduler uses UTC)

**What goes wrong:**
```python
# WRONG - uses system time (likely UTC)
import datetime
if datetime.datetime.now().hour == 0:
    reset_broken_streaks()  # Runs at UTC midnight = 3am Amman time

# User in Jordan completes lesson at 11:45pm Amman time (8:45pm UTC)
# Scheduler hasn't run yet (midnight UTC = 3am Amman)
# User's streak_date is yesterday's date
# Streak increments correctly

# Scheduler runs at UTC midnight (3am Amman)
# Checks all players: "If streak_date < today (Amman), reset streak to 0"
# User's streak_date is yesterday, reset to 0 ❌
# User wakes up at 6am, sees streak = 0, rage quits
```

**Timeline example:**
```
User in Amman (UTC+3):
- 11:00pm Amman (8pm UTC): Completes lesson, streak = 5, streak_date = "2026-02-01"
- 11:59pm Amman (8:59pm UTC): Expects streak maintained
- 12:00am UTC (3am Amman): Scheduler runs
  - Checks if streak_date < "2026-02-02" (Amman today)
  - "2026-02-01" < "2026-02-02" → RESET ❌
- 6:00am Amman: User opens app, streak = 0, support ticket: "I just played last night!"
```

**Production evidence:**
"GitHub and many platforms track streaks in UTC, causing confusion. To a server in UTC, midnight marks boundary, but to a user in Melbourne, midnight UTC is 11 AM" ([Trophy Streaks Guide](https://trophy.so/blog/handling-time-zones-gamification)). "Traveling across timezones causes streaks to vanish - 5 out of 7 top-rated habit apps exhibited streak resets within 12 hours of simulated transatlantic travel" ([Time Zone Streak Issues](https://www.alibaba.com/product-insights/why-is-my-ai-habit-tracker-resetting-streaks-after-timezone-changes-during-travel.html)).

**Warning signs:**
- Support tickets: "My streak was reset but I played yesterday evening"
- Tickets cluster around 3am-6am Amman time
- Logs show `streak_reset` event timestamps at UTC midnight (3am Amman)
- User retention drops 15% every Monday (people notice weekend streaks broken)

**Prevention (Memora already handles this correctly in WalletService):**
```python
# wallet.py - CORRECT timezone handling
from zoneinfo import ZoneInfo
AMMAN_TZ = ZoneInfo("Asia/Amman")

def get_amman_today() -> str:
    return datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")
```

**But scheduled task MUST use same timezone:**
```python
# RIGHT - Explicit Amman timezone
def daily_streak_reset():
    """Reset broken streaks. Runs at midnight Asia/Amman."""
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Amman")).strftime("%Y-%m-%d")
    # ...
```

**Grace period recommendation:**
```python
# Add 3-hour grace period for late completions
def should_reset_streak(streak_date: str) -> bool:
    """Check if streak should be reset (missed >1 day)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    AMMAN_TZ = ZoneInfo("Asia/Amman")
    now = datetime.now(AMMAN_TZ)
    grace_cutoff = now - timedelta(hours=27)  # 1 day + 3 hour grace

    streak_dt = datetime.strptime(streak_date, "%Y-%m-%d").replace(tzinfo=AMMAN_TZ)
    return streak_dt < grace_cutoff
```

**System-level fix (deployment requirement):**
```bash
# Set server timezone to Asia/Amman
timedatectl set-timezone Asia/Amman
```

**Recommendation for Memora:**
1. **Phase 4:** Verify deployment server timezone is Asia/Amman
2. **Phase 4:** Add grace period (3-6 hours) to reduce "midnight edge case" anxiety
3. **Testing:** Mock system time in tests to verify midnight boundary logic

**Phase:** Phase 4 (Scheduled Tasks) - Critical for user trust

---

### Pitfall 6: FastAPI Redis Connection Pool Exhaustion Under Load

**Risk:** High concurrency exhausts Redis connection pool, causing request failures
**Impact:** 503 errors, cascading failures, <20ms target violated
**Likelihood:** MEDIUM (depends on pool configuration vs actual load)

**What goes wrong:**
```python
# Redis pool configured with max_connections=10
redis_pool = redis.ConnectionPool(max_connections=10)

# Under normal load: 50 req/sec, avg response 15ms → ~1 connection in use
# Spike load: 500 req/sec, p99 response 80ms → 40 concurrent connections needed
# Pool exhausted at 10 connections → requests wait in queue → timeout → 503
```

**Production evidence:**
"ConnectionError with redis.asyncio.ConnectionPool in FastAPI/uvicorn - a very small and fast asyncio FastAPI service that needs to read from Redis can give random errors under heavy load" ([redis-py Issue #3230](https://github.com/redis/redis-py/issues/3230)). "Connection exhaustion occurs when all available connections in the database's configured maximum are in use" ([Database Connection Exhaustion](https://leapcell.io/blog/understanding-and-mitigating-database-connection-exhaustion-in-high-concurrency-web-applications)).

**Warning signs:**
- Error logs: `ConnectionError: Error while reading from socket: (104, 'Connection reset by peer')`
- Redis `INFO clients` shows `connected_clients` near `maxclients` limit
- FastAPI response time p99 spikes during traffic bursts
- `asyncio` warnings: "Task was destroyed but it is pending"

**Prevention:**

**Calculate required pool size:**
```python
# Formula: max_connections = (peak_rps × p99_latency_seconds) × safety_margin
# Example: (500 rps × 0.08s) × 1.5 = 60 connections
```

**Configure pool correctly:**
```python
# fastapi_app/main.py
redis_pool = redis.ConnectionPool.from_url(
    settings.REDIS_URL,
    max_connections=50,  # Verify sufficient for load
    decode_responses=True,
    socket_keepalive=True,
    socket_timeout=5,
    retry_on_timeout=True,
    health_check_interval=30,
)
```

**Recommendation for Memora:**
1. **Phase 1:** Load test with 1000 concurrent sessions, measure pool usage
2. **Phase 1:** Set `max_connections = 50` (safer than current if unknown)
3. **Phase 4:** Add connection pool monitoring to scheduled health check task

**Phase:** Phase 1 (Game Sessions) - Prevent early, before scale-up

---

## Moderate Pitfalls

### Pitfall 7: Leaderboard Rank Calculation Inefficiency

**Risk:** Using `ZRANK` for every player's rank becomes O(N) bottleneck at 100K players
**Impact:** API response times spike to >100ms for rank queries
**Likelihood:** HIGH (naive implementation will hit this)

**What goes wrong:**
```python
# WRONG - N queries for N players
async def get_top_100_with_ranks():
    members = await redis.zrevrange("leaderboard", 0, 99)
    ranks = []
    for player_id in members:
        rank = await redis.zrevrank("leaderboard", player_id)  # 100 round-trips!
        ranks.append((player_id, rank + 1))
    return ranks
```

**Prevention:**
```python
# RIGHT - Single query with scores, derive rank from position
async def get_top_100_with_ranks():
    members_scores = await redis.zrevrange("leaderboard", 0, 99, withscores=True)
    return [
        {"player_id": member, "rank": idx + 1, "xp": score}
        for idx, (member, score) in enumerate(members_scores)
    ]
```

**Phase:** Phase 2 (Leaderboards) - Optimize from start

---

### Pitfall 8: Background Job Non-Idempotency Causing Duplicate Execution

**Risk:** Scheduled task runs twice (worker restart, network retry) causing duplicate side effects
**Impact:** Streak reset happens twice, XP deducted twice, leaderboard corrupted
**Likelihood:** MEDIUM (Frappe scheduler is reliable, but not impossible)

**Production evidence:**
"Failures and retries can lead to duplicate job execution. Non-idempotent jobs could lead to issues like overbilling customers or sending duplicate notifications" ([Idempotency in Distributed Systems](https://dzone.com/articles/importance-of-idempotency-in-distributed-systems)). "A SaaS company with non-idempotent background jobs was losing $15,000 monthly in duplicate payment processing fees" ([Background Job Pitfalls](https://yogeshbhandari.com.np/blog/common-background-job-and-queue-pitfalls-that-kill-performance-and-how-to-fix-them/)).

**Prevention:**

**Pattern 1: Idempotency key with Redis**
```python
def daily_streak_reset():
    """Reset broken streaks (idempotent via execution key)."""
    from datetime import date

    # Execution key includes date - ensures once per day
    exec_key = f"memora:task_execution:streak_reset:{date.today()}"

    # Check if already executed today
    if await redis.exists(exec_key):
        logger.info("Streak reset already executed today, skipping")
        return

    # Mark as executing (with short TTL in case of crash)
    await redis.set(exec_key, "running", ex=300)  # 5 min timeout

    try:
        # Perform task
        reset_count = perform_streak_resets()

        # Mark as complete (with 25-hour TTL to prevent re-run until next day)
        await redis.set(exec_key, f"complete:{reset_count}", ex=90000)
        logger.info(f"Streak reset complete: {reset_count} players")
    except Exception as e:
        # Delete execution key so task can retry
        await redis.delete(exec_key)
        raise
```

**Pattern 2: Compare-and-set logic**
```python
def reset_broken_streaks():
    """Reset streaks only if actually broken (idempotent)."""
    today = get_amman_today()
    yesterday = get_amman_yesterday()

    for player_id in get_active_players():
        wallet_key = f"memora:wallet:{player_id}"
        streak_date = await redis.hget(wallet_key, "streak_date")

        # IDEMPOTENT CHECK: Only reset if streak_date is stale
        if streak_date and streak_date < yesterday:
            await redis.hset(wallet_key, "streak", 0)
            await redis.hset(wallet_key, "streak_date", today)
            logger.info(f"Reset broken streak for {player_id}")
```

**Recommendation for Memora:**
Use **Pattern 1** (idempotency key) for daily tasks, **Pattern 2** (compare-and-set) for hourly cleanup.

**Phase:** Phase 4 (Scheduled Tasks) - Critical for reliability

---

### Pitfall 9: Session Cleanup Task Performance Degradation

**Risk:** Hourly session cleanup scans millions of keys, blocks Redis for seconds
**Impact:** API requests queue behind cleanup, violating <20ms target
**Likelihood:** MEDIUM (depends on session volume, SCAN implementation)

**What goes wrong:**
```python
# WRONG - blocks Redis with KEYS command
def cleanup_expired_sessions():
    """Remove expired sessions (hourly task)."""
    # KEYS is O(N) and blocks Redis!
    session_keys = await redis.keys("memora:game_session:*")  # ❌ NEVER use KEYS

    for key in session_keys:
        ttl = await redis.ttl(key)
        if ttl == -1:  # No TTL set (pitfall #1)
            await redis.delete(key)
```

**Prevention:**

**Use SCAN instead of KEYS (recommended)**
```python
async def cleanup_expired_sessions():
    """Remove expired sessions using non-blocking SCAN."""
    cursor = 0
    deleted_count = 0
    scan_pattern = "memora:game_session:*"

    while True:
        # SCAN returns iterator, doesn't block Redis
        cursor, keys = await redis.scan(cursor, match=scan_pattern, count=100)

        for key in keys:
            ttl = await redis.ttl(key)

            # Check if TTL is missing (shouldn't happen if pitfall #1 prevented)
            if ttl == -1:
                await redis.delete(key)
                deleted_count += 1
                logger.warning(f"Deleted session without TTL: {key}")

        # Cursor returns to 0 when iteration complete
        if cursor == 0:
            break

    logger.info(f"Session cleanup complete: {deleted_count} sessions deleted")
    return deleted_count
```

**Recommendation for Memora:**
Use SCAN-based defensive cleanup - simple, relies on TTL doing the work, cleanup only catches bugs.

**Phase:** Phase 4 (Scheduled Tasks) - Implement with SCAN from start

**Performance target:**
Cleanup task should complete in <10 seconds even with 100K sessions, never block Redis for >10ms.

---

### Pitfall 10: Leaderboard Date Boundary Inconsistency

**Risk:** Daily leaderboard switches at midnight but user sees yesterday's leaderboard until cache expires
**Impact:** User confusion, unfair rankings displayed briefly
**Likelihood:** MEDIUM (requires cache invalidation strategy)

**What goes wrong:**
```
11:59pm Amman: User checks leaderboard
- Reads from `memora:leaderboard:daily_xp:2026-02-01`
- Top player: Alice with 5000 XP

12:00am Amman: Midnight strikes
- System should switch to `memora:leaderboard:daily_xp:2026-02-02` (empty)
- But API still returns cached response from 11:59pm
- User completes lesson at 12:01am, earns 100 XP
- Checks leaderboard again: Still shows yesterday's board with Alice at top
```

**Prevention:**

**Include date in Redis key (recommended)**
```python
def get_daily_leaderboard_key() -> str:
    """Get leaderboard key for current day."""
    today = datetime.now(AMMAN_TZ).strftime("%Y-%m-%d")
    return f"memora:leaderboard:daily_xp:{today}"

# Automatically switches at midnight
@router.get("/leaderboard/daily")
async def get_daily_leaderboard():
    key = get_daily_leaderboard_key()  # ← Key changes at midnight
    top_100 = await redis.zrevrange(key, 0, 99, withscores=True)
    return {"date": today, "rankings": top_100}
```

**Recommendation for Memora:**
Use date-in-key strategy (already implemented by hourly sharding). Add archival task in Phase 4 for historical leaderboard viewing.

**Phase:** Phase 2 (Leaderboards) - Design decision, Phase 4 for archival

---

## Integration-Specific Pitfalls

### Pitfall 11: Game Session State vs JWT Stateless Architecture Conflict

**Risk:** Game sessions introduce server-side state that contradicts JWT stateless design philosophy
**Impact:** Token invalidation doesn't cascade to sessions, zombie sessions accumulate
**Likelihood:** MEDIUM (architectural inconsistency, not a bug)

**The tension:**
```
JWT stateless auth (existing):
- Token contains all needed data (user_id, family_id, exp)
- No server-side session lookup required
- Token revocation handled via family_id (session service)

Game sessions (new):
- Server-side state in Redis: lesson_id, stage_progress, timestamp
- Session_id is separate from JWT token
- Requires lookup: GET session_id → retrieve state

Conflict scenario:
1. Player logs in → JWT issued, family_id stored in SessionService
2. Player starts lesson → game session created (session_id in Redis)
3. Player logs in from new device → family_id changes (old JWT invalidated)
4. Old JWT now returns 401 Unauthorized ✓
5. But game session still exists in Redis ❌
```

**Prevention:**

**Include family_id in session (recommended for Memora)**
```python
# Store family_id in game session
async def start_lesson(player_id: str, lesson_id: str, token_claims: dict) -> str:
    session_id = str(uuid.uuid4())

    key = f"memora:game_session:{session_id}"
    await redis.hset(key, mapping={
        "player_id": player_id,
        "lesson_id": lesson_id,
        "family_id": token_claims["family_id"],  # ← Bind to token
        "created_at": int(datetime.utcnow().timestamp()),
    })
    await redis.expire(key, 3600)

    return session_id

# Validate family_id on every session operation
async def complete_stage(session_id: str, stage_idx: int, token_claims: dict):
    """Complete stage in session (validates token family)."""
    key = f"memora:game_session:{session_id}"
    session_data = await redis.hgetall(key)

    if not session_data:
        raise SessionNotFoundError(f"Session {session_id} not found")

    # Verify token family matches session
    session_family_id = session_data.get("family_id")
    if session_family_id != token_claims["family_id"]:
        # Session was created with old token, invalidate
        await redis.delete(key)
        raise SessionInvalidatedError("Session invalidated by new login")

    # Continue with stage completion
```

**Phase:** Phase 1 (Game Sessions) - Architectural decision upfront

---

### Pitfall 12: Redis-MariaDB Consistency Gap During Failures

**Risk:** Redis updated but sync to MariaDB fails, causing permanent data loss
**Impact:** XP earned but not persisted to MariaDB, leaderboard rankings inconsistent
**Likelihood:** LOW (dirty set prevents this, but edge cases exist)

**What goes wrong:**
```
Existing v1.0 architecture (correct):
1. User completes lesson
2. FastAPI updates Redis: SETBIT progress, HINCRBY xp
3. FastAPI adds player_id to DIRTY_WALLETS_KEY set
4. Background sync (every 1 minute): reads dirty set, syncs to MariaDB
5. On success: SREM player_id from dirty set

Edge case failure scenario:
1. User completes lesson at 10:00:00
2. Redis updated: xp = 1000
3. Added to dirty set ✓
4. Sync runs at 10:01:00
5. MariaDB write starts: UPDATE xp = 1000
6. MariaDB write hangs (network issue, deadlock, timeout)
7. Sync task times out at 10:01:30
8. Sync task REMOVES player from dirty set anyway ❌
9. Retry never happens
10. MariaDB has old xp = 900, Redis has xp = 1000
```

**Existing protection (v1.0):**
Memora already uses dirty set pattern correctly - only removes from set AFTER successful sync.

**But new pitfalls in v1.1:**
- **Leaderboards:** Not backed by MariaDB yet, Redis is source of truth
- **Game sessions:** Ephemeral, no MariaDB backup
- **Device list:** Need to verify if backed by MariaDB

**Prevention:**

**Write-ahead log for leaderboards (new in v1.1)**
```python
# On XP award, log to both Redis leaderboard AND MariaDB interaction log
async def award_xp_and_update_leaderboard(player_id: str, xp: int):
    """Award XP with dual-write to Redis and MariaDB."""
    # Update Redis (fast path)
    new_total = await wallet_service.award_xp(player_id, xp)
    await redis.zadd("memora:leaderboard:daily_xp", {player_id: new_total})

    # Log to MariaDB (async, eventual consistency)
    frappe.enqueue(
        "memora_admin.tasks.log_xp_change",
        player_id=player_id,
        xp_amount=xp,
        new_total=new_total,
        timestamp=datetime.utcnow(),
    )

# Rebuild leaderboard from MariaDB logs (disaster recovery)
async def rebuild_daily_leaderboard_from_logs(date: str):
    """Rebuild leaderboard from interaction logs (recovery tool)."""
    interactions = frappe.get_all(
        "Memora Interaction Log",
        filters={"date": date, "xp_earned": [">", 0]},
        fields=["player", "xp_earned"],
    )

    # Aggregate XP per player
    xp_by_player = defaultdict(int)
    for interaction in interactions:
        xp_by_player[interaction.player] += interaction.xp_earned

    # Rebuild Redis sorted set
    key = f"memora:leaderboard:daily_xp:{date}"
    await redis.delete(key)  # Clear corrupted data

    for player_id, total_xp in xp_by_player.items():
        await redis.zadd(key, {player_id: total_xp})

    logger.info(f"Rebuilt leaderboard for {date}: {len(xp_by_player)} players")
```

**Device list backed by MariaDB (recommended)**
```python
# Memora Player Profile already has "authorized_devices" child table (existing DocType)
# Use it as source of truth

async def register_device(player_id: str, device_id: str):
    """Register device with dual-write."""
    # Check limit in Redis
    redis_key = f"memora:devices:{player_id}"
    device_count = await redis.scard(redis_key)

    if device_count >= 3:
        raise DeviceLimitExceeded()

    # Add to Redis
    await redis.sadd(redis_key, device_id)

    # Add to MariaDB (source of truth)
    profile = frappe.get_doc("Memora Player Profile", player_id)
    profile.append("authorized_devices", {
        "device_id": device_id,
        "registered_at": datetime.now(),
    })
    profile.save()
```

**Recommendation for Memora:**
1. **Phase 1:** Verify existing sync error handling (already correct in v1.0)
2. **Phase 2:** Implement leaderboard rebuild tool for disaster recovery
3. **Phase 3:** Use existing Frappe child table for device management

**Phase:** Phase 2 (Leaderboards) for WAL, Phase 3 (Devices) for MariaDB backing

---

## Sources

### Redis Session Management
- [Production War Story: Killing Redis to Save Session Affinity](https://medium.com/@chopra.kanta.73/production-war-story-killing-redis-to-save-session-affinity-ef8dc011e7f2)
- [Top 10 Redis Mistakes Killing Performance](https://medium.com/@techInFocus/top-10-redis-mistakes-that-are-killing-your-apps-performance-72a7326907c7)
- [Redis Best Practices - High Performance](https://www.dragonflydb.io/guides/redis-best-practices)
- [Memory leak in go-redis v9.17.2](https://github.com/redis/go-redis/issues/3678)
- [Spring Session Index Keys TTL Issue](https://github.com/spring-projects/spring-session/issues/3183)
- [Redis Memory Usage Mystery](https://www.nfq.com/blog/redis-the-memory-usage-mystery)

### Leaderboards & Hot Keys
- [Leaderboards | Redis Official](https://redis.io/solutions/leaderboards/)
- [Redis Sorted Sets Best Practices](https://www.dragonflydb.io/guides/redis-sorted-sets-best-practices)
- [Understanding Redis Hotkeys and Performance Bottlenecks](https://master-spring-ter.medium.com/understanding-redis-hotkeys-bigkeys-and-other-performance-bottlenecks-optimization-strategies-in-7ae47eaa2706)
- [Deep Dive of BigKey and HotKey Issues](https://dev.to/mrboogiej/deep-dive-of-bigkey-and-hotkey-issues-in-redis-what-they-are-how-to-discover-how-to-handle-4ldl)
- [Leaderboard System Design](https://systemdesign.one/leaderboard-system-design/)

### Device Management & Race Conditions
- [Mobile Device Management Mistakes to Avoid](https://blog.scalefusion.com/mobile-device-management-mistakes-that-you-must-avoid/)
- [Common MDM Challenges](https://jumpcloud.com/blog/mobile-device-management-challenges)
- [CVE-2026-20921: SMB Server Race Condition](https://windowsforum.com/threads/cve-2026-20921-smb-server-race-condition-privilege-escalation-and-mitigation.396784/)
- [Race Condition Best Practices](https://www.techtarget.com/searchstorage/definition/race-condition)

### Background Jobs & Idempotency
- [Idempotency in Distributed Systems](https://dzone.com/articles/importance-of-idempotency-in-distributed-systems)
- [Why Idempotence Matters for Durable Systems](https://temporal.io/blog/idempotency-and-durable-execution)
- [Common Background Job Pitfalls](https://yogeshbhandari.com.np/blog/common-background-job-and-queue-pitfalls-that-kill-performance-and-how-to-fix-them/)
- [Ensuring Job Deduplication](https://www.schedo.dev/blog/job-deduplication)

### Redis-Database Consistency
- [How to Ensure Consistency Between Redis and Database](https://betterprogramming.pub/how-to-ensure-the-consistency-between-redis-and-database-62f09de0bdde)
- [Three Ways to Maintain Cache Consistency](https://redis.io/blog/three-ways-to-maintain-cache-consistency/)
- [Redis Cluster Eventual Consistency](https://www.geeksforgeeks.org/system-design/does-redis-have-eventual-consistency/)

### Redis Persistence
- [Redis Persistence Documentation](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
- [Redis Persistence Explained: AOF & RDB](https://leapcell.medium.com/redis-persistence-explained-aof-rdb-f2c37a7b197b)
- [How Redis Persistence Actually Works](https://medium.com/@sohail_saifi/how-redis-persistence-actually-works-and-when-it-fails-c3715d11529f)

### Connection Pool & Concurrency
- [FastAPI Redis ConnectionError Issue](https://github.com/redis/redis-py/issues/3230)
- [Database Connection Exhaustion in High-Concurrency Apps](https://leapcell.io/blog/understanding-and-mitigating-database-connection-exhaustion-in-high-concurrency-web-applications)
- [Redis Python Connection Pool Best Practices](https://www.pythontutorials.net/blog/redis-py-when-to-use-connection-pool/)

### Timezone & Streaks
- [Implementing a Daily Streak System](https://tigerabrodi.blog/implementing-a-daily-streak-system-a-practical-guide)
- [Handling Time Zones in Gamification](https://trophy.so/blog/handling-time-zones-gamification)
- [Why Habit Tracker Resets Streaks After Timezone Changes](https://www.alibaba.com/product-insights/why-is-my-ai-habit-tracker-resetting-streaks-after-timezone-changes-during-travel.html)

### JWT & Stateless Auth
- [JWTs vs Sessions Authentication](https://stytch.com/blog/jwts-vs-sessions-which-is-right-for-you/)
- [Stop Using JWT for Authentication: The Stateless Myth](https://deoxy.dev/blog/stop-using-jwt-for-auth/)
- [JWT Token Revocation Challenges](https://github.com/OWASP/ASVS/issues/1790)

---

**Research completed:** 2026-02-02
**Confidence level:** HIGH (verified with production incidents, 2026 sources)
**Downstream:** Use in roadmap creation for v1.1 milestone (phase ordering, research flags)
