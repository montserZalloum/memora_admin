# Implementation Plan: Admin Announcement System

**Branch**: `032-admin-announcements` | **Date**: 2026-02-28 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/032-admin-announcements/spec.md`

## Summary

Bilingual announcement system with plan-based targeting, duration controls, display frequency options, and Redis caching. Admins create announcements via Frappe Desk (DocType CRUD). Players fetch active announcements via a single FastAPI GET endpoint served from a shared Redis cache key. Cache invalidation uses the established two-pronged pattern (direct DEL + pubsub). The system supports 50K concurrent users with < 10ms reads from cache.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: Frappe Framework (ORM, DocType, hooks), FastAPI, redis.asyncio, Pydantic v2, structlog
**Storage**: MariaDB via Frappe ORM (source of truth), Redis at `redis://127.0.0.1:13001` (hot cache)
**Testing**: pytest + pytest-asyncio (FastAPI), FrappeTestCase (Frappe DocType)
**Target Platform**: Linux server
**Project Type**: Dual architecture (Frappe admin + FastAPI sidecar)
**Performance Goals**: < 10ms announcement retrieval from cache; cache invalidation < 2s after admin action
**Constraints**: Sub-20ms API response; no per-user server-side state; display frequency enforcement is client-side only
**Scale/Scope**: 50K concurrent users; < 20 active announcements at any time; single shared cache key

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Check

| Principle | Applicable? | Status | Notes |
|-----------|------------|--------|-------|
| I. Self-Healing Cache (NON-NEGOTIABLE) | **Yes** | PASS | Cache-miss hydration from MariaDB via Frappe API. Single key with 5-min TTL. |
| II. Sub-20ms Game API Performance | **Yes** | PASS | Single Redis GET + JSON parse + in-memory filter. No Frappe ORM in hot path. |
| III. Content Hierarchy Integrity | No | N/A | Announcements are independent of content hierarchy. |
| IV. Double-Gate Access Control | No | N/A | Announcements use plan-based targeting, not content access gates. |
| V. Cryptographic Voucher Security | No | N/A | No voucher or security-sensitive data. |
| VI. Financial Precision | No | N/A | No monetary calculations. |
| VII. Auditable State Machines | No | N/A | Simple published/unpublished toggle. No complex lifecycle. |
| VIII. Test-First Coverage | **Yes** | PASS | Tests planned for DocType validation, service logic, and endpoint. |

### Post-Design Re-Check

| Principle | Status | Design Evidence |
|-----------|--------|-----------------|
| I. Self-Healing Cache | PASS | `AnnouncementService.get_active_announcements()` hydrates from `memora_admin.api.announcements.get_active_announcements` on cache miss. Redis key `memora:announcements:active` with 5-min TTL. Two-pronged invalidation (DEL + pubsub) on admin actions. |
| II. Sub-20ms Performance | PASS | Endpoint does: 1 Redis GET (~0.3ms) + JSON parse (~0.1ms) + filter < 20 items (~0.01ms) = well under 10ms. FrappeClient call only on cache miss (every 5 min). |
| VIII. Test-First Coverage | PASS | Unit tests for DocType validation, service filtering logic. Integration test for endpoint with real Redis. |

**Gate result**: PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/032-admin-announcements/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: research decisions
├── data-model.md        # Phase 1: entity model
├── quickstart.md        # Phase 1: getting started guide
├── contracts/
│   └── announcements.yaml  # Phase 1: OpenAPI contract
└── tasks.md             # Phase 2: implementation tasks (created by /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── doctype/
│   ├── memora_announcement/
│   │   ├── memora_announcement.json       # DocType schema
│   │   ├── memora_announcement.py         # Document class (validate, compute dates)
│   │   ├── memora_announcement.js         # Form script (conditional field visibility)
│   │   └── test_memora_announcement.py    # DocType validation tests
│   └── memora_announcement_target_plan/
│       └── memora_announcement_target_plan.json  # Child table schema
├── api/
│   └── announcements.py                   # Whitelist API for cache hydration
└── events/
    └── announcement_sync.py               # Cache invalidation hook

fastapi_app/
├── api/v1/endpoints/
│   └── announcements.py                   # GET /api/v1/announcements/
├── services/
│   └── announcements.py                   # Cache read + hydration + filtering
├── models/
│   └── announcements.py                   # Pydantic response schemas
├── api/
│   └── deps.py                            # + AnnouncementServiceDep
└── core/
    └── redis_keys.py                       # + announcements_active_key(), ANNOUNCEMENTS_CACHE_TTL
```

**Structure Decision**: Follows the existing dual-architecture pattern. Frappe DocTypes for admin CRUD, FastAPI endpoint for player reads. New files integrate into existing directories — no structural changes.

## Complexity Tracking

No constitution violations. No complexity justifications needed.
