# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-01)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** Phase 3 - Access Control (Phase 2 complete)

## Current Position

Phase: 2 of 7 (Authentication)
Plan: 3 of 3 in current phase (02-01, 02-02, 02-03 complete)
Status: Phase complete
Last activity: 2026-02-02 - Completed 02-03-PLAN.md (Auth endpoints)

Progress: [#######...] 100% (of discovered plans: 7/7)

## Performance Metrics

**Velocity:**
- Total plans completed: 7
- Average duration: 2.4min
- Total execution time: 0.28 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-infrastructure-foundation | 4 | 8min | 2.0min |
| 02-authentication | 3 | 9min | 3.0min |

**Recent Trend:**
- Last 5 plans: 1min, 2min, 3min, 3min, 4min
- Trend: stable

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: 7 phases derived from 30 v1 requirements (depth: comprehensive)
- [Roadmap]: Session/Interaction flow, Leaderboards, Achievements deferred to v2
- [01-01]: Use pydantic-settings with SettingsConfigDict for env file loading
- [01-01]: Environment-based logging (JSON prod, colored dev console)
- [01-01]: Request ID middleware using structlog contextvars
- [01-02]: Redis pool stored in app.state for dependency access across requests
- [01-02]: Fail-fast: verify_redis_connection raises RuntimeError if Redis unreachable
- [01-02]: Kubernetes-style health: /live (fast), /ready (checks dependencies)
- [01-03]: Upstream includes keepalive 32 for connection pooling
- [01-03]: Location block as commented example (requires manual server block insertion)
- [01-04]: Documentation-only plan - no code changes required
- [01-04]: 5-phase migration approach for clarity and safety
- [02-01]: Use PyJWT (not python-jose) for JWT operations - cleaner API
- [02-01]: Rich access token payload (sub, email, role, tz, name, fid) for stateless auth
- [02-01]: FrappeAuthService returns None on any failure (generic response)
- [02-02]: IP rate limit checked before account (fails fast on distributed attacks)
- [02-02]: Email normalized to lowercase for consistent rate limiting
- [02-02]: Handles both bytes and str Redis responses for compatibility
- [02-03]: HTTPBearer for token extraction from Authorization header
- [02-03]: Generic 'Invalid credentials' for all auth failures (no enumeration)
- [02-03]: Refresh token not rotated (reusable per CONTEXT.md)

### Pending Todos

None yet.

### Blockers/Concerns

None yet.

## Session Continuity

Last session: 2026-02-02T06:56:14Z
Stopped at: Completed 02-03-PLAN.md (Auth endpoints)
Resume file: None

Previous plan summaries:

01-01: FastAPI scaffold with pydantic-settings configuration, structured logging, and health check endpoint
01-02: Async Redis connection pool with fail-fast startup, dependency injection, and readiness health check
01-03: Nginx upstream for FastAPI sidecar with X-Request-ID propagation and Frappe integration documentation
01-04: Server migration guide for relocating Redis/FastAPI to dedicated server with rollback procedures
02-01: JWT utilities with PyJWT, auth Pydantic models, and async Frappe credential verification service
02-02: Session management with token family ID for single-session enforcement and dual-key rate limiting with atomic Lua script
02-03: Login/refresh endpoints with dual rate limiting, session-based token family validation, and stateless JWT auth dependency
