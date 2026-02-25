# Feature Specification: Redis Hardening

**Feature Branch**: `027-redis-hardening`
**Created**: 2026-02-25
**Status**: Draft
**Input**: User description: "Redis Hardening — Separate Redis instance, AOF persistence, TTL on keys, memory monitoring and buffer safety"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Data Isolation from Frappe Cache Flushes (Priority: P1)

As a platform operator, I need Memora's game data (wallets, progress, dirty sets, interaction buffers) to be stored in a dedicated Redis instance separate from Frappe's cache, so that Frappe operations like `bench clear-cache` or `bench update` cannot wipe Memora data and cause permanent loss of unsynced student progress.

**Why this priority**: This is the foundation for all other hardening measures. Without data isolation, a single Frappe operation can destroy student XP, progress, and interaction history that hasn't been synced to MariaDB. This is a data loss prevention measure.

**Independent Test**: Can be tested by starting the dedicated Memora Redis on port 13001, switching all Memora services to use it, then running `bench clear-cache` on port 13000 and verifying Memora keys survive on 13001.

**Acceptance Scenarios**:

1. **Given** a dedicated Redis instance running on port 13001 with Memora data, **When** an operator runs `bench clear-cache` (which flushes port 13000), **Then** all Memora keys on port 13001 remain intact.
2. **Given** the FastAPI sidecar is configured to use port 13001, **When** students interact with the game API, **Then** all reads and writes target port 13001, not 13000.
3. **Given** Frappe sync tasks (wallet sync, progress sync, interaction flush) are running, **When** they connect to Redis, **Then** they connect to port 13001 via the `redis_memora` config key with a backward-compatible fallback to `redis_cache`.
4. **Given** all FastAPI and Frappe tests, **When** tests execute, **Then** they connect to port 13001 and pass without regression.

---

### User Story 2 - Crash Recovery via AOF Persistence (Priority: P1)

As a platform operator, I need Redis to persist every write to an append-only file on disk, so that if the Redis process crashes or the server loses power, unsynced dirty sets and interaction buffers are recovered on restart rather than permanently lost.

**Why this priority**: Dirty sets and interaction buffers contain data not yet written to MariaDB. Without AOF, a Redis crash means permanent data loss — students lose XP, progress, and interaction history with no recovery path.

**Independent Test**: Can be tested by writing data to Redis, restarting the Redis service, and verifying the data survives the restart.

**Acceptance Scenarios**:

1. **Given** the dedicated Redis instance has `appendonly yes` and `appendfsync everysec`, **When** the Redis process is restarted, **Then** all keys that existed before the restart are recovered from the AOF log.
2. **Given** a student completes a lesson and the wallet dirty set is updated, **When** the Redis process crashes within the next second, **Then** at most 1 second of writes is lost (the `everysec` guarantee).
3. **Given** the Redis instance is running, **When** an operator checks `INFO persistence`, **Then** `aof_enabled:1` is reported.

---

### User Story 3 - Bounded Memory via Key TTLs (Priority: P2)

As a platform operator, I need all cacheable Redis keys to have appropriate time-to-live values, so that inactive student data and stale leaderboards are automatically evicted, preventing unbounded memory growth as the student population scales across seasons.

**Why this priority**: Without TTLs, memory grows linearly with total students ever enrolled (not just active students). This becomes critical at scale (100k+ users). Since all keys self-heal on cache miss, TTL is safe — the next request rebuilds the data from MariaDB.

**Independent Test**: Can be tested by checking that wallet, progress, and access keys have positive TTL values after writes, and that the leaderboard cleanup task removes old daily/weekly keys.

**Acceptance Scenarios**:

1. **Given** a wallet key is created or updated, **When** checking its TTL, **Then** it has a 48-hour TTL (172800 seconds).
2. **Given** a progress bitmap key is created or updated, **When** checking its TTL, **Then** it has a 48-hour TTL.
3. **Given** an access set key is hydrated, **When** checking its TTL, **Then** it has a 24-hour TTL (86400 seconds).
4. **Given** a plan free subjects key is set, **When** checking its TTL, **Then** it has a 12-hour TTL (43200 seconds).
5. **Given** daily leaderboard keys older than 30 days exist, **When** the cleanup task runs at 03:00, **Then** those keys are deleted. Weekly leaderboard keys older than 90 days are also deleted. Alltime leaderboards are never deleted.
6. **Given** a student hasn't used the app for 48 hours, **When** their wallet/progress keys expire, **Then** the next API request triggers `ensure_hydrated()` and rebuilds the data from MariaDB seamlessly.
7. **Given** Lua scripts (streak update, session complete) write to wallet/progress keys, **When** the Lua script executes, **Then** the key TTL is refreshed atomically within the same script.

---

### User Story 4 - Memory Monitoring and Buffer Backlog Detection (Priority: P2)

As a platform operator, I need visibility into Redis memory usage, buffer sizes, and dirty set counts, so that I can detect and respond to problems (buffer backlog, memory pressure, sync falling behind) before they cause data loss or degraded performance.

**Why this priority**: Without monitoring, the interaction buffer can grow without bound if the flush task stalls, eventually consuming all Redis memory. This story provides the safety net for all other changes.

**Independent Test**: Can be tested by calling the health check endpoint and verifying it returns memory stats, buffer length, and dirty set counts. The monitoring task can be verified by checking Frappe error logs for periodic entries.

**Acceptance Scenarios**:

1. **Given** the FastAPI server is running, **When** calling `GET /api/v1/health/redis`, **Then** a JSON response is returned containing: status, used_memory_mb, max_memory_mb, memory_usage_percent, interaction_buffer_length, dirty_wallets_count, dirty_progress_count, and connected_clients.
2. **Given** the interaction buffer has fewer than 50,000 items, **When** the flush task runs, **Then** it processes items in batches of 1,000.
3. **Given** the interaction buffer exceeds 50,000 items, **When** the flush task runs, **Then** it processes items in batches of 5,000 to catch up faster.
4. **Given** the interaction buffer exceeds 10,000 items, **When** the flush task runs, **Then** a CRITICAL log entry is recorded noting the backlog size.
5. **Given** the scheduled monitoring task runs every 5 minutes, **When** Redis memory exceeds 80% of max, **Then** a WARNING log entry is recorded.
6. **Given** the scheduled monitoring task runs, **When** dirty wallet or progress set sizes exceed 1,000, **Then** a WARNING log entry is recorded indicating sync is falling behind.
7. **Given** the Redis health endpoint, **When** called without authentication, **Then** it responds successfully (no auth required — internal health check).

---

### User Story 5 - Production Deployment Guide (Priority: P3)

As a sysadmin deploying Memora to a fresh production server, I need a comprehensive step-by-step guide documenting every configuration step for the Redis hardening setup, so that I can replicate the setup correctly without prior knowledge of the codebase.

**Why this priority**: The production server doesn't exist yet, but when it does, the person setting it up needs clear instructions. This is documentation, not runtime functionality.

**Independent Test**: Can be tested by having a person unfamiliar with the codebase follow the guide on a fresh server and verify all services work.

**Acceptance Scenarios**:

1. **Given** a new section exists at the end of README.md, **When** a sysadmin reads it, **Then** they find step-by-step instructions for: Redis config, systemd service, directory permissions, AOF settings, config file updates, verification steps, migration timing, and monitoring setup.
2. **Given** the deployment guide, **When** checking for environment differences, **Then** a clear table shows which values differ between development and production (maxmemory, batch sizes, etc.).
3. **Given** CLAUDE.md, **When** a developer reads the "Redis Resilience" section, **Then** they understand the dual-Redis architecture (Frappe on 13000, Memora on 13001).

---

### Edge Cases

- What happens if the Memora Redis instance (13001) is not running when FastAPI starts? The health check endpoint should report unhealthy status; API requests should fail gracefully with appropriate error responses.
- What happens if `site_config.json` does not have the `redis_memora` key? The `get_redis()` function falls back to `frappe.conf.redis_cache` for backward compatibility during migration.
- What happens if the leaderboard cleanup task runs when there are no old keys to delete? It completes silently without errors.
- What happens if a wallet key's TTL is refreshed by both `award_xp` and a Lua script in quick succession? The later EXPIRE simply overwrites the earlier one with a fresh 48h window — no conflict.
- What happens if the AOF file grows very large? Redis automatically rewrites/compacts the AOF file periodically. Disk usage should be monitored.
- What happens during the migration window when some services point to 13000 and others to 13001? The spec requires all pending data to be flushed to MariaDB first, then all config changes applied together, then all services restarted. The self-healing `ensure_hydrated()` pattern handles any cache misses.
- What happens if the interaction buffer grows beyond 50,000 items even with 5,000 batch size? The monitoring task logs CRITICAL warnings every 5 minutes, alerting the operator. The system continues processing at maximum batch rate.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST run a dedicated Redis instance on port 13001, separate from Frappe's Redis on port 13000.
- **FR-002**: System MUST configure the dedicated Redis with `maxmemory-policy volatile-ttl` to protect keys without TTL from eviction.
- **FR-003**: System MUST configure the dedicated Redis with `maxmemory 128mb` for the development server.
- **FR-004**: System MUST enable AOF persistence with `appendonly yes` and `appendfsync everysec` on the dedicated Redis instance.
- **FR-005**: System MUST update the FastAPI `.env` file to use `redis://127.0.0.1:13001` as the Redis URL.
- **FR-006**: System MUST add a `redis_memora` key to `site_config.json` with value `redis://127.0.0.1:13001`.
- **FR-007**: All Frappe sync tasks and event handlers that access Memora Redis data MUST read from the `redis_memora` config key, with fallback to `redis_cache` for backward compatibility.
- **FR-008**: Wallet keys MUST have a 48-hour TTL, refreshed on every write (including Lua scripts).
- **FR-009**: Progress bitmap keys MUST have a 48-hour TTL, refreshed on every write (including Lua scripts).
- **FR-010**: Access set keys MUST have a 24-hour TTL, refreshed on hydration.
- **FR-011**: Plan free subjects keys MUST have a 12-hour TTL.
- **FR-012**: A scheduled task MUST clean up daily leaderboard keys older than 30 days and weekly leaderboard keys older than 90 days. Alltime leaderboards MUST NOT be cleaned up.
- **FR-013**: The leaderboard cleanup task MUST also clean up subject-filtered variants (e.g., `daily:{date}:subject:*`).
- **FR-014**: The `flush_interaction_buffer` task MUST dynamically increase batch size from 1,000 to 5,000 when the buffer exceeds 50,000 items.
- **FR-015**: The `flush_interaction_buffer` task MUST log a CRITICAL warning when the buffer exceeds 10,000 items.
- **FR-016**: System MUST provide a `GET /api/v1/health/redis` endpoint returning memory usage, buffer length, dirty set counts, and client connections.
- **FR-017**: The Redis health endpoint MUST NOT require authentication.
- **FR-018**: A scheduled monitoring task MUST run every 5 minutes, logging Redis memory usage, buffer length, dirty set sizes, and total key count.
- **FR-019**: The monitoring task MUST log WARNING when memory exceeds 80% of max, and WARNING when dirty set sizes exceed 1,000.
- **FR-020**: All test configurations (FastAPI tests and Frappe sync tests) MUST be updated to use port 13001.
- **FR-021**: The dedicated Redis instance MUST be managed via a systemd service with automatic restart on failure.
- **FR-022**: A production deployment guide MUST be added to README.md documenting all setup steps with a dev/production configuration comparison table.
- **FR-023**: CLAUDE.md MUST be updated to document the dual-Redis architecture.

### Key Entities

- **Memora Redis Instance**: A dedicated Redis server on port 13001 with AOF persistence, volatile-ttl eviction policy, and 128mb memory limit (dev). Stores all Memora game state separately from Frappe's cache.
- **Redis Config**: Configuration file at `/etc/redis/redis-memora.conf` defining the instance's behavior.
- **Systemd Service**: Service unit at `/etc/systemd/system/redis-memora.service` managing the Redis lifecycle.
- **Redis Health Report**: A JSON object returned by the health endpoint containing memory, buffer, and dirty set metrics.
- **Leaderboard Cleanup Policy**: Rules defining retention periods — 30 days for daily leaderboards, 90 days for weekly, indefinite for alltime.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frappe cache flush operations (`bench clear-cache`, `bench update`) have zero impact on Memora game data — no student data loss events after deployment.
- **SC-002**: After a Redis restart, all previously stored keys are recovered within 5 seconds (AOF replay).
- **SC-003**: Inactive student cache entries (wallet, progress, access) are automatically evicted within their TTL windows (48h, 48h, 24h respectively), keeping memory usage proportional to active users rather than total users.
- **SC-004**: Operators can detect buffer backlog issues within 5 minutes via the monitoring task and health endpoint.
- **SC-005**: The interaction buffer flush task can process up to 5,000 items per minute under backlog conditions, preventing unbounded memory growth.
- **SC-006**: All existing tests pass without regression after the Redis port migration.
- **SC-007**: A sysadmin unfamiliar with the codebase can replicate the Redis setup on a fresh production server by following the deployment guide without additional assistance.
- **SC-008**: The maximum data loss window in the event of a Redis crash is 1 second of writes (the `appendfsync everysec` guarantee).

## Assumptions

- The development server has sufficient disk space for AOF files (Redis AOF auto-compacts periodically).
- The `redis-server` binary is installed at `/usr/bin/redis-server` and the `redis` user/group exists on the system.
- Port 13001 is not in use by any other service on the development server.
- The Frappe site config at `sites/x.conanacademy.com/site_config.json` supports custom keys (standard Frappe behavior).
- 128mb is sufficient for the development server's current student population.
- The `volatile-ttl` eviction policy is appropriate because all important keys without TTL (dirty sets, interaction buffer) should never be evicted, while cache keys with TTL can be safely evicted.
- Leaderboard retention of 30 days (daily) and 90 days (weekly) provides sufficient historical data for student engagement analysis.
