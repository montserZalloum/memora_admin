# Feature Specification: Global API Rate Limiting

**Feature Branch**: `022-global-rate-limiting`
**Created**: 2026-02-22
**Status**: Draft
**Input**: Security audit identified missing global rate limiting on FastAPI endpoints

## Problem Statement

The FastAPI sidecar has **no global rate limiter**. Rate limiting exists only on 3 specific flows (login, voucher redeem, OTP), but the remaining ~15 endpoints are completely unthrottled. Any authenticated user (or attacker with a stolen JWT) can spam endpoints without restriction, potentially overloading the database, cache layer, and backend services.

### Current State

| Endpoint                  | Rate Limited?                          | Risk                                                          |
|---------------------------|:--------------------------------------:|---------------------------------------------------------------|
| `POST /auth/player/login` | Yes (10/min IP, 5/min account)        | --                                                            |
| `POST /voucher/redeem`    | Yes (5 fails/hr player, 20 fails/hr IP) | --                                                         |
| OTP (register, reset)     | Yes (3/10min phone, 10/10min IP)      | --                                                            |
| `GET /voucher/preview`    | No (intentionally)                     | Low                                                           |
| `POST /reviews/submit`    | No                                     | **High** -- writes to Interaction Log, triggers FSRS processor |
| `POST /session/start`     | No                                     | **High** -- creates game session                              |
| `POST /session/end`       | No                                     | **High** -- writes progress, XP, streaks                      |
| `POST /webhooks/payment`  | No (has idempotency key)               | Medium                                                        |
| `GET /catalog/*`          | No                                     | Medium -- hits cache/backend                                  |
| `GET /profile/*`          | No                                     | Medium                                                        |
| `GET /leaderboard/*`      | No                                     | Medium                                                        |
| `GET /progress/*`         | No                                     | Medium                                                        |
| `GET /plans/*`            | No                                     | Medium                                                        |
| `GET /settings/*`         | No                                     | Low                                                           |
| `GET /health/*`           | No                                     | Low (should stay unlimited)                                   |
| WebSocket `/ws/*`         | No                                     | Medium -- connection flooding                                 |

### Why This Matters

1. **Database protection**: `POST /reviews/submit` writes to the Interaction Log and triggers the memory state processor which hits a partitioned table designed for 10B+ rows. Unthrottled writes can cause lock contention.
2. **Cache saturation**: Session start/end, progress, and wallet operations all go through the cache layer. A flood of requests can exhaust connection pools.
3. **Backend overload**: Several GET endpoints fall through to backend API calls on cache misses. The backend is single-threaded per worker -- easy to saturate.
4. **Cost**: Even cached responses consume CPU/bandwidth. At scale (100k+ students), a misbehaving client can amplify costs.

## User Scenarios & Testing *(mandatory)*

### User Story 1 -- Global Per-IP Rate Limit (Priority: P1)

As a platform operator, I want all API endpoints to be protected by a global per-IP rate limit so that no single IP can overwhelm the system, regardless of authentication status.

**Why this priority**: This is the single highest-impact change. One protection layer covers everything. Without it, any endpoint is a potential abuse vector.

**Independent Test**: Send 101 requests from the same IP within 60 seconds to any endpoint (e.g., `/api/v1/health/live`). The 101st request should return `429 Too Many Requests` with a `Retry-After` header.

**Acceptance Scenarios**:

1. **Given** an IP has made fewer than 100 requests in the current 60-second window, **When** a new request arrives, **Then** it is processed normally.
2. **Given** an IP has made exactly 100 requests in the current 60-second window, **When** the 101st request arrives, **Then** the system returns HTTP 429 with `{"error": "RATE_LIMITED", "retry_after": <seconds>}` and a `Retry-After` header.
3. **Given** an IP was rate-limited, **When** the 60-second window expires, **Then** subsequent requests are allowed again.
4. **Given** a request to `/api/v1/health/live` or `/api/v1/health/ready`, **When** processed, **Then** it is **exempt** from the global rate limit (health checks must always respond for load balancer probes).

---

### User Story 2 -- Per-Player Rate Limit on Write Endpoints (Priority: P2)

As a platform operator, I want write endpoints (reviews, session start/end) to have tighter per-player rate limits so that even a legitimate authenticated user cannot accidentally or intentionally flood the system with writes.

**Why this priority**: The global IP limit catches external abuse, but a legitimate user with a buggy client could still generate excessive writes from different IPs (e.g., mobile network rotation). Per-player limits on write-heavy endpoints add a second layer of defense.

**Independent Test**: Using a valid JWT, send 31 `POST /reviews/submit` requests in 60 seconds. The 31st should return 429.

**Acceptance Scenarios**:

1. **Given** a player has submitted fewer than 30 review requests in the current 60-second window, **When** a new review is submitted, **Then** it is processed normally.
2. **Given** a player has submitted 30 review requests in 60 seconds, **When** the 31st arrives, **Then** return HTTP 429 with `retry_after`.
3. **Given** a player has started fewer than 10 sessions in 60 seconds, **When** a new session start arrives, **Then** it is processed normally.
4. **Given** a player has started 10 sessions in 60 seconds, **When** the 11th arrives, **Then** return HTTP 429.

---

### User Story 3 -- WebSocket Connection Limiting (Priority: P3)

As a platform operator, I want to limit the number of concurrent WebSocket connections per player so that connection flooding is prevented.

**Why this priority**: Lower priority because WebSocket abuse requires maintaining many connections simultaneously, which is harder to do accidentally. But still needed for completeness.

**Independent Test**: Open 6 WebSocket connections with the same player JWT. The 6th connection should be rejected.

**Acceptance Scenarios**:

1. **Given** a player has fewer than 5 active WebSocket connections, **When** a new connection is opened, **Then** it is accepted.
2. **Given** a player has exactly 5 active WebSocket connections, **When** the 6th is attempted, **Then** it is rejected with close code 4029 and reason "Too many connections".

---

### Edge Cases

- What happens when the rate limiting data store is unavailable? -> **Fail open**: allow requests through rather than blocking all traffic. Log a warning. The system must not become a self-DOS.
- What happens with `X-Forwarded-For` header spoofing? -> Use the **rightmost untrusted IP** in the X-Forwarded-For chain (or `request.client.host` if no proxy). Document the expected proxy configuration.
- What happens to existing per-endpoint rate limits (login, voucher)? -> They remain **unchanged**. The global limit is a safety net; endpoint-specific limits are tighter. A request could pass the global limit but still be blocked by the endpoint-specific limit.
- What about rate limit key collision across features? -> Use a distinct key prefix for global rate limiting, separate from existing endpoint-specific prefixes.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST enforce a global per-IP rate limit (100 requests per 60 seconds) on all API endpoints except health checks and payment webhooks.
- **FR-002**: The global rate limit MUST use atomic counter operations with fixed-window expiry (INCR + conditional EXPIRE), consistent with existing rate limiter patterns in the codebase.
- **FR-003**: Rate-limited responses MUST return HTTP 429 with a JSON body `{"error": "RATE_LIMITED", "retry_after": <seconds>}` and a `Retry-After` response header.
- **FR-003a**: All non-exempt responses MUST include global rate limit headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`. Per-player rate limit headers are only included on 429 responses.
- **FR-004**: Health check endpoints (`/api/v1/health/live`, `/api/v1/health/ready`) and payment webhook endpoints (`/api/v1/webhooks/payment`) MUST be exempt from all rate limiting.
- **FR-005**: The system MUST enforce per-player rate limits on write endpoints: `/reviews/submit` (30/min), `/session/start` (10/min), `/session/end` (10/min).
- **FR-006**: Per-player rate limits MUST use the `player_id` from the authenticated session (not IP or account).
- **FR-007**: The system MUST limit concurrent WebSocket connections to 5 per player.
- **FR-008**: If the rate limiting data store is unavailable, the rate limiter MUST fail open (allow requests) and log a warning.
- **FR-009**: Existing endpoint-specific rate limits (login, voucher redeem, OTP) MUST remain unchanged and independent.
- **FR-010**: The rate limit check MUST NOT add more than 2ms latency to request processing (single data store round-trip).

### Non-Functional Requirements

- **NFR-001**: Rate limit state MUST be stored in the existing cache infrastructure (no new infrastructure required).
- **NFR-002**: All rate limit keys MUST have a TTL to prevent unbounded memory growth.
- **NFR-003**: The implementation MUST follow existing codebase patterns for atomicity and structured logging.

### Key Entities

- **Rate Limit Counter**: Tracks request count per IP within a sliding time window. Key attributes: IP address, count, window expiry.
- **Player Write Limit**: Tracks write request count per player per endpoint within a sliding window. Key attributes: player ID, endpoint scope, count, window expiry.
- **Connection Counter**: Tracks active WebSocket connections per player. Key attributes: player ID, active connection count.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After deployment, no API endpoint (except health) is callable more than 100 times per minute from a single IP without receiving a 429 response.
- **SC-002**: After deployment, `POST /reviews/submit` is callable at most 30 times per minute per player without receiving a 429 response.
- **SC-003**: After deployment, `POST /session/start` and `/session/end` are callable at most 10 times per minute per player without receiving a 429 response.
- **SC-004**: After deployment, no more than 5 concurrent WebSocket connections are allowed per player.
- **SC-005**: When the rate limiting data store is unavailable, all requests pass through (fail open) and a warning is logged.
- **SC-006**: Rate limit enforcement adds less than 2ms p99 latency to request processing.
- **SC-007**: All existing tests continue to pass with no modifications (existing endpoint-specific limits are unaffected).

## Clarifications

### Session 2026-02-22

- Q: Should payment webhook endpoints be exempt from the global per-IP rate limit? → A: Yes, exempt. Payment webhooks come from trusted provider IPs and already have idempotency protection. Dropping them risks revenue loss.
- Q: Should all API responses include rate limit headers? → A: Global limit headers only (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`) on all responses. Per-player limits return headers only on 429. Rationale: global counter data is already available from the atomic increment (no extra round-trip); per-player headers would add unnecessary complexity.

## Assumptions

- The application sits behind a reverse proxy (nginx/Caddy) that sets `X-Forwarded-For`. The rate limiter should extract the client IP from this header.
- The existing cache infrastructure has sufficient capacity for the additional rate limit keys (each key is ~50 bytes with a 60-second TTL -- negligible even at 100k concurrent users).
- The suggested limits (100/min global, 30/min reviews, 10/min sessions) are starting points and may be tuned after observing production traffic patterns.
- The WebSocket connection manager is the single entry point for all WebSocket connections.
- Rate limit numbers are configurable without code changes (start conservative with higher limits, tighten based on observed traffic).
