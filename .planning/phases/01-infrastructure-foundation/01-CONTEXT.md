# Phase 1: Infrastructure Foundation - Context

**Gathered:** 2026-02-01
**Status:** Ready for planning

<domain>
## Phase Boundary

FastAPI sidecar runs alongside Frappe with shared Redis and proper Nginx routing. This phase delivers the foundation for game APIs: server scaffold, Redis pooling, reverse proxy config, and environment-based settings. Authentication and game logic are separate phases.

</domain>

<decisions>
## Implementation Decisions

### Error handling philosophy
- Fail fast at startup: FastAPI should not start if Redis is unreachable
- Operation-specific retry policy:
  - **Read operations** (fetching progress, wallet): Retry 1-2 times with backoff — safe because idempotent
  - **Write operations** (XP awards, progress updates): Fail immediately — no retries to prevent duplicate processing; client handles retry
- Error responses use categorized codes (e.g., `REDIS_UNAVAILABLE`, `AUTH_FAILED`) for client-specific handling
- Every response includes `request_id` for debugging and support correlation

### Logging & observability
- Log format: Environment-based — structured JSON in production, human-readable colored output in development
- Request correlation: `request_id` included in every log line for tracing
- Request logging:
  - Standard: Path, method, status code, duration, user ID (if authenticated), request_id
  - Verbose mode (dev only): Add request/response bodies
- Redis logging: Only slow operations (threshold-based) to avoid noise — use for identifying performance issues

### Health check endpoints
- Two endpoints (Kubernetes-style):
  - `/health/live` — Liveness check, fast, no dependencies
  - `/health/ready` — Readiness check, verifies Redis connection
- Readiness response includes dependency details: `{redis: "ok", ...}` format for debugging
- No caching on health checks — always fresh status
- Response includes API version only (not uptime or environment)

### Claude's Discretion
- Exact retry backoff timing for read operations
- Slow operation threshold for Redis logging (suggest ~50ms)
- Specific error code taxonomy beyond the examples given
- Log level configuration approach

</decisions>

<specifics>
## Specific Ideas

- Read vs write retry distinction is intentional: "نمنع تكرار العملية بالخطأ وخصم النقاط مرتين" — prevent accidental double-deduction
- Kubernetes-style health endpoints suggest future containerization readiness

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-infrastructure-foundation*
*Context gathered: 2026-02-01*
