# API Contract: Rate Limiting

**Date**: 2026-02-22
**Branch**: `022-global-rate-limiting`

## Response Headers (All Non-Exempt Responses)

Every response from non-exempt endpoints includes these headers:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1740268800
```

| Header | Type | Description |
|--------|------|-------------|
| `X-RateLimit-Limit` | int | Maximum requests allowed in the current window |
| `X-RateLimit-Remaining` | int | Requests remaining in the current window (`max(0, limit - count)`) |
| `X-RateLimit-Reset` | int | Unix timestamp when the current window resets |

## Rate-Limited Response (HTTP 429)

When any rate limit is exceeded:

**Status Code**: `429 Too Many Requests`

**Response Headers**:
```
Retry-After: 45
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1740268800
```

**Response Body**:
```json
{
    "error": "RATE_LIMITED",
    "retry_after": 45
}
```

| Field | Type | Description |
|-------|------|-------------|
| `error` | string | Always `"RATE_LIMITED"` |
| `retry_after` | int | Seconds until the client can retry |

## Exempt Endpoints

These endpoints are NOT rate limited and do NOT include rate limit headers:

| Endpoint | Reason |
|----------|--------|
| `GET /api/v1/health/live` | Load balancer health probe |
| `GET /api/v1/health/ready` | Load balancer readiness probe |
| `POST /api/v1/webhooks/payment` | Trusted payment provider callbacks |

## Rate Limit Tiers

### Tier 1: Global Per-IP (Middleware)

| Attribute | Value |
|-----------|-------|
| Scope | All non-exempt endpoints |
| Key | IP address (from `X-Forwarded-For` or `request.client.host`) |
| Limit | 100 requests |
| Window | 60 seconds |
| Applied | Before routing (middleware layer) |

### Tier 2: Per-Player Write Limits (Dependency)

Applied after authentication. Uses `player_id` from JWT.

| Endpoint | Limit | Window | Redis Key |
|----------|-------|--------|-----------|
| `POST /api/v1/reviews/{subject}/submit` | 30 | 60s | `memora:rl:reviews:{player_id}` |
| `POST /api/v1/sessions/start` | 10 | 60s | `memora:rl:session_start:{player_id}` |
| `POST /api/v1/sessions/end` | 10 | 60s | `memora:rl:session_end:{player_id}` |

### Tier 3: WebSocket Connection Limit

| Attribute | Value |
|-----------|-------|
| Endpoint | `WS /api/v1/notifications/ws` |
| Limit | 5 concurrent connections per player |
| Enforcement | Before `websocket.accept()` |
| Rejection | Close code `4029`, reason `"Too many connections"` |

## Interaction Between Tiers

1. **Global IP limit** is checked first (middleware, before routing)
2. If global passes, request reaches endpoint
3. **Per-player limit** is checked at endpoint level (dependency, after JWT auth)
4. Both must pass for the request to proceed
5. If global rejects, per-player is never checked

Example: A player sending 101 requests from one IP in 60s will be blocked by the global limit at request 101, even if their per-player limit (30/min for reviews) is not yet reached.

## WebSocket Rejection Protocol

When a player exceeds the WebSocket connection limit:

```
WebSocket Close Frame:
  Code: 4029
  Reason: "Too many connections"
```

The connection is closed **before** `websocket.accept()` — no upgrade occurs.

## Error Scenarios

| Scenario | Behavior | Client Action |
|----------|----------|---------------|
| Global IP limit exceeded | HTTP 429 + Retry-After | Wait `retry_after` seconds, retry |
| Per-player write limit exceeded | HTTP 429 + Retry-After | Wait `retry_after` seconds, retry |
| WebSocket limit exceeded | Close 4029 | Close oldest connection, reconnect |
| Redis unavailable | Request passes through (fail open) | No client impact |
