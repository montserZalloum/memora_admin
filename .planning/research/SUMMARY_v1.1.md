# Project Research Summary

**Project:** Memora v1.1 - Feature Expansion
**Domain:** Game sessions, leaderboards, device management, scheduled tasks
**Researched:** 2026-02-02
**Confidence:** HIGH

## Executive Summary

Memora v1.1 extends the existing dual-architecture platform (FastAPI sidecar + Frappe backend) with four feature areas: game sessions for lesson flow tracking, leaderboards for competitive engagement, device management for security, and scheduled tasks for maintenance. Research confirms that **all features can be implemented with zero new dependencies** using the existing validated stack (redis-py, Frappe scheduler, FastAPI). The only recommended change is upgrading redis-py from 5.0.0 to 7.1.0 for performance improvements and Python 3.14 support.

The architecture is well-suited for these additions. Game sessions use Redis hashes with TTL (following existing session patterns), leaderboards use Redis sorted sets (ZADD/ZREVRANGE operations achieve O(log N) performance), device management uses Redis sets (simple 3-device limit enforcement), and scheduled tasks leverage Frappe's existing scheduler hooks. All new features follow the established pattern: Redis as hot data source with eventual consistency sync to MariaDB via dirty sets.

Critical integration risks center on maintaining the sub-20ms performance target while adding stateful features to a system designed for stateless responses. The top pitfalls are: session state memory leaks from missing TTL, leaderboard hot key bottlenecks at scale, device limit race conditions during concurrent logins, Redis-MariaDB consistency gaps during failures, and timezone-naive streak resets. These are integration-specific risks, not generic pitfalls. Prevention strategies include defensive TTL enforcement, leaderboard sharding by hour, atomic device registration with pipeline, and consistent Asia/Amman timezone handling across all code paths.

## Key Findings

### Recommended Stack

**No new dependencies required.** The existing stack (redis-py 5.0+, FastAPI 0.115+, Frappe v15) already provides all required capabilities. Game sessions use SETEX/EXPIRE for TTL-based cleanup, leaderboards use sorted set commands (ZADD, ZREVRANGE, ZINCRBY), device management uses set operations (SADD, SCARD, SISMEMBER), and scheduled tasks use Frappe's built-in cron scheduler.

**Core technologies (unchanged):**
- **redis-py 7.1.0** (upgrade recommended from 5.0.0) — Async sorted set operations, TTL management, connection pooling — Performance improvements, Python 3.14 support, sorted set consistency fixes
- **FastAPI 0.115+** — Sidecar API endpoints with dependency injection — Existing service layer pattern extends cleanly to new features
- **Frappe scheduler** — Cron-based task execution via hooks.py — Already proven in v1.0 for dirty set sync, sufficient for hourly/daily tasks

**Critical version note:** redis-py 7.1.0 adds `score_cast_func` to ZRANK/ZREVRANK for consistency with other sorted set commands, and improves async performance with better lock handling.

**Technologies NOT added:** APScheduler (redundant with Frappe scheduler), Celery (overkill for current scale), fingerprintjs (privacy concerns), redis-om-python (unnecessary abstraction).

### Expected Features

Research confirms clear table stakes vs differentiators for each feature area.

**Must have (table stakes):**
- **Game sessions:** Start/end session endpoints with TTL auto-cleanup (1-hour expiration), session existence check before accepting stage completions, session metadata for analytics
- **Leaderboards:** All-time XP leaderboard (global ranking), daily XP leaderboard with midnight reset, top N retrieval (top 10/50/100), user rank lookup
- **Device management:** Device registration on login with limit enforcement (3 devices), device listing endpoint for self-service, device deauthorization
- **Scheduled tasks:** Daily streak reset at midnight Asia/Amman, session cleanup (hourly), leaderboard snapshot to MariaDB

**Should have (competitive):**
- **Game sessions:** Session recovery on crash (resume lesson progress), concurrent session detection (prevent simultaneous sessions)
- **Leaderboards:** Streak leaderboard (compete on consistency), cached results (5-min cache for top 100), user rank context (show ±2 positions around user)
- **Device management:** Automatic device cleanup (90+ days inactive), concurrent session detection (prevent account sharing)
- **Scheduled tasks:** Redis keyspace notification events (2026 pattern, instant reaction to expiration)

**Defer (v2+):**
- League-based leaderboards (complex cohort logic, requires user base)
- Real-time leaderboard updates via WebSocket (adds complexity)
- Device fingerprinting (privacy concerns, simple device ID sufficient)
- 2FA for new devices (adds friction, defer until security audit)

**Critical anti-patterns to avoid:**
- Leaderboards: Public rankings without privacy controls demotivate low performers (JMIR research, Penn State 2024)
- Game sessions: Session state in client only (crashes lose progress, no validation)
- Device management: Forcing device deauthorization flow (creates friction, auto-remove least recently used instead)
- Scheduled tasks: Polling for expired sessions (use Redis TTL auto-expiration)

### Architecture Approach

All v1.1 features integrate seamlessly with the existing dual-architecture pattern. **No architectural changes required** — features are additive, following established patterns.

**Major components:**
1. **FastAPI Services (new)** — GameSessionService (session lifecycle), LeaderboardService (rankings), DeviceService (authorization) — Follows existing ProgressService/WalletService dependency injection pattern
2. **Redis Key Schema (extensions)** — Session hashes with TTL, sorted sets for leaderboards, sets for device lists — Extends existing key prefixing (memora:*)
3. **Frappe Scheduled Tasks (additions)** — Hourly session cleanup, daily streak reset, daily leaderboard snapshot — Extends existing hooks.py scheduler_events
4. **Frappe DocTypes (new)** — Memora Game Session (audit trail), Memora Leaderboard Entry (historical snapshots) — MariaDB backing for Redis hot data

**Integration patterns applied:**
- **Redis as source of truth:** Hot data in Redis, periodic sync to MariaDB (existing pattern from progress/wallets)
- **Service layer:** Business logic in injectable services (existing pattern)
- **TTL for ephemeral data:** Auto-expiring keys reduce manual cleanup (existing auth session pattern)
- **Dirty set pattern:** Track changed entities for batch sync (existing pattern from v1.0)

**Data flow example (game sessions):**
1. Student starts lesson → POST /api/v1/sessions/start → Redis hash created with 2-hour TTL
2. Student completes stages → POST /api/v1/sessions/{id}/stage → HINCRBY stages_completed
3. Student completes lesson → POST /api/v1/sessions/{id}/end → Triggers existing /progress/complete endpoint
4. Session expires after 2 hours (Redis TTL) → Hourly cleanup task removes stale sessions

**Performance characteristics:**
- Game sessions: HSET/HINCRBY = O(1), <2ms latency
- Leaderboards: ZADD = O(log N), ZREVRANGE = O(log N + M), <5ms for top 100 at 100K users
- Device management: SADD/SISMEMBER = O(1), <2ms latency
- Scheduled tasks: Session cleanup <500ms (SCAN-based), streak reset <5s (dirty set iteration)

### Critical Pitfalls

Top 7 pitfalls with highest risk and impact for Memora v1.1:

1. **Session state memory leaks via missing TTL** — Session keys without proper TTL cause Redis OOM. Prevention: Always set TTL atomically with session creation using pipeline. Add hourly cleanup task as defense. Monitor session key count growth. (Phase 1: Game Sessions)

2. **Leaderboard hot key bottleneck** — Single global leaderboard sorted set creates chokepoint at 100K users (278 writes/sec). In Redis Cluster, all requests hit one node while 98 nodes idle. Prevention: Shard by hour within day (24 keys), aggregate on read with ZUNIONSTORE for top 100. Load test with 1000 concurrent completions, verify p99 <20ms. (Phase 2: Leaderboards)

3. **Device limit race condition on concurrent login** — Two devices login simultaneously, both bypass 3-device limit. Prevention: Use pipeline for atomic SADD + SCARD, rollback if count exceeds limit. Integration test with concurrent registration attempts. (Phase 3: Device Management)

4. **Timezone-naive daily streak reset** — Scheduler runs at UTC midnight (3am Amman) instead of midnight Asia/Amman, breaking user streaks unfairly. Prevention: Use `ZoneInfo("Asia/Amman")` consistently across all code paths. Add 3-hour grace period. Verify server timezone in deployment. (Phase 4: Scheduled Tasks)

5. **Redis persistence gap causing session data loss** — Redis restart loses state if AOF/RDB not configured. Prevention: Document Redis persistence requirements (appendonly yes, appendfsync everysec) in deployment guide. Classify data by criticality: ephemeral (sessions), critical (wallets - backed by MariaDB), hybrid (leaderboards - rebuild from logs). (Phase 0: Pre-Development)

6. **FastAPI Redis connection pool exhaustion** — High concurrency exhausts pool (default 10 connections), causing 503 errors. Prevention: Calculate required pool size (peak_rps × p99_latency × margin), configure max_connections=50. Load test with 1000 concurrent sessions. (Phase 1: Game Sessions)

7. **Background job non-idempotency causing duplicate execution** — Task runs twice (worker restart, network retry), causing duplicate side effects. Prevention: Use idempotency key pattern with Redis (exec_key includes date, check exists before running, mark complete after success). Compare-and-set logic for streak resets. (Phase 4: Scheduled Tasks)

**Moderate pitfalls:**
- Leaderboard rank calculation inefficiency: Avoid N queries for N players, use ZREVRANGE with withscores (derive rank from position)
- Session cleanup performance: Use SCAN not KEYS to avoid blocking Redis
- Date boundary inconsistency: Include date in leaderboard key (auto-switches at midnight)

**Integration-specific risks:**
- Game sessions vs JWT stateless conflict: Include family_id in session data, validate on every operation
- Redis-MariaDB consistency gaps: Leaderboards need rebuild tool from interaction logs for disaster recovery

## Implications for Roadmap

Based on research, suggested phase structure prioritizes foundational features first, then competitive layers.

### Phase 1: Device Management (Foundation)
**Rationale:** Simplest feature with no dependencies on other v1.1 features. Establishes device infrastructure for session tracking. Low complexity (Redis set operations only). Can be developed in parallel with sessions.

**Delivers:** Device registration on login, 3-device limit enforcement, device listing/revocation endpoints, JWT device_id claim, auth middleware device check

**Addresses:**
- Must-have: Device registration, limit enforcement, self-service management
- Security: Prevent account sharing via concurrent session detection

**Avoids:**
- Pitfall #3: Device limit race condition (atomic pipeline registration)
- Anti-pattern: Forcing device deauth flow (auto-remove least recently used)

**Estimated effort:** 2 plans (DeviceService + endpoints, then auth integration)

**Research flag:** Standard pattern, skip phase-specific research. Redis sets well-documented.

---

### Phase 2: Game Sessions (Core Mechanic)
**Rationale:** Core lesson flow tracking. Needed before leaderboards can show session-level analytics. Builds on existing progress system (bitmap, XP, streaks). Integrates with Phase 1 device tracking (optional session.device_id field).

**Delivers:** Start/end session endpoints, session state management (Redis hash with TTL), stage completion tracking, session cleanup task (hourly), Memora Game Session DocType for audit trail

**Addresses:**
- Must-have: Session lifecycle, TTL auto-cleanup, session existence checks
- Should-have: Session recovery on crash, concurrent session detection

**Avoids:**
- Pitfall #1: Session state memory leaks (atomic TTL with pipeline, defensive cleanup)
- Pitfall #6: Connection pool exhaustion (load test, configure max_connections=50)
- Pitfall #11: JWT stateless conflict (include family_id in session, validate on operations)

**Uses stack:** redis-py 7.1.0 (HSET, EXPIRE, HGETALL), FastAPI dependency injection, existing HierarchyService

**Estimated effort:** 3 plans (GameSessionService + start/stage endpoints, end session + completion integration, cleanup task + Frappe sync)

**Research flag:** Standard pattern. Session management well-documented. Possible quick research on session recovery patterns if implementing should-have features.

---

### Phase 3: Leaderboards (Competitive Feature)
**Rationale:** Adds competitive engagement layer. Depends on XP being accumulated (v1.0 ready). Benefits from session context (Phase 2) for future analytics. Architecture decision (sharding strategy) critical upfront.

**Delivers:** LeaderboardService with sorted set operations, daily/all-time/streak leaderboard endpoints, XP award integration (update rankings), snapshot task (daily cron), Memora Leaderboard Entry DocType

**Addresses:**
- Must-have: All-time and daily XP leaderboards, top N retrieval, user rank lookup, midnight reset
- Should-have: Streak leaderboard, cached results (5-min), rank context (±2 positions)
- Anti-pattern: Public rankings without privacy (start with opt-in or private)

**Avoids:**
- Pitfall #2: Leaderboard hot key bottleneck (hourly sharding, ZUNIONSTORE aggregation, load test p99 <20ms)
- Pitfall #7: Rank calculation inefficiency (ZREVRANGE with withscores, derive rank from position)
- Pitfall #10: Date boundary inconsistency (date-in-key strategy)
- Pitfall #12: Redis-MariaDB consistency gaps (implement rebuild tool from interaction logs)

**Implements architecture:** Redis sorted sets (ZADD, ZINCRBY, ZREVRANGE, ZREVRANK), time-based sharding pattern, leaderboard snapshot to MariaDB

**Estimated effort:** 3 plans (LeaderboardService + sorted set operations, leaderboard endpoints + rankings, snapshot task + rebuild tool)

**Research flag:** **Needs deeper research on sharding strategy.** Hourly sharding vs player_id sharding trade-offs. Load testing critical for validation. Otherwise standard Redis sorted set patterns.

---

### Phase 4: Scheduled Tasks (Maintenance & Polish)
**Rationale:** Background maintenance for all v1.1 features. Depends on sessions (cleanup), leaderboards (snapshot), and wallets (streak reset). Implements last after features stabilize. Timezone handling critical for user trust.

**Delivers:** Broken streak detection and reset (daily cron at midnight Asia/Amman), session cleanup task (hourly), leaderboard snapshot task (daily 23:59), task logging to Memora Sync Log

**Addresses:**
- Must-have: Daily streak reset, session cleanup, leaderboard snapshot, task logging
- Should-have: Redis keyspace notification events (2026 pattern for instant expiration reaction)

**Avoids:**
- Pitfall #5: Timezone-naive streak reset (ZoneInfo Asia/Amman, grace period, verify server timezone)
- Pitfall #8: Non-idempotency (idempotency key pattern, compare-and-set logic)
- Pitfall #9: Session cleanup performance degradation (SCAN not KEYS, <10s completion, never block >10ms)

**Uses stack:** Frappe scheduler hooks.py cron, existing dirty set pattern for streak iteration, Redis SCAN for defensive cleanup

**Estimated effort:** 2 plans (streak reset task + idempotency, session cleanup + leaderboard snapshot)

**Research flag:** Standard patterns. Frappe scheduler well-understood (already used in v1.0). Possible quick research on Redis keyspace notifications if implementing should-have feature.

---

### Phase Ordering Rationale

**Dependency-driven sequencing:**
- Device Management (Phase 1) has zero dependencies, can start immediately
- Game Sessions (Phase 2) depend on progress tracking (v1.0 complete) but not other v1.1 features
- Leaderboards (Phase 3) depend on XP accumulation (v1.0) and benefit from session analytics (Phase 2)
- Scheduled Tasks (Phase 4) depend on all features stabilizing (cleanup sessions, reset streaks, snapshot leaderboards)

**Parallelization opportunities:**
- Phase 1 and Phase 2 can develop simultaneously (no cross-dependencies)
- Phase 3 can start before Phase 2 completes (only depends on v1.0 XP)
- Phase 4 waits for Phases 1-3 to stabilize

**Risk mitigation order:**
- Phase 1 establishes atomic patterns (device limit race condition prevention)
- Phase 2 validates TTL enforcement and connection pooling (foundation for scale)
- Phase 3 proves sharding strategy under load (critical architecture decision)
- Phase 4 ensures timezone consistency and idempotency (user trust and reliability)

**Architecture evolution:**
- Phase 1: Extends auth with device claims (minimal change)
- Phase 2: Adds stateful sessions to stateless architecture (careful JWT integration)
- Phase 3: Introduces hot key sharding pattern (first time for sorted sets)
- Phase 4: Consolidates background maintenance patterns (cleanup, reset, snapshot)

**Pitfall prevention sequence:**
- Critical pitfalls addressed in phases where they originate (e.g., session TTL in Phase 1, hot key in Phase 3)
- Integration pitfalls addressed before combining features (e.g., JWT-session conflict in Phase 2 before Phase 4 needs sessions)
- Performance pitfalls validated early (connection pool in Phase 1, sharding load test in Phase 3)

### Research Flags

Phases likely needing deeper research during planning:

- **Phase 3 (Leaderboards):** Sharding strategy trade-off analysis (hourly vs player_id sharding, ZUNIONSTORE performance at scale, Redis Cluster key distribution). Load testing setup for 100K users. Leaderboard rebuild algorithm from MariaDB interaction logs.

Phases with standard patterns (skip research-phase):

- **Phase 1 (Device Management):** Redis sets are well-documented, 3-device limit is standard industry pattern, atomic pipeline operations straightforward.
- **Phase 2 (Game Sessions):** Session management with TTL extensively covered in OWASP and Redis official docs, connection pooling standard FastAPI pattern.
- **Phase 4 (Scheduled Tasks):** Frappe scheduler already proven in v1.0, idempotency patterns well-established, timezone handling clear from research.

**Research confidence by phase:**
- Phase 1: HIGH (verified patterns)
- Phase 2: HIGH (official docs + existing codebase)
- Phase 3: MEDIUM-HIGH (sorted sets documented, but sharding strategy needs load test validation)
- Phase 4: HIGH (scheduler proven, timezone handling verified)

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | No new dependencies. redis-py 7.1.0 upgrade backward compatible. All required capabilities verified in official docs and changelog. |
| Features | HIGH | Table stakes confirmed across JMIR academic research, OWASP security docs, Google Play leaderboard patterns. Anti-patterns verified with Penn State 2024 and gamification trends. |
| Architecture | HIGH | Integration points match existing v1.0 patterns. Redis key schema extensions verified. Service layer follows established dependency injection. Frappe hooks proven. |
| Pitfalls | HIGH | Production incidents documented (redis-py #3678, Spring Session #3183). Race conditions verified (CVE-2026-20921). Timezone issues confirmed (Trophy.so, habit tracker research). |

**Overall confidence:** HIGH

Research sourced from official documentation (Redis, FastAPI, Frappe), academic studies (JMIR, Penn State), production incident reports (GitHub issues, CVEs), and 2026 industry best practices. Multiple sources cross-verified for critical findings (leaderboard demotivation, timezone streak issues, hot key bottlenecks).

### Gaps to Address

**Redis Cluster sharding validation (Phase 3):**
- Research provides hourly sharding pattern recommendation, but actual performance under Memora's load needs validation
- How to handle: Load test in Phase 3 planning with simulated 100K users, measure ZUNIONSTORE latency for 24-key aggregation
- Backup plan: If hourly sharding insufficient, implement player_id sharding with background merge task

**Leaderboard privacy controls (Phase 3):**
- Research flags public rankings as demotivating, but implementation approach unclear (opt-in vs leagues vs private-only)
- How to handle: Consult product requirements during Phase 3 planning, start with private leaderboards (friends-only or top 10 + user rank)
- Research shows league-based cohorts are complex and deferred to v2+

**Redis persistence configuration (Phase 0):**
- Pitfall #5 identifies need for AOF/RDB config, but deployment environment unknown
- How to handle: Document requirements in deployment guide during Phase 1, verify with DevOps before Phase 2 (sessions store ephemeral data)
- Trade-off: Accept max 1-second data loss window with appendfsync everysec

**Session recovery UX (Phase 2 optional):**
- Should-have feature but implementation pattern unclear (show modal? auto-resume? prompt user?)
- How to handle: Defer to Phase 2 planning, reference Duolingo "Continue where you left off" pattern from research
- Consider skipping for v1.1 MVP if timeline tight (sessions auto-expire in 2 hours, user can restart lesson)

**Keyspace notifications for session cleanup (Phase 4 optional):**
- 2026 pattern recommended but requires Redis config change (notify-keyspace-events Ex)
- How to handle: Start with cron-based cleanup (proven pattern), evaluate keyspace notifications post-v1.1 if cleanup becomes bottleneck
- Trade-off: Cron every hour vs instant expiration events (acceptable 1-hour lag for cleanup)

## Sources

### Primary (HIGH confidence)

**Official Documentation:**
- [Redis Sorted Sets Documentation](https://redis.io/docs/latest/develop/data-types/sorted-sets/) — Leaderboard operations, O(log N) complexity
- [Redis EXPIRE Command](https://redis.io/docs/latest/commands/expire/) — TTL mechanics
- [Redis Leaderboards Guide](https://redis.io/solutions/leaderboards/) — Official leaderboard patterns
- [Redis Persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/) — AOF/RDB trade-offs
- [FastAPI Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/) — Dependency injection patterns
- [Frappe Background Jobs](https://docs.frappe.io/framework/user/en/api/background_jobs) — Scheduler documentation
- [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) — TTL, security patterns

**Version Changelogs:**
- [redis-py Releases on GitHub](https://github.com/redis/redis-py/releases) — v7.1.0 sorted set improvements, async performance

**Academic Research:**
- [JMIR Serious Games](https://pmc.ncbi.nlm.nih.gov/articles/PMC8097522/) — Leaderboard demotivation research (2021)
- [Penn State Educational Gaming](https://sites.psu.edu/zaczidik/2024/09/15/leaderboards-in-educational-gaming-striking-a-balance-between-motivation-and-meaningful-learning/) — Balance competition with learning (2024)

**Production Incidents:**
- [redis-py memory leak #3678](https://github.com/redis/redis-py/issues/3678) — High concurrency queuedNewConn accumulation
- [Spring Session TTL Issue #3183](https://github.com/spring-projects/spring-session/issues/3183) — Indexed keys lacking TTL
- [CVE-2026-20921](https://windowsmanagementexperts.com/7-microsoft-intune-best-practices/) — Race condition documentation

### Secondary (MEDIUM confidence)

**Best Practices Guides (2026):**
- [Redis Sorted Sets: 9+ Proven Best Practices - DragonflyDB](https://www.dragonflydb.io/guides/redis-sorted-sets-best-practices) — Sharding, ZREMRANGEBYRANK capping
- [Best Practices for Redis EXPIRE and TTL](https://devops.aibit.im/article/best-practices-redis-expire-ttl) — TTL enforcement patterns
- [Dependency Injection in FastAPI: 2026 Playbook](https://thelinuxcode.com/dependency-injection-in-fastapi-2026-playbook-for-modular-testable-apis/) — Service layer patterns
- [Python Job Scheduling: 2026 Overview](https://research.aimultiple.com/python-job-scheduling/) — Scheduler comparison (APScheduler vs cron)

**Industry Patterns:**
- [Google Play Games Services - Leaderboards](https://developers.google.com/games/services/common/concepts/leaderboards) — Automatic daily/weekly/all-time
- [LootLocker Leaderboard Resets](https://lootlocker.com/blog/leaderboard-resets-rewards) — Reset patterns, cron expressions
- [Duolingo Session Tracking](https://medium.com/@salamprem49/duolingo-streak-system-detailed-breakdown-design-flow-886f591c953f) — Session flow, recovery
- [Trophy.so Time Zones in Gamification](https://trophy.so/blog/handling-time-zones-gamification) — UTC vs local time issues

**Performance Analysis:**
- [Understanding Redis Hotkeys](https://master-spring-ter.medium.com/understanding-redis-hotkeys-bigkeys-and-other-performance-bottlenecks-optimization-strategies-in-7ae47eaa2706) — Hot key bottleneck patterns
- [Deep Dive of BigKey and HotKey Issues](https://dev.to/mrboogiej/deep-dive-of-bigkey-and-hotkey-issues-in-redis-what-they-are-how-to-discover-how-to-handle-4ldl) — Detection and mitigation
- [Leaderboard System Design](https://systemdesign.one/leaderboard-system-design/) — Architecture patterns at scale

**Integration & Consistency:**
- [Ensure Consistency Between Redis and Database](https://betterprogramming.pub/how-to-ensure-the-consistency-between-redis-and-database-62f09de0bdde) — Sync patterns
- [Redis Persistence Explained: AOF & RDB](https://leapcell.medium.com/redis-persistence-explained-aof-rdb-f2c37a7b197b) — Re-execution on restart
- [Idempotency in Distributed Systems](https://dzone.com/articles/importance-of-idempotency-in-distributed-systems) — Duplicate execution prevention

### Codebase Analysis (HIGH confidence)

- Existing v1.0 implementation: ProgressService, WalletService, SessionService patterns
- Redis key schema from `fastapi_app/core/constants.py`
- Frappe hooks configuration from `memora_admin/hooks.py`
- Sync task patterns from `memora_admin/memora_admin/tasks/sync.py`
- Timezone handling from `services/wallet.py` (ZoneInfo Asia/Amman)

---

*Research completed: 2026-02-02*
*Ready for roadmap: yes*
*Total estimated effort: 10 plans across 4 phases (2-3 weeks with parallel work)*
