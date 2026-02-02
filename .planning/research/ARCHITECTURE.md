# Architecture Research: v1.1 Integration

**Domain:** Gamified Educational Platform (Memora)
**Researched:** 2026-02-02
**Confidence:** HIGH

## Executive Summary

The v1.1 features integrate seamlessly with the existing dual-architecture pattern (FastAPI sidecar + Frappe backend). All four new capabilities follow established patterns:

- **Game Sessions**: Redis hash with TTL, extending existing session pattern
- **Leaderboards**: Redis sorted sets (ZADD/ZRANGE), new data structure but standard Redis pattern
- **Device Management**: Redis set with limit checks, similar to access control pattern
- **Scheduled Tasks**: Frappe scheduler hook additions, extending existing sync tasks

No architectural changes required. All features are additive, building on proven v1.0 patterns.

## Component Changes

### FastAPI Sidecar (Port 8001)

**New Services:**

| Service | Purpose | Redis Operations | Dependencies |
|---------|---------|-----------------|--------------|
| **GameSessionService** | Track lesson flow state | HSET, HGET, EXPIRE, DEL | HierarchyService |
| **LeaderboardService** | Manage rankings | ZADD, ZINCRBY, ZREVRANGE, ZRANK | WalletService |
| **DeviceService** | Enforce device limits | SADD, SCARD, SMEMBERS, SREM | SessionService |

**Modified Services:**

| Service | Changes | Reason |
|---------|---------|--------|
| **WalletService** | None required | Leaderboards read XP directly from Redis hash |
| **SessionService** | Optional: Add device_id to session metadata | Track which device owns session |
| **ProgressService** | None required | Game sessions layer on top, no progress changes |

**New Endpoints:**

```
POST   /api/v1/sessions/start         - Create game session
POST   /api/v1/sessions/{id}/stage    - Record stage interaction
POST   /api/v1/sessions/{id}/end      - Finalize session

GET    /api/v1/leaderboards/daily     - Daily XP rankings
GET    /api/v1/leaderboards/all-time  - Total XP rankings
GET    /api/v1/leaderboards/streak    - Streak rankings

POST   /api/v1/devices/register       - Register device on login
GET    /api/v1/devices                - List player's devices
DELETE /api/v1/devices/{id}           - Revoke device
```

### Redis Key Schema

**New Keys:**

| Pattern | Type | TTL | Purpose |
|---------|------|-----|---------|
| `memora:session:{session_id}` | Hash | 2 hours | Session state (lesson, stage, start_ts, interactions) |
| `memora:leaderboard:daily:{date}` | Sorted Set | 48 hours | Daily XP rankings (score: xp, member: player_id) |
| `memora:leaderboard:alltime` | Sorted Set | None | Total XP rankings (synced from wallet hash) |
| `memora:leaderboard:streak` | Sorted Set | None | Current streak rankings (synced from wallet hash) |
| `memora:devices:{player_id}` | Set | None | Authorized device IDs (limit: 3) |
| `memora:device:{device_id}` | Hash | 90 days | Device metadata (player_id, last_seen, device_info) |

**Existing Keys (No Changes):**

- `memora:progress:{user_id}:{subject_id}:v{version}` - Bitmap progress
- `memora:wallet:{player_id}` - XP and streak hash
- `memora:session:{user_id}` - Single-session enforcement (auth)
- `memora:access:{user_id}` - Access grant set
- `memora:season:{season_id}` - Season metadata hash
- `memora:dirty:progress` - Dirty set for sync
- `memora:dirty:wallets` - Dirty set for sync
- `memora:buffer:interactions` - Interaction log buffer

### Frappe Module (Port 8000)

**New DocTypes:**

| DocType | Purpose | Fields |
|---------|---------|--------|
| **Memora Game Session** | Session audit trail | session_id, player, lesson, start_ts, end_ts, stages_completed, status |
| **Memora Leaderboard Entry** | Leaderboard snapshots | date, player, rank, xp, leaderboard_type |

**Modified DocTypes:**

| DocType | Changes | Reason |
|---------|---------|--------|
| **Memora Player Profile** | Add `last_device_sync` timestamp | Track device list freshness |
| **Memora Player Device** | Already exists, no changes | Supports device management |
| **Memora Interaction Log** | Add `session_id` field (optional) | Link interactions to sessions |

**New Frappe Hooks:**

```python
# In memora_admin/hooks.py
scheduler_events = {
    "cron": {
        # Existing (unchanged)
        "* * * * *": [
            "memora_admin.memora_admin.tasks.sync.sync_dirty_progress",
            "memora_admin.memora_admin.tasks.sync.sync_dirty_wallets",
            "memora_admin.memora_admin.tasks.sync.flush_interaction_buffer",
        ],
        "*/2 * * * *": [
            "memora_admin.memora_admin.tasks.build_worker.process_pending_builds"
        ],

        # NEW: Session cleanup (hourly)
        "0 * * * *": [
            "memora_admin.memora_admin.tasks.cleanup.cleanup_expired_sessions"
        ],

        # NEW: Streak reset (daily at midnight Asia/Amman)
        "0 0 * * *": [
            "memora_admin.memora_admin.tasks.streak.reset_broken_streaks"
        ],

        # NEW: Leaderboard snapshot (daily at 11:59 PM)
        "59 23 * * *": [
            "memora_admin.memora_admin.tasks.leaderboard.snapshot_daily_leaderboard"
        ]
    }
}
```

**New Scheduled Tasks:**

| Task | Frequency | Purpose | Implementation |
|------|-----------|---------|----------------|
| `cleanup_expired_sessions` | Hourly (top of hour) | Remove Redis sessions older than 2 hours | Scan `memora:session:*` keys with TTL check |
| `reset_broken_streaks` | Daily (00:00 Asia/Amman) | Reset streaks for players who missed yesterday | Check `streak_date` in wallet hashes, set streak=0 if broken |
| `snapshot_daily_leaderboard` | Daily (23:59 local) | Save top 100 to Leaderboard Entry DocType | ZREVRANGE on daily leaderboard, batch insert |

## Data Flow

### Game Sessions Flow

```
1. Student taps "Start Lesson" in app
   ↓
2. POST /api/v1/sessions/start
   - Creates Redis hash: memora:session:{uuid}
   - HSET fields: lesson, subject, player_id, start_ts
   - EXPIRE 7200 (2 hours TTL)
   ↓
3. Student completes stages
   - POST /api/v1/sessions/{id}/stage for each stage
   - HINCRBY stages_completed
   - LPUSH interaction to buffer (existing pattern)
   ↓
4. Student completes lesson
   - POST /api/v1/sessions/{id}/end
   - Triggers existing /progress/complete endpoint
   - Marks session as "completed" (HSET status=completed)
   - Optional: Sync to Frappe (async job)
   ↓
5. Session expires after 2 hours (Redis TTL)
   - Hourly cleanup task removes stale sessions
   - Audit trail persisted to Memora Game Session DocType
```

**Key Decision:** Sessions are fire-and-forget. No session required to complete lesson (backward compatible). Sessions provide context for analytics, not enforcement.

### Leaderboards Flow

```
1. Student completes lesson → XP awarded
   ↓
2. WalletService.award_xp (existing)
   - HINCRBY memora:wallet:{player_id} xp {amount}
   - Marks dirty for MariaDB sync
   ↓
3. LeaderboardService.update_rankings (NEW)
   - ZADD memora:leaderboard:daily:{today} {new_xp} {player_id}
   - ZADD memora:leaderboard:alltime {total_xp} {player_id}
   - O(log N) operations, sub-millisecond
   ↓
4. Student views leaderboard
   - GET /api/v1/leaderboards/daily
   - ZREVRANGE memora:leaderboard:daily:{today} 0 99 WITHSCORES
   - Returns top 100 with ranks (0-indexed from top)
   ↓
5. Daily snapshot (23:59 cron)
   - Frappe task reads sorted set
   - Batch inserts to Memora Leaderboard Entry
   - Provides historical data for analytics
```

**Redis Commands:**

- **Update rank:** `ZADD leaderboard:daily:{date} {xp} {player_id}` (upsert)
- **Increment XP:** `ZINCRBY leaderboard:daily:{date} {amount} {player_id}` (atomic)
- **Get top N:** `ZREVRANGE leaderboard:daily:{date} 0 99 WITHSCORES`
- **Get player rank:** `ZREVRANK leaderboard:daily:{date} {player_id}`
- **Get player score:** `ZSCORE leaderboard:daily:{date} {player_id}`

**Memory Management:**

- Daily leaderboards: 48-hour TTL (today + yesterday)
- Pruning: ZREMRANGEBYRANK to keep top 10,000 players max
- All-time leaderboard: No TTL, but capped at top 10,000

### Device Management Flow

```
1. Student logs in on new device
   ↓
2. POST /api/v1/auth/login (existing endpoint, modified)
   - Validates credentials (existing)
   - Generates device_id from device_info fingerprint
   - Calls DeviceService.register_device
   ↓
3. DeviceService.register_device
   - SCARD memora:devices:{player_id} → check count
   - If count >= 3: Return 403 "Device limit reached"
   - SADD memora:devices:{player_id} {device_id}
   - HSET memora:device:{device_id} (metadata)
   ↓
4. Student uses app
   - JWT contains device_id claim
   - Middleware validates device_id in set (SISMEMBER)
   - If not in set: Return 401 "Device not authorized"
   ↓
5. Student revokes old device
   - DELETE /api/v1/devices/{device_id}
   - SREM memora:devices:{player_id} {device_id}
   - DEL memora:device:{device_id}
   - Old device gets 401 on next API call
```

**Integration with Auth:**

- Login endpoint modified to call `DeviceService.register_device`
- JWT payload adds `device_id` claim
- Auth middleware validates device_id (new check after user validation)

**Frappe Sync:**

- Device list synced to Memora Player Profile.authorized_devices table
- Sync task: `sync_authorized_devices` (runs every 5 minutes)
- Provides device management UI in Frappe Desk

### Scheduled Tasks Integration

**Existing Pattern:**

Frappe scheduler (hooks.py) runs cron tasks:
- 1-minute: Dirty set sync (progress, wallets, interactions)
- 2-minute: Build worker for content pipeline

**New Tasks:**

| Task | Cron | Frappe Function | Redis Operations |
|------|------|-----------------|------------------|
| Session cleanup | `0 * * * *` | `tasks.cleanup.cleanup_expired_sessions` | SCAN, TTL, DEL |
| Streak reset | `0 0 * * *` | `tasks.streak.reset_broken_streaks` | HGET, HSET on wallet hashes |
| Leaderboard snapshot | `59 23 * * *` | `tasks.leaderboard.snapshot_daily_leaderboard` | ZREVRANGE, batch insert |

**Implementation Files:**

```
memora_admin/memora_admin/tasks/
├── sync.py               (existing - progress, wallets, interactions)
├── build_worker.py       (existing - content builds)
├── cleanup.py            (NEW - session cleanup)
├── streak.py             (NEW - streak resets)
└── leaderboard.py        (NEW - leaderboard snapshots)
```

## Integration Points

### 1. Game Sessions → Progress Tracking

**Connection:** Session end triggers completion flow

```python
# In sessions/endpoints.py
@router.post("/sessions/{session_id}/end")
async def end_session(session_id: str, user: CurrentUser):
    session = await session_service.get_session(session_id)

    # Trigger existing completion endpoint
    complete_request = CompleteRequest(
        subject=session["subject"],
        lesson=session["lesson"]
    )

    # Reuse existing completion logic (idempotent)
    response = await complete_lesson(complete_request, user, ...)

    # Mark session completed
    await session_service.finalize_session(session_id, status="completed")
```

**Benefit:** No duplication of completion logic. Sessions add tracking layer without changing progress mechanics.

### 2. Leaderboards → Wallet Service

**Connection:** Leaderboard updates piggyback on XP awards

```python
# In services/wallet.py (existing WalletService)
async def award_xp(self, player_id: str, amount: int) -> int:
    # Existing logic (unchanged)
    new_total = await self.redis.hincrby(wallet_key, "xp", amount)
    await self.redis.sadd(DIRTY_WALLETS_KEY, player_id)

    # NEW: Update leaderboard (injected dependency)
    if self.leaderboard_service:
        await self.leaderboard_service.update_rankings(player_id, new_total, amount)

    return new_total
```

**Alternative (Cleaner):** Leaderboard service listens to Redis pub/sub on `memora:events:xp_awarded` channel. Decouples services, follows event-driven pattern.

### 3. Device Management → Auth Middleware

**Connection:** JWT validation adds device check

```python
# In middleware/auth.py (existing)
async def verify_token(token: str = Depends(oauth2_scheme)):
    payload = decode_jwt(token)  # Existing
    user_id = payload["sub"]
    device_id = payload.get("device_id")  # NEW claim

    # Existing checks (session family, expiry)
    # ...

    # NEW: Device authorization check
    if device_id:
        is_authorized = await device_service.is_device_authorized(user_id, device_id)
        if not is_authorized:
            raise HTTPException(401, "Device not authorized")

    return CurrentUser(user_id=user_id, device_id=device_id)
```

**Backward Compatibility:** Old tokens without `device_id` claim skip device check. Gradual migration.

### 4. Scheduled Tasks → Frappe Scheduler

**Connection:** New tasks registered in hooks.py scheduler_events

No code changes to scheduler itself. Frappe scheduler automatically discovers and runs tasks based on cron expressions in hooks.py.

**After adding tasks:**

```bash
bench migrate  # Registers new scheduled events
bench restart   # Restarts scheduler worker
```

## Build Order Recommendation

### Phase 1: Device Management (Foundation)

**Rationale:** Simplest feature, no dependencies, establishes device infrastructure for later use.

**Deliverables:**
- DeviceService (Redis set operations)
- Device endpoints (register, list, revoke)
- JWT device_id claim
- Auth middleware device check

**Estimated Effort:** 2 plans
- Plan 1: DeviceService + endpoints
- Plan 2: Auth integration + JWT claims

**Dependencies:** None

---

### Phase 2: Game Sessions (Core Mechanic)

**Rationale:** Builds on existing progress system, needed before leaderboards can show session-level data.

**Deliverables:**
- GameSessionService (Redis hash with TTL)
- Session endpoints (start, stage, end)
- Session cleanup task (hourly cron)
- Memora Game Session DocType

**Estimated Effort:** 3 plans
- Plan 1: GameSessionService + start/stage endpoints
- Plan 2: End session + completion integration
- Plan 3: Cleanup task + Frappe sync

**Dependencies:**
- Progress tracking (v1.0 complete)
- Interaction buffer (v1.0 complete)

---

### Phase 3: Leaderboards (Competitive Feature)

**Rationale:** Depends on wallet XP (v1.0) and benefits from session context (Phase 2).

**Deliverables:**
- LeaderboardService (Redis sorted sets)
- Leaderboard endpoints (daily, all-time, streak)
- XP award integration (update rankings)
- Snapshot task (daily cron)
- Memora Leaderboard Entry DocType

**Estimated Effort:** 3 plans
- Plan 1: LeaderboardService + Redis sorted set operations
- Plan 2: Leaderboard endpoints + rankings
- Plan 3: Snapshot task + historical data

**Dependencies:**
- WalletService (v1.0 complete)
- Optional: Game sessions (for session-level leaderboards in future)

---

### Phase 4: Streak Maintenance (Gamification Polish)

**Rationale:** Final polish for gamification system. Depends on leaderboards to showcase streaks.

**Deliverables:**
- Broken streak detection task (daily cron)
- Streak reset logic (wallet hash updates)
- Streak leaderboard (reuses LeaderboardService)

**Estimated Effort:** 1 plan
- Plan 1: Streak reset task + streak leaderboard

**Dependencies:**
- WalletService (v1.0 complete)
- LeaderboardService (Phase 3)

---

### Total Estimated Effort

**9 plans across 4 phases**

**Timeline:** ~2-3 weeks with parallel work on independent phases.

## Architecture Patterns Applied

### 1. Redis as Source of Truth (Existing Pattern)

**Pattern:** Hot data lives in Redis, syncs to MariaDB on schedule.

**Applied to v1.1:**
- Game sessions: Redis hash (hot) → Memora Game Session DocType (cold)
- Leaderboards: Redis sorted sets (hot) → Memora Leaderboard Entry (cold)
- Devices: Redis set (hot) → Memora Player Profile.authorized_devices (cold)

**Consistency:** Same dirty set pattern used for progress/wallets.

### 2. Service Layer (Existing Pattern)

**Pattern:** Business logic in dedicated service classes, injected via FastAPI dependencies.

**Applied to v1.1:**
- `GameSessionService` - Session lifecycle management
- `LeaderboardService` - Ranking calculations
- `DeviceService` - Device authorization

**Benefits:** Testable, reusable, follows existing ProgressService/WalletService pattern.

### 3. Frappe Hooks (Existing Pattern)

**Pattern:** Doc events for immediate sync, scheduler_events for periodic tasks.

**Applied to v1.1:**
- Hourly cron for session cleanup
- Daily cron for streak reset
- Daily cron for leaderboard snapshot

**Benefits:** Reuses Frappe scheduler, no custom cron management needed.

### 4. TTL for Ephemeral Data (Existing Pattern)

**Pattern:** Redis keys with expiry for self-cleaning data (used for auth sessions).

**Applied to v1.1:**
- Game sessions: 2-hour TTL (lesson duration + grace period)
- Daily leaderboards: 48-hour TTL (keep today + yesterday)
- Device metadata: 90-day TTL (inactive device cleanup)

**Benefits:** Reduces manual cleanup, prevents memory bloat.

## Performance Characteristics

### Game Sessions

| Operation | Redis Command | Complexity | Target |
|-----------|--------------|------------|--------|
| Start session | HSET + EXPIRE | O(1) | <2ms |
| Record stage | HINCRBY | O(1) | <1ms |
| End session | HGET + HSET + DEL | O(1) | <3ms |
| Hourly cleanup | SCAN + TTL + DEL | O(N) | <100ms for 10K sessions |

**Scalability:** 10K concurrent sessions = ~5MB Redis memory (500 bytes per session hash).

### Leaderboards

| Operation | Redis Command | Complexity | Target |
|-----------|--------------|------------|--------|
| Update rank | ZADD | O(log N) | <2ms |
| Increment XP | ZINCRBY | O(log N) | <2ms |
| Get top 100 | ZREVRANGE 0 99 | O(log N + 100) | <5ms |
| Get player rank | ZREVRANK | O(log N) | <2ms |

**Scalability:** 100K players in leaderboard = O(log 100K) = ~17 operations worst case.

**Memory:** 100K players × 40 bytes (member + score) = ~4MB per leaderboard.

### Device Management

| Operation | Redis Command | Complexity | Target |
|-----------|--------------|------------|--------|
| Check authorized | SISMEMBER | O(1) | <1ms |
| Register device | SCARD + SADD | O(1) | <2ms |
| List devices | SMEMBERS | O(N) | <2ms (N=3 max) |
| Revoke device | SREM | O(1) | <1ms |

**Scalability:** O(1) operations, no bottlenecks. Device limit (3) keeps sets tiny.

### Scheduled Tasks

| Task | Frequency | Execution Time | Impact |
|------|-----------|----------------|--------|
| Session cleanup | Hourly | <500ms | Low (SCAN with LIMIT) |
| Streak reset | Daily | <5s | Medium (iterates wallets) |
| Leaderboard snapshot | Daily | <2s | Low (batch insert 100 rows) |

**Optimization:** Streak reset uses dirty set pattern (only check players who earned XP yesterday).

## Anti-Patterns Avoided

### 1. Session State in JWT

**Bad:** Store session data in JWT payload (lesson, stage progress)

**Why Bad:** JWTs are immutable; can't update state without reissuing token.

**Solution:** Separate Redis session hash, JWT only contains session_id reference.

---

### 2. Leaderboard as Database Query

**Bad:** Calculate rankings with SQL ORDER BY on every request

**Why Bad:** O(N log N) query on 100K players = seconds of latency.

**Solution:** Redis sorted sets maintain sorted order (O(log N) updates, O(1) retrieval).

---

### 3. Device List in JWT

**Bad:** Embed authorized device IDs in JWT claims

**Why Bad:** Revoking device requires waiting for JWT expiry (up to 30 days).

**Solution:** Real-time Redis set check on every request (O(1) with SISMEMBER).

---

### 4. Synchronous Session Persistence

**Bad:** Write session to MariaDB on every stage completion

**Why Bad:** Adds 10-50ms database latency to every interaction.

**Solution:** Redis-only sessions with periodic sync (hourly or on session end).

---

### 5. Global Leaderboard Lock

**Bad:** Acquire distributed lock before updating leaderboard

**Why Bad:** Serializes all XP awards, creates bottleneck.

**Solution:** ZADD is atomic; no lock needed for sorted set updates.

## Migration Strategy

### Backward Compatibility

**All v1.1 features are opt-in additions:**

1. **Sessions:** Endpoints are new, existing /progress/complete unchanged
2. **Leaderboards:** New endpoints, no changes to wallet or progress
3. **Devices:** Old tokens without device_id still work (gradual migration)
4. **Tasks:** New cron jobs, existing tasks unchanged

**No breaking changes to v1.0 API.**

### Rollout Plan

**Week 1:** Device management (low risk, independent feature)
**Week 2:** Game sessions (builds on progress, validates session pattern)
**Week 3:** Leaderboards + streak tasks (final gamification layer)

**Feature Flags:** Environment variable `ENABLE_V1_1_FEATURES` controls endpoint registration.

## Sources

### Redis Patterns

- [Redis Leaderboards Official Documentation](https://redis.io/solutions/leaderboards/)
- [Redis Sorted Sets Documentation](https://redis.io/docs/latest/develop/data-types/sorted-sets/)
- [Redis ZADD Command Reference](https://redis.io/commands/zadd/)
- [Redis Sorted Sets Best Practices - DragonflyDB](https://www.dragonflydb.io/guides/redis-sorted-sets-best-practices)
- [Leaderboard System Design - System Design One](https://systemdesign.one/leaderboard-system-design/)
- [Redis Session Management Official](https://redis.io/solutions/session-management/)
- [Redis TTL Command](https://redis.io/commands/ttl/)

### FastAPI Patterns

- [FastAPI Middleware Patterns - Johal.in](https://johal.in/fastapi-middleware-patterns-custom-logging-metrics-and-error-handling-2026-2/)
- [FastAPI Security Guide - David Muraya](https://davidmuraya.com/blog/fastapi-security-guide/)
- [7 FastAPI Security Patterns - Hash Block](https://medium.com/@connect.hashblock/7-fastapi-security-patterns-that-actually-ship-19c52d717668)

### Frappe Scheduler

- [Frappe Scheduler Source Code](https://github.com/frappe/frappe/blob/develop/frappe/utils/scheduler.py)
- [Frappe Background Jobs Documentation](https://docs.frappe.io/framework/v14/user/en/api/background_jobs)
- [Understanding Frappe's Scheduler - Frappe Blog](https://frappe.io/blog/engineering/if-you-wish-to-truly-understand-frappes-scheduler-you-must-first-invent-the-universe)
- [Efficient Job Scheduling in Frappe - Frappe Forum](https://discuss.frappe.io/t/efficient-job-scheduling-in-frappe/133819)

### Codebase Analysis

- Existing v1.0 implementation (ProgressService, WalletService, SessionService)
- Redis key schema from `fastapi_app/core/constants.py`
- Frappe hooks configuration from `memora_admin/hooks.py`
- Sync task patterns from `memora_admin/memora_admin/tasks/sync.py`

---

*Architecture research completed: 2026-02-02*
*Confidence: HIGH - Based on v1.0 codebase analysis + official Redis/Frappe documentation*
