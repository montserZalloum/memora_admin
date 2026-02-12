# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-12)

**Core value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.
**Current focus:** v2.0 Mobile-First Player Authentication — Phase 32 in progress (Plan 01 complete)

## Current Position

Phase: 32 of 32 (Event Handler & API Migration)
Plan: 1 of 3
Status: Plan 01 complete
Last activity: 2026-02-12 — Completed 32-01-PLAN.md (Event handler migration to PLAYER-##### identity)

Progress: [==============================] 99% (92/~94 plans, 32/32 phases)

## Performance Metrics

**Velocity:**
- Total plans completed: 92
- Milestones shipped: 11

**By Milestone:**

| Milestone | Phases | Plans | Status |
|-----------|--------|-------|--------|
| v1.0 MVP | 7 | 30 | Shipped 2026-02-02 |
| v1.1 Feature Expansion | 4 | 13 | Shipped 2026-02-03 |
| v1.2 Plan System Enhancement | 1 | 4 | Shipped 2026-02-03 |
| v1.2.1 Gap Closure | 1 | 1 | Shipped 2026-02-03 |
| v1.3 Profiles & Devices | 7 | 16 | Shipped 2026-02-07 |
| v1.4 Product Store | 3 | 5 | Shipped 2026-02-08 |
| v1.5 Real-Time Notifications | 1 | 2 | Shipped 2026-02-08 |
| v1.6 FSRS Review System | 1 | 3 | Shipped 2026-02-09 |
| v1.7 Profile Page API | 1 | 2 | Shipped 2026-02-10 |
| v1.8 Memory State Redesign | 1 | 5 | Shipped 2026-02-11 |
| v1.9 Tech Debt & Reliability | 1 | 4 | Shipped 2026-02-12 |
| v2.0 Mobile-First Auth | 4 | TBD | In Progress |

## Accumulated Context

### Decisions

All decisions logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Phone+password auth for players (Frappe User overhead unnecessary for mobile-first audience)
- PLAYER-.#####. autoname (decouples identity from phone number)
- Separate login endpoints (clean player/admin separation)
- Static OTP "1111" stub (ship auth flow now, swap real SMS later)
- 3-step password reset (most secure OTP flow per OWASP)
- Mobile not reqd in JSON schema; mandatory enforced in validate() for new docs only (backward compat)
- __setup__() for flags (not __init__()) per Frappe Document lifecycle
- create_access_token: email/mobile keyword-only params via * separator (prevents positional arg confusion)
- OTPProvider protocol for pluggable SMS delivery (StaticOTPProvider dev stub)
- LoginProfile drops gender field (mobile-first simplification)
- Player login uses FrappeClient.call(verify_player_password) -- single call, no Frappe session
- Admin login retains FrappeAuthService (Frappe User auth unchanged)
- Player refresh TTL from session_timeout_days (Memora Settings); admin refresh TTL from .env
- Admin tokens include role="System Manager" claim, preserved across refreshes
- Registration: upfront phone check via check_phone_exists Frappe API (better UX before OTP)
- Season auto-populated from latest published season (not user-facing)
- Major auto-derived from plan; fallback to first major of selected grade
- Anti-enumeration: password reset request always returns same response + consistent timing regardless of phone existence
- check_phone_exists returns player_name for mobile-to-docname resolution (used by password reset confirm)
- Event handlers use doc.player/doc.name directly as Redis identity key (no frappe.get_doc lookup needed)
- Two-pronged invalidation pattern (direct op + pubsub) adopted for profile_sync and plan_change_sync
- User field removed from Player Profile schema (clean break, PLAYER-##### is sole identity)

### Pending Todos

1 todo(s) in `.planning/todos/pending/`:
- **Implement track-level access enforcement and CDN flag** (api) — backend `TRK-*` grant check + `is_sold_separately` in `_h.json`

### Blockers/Concerns

- Payment gateway integration (PRCHS-03) deferred to future work — all transactions currently manual approval
- `profile_sync.py` doc.user issue RESOLVED in Phase 32 Plan 01 (migrated to doc.name + get_fastapi_redis())
- Research pitfall resolved: `flags.ignore_save_passwords` + `update_password()` pattern implemented in Phase 29
- Mobile-to-docname resolution required before any __Auth table operation (check_password keys by PLAYER-##### docname, not phone)

## Session Continuity

Last session: 2026-02-12
Stopped at: Completed 32-01-PLAN.md (Event handler migration to PLAYER-##### identity)
Resume file: None
Next action: Execute 32-02-PLAN.md (Frappe API migration)
