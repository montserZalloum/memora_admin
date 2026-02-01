# Project Research Summary

**Project:** Memora - Gamified Educational Platform Backend
**Domain:** FastAPI Sidecar Integration with Frappe for High-Performance Game Mechanics
**Researched:** 2026-02-01
**Confidence:** HIGH

## Executive Summary

Memora is a gamified education platform targeting Arabic-speaking students. The architecture consists of a FastAPI sidecar (port 8001) handling high-performance game mechanics (sub-20ms responses) alongside an existing Frappe v15 application (port 8000) managing content and admin workflows. The two services share a Redis instance for hot data, with Frappe owning MariaDB persistence and the FastAPI sidecar focused exclusively on read-heavy game state operations.

The recommended approach leverages modern Python async patterns: FastAPI 0.128+ with redis-py 7.1 (using `redis.asyncio`, not abandoned aioredis), PyJWT 2.11 (replacing abandoned python-jose), and orjson for 2-4x JSON serialization speedup. Progress tracking uses Redis bitmaps for O(1) lesson completion checks, sorted sets for leaderboards, and hash structures for wallets (XP, streaks). All game state flows through Redis first, then syncs to MariaDB via Frappe scheduled tasks every 1 minute.

The key architectural risks center on data integrity: Redis-to-MariaDB sync creates a 1-2 minute data loss window requiring AOF persistence configuration; bitmap memory can explode with non-contiguous user IDs (mandate contiguous `bitmap_slot` allocation); and timezone-naive streak calculations will break user engagement. Critical prevention: enable Redis AOF with `appendfsync everysec`, use contiguous bitmap offsets, store user timezones, whitelist JWT algorithms, and implement single-writer locks for the build pipeline to prevent race conditions.

## Key Findings

### Recommended Stack

The Python async ecosystem has matured significantly for high-performance APIs. The critical finding is that **aioredis standalone was abandoned** in December 2021 and merged into redis-py 4.2.0+, requiring developers to use `import redis.asyncio as redis` instead. Similarly, **python-jose was effectively abandoned** 2021-2024 with security concerns, prompting FastAPI documentation to shift recommendations to PyJWT 2.11. The orjson library provides 2-4x faster JSON serialization than the standard library, critical for meeting <20ms response targets.

**Core technologies:**
- **FastAPI 0.128.0**: Native async, automatic OpenAPI docs, Pydantic v2 validation (5-50x faster than v1)
- **redis-py 7.1.0**: Official async client (`redis.asyncio`) with connection pooling; aioredis standalone is ABANDONED
- **PyJWT 2.11.0**: JWT encoding/decoding; python-jose is ABANDONED and has known security issues
- **orjson 3.11.6**: 2-4x faster than standard json, critical for <20ms target
- **Uvicorn 0.40.0 [standard]**: Production ASGI server with uvloop and httptools for maximum async performance
- **pydantic-settings 2.12.0**: Type-safe configuration from environment variables with validation

**Deployment approach:**
- Containerized: Direct uvicorn with `--workers 4` (one per CPU core for async)
- Traditional: Gunicorn 25.0.0 with uvicorn workers for process management
- Worker formula: `workers = CPU_cores` (not 2N+1; async handles concurrency within process)

### Expected Features

Research identified clear feature tiers based on gamified education domain patterns and Memora's existing 31-DocType data model.

**Must have (table stakes):**
- **Lesson completion tracking**: Core educational value; uses bitmap-based progress with O(1) lookups
- **XP accumulation**: Every lesson completion awards XP; Redis HINCRBY on wallet hash
- **Streak tracking**: Duolingo has trained users to expect daily streaks; critical timezone handling required
- **Subscription validation**: Double-Gate pattern (season status + player grants) for paid content access
- **Weekly leaderboard**: Competition drives engagement; Redis sorted sets with sharding for hot-key prevention
- **JWT authentication**: Standard mobile API auth with stateless verification

**Should have (competitive advantage):**
- **Bitmap-based progress**: Memora's innovation—sub-millisecond completion checks vs traditional O(n) queries
- **Excluded bits pattern**: Handle deleted lessons without breaking existing progress data
- **Double-Gate access control**: Season-wide + individual grants enable instant bulk updates
- **Build pipeline with debouncing**: Collect content changes for 2 minutes before building to reduce redundant work
- **Hierarchical JSON structure**: `_h.json` (navigation), `_c.json` (unit content), lesson JSON (stages) for smaller payloads
- **Interaction buffering**: Buffer high-frequency events in Redis lists, batch flush to MariaDB

**Defer (v2+):**
- Friend streaks (requires social graph not in current scope)
- Push notifications (requires Firebase integration)
- League-based competition (needs user base for cohorts)
- Offline support (significant client complexity)
- Anti-cheat system (premature optimization)

**Anti-features (explicitly avoid):**
- Server-time streak calculations (timezone bugs)
- Real-time leaderboard on every request (expensive at scale)
- Sync on every action (unnecessary DB writes)
- Client-side access enforcement (security bypass)
- Per-lesson database access checks (O(n) kills performance)

### Architecture Approach

The architecture follows a **sidecar pattern** where FastAPI handles high-frequency reads and writes to Redis while Frappe manages admin, content, and persistence. The two applications communicate via Redis pub/sub for cache invalidation and share a Redis instance using key prefixes for isolation (`memora:*` vs `frappe:*`).

**Major components:**

1. **FastAPI Sidecar (Port 8001)**: Owns game API endpoints (progress, wallet, leaderboard), session management, hot data layer (Redis reads/writes), access control validation, and interaction buffering. Does NOT own content creation, user registration, or subscription purchases. Never imports `frappe` module to avoid session isolation bugs.

2. **Frappe Application (Port 8000)**: Owns content management (31 DocTypes for curriculum structure), academic structure (Grade, Major, Season), player master data, business logic (Product Grant, Plan Overrider), build queue orchestration, and cold data persistence (MariaDB as source of truth). Does NOT own real-time game state or high-frequency operations.

3. **Redis Shared Instance**: Partitioned by key prefix with clear ownership boundaries. `memora:progress:*` (FastAPI writes, Frappe syncs), `memora:wallet:*` (FastAPI writes, Frappe syncs), `memora:access:*` (Frappe writes, FastAPI reads), `memora:season:*` (Frappe writes, FastAPI reads), `memora:session:*` (FastAPI owns), `memora:leaderboard:*` (FastAPI owns), `memora:buffer:*` (FastAPI writes, Frappe reads for batch sync).

4. **Build Pipeline**: Frappe hooks trigger builds on content changes, debounced for 2 minutes. Generates hierarchy JSON (`_h.json`), bitmap structure (`_b.json`), unit content (`_c.json`), and lesson JSON. Uploads to CDN (mock → R2 swap), publishes invalidation message via Redis pub/sub to FastAPI cache.

5. **Sync Mechanisms**: Frappe scheduled tasks run every 1 minute to sync dirty sets from Redis to MariaDB. Progress bitmaps converted to hex strings, wallet state (XP, streak) updated, interaction buffer flushed to `Memora Interaction Log`. Dirty sets cleared after successful sync.

**Key patterns:**
- **Double-Gate access**: Gate 1 checks season status/expiry (~1ms), Gate 2 checks player access set membership (~1ms)
- **Bitmap progress tracking**: O(1) GETBIT for completion check, SETBIT for marking complete, BITCOUNT for percentage
- **Interaction buffering**: RPUSH to Redis list on every stage completion, batch INSERT during sync to reduce DB load
- **Cache invalidation via pub/sub**: Frappe publishes to `memora:invalidate` channel after builds, FastAPI subscriber clears local cache

### Critical Pitfalls

Research identified 10 critical/moderate pitfalls with high-confidence prevention strategies:

1. **Redis Bitmap Memory Explosion**: Non-contiguous player IDs cause massive memory allocation (user ID 8,000,000 = ~1MB). Setting bit offset 2^32-1 blocks server for 300ms. **Prevention**: Use contiguous `bitmap_slot` column (auto-increment from 0) separate from Frappe document names. Estimate memory: 100K users × 100 subjects × ~125 bytes/bitmap = ~1.25MB.

2. **Redis-to-MariaDB Sync Data Loss**: 1-minute sync interval + default Redis persistence creates 1-2 minute data loss window on crash. **Prevention**: Enable AOF persistence with `appendfsync everysec` (limits loss to ~1 second), implement idempotent sync with `last_synced_ts` tracking, write-ahead pattern for critical XP awards.

3. **JWT Algorithm Confusion Attack**: Attacker changes algorithm from RS256 to HS256, signs with public key, server accepts forged tokens. Complete authentication bypass. **Prevention**: Explicitly whitelist algorithms in PyJWT config, reject "none" algorithm, validate claims (iss, aud, exp), use `verify()` not `decode()`.

4. **Build Pipeline Race Conditions**: 2-minute debounce coalesces triggers but concurrent workers may overwrite CDN files or generate JSON from inconsistent DB state. **Prevention**: Single-writer lock using `SETNX build:lock:{subject_id}`, version stamping in JSON files, atomic CDN updates, FIFO queue ordering per subject.

5. **Streak Timezone Bugs**: Server UTC calculations break streaks for users crossing midnight in their local timezone. User completes lesson at 11:50 PM local (8:50 PM UTC previous day), next day at 12:10 AM local (9:10 PM UTC same day)—server sees "same UTC day, no previous day activity," breaks streak unfairly. **Prevention**: Store user timezone in `Memora Player Profile`, calculate streaks in user-local time, 3-6 hour grace period after midnight, use pytz/zoneinfo for DST handling.

**Additional moderate pitfalls:**
- Leaderboard hot-key problem at 100K users (shard into N sorted sets)
- Excluded bits index drift (never reuse bit indexes, maintain `max_bit_index` counter)
- Device limit bypass via race conditions (atomic check-and-add using Redis transactions)
- FastAPI-Frappe session isolation (clear boundary: FastAPI NEVER imports frappe)
- AOF rewrite disk exhaustion (monitor disk, need 2x AOF size for rewrite)

## Implications for Roadmap

Based on research, the build order must follow strict dependency chains to avoid rework and ensure data integrity from day one.

### Phase 1: Infrastructure Foundation
**Rationale:** All game mechanics depend on Redis access patterns, authentication middleware, and deployment configuration. Building this first prevents cascading changes later.

**Delivers:**
- FastAPI project scaffold with async Redis client (`redis.asyncio`)
- Shared Redis key schema documented (all `memora:*` prefixes)
- Nginx reverse proxy configuration (FastAPI at `/api/v1/`, Frappe at `/`)
- JWT authentication middleware with algorithm whitelisting
- Redis persistence config (AOF with `appendfsync everysec`)
- Connection pooling setup (max 50 connections, decode_responses)

**Addresses:**
- JWT algorithm confusion attack (Pitfall #3)
- AOF persistence configuration (Pitfall #2)
- Redis connection management anti-pattern

**Avoids:**
- Late-stage authentication refactoring
- Redis data loss on crash (configure persistence early)

**Research flag:** Standard patterns, skip research-phase.

---

### Phase 2: Access Control & Season Meta
**Rationale:** Content access validation must work before any progress tracking begins. Double-Gate pattern is foundational security.

**Delivers:**
- Season meta sync (Frappe `on_update` hook → Redis hash `memora:season:{id}`)
- Player access set management (Frappe hooks → `memora:access:{player_id}` sets)
- Double-Gate middleware (season check + player grant check)
- Plan → Subjects mapping in Redis (`memora:plan:{id}:subjects`)
- Access check endpoint for client pre-validation

**Addresses:**
- Subscription validation (table stakes feature)
- Double-Gate access control (differentiator)
- Season expiry mid-lesson (edge case: grace period implementation)

**Avoids:**
- Late-stage access control bolted on (causes permission bypass bugs)

**Research flag:** Custom pattern for Memora, but PRD is detailed. Skip research-phase.

---

### Phase 3: Progress Tracking (Bitmaps)
**Rationale:** Core educational value. Must be implemented with correct memory patterns from day one (refactoring bitmaps with data is expensive).

**Delivers:**
- `bitmap_slot` allocation system (contiguous IDs, auto-increment)
- Lesson completion write (SETBIT `memora:progress:{player}:{subject}`)
- Progress fetch endpoint (GETBIT for single lesson, BITCOUNT for percentage)
- Dirty progress tracking (`memora:dirty:progress` set)
- Unit/Track/Topic rollup calculations (aggregate from lesson bitmaps)

**Addresses:**
- Lesson completion tracking (table stakes)
- Bitmap-based progress (differentiator)
- Bitmap memory explosion (Pitfall #1)

**Avoids:**
- Non-contiguous ID allocation (causes memory explosion)
- Per-lesson database queries (O(n) performance killer)

**Research flag:** Standard Redis bitmap pattern, but memory implications are critical. No additional research needed if following PRD constraints.

---

### Phase 4: Session & Interaction Flow
**Rationale:** Session state enables mid-lesson resume and interaction buffering. Builds on progress tracking.

**Delivers:**
- Session management (start/end lesson endpoints)
- Session state in Redis (`memora:session:{id}` hash with 1-hour TTL)
- Stage completion endpoint (writes to progress bitmap)
- Interaction buffering (RPUSH to `memora:buffer:interactions`)
- Idempotency handling (completion_id deduplication)

**Addresses:**
- Session state persistence (table stakes)
- Interaction buffering (differentiator)
- Multiple concurrent stage completions (edge case)

**Avoids:**
- Missing TTL on session keys (memory leak)
- Duplicate XP awards from retry logic

**Research flag:** Standard pattern, skip research-phase.

---

### Phase 5: Wallet & Gamification (XP, Streaks)
**Rationale:** Depends on session completion flow. Streak logic is complex (timezone handling) and must be correct from day one.

**Delivers:**
- XP calculation (base + heart bonus) on lesson completion
- Wallet update (HINCRBY `memora:wallet:{player}` for XP)
- Streak calculation in user-local timezone (requires timezone storage)
- Streak update logic (compare `last_streak_activity_date`)
- Grace period for streak maintenance (3-6 hours after midnight)
- Wallet fetch endpoint (HGETALL for XP, streak, total lessons)

**Addresses:**
- XP accumulation (table stakes)
- Streak tracking (table stakes)
- Streak timezone bugs (Pitfall #5)

**Avoids:**
- Server-time calculations (breaks streaks unfairly)
- Missing timezone field (requires schema change later)

**Research flag:** Timezone handling is tricky. Consider research-phase for edge cases (DST transitions, timezone changes).

---

### Phase 6: Sync Mechanisms (Redis → MariaDB)
**Rationale:** Game mechanics must work before implementing persistence. Sync is background process that doesn't block gameplay.

**Delivers:**
- Progress sync task (reads dirty set, fetches bitmaps, converts to hex, updates MariaDB)
- Wallet sync task (HGETALL wallets, batch update MariaDB)
- Interaction flush task (LRANGE buffer, batch INSERT, LTRIM)
- Dirty set management (SADD on writes, DEL after sync)
- Sync Log DocType integration
- Idempotent sync with `last_synced_ts`

**Addresses:**
- Data persistence requirement
- Redis-to-MariaDB sync data loss (Pitfall #2)
- Sync job ordering (edge case: streak reset before wallet sync)

**Avoids:**
- Real-time database writes (performance killer at scale)
- Data loss on Redis crash (AOF + regular sync limits window)

**Research flag:** Standard Frappe scheduled task pattern, skip research-phase.

---

### Phase 7: Build Pipeline (Content → CDN)
**Rationale:** Can be developed in parallel with game mechanics. Independent workflow from Frappe content changes to CDN.

**Delivers:**
- Frappe `doc_events.on_update` hooks for content DocTypes
- Build queue with 2-minute debouncing (collect triggers, batch process)
- JSON generation (hierarchy `_h.json`, bitmap structure `_b.json`, content `_c.json`, lesson JSON)
- Bitmap index allocation (`bit_index`, `excluded_bits` for deleted lessons)
- Mock CDN upload interface (swap to R2 in production)
- Pub/sub cache invalidation (publish to `memora:invalidate`)
- Single-writer lock (`SETNX build:lock:{subject_id}`)

**Addresses:**
- Fast content loading (table stakes)
- Build pipeline with debouncing (differentiator)
- Hierarchical JSON separation (differentiator)
- Build pipeline race conditions (Pitfall #4)
- Excluded bits index drift (Pitfall #7)

**Avoids:**
- Concurrent build overwrites
- Reusing bit indexes (causes progress corruption)
- Building unpublished content (edge case: status filter)

**Research flag:** Custom pipeline with debouncing. Consider research-phase for distributed lock patterns and CDN integration.

---

### Phase 8: Leaderboards
**Rationale:** Self-contained feature. Depends on wallet XP updates but doesn't block core functionality.

**Delivers:**
- Leaderboard sorted sets (daily, weekly, monthly, all-time)
- Sharded leaderboards (16 shards to prevent hot-key problem)
- Leaderboard update on lesson completion (ZINCRBY)
- Leaderboard query endpoint (ZREVRANGE for top N, ZREVRANK for player position)
- Reset logic for daily/weekly/monthly boards

**Addresses:**
- Weekly leaderboard (table stakes)
- Multiple timeframes (differentiator)
- Leaderboard hot-key problem (Pitfall #6)

**Avoids:**
- Single sorted set for 100K users (hot-key bottleneck)
- Real-time leaderboard refresh on every request (cache instead)

**Research flag:** Standard Redis sorted set pattern, but sharding is important. Skip research-phase.

---

### Phase 9: Achievement System
**Rationale:** Polish feature. Depends on wallet, progress, and potentially leaderboard data. Can be last.

**Delivers:**
- Achievement evaluation logic (threshold-based: 100 lessons, 7-day streak)
- Achievement types (lessons_completed, streak_days, total_xp, perfect_lesson)
- Achievement unlock tracking (`memora:achievements:{player}` set)
- Achievement notification endpoint (poll for new unlocks)

**Addresses:**
- Threshold-based achievements (differentiator)
- Achievement variety (differentiator)

**Avoids:**
- Real-time WebSocket notifications (defer to v2)

**Research flag:** Standard gamification pattern, skip research-phase.

---

### Phase Ordering Rationale

The suggested order follows three principles from research:

1. **Foundation before features**: Redis data patterns, authentication, and persistence configuration must be correct from day one. Refactoring these with production data is expensive and risky. Infrastructure (Phase 1) addresses critical pitfalls early (JWT algorithm confusion, AOF persistence, connection pooling).

2. **Read before write, access before progress**: Access control (Phase 2) gates all content delivery. Progress tracking (Phase 3) writes data that must be validated against access. Building access first prevents security bypasses. Building progress with correct bitmap patterns (contiguous `bitmap_slot`) prevents Pitfall #1 (memory explosion).

3. **Core game loop before polish**: Phases 3-6 deliver the core gameplay experience (complete lesson → track progress → earn XP/streaks → sync to database). Leaderboards (Phase 8) and achievements (Phase 9) are engagement multipliers but not essential for MVP. Build pipeline (Phase 7) can proceed in parallel as it's independent.

**Dependency chain:**
```
Phase 1 (Infrastructure)
    ↓
Phase 2 (Access Control) ← required by all content endpoints
    ↓
Phase 3 (Progress) ← requires access validation
    ↓
Phase 4 (Sessions) ← writes to progress
    ↓
Phase 5 (Wallet/Streaks) ← triggered by session completion
    ↓
Phase 6 (Sync) ← persists progress + wallet
    ↓
Phase 8 (Leaderboards) ← depends on wallet XP
    ↓
Phase 9 (Achievements) ← depends on wallet + progress

Phase 7 (Build Pipeline) ← independent, can be parallel with 4-6
```

### Research Flags

**Phases needing deeper research during planning:**
- **Phase 5 (Wallet/Streaks)**: Timezone edge cases (DST transitions, user timezone changes mid-month), grace period tuning. Research focus: "streak calculation edge cases in user timezones."
- **Phase 7 (Build Pipeline)**: Distributed lock patterns for single-writer guarantee at scale, CDN swap strategy (mock → R2), debouncing implementation in Frappe scheduler. Research focus: "distributed debounce patterns and CDN abstraction layers."

**Phases with standard patterns (skip research-phase):**
- **Phase 1 (Infrastructure)**: FastAPI + Redis setup is well-documented in official docs
- **Phase 2 (Access Control)**: Double-Gate pattern is custom to Memora but fully specified in PRD
- **Phase 3 (Progress)**: Redis bitmap pattern is standard; memory constraints documented in research
- **Phase 4 (Sessions)**: Standard session management with TTL
- **Phase 6 (Sync)**: Frappe scheduled tasks are standard; dirty set pattern is simple
- **Phase 8 (Leaderboards)**: Redis sorted set leaderboards are well-documented; sharding pattern is standard
- **Phase 9 (Achievements)**: Standard threshold evaluation logic

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | **HIGH** | All technologies verified on PyPI with recent releases (FastAPI 0.128.0 Dec 2025, redis-py 7.1.0 Nov 2025, PyJWT 2.11.0 Jan 2026). Official documentation for all components. Abandonment of aioredis and python-jose confirmed from multiple sources. |
| Features | **MEDIUM-HIGH** | Table stakes features based on industry patterns (Duolingo gamification research, Redis leaderboard solutions). Differentiators are Memora-specific innovations (bitmap progress, excluded_bits, Double-Gate) validated against PRD. Anti-features validated against community pitfalls. |
| Architecture | **MEDIUM-HIGH** | Sidecar pattern, Redis partitioning, and pub/sub cache invalidation are standard distributed system patterns. Frappe-FastAPI integration boundary verified from official Frappe docs and community discussions. Specific Redis key schema is custom but follows best practices. |
| Pitfalls | **HIGH** | Critical pitfalls (bitmap memory, JWT attacks, timezone bugs) verified from official Redis docs, security research, and gamification domain experts. Prevention strategies cross-referenced with multiple sources. Edge cases derived from PRD analysis. |

**Overall confidence:** **HIGH**

The stack choices are production-ready with official documentation. The architecture follows established patterns for async Python APIs with Redis hot data. The pitfalls are well-researched with proven prevention strategies. The main uncertainty is in execution-level details (exact sync intervals, leaderboard shard count tuning) that require load testing, not research.

### Gaps to Address

**During planning:**
- **Exact sync interval tuning**: Research suggests 1 minute, but this needs validation under load. Plan Phase 6 with configurable intervals for A/B testing.
- **Leaderboard shard count**: Research suggests 16 shards for 100K users, but this is an estimate. Plan Phase 8 with parameterized shard count.
- **Grace period for streaks**: Research suggests 3-6 hours, but this affects user behavior. Plan Phase 5 with feature flag for A/B testing.
- **Build debounce interval**: 2 minutes suggested, but may need adjustment based on editor workflows. Plan Phase 7 with configurable debounce.

**During implementation:**
- **Redis memory monitoring**: Set up alerts for bitmap memory usage early in Phase 3 to validate estimates.
- **AOF rewrite disk space**: Monitor during Phase 1 infrastructure setup; provision 2x AOF size.
- **Device limit enforcement**: Test race conditions during Phase 2; validate atomic check-and-add under concurrent load.
- **CDN swap strategy**: Design abstraction layer in Phase 7 but defer actual R2 integration to deployment phase.

**Validation during early phases:**
- **Bitmap slot allocation**: Verify contiguous ID strategy in Phase 3; monitor memory with first 1K users.
- **Timezone handling**: Test streak calculations across timezones in Phase 5; include DST transition dates.
- **Build pipeline locks**: Test concurrent build triggers in Phase 7; verify single-writer guarantee.

## Sources

### Primary (HIGH confidence)

**Stack Research:**
- [FastAPI Official Documentation](https://fastapi.tiangolo.com/) - Lifespan events, dependencies, background tasks
- [redis-py Asyncio Documentation](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html) - Connection pooling, async patterns
- [Redis Bitmaps](https://redis.io/docs/latest/develop/data-types/bitmaps/) - Memory usage, SETBIT performance
- [Redis Sorted Sets](https://redis.io/docs/latest/develop/data-types/sorted-sets/) - Leaderboard patterns
- [PyJWT Documentation](https://pyjwt.readthedocs.io/en/latest/usage.html) - Algorithm enforcement
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) - Configuration management
- PyPI verification for all package versions (FastAPI 0.128.0, Uvicorn 0.40.0, redis 7.1.0, PyJWT 2.11.0, orjson 3.11.6)

**Architecture Research:**
- [Redis Persistence Documentation](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/) - AOF configuration
- [FastAPI Behind a Proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) - Nginx integration
- [Frappe Background Jobs](https://docs.frappe.io/framework/user/en/api/background_jobs) - Scheduled tasks

**Pitfalls Research:**
- [Redis SETBIT Command](https://redis.io/docs/latest/commands/setbit/) - Memory allocation behavior
- [Redis Transactions (WATCH)](https://redis.io/docs/latest/develop/using-commands/transactions/) - Atomic operations
- [JWT Security Best Practices - 42Crunch](https://42crunch.com/7-ways-to-avoid-jwt-pitfalls/) - Algorithm confusion
- [PortSwigger JWT Attacks](https://portswigger.net/web-security/jwt) - Vulnerability patterns

### Secondary (MEDIUM confidence)

**Features Research:**
- [Duolingo Gamification Secrets](https://www.orizon.co/blog/duolingos-gamification-secrets) - Streak engagement data (3.6x), freeze mechanics (21% churn reduction)
- [Trophy.so - How to Build Streaks](https://trophy.so/blog/how-to-build-a-streaks-feature) - Timezone edge cases
- [Redis Leaderboards Solution](https://redis.io/solutions/leaderboards/) - Sorted set patterns
- [TalentLMS - Gamification Mistakes](https://www.talentlms.com/blog/common-gamification-mistakes-avoid/) - Anti-patterns

**Stack Research:**
- [FastAPI Best Practices - zhanymkanov](https://github.com/zhanymkanov/fastapi-best-practices) - Production patterns
- [orjson Benchmarks](https://undercodetesting.com/boost-fastapi-performance-by-20-with-orjson/) - Serialization speedup
- [Gunicorn + Uvicorn Guide](https://medium.com/@iklobato/mastering-gunicorn-and-uvicorn-the-right-way-to-deploy-fastapi-applications-aaa06849841e) - Worker configuration

**Architecture Research:**
- [Redis Pub/Sub Cache Invalidation](https://www.milanjovanovic.tech/blog/solving-the-distributed-cache-invalidation-problem-with-redis-and-hybridcache) - Pattern validation
- [Nginx Reverse Proxy Guide](https://www.getpagespeed.com/server-setup/nginx/nginx-reverse-proxy) - Configuration patterns

**Pitfalls Research:**
- [Trophy.so Timezone Handling](https://trophy.so/blog/handling-time-zones-gamification) - Streak timezone bugs
- [Leaderboard System Design](https://systemdesign.one/leaderboard-system-design/) - Hot-key problem
- [FusionAuth Device Limiting](https://fusionauth.io/docs/extend/examples/device-limiting) - Race conditions

### Tertiary (LOW confidence, needs validation)

- [Frappe FastAPI Discussion](https://discuss.frappe.io/t/fastapi-vs-werkzeug/72785) - Session isolation concerns
- [GitLab Pipeline Race Conditions](https://gitlab.com/gitlab-org/gitlab/-/issues/202691) - Build concurrency
- [Redis Persistence Failure Scenarios](https://medium.com/@sohail_saifi/how-redis-persistence-actually-works-and-when-it-fails-c3715d11529f) - AOF edge cases

### Project-Specific (HIGH confidence)

- Memora PROJECT.md - Requirements and constraints
- Memora DocType schemas - 31 DocTypes, existing data model
- Memora PRD - Double-Gate pattern, bitmap structure, build pipeline spec

---

*Research completed: 2026-02-01*
*Ready for roadmap: yes*
