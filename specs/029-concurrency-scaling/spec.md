# Feature Specification: 100k Concurrency Scaling Optimizations

**Feature Branch**: `029-concurrency-scaling`
**Created**: 2026-02-27
**Status**: Draft
**Input**: PRD for scaling FastAPI sidecar to handle 100k concurrent users

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Platform handles 100k concurrent users without degradation (Priority: P1)

As a platform operator, I need the system to serve 100,000 concurrent student sessions without response time degradation, connection pool exhaustion, or dropped requests, so students can use the app during peak school hours.

**Why this priority**: This is the core business-critical requirement. If the platform can't handle load, no other feature matters.

**Independent Test**: Deploy to staging with production-equivalent settings and verify health checks, progress lookups, and WebSocket connections remain responsive under simulated load.

**Acceptance Scenarios**:

1. **Given** 100k concurrent users making progress requests, **When** the Redis connection pool is sized per production settings, **Then** no requests fail due to connection pool exhaustion.
2. **Given** a student with 500 lessons in a subject, **When** they request their progress, **Then** the response completes within the existing <20ms performance target.
3. **Given** the system is under peak load, **When** a new student opens the app, **Then** their request is served without queueing behind unrelated users' WebSocket operations.

---

### User Story 2 - Operations team tunes scaling parameters without code changes (Priority: P1)

As a DevOps engineer, I need all scaling-related values (connection pool sizes, rate limits, timeouts, concurrency settings) to be configurable via environment variables, so I can tune the system for different deployment environments without rebuilding or modifying code.

**Why this priority**: Environment-specific tuning is essential for safely deploying scaling changes. Development must keep lightweight defaults while production gets scaled-up values.

**Independent Test**: Set different values for scaling settings via environment variables, restart the service, and verify the new values are reflected in behavior (e.g., pool size logged at startup, rate limit headers reflect configured limits).

**Acceptance Scenarios**:

1. **Given** no scaling environment variables are set (development), **When** the app starts, **Then** it uses conservative default values (e.g., 20 Redis connections, 100 req/60s rate limit).
2. **Given** production environment variables are set, **When** the app starts, **Then** it uses the production values (e.g., 200 Redis connections, 500 req/60s rate limit).
3. **Given** an invalid or missing environment variable, **When** the app starts, **Then** it falls back to the default value without crashing.

---

### User Story 3 - Progress data retrieval scales with subject size (Priority: P1)

As a student using the platform, I need my lesson completion progress to load quickly regardless of how many lessons are in my subject, so the app feels responsive even for large subjects (500+ lessons).

**Why this priority**: The current approach sends one command per lesson to check completion. For large subjects this amplifies into hundreds of individual operations, creating a critical performance bottleneck at scale.

**Independent Test**: Request progress for a subject with 500 lessons and verify it completes in a single data fetch instead of 500 individual lookups.

**Acceptance Scenarios**:

1. **Given** a student has a subject with 500 lessons, **When** they request their progress, **Then** the system fetches the entire bitmap in one operation instead of 500 individual bit lookups.
2. **Given** an empty progress bitmap (new student), **When** progress is requested, **Then** an empty result is returned without errors.
3. **Given** a student with sparse completions (5 out of 500 lessons), **When** progress is requested, **Then** exactly those 5 completed bits are returned correctly.

---

### User Story 4 - WebSocket broadcasts don't block other operations (Priority: P2)

As a student receiving real-time notifications, I need message delivery to happen in parallel across connections, so one slow client connection doesn't delay notifications to other connected devices or block the notification pipeline.

**Why this priority**: Sequential message sends create a cascading delay problem under load. One slow client can stall the entire notification pipeline.

**Independent Test**: With multiple WebSocket connections active, verify that a slow/unresponsive connection doesn't delay message delivery to other connections.

**Acceptance Scenarios**:

1. **Given** a user has 3 active connections and broadcast concurrency is enabled, **When** a notification is sent, **Then** all connections receive the message concurrently (not sequentially).
2. **Given** one connection is slow/unresponsive, **When** a broadcast happens, **Then** other connections receive their messages without waiting for the slow one.
3. **Given** development mode (concurrency disabled), **When** messages are sent, **Then** sequential behavior is preserved for easier debugging.

---

### User Story 5 - WebSocket connection management scales across users (Priority: P2)

As one of 100k concurrent users with an active WebSocket connection, I need connection management to not serialize across unrelated users, so my connect/disconnect operations don't contend with other users' operations.

**Why this priority**: A single global lock for all connection operations becomes a bottleneck under high connection churn from 100k users.

**Independent Test**: Verify that connect/disconnect operations for different users don't contend on the same lock.

**Acceptance Scenarios**:

1. **Given** users A and B are connecting simultaneously, **When** both call connect, **Then** neither blocks the other (per-user locking eliminates cross-user contention).
2. **Given** a user disconnects their last connection, **When** the connection is cleaned up, **Then** the per-user lock is also cleaned up to prevent memory leaks.
3. **Given** rapid connect/disconnect cycles from many users, **When** the system is under churn, **Then** operations complete without global serialization.

---

### User Story 6 - Progress summary loads all subjects in parallel (Priority: P2)

As a student viewing my dashboard, I need the progress summary (listing all my subjects with completion percentages) to load quickly, even if I'm enrolled in 8+ subjects, so the dashboard feels responsive.

**Why this priority**: Sequential per-subject lookups multiply latency linearly with the number of subjects. Parallelizing them reduces wall-clock time to approximately the latency of a single lookup.

**Independent Test**: Request the progress summary for a student enrolled in 8 subjects and verify all subjects are fetched concurrently rather than sequentially.

**Acceptance Scenarios**:

1. **Given** a student is enrolled in 8 subjects, **When** they request the progress summary, **Then** all 8 subjects are fetched in parallel (not 16 sequential data lookups).
2. **Given** one subject's data is unavailable, **When** the summary is built, **Then** other subjects still return their results while the missing one is skipped gracefully.

---

### User Story 7 - Rate limiter behavior is configurable for outages (Priority: P3)

As a platform operator, I need the rate limiter's behavior during data store outages to be configurable (pass-through in development, reject in production), so development isn't blocked by restarts while production stays protected.

**Why this priority**: Important for production resilience but lower priority than core scaling fixes since the current pass-through behavior already works for development.

**Independent Test**: Simulate data store unavailability and verify the middleware either passes requests through or returns a service unavailable response based on configuration.

**Acceptance Scenarios**:

1. **Given** the data store is unreachable and fail-open is enabled, **When** a request arrives, **Then** it passes through without rate limiting (with a warning log).
2. **Given** the data store is unreachable and fail-open is disabled, **When** a request arrives, **Then** a service unavailable response is returned with a Retry-After header.

---

### User Story 8 - Upstream API client handles production load (Priority: P3)

As a backend service, the upstream HTTP client needs configurable connection limits and timeouts, so it can handle the increased connection volume from 100k users performing operations that require upstream API calls (e.g., data hydration after cache misses).

**Why this priority**: Upstream calls are on the cold path (cache misses only), so this is less critical than connection pool sizing. However, during a cache recovery event, many concurrent hydration calls can overwhelm the default limits.

**Independent Test**: Verify that the HTTP client respects the configured max_connections and timeout values from settings.

**Acceptance Scenarios**:

1. **Given** production settings with higher connection limits and shorter timeout, **When** the client is initialized, **Then** it uses those values instead of hardcoded defaults.
2. **Given** an API call exceeds the configured timeout, **When** the timeout fires, **Then** the request fails fast instead of holding the connection indefinitely.

---

### Edge Cases

- What happens when the binary bitmap data contains byte sequences that are invalid in certain text encodings? The lossless byte-preserving re-encoding must handle all values 0-255.
- What happens when `get_completed_bits()` is called with `bit_range=0`? Should return an empty set without errors.
- What happens when a per-user lock is acquired for disconnect, but the user reconnects before disconnect completes? The per-user lock ensures serialization within that user.
- What happens if environment variables contain non-integer values for integer settings? Validation rejects them at startup (fail-fast).
- What happens when parallel progress summary encounters an exception for one subject? Individual failures should be caught; other subjects still return results.
- What happens when the WebSocket connection set is modified during iteration in parallel broadcast? The set snapshot at function entry prevents mutation issues.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support configurable connection pool size via environment variable, defaulting to 20 connections per worker in development.
- **FR-002**: System MUST support configurable global IP rate limit (requests per window and window duration) via environment variables.
- **FR-003**: System MUST support configurable upstream HTTP client connection limits and timeout via environment variables.
- **FR-004**: System MUST support configurable WebSocket broadcast concurrency via environment variable, where 0 means sequential (development) and >0 means parallel with backpressure control (production).
- **FR-005**: System MUST support configurable rate limiter fail-open/fail-closed behavior via environment variable.
- **FR-006**: System MUST retrieve the entire progress bitmap in a single data fetch instead of N individual bit lookups, decoding bits client-side.
- **FR-007**: System MUST correctly handle binary data encoding constraints when reading bitmap data, ensuring lossless byte round-tripping for all values 0-255.
- **FR-008**: System MUST parallelize per-subject lookups in the progress summary endpoint instead of sequential awaits.
- **FR-009**: System MUST replace the global WebSocket connection lock with per-user locks to eliminate cross-user contention.
- **FR-010**: System MUST clean up per-user locks when a user's last WebSocket connection is removed to prevent memory leaks.
- **FR-011**: System MUST log the configured connection pool size at startup for operational visibility.
- **FR-012**: System MUST provide a production environment reference file documenting recommended values for all scaling parameters.
- **FR-013**: All changes MUST preserve existing behavior in development mode (no configuration file changes, conservative defaults).
- **FR-014**: All existing tests MUST continue to pass after changes.

### Key Entities

- **Settings**: Configuration object holding all scaling parameters, loaded from environment variables with development-safe defaults.
- **Connection Pool**: Shared pool of data store connections per worker process; size must account for API handlers, middleware, pub/sub listeners, and WebSocket support.
- **Progress Bitmap**: Binary bitmap storing per-lesson completion state; key per user+subject+version.
- **WebSocket Connection Manager**: Manages per-user WebSocket connection sets with locking for thread-safe mutations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Progress retrieval for a 500-lesson subject uses 1 data fetch round-trip instead of 500, reducing operation volume by ~99.8%.
- **SC-002**: All scaling parameters (pool sizes, timeouts, rate limits, concurrency) are configurable via environment variables without code changes.
- **SC-003**: WebSocket connect/disconnect operations for different users execute without mutual blocking.
- **SC-004**: Progress summary for a student enrolled in 8 subjects completes in approximately the time of 1 subject lookup (parallel) instead of 8 sequential lookups.
- **SC-005**: No existing tests break after all changes are applied.
- **SC-006**: Development environment continues to work with zero configuration changes.
- **SC-007**: Rate limiter behavior during data store outages is operator-configurable (fail-open or fail-closed).
- **SC-008**: All changes can be rolled back by simply removing production environment variables, reverting to equivalent defaults.

## Assumptions

- The lossless byte-encoding approach for handling text-decoded binary data is safe for all byte values 0-255. If this assumption proves incorrect during testing, the fallback is to revert to the original per-bit approach.
- Per-user WebSocket locks use negligible memory compared to the WebSocket connection objects themselves (one lock per connected user).
- The parallel progress summary approach does not need per-subject error boundaries because individual lookups already handle their own exceptions internally.
- The upstream HTTP client already uses connection limits (confirmed in existing code), so the change makes these configurable rather than introducing a new concept.
- Tests override settings via a test-specific settings override mechanism, so new settings fields with defaults won't break existing tests.
- The plan-level broadcast method will also benefit from parallel per-user sends transitively.

## Dependencies

- **Text-decoded data store responses**: The data store connection is configured to decode responses as text. Binary data reading must work around this constraint using lossless encoding.
- **Cached settings**: Settings are frozen after first access per worker. New fields must have defaults that work without any configuration file changes.
- **Test infrastructure**: Must not modify test configuration, test fixtures, or development configuration files.
- **Existing middleware registration**: The rate limit middleware is already registered with limit and window parameters. These must continue to work while also supporting the new fail-open setting.

## Scope Boundaries

### In Scope

- Adding configurable scaling settings to the configuration system
- Production environment reference file
- Configurable connection pool size
- Single-fetch bitmap decode replacing per-bit pipeline
- Parallel progress summary
- Per-user WebSocket locks replacing global lock
- Configurable parallel WebSocket broadcast
- Configurable rate limiter fail-open/fail-closed
- Configurable upstream client timeouts and connection limits

### Out of Scope

- Load/stress testing tooling (k6, Locust)
- Worker count configuration
- WebSocket compression (per-message-deflate)
- Data store clustering/sentinel setup
- Topic-level per-bit pipeline optimization (scoped per-topic, 5-20 items, acceptable at current scale)
- Changes to development configuration values
- Changes to test configuration or fixtures
- New package dependencies
