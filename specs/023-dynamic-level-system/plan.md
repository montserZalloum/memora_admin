# Implementation Plan: Dynamic Level System

**Branch**: `023-dynamic-level-system` | **Date**: 2026-02-22 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/023-dynamic-level-system/spec.md`

## Summary

Replace hardcoded `LEVEL_THRESHOLDS`, `LEVEL_TITLES`, and `calculate_level()` in `fastapi_app/core/constants.py` with an admin-configurable system. A Frappe Single DocType (`Memora Level Settings`) stores curve parameters and level titles. On save, config is pushed to Redis (`memora:config:levels`). FastAPI reads from Redis with hardcoded fallback defaults. Level computation uses O(1) inverse quadratic formula instead of list iteration.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: Frappe Framework (ORM, Single DocType, hooks), FastAPI, `redis.asyncio`, `structlog`
**Storage**: MariaDB via Frappe ORM (Level Settings DocType), Redis at `redis://127.0.0.1:13000` (config cache)
**Testing**: `FrappeTestCase` for DocType validation, `pytest` + `pytest-asyncio` for FastAPI level_config module
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Dual architecture (Frappe admin + FastAPI sidecar)
**Performance Goals**: O(1) level calculation, sub-millisecond Redis config read, <5s propagation from admin save to API
**Constraints**: No Frappe ORM in FastAPI hot paths, `decode_responses=True` on Redis pool, 100k concurrent users
**Scale/Scope**: 1 Single DocType, 1 child table DocType, 1 FastAPI module, 1 sync hook, modifications to 4 existing files

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache Architecture | PASS | Hardcoded fallback defaults on cache miss. No Redis-only state — MariaDB is source of truth via Frappe DocType. Two-pronged invalidation (direct SET + pubsub). |
| II. Sub-20ms Game API Performance | PASS | O(1) formula computation. Single Redis GET for config (~0.1ms). No Frappe ORM in hot path. |
| III. Content Hierarchy Integrity | N/A | Level system does not affect content hierarchy or bitmaps. |
| IV. Double-Gate Access Control | N/A | Level system does not affect access control. |
| V. Cryptographic Voucher Security | N/A | No voucher changes. |
| VI. Financial Precision | N/A | No monetary calculations. |
| VII. Auditable State Machines | N/A | Level Settings has no lifecycle states. |
| VIII. Test-First Coverage | PASS | Existing `TestLevelCalculation` tests migrated with identical assertions. New validation tests for DocType. |

**Gate result**: PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/023-dynamic-level-system/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 research decisions
├── data-model.md        # Entity definitions
├── quickstart.md        # Implementation guide
├── contracts/           # API & cache contracts
│   ├── level-config-redis.md
│   └── calculate-level.md
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/memora_admin/doctype/
├── memora_level_settings/          # NEW: Single DocType
│   ├── memora_level_settings.json  # Schema (issingle: 1)
│   ├── memora_level_settings.py    # Validation + on_update sync
│   ├── memora_level_settings.js    # Form handler (minimal)
│   ├── test_memora_level_settings.py
│   └── __init__.py
└── memora_level_title/             # NEW: Child table
    ├── memora_level_title.json     # Schema (istable: 1)
    ├── memora_level_title.py       # pass
    └── __init__.py

memora_admin/events/
└── level_sync.py                   # NEW: Frappe → Redis sync hook

fastapi_app/core/
├── constants.py                    # MODIFIED: Remove level code
├── level_config.py                 # NEW: LevelConfig + calculate_level
└── pubsub.py                       # MODIFIED: Add level_config handler

fastapi_app/services/
└── profile_page.py                 # MODIFIED: Use level_config module

fastapi_app/tests/
└── test_xp_calculation.py          # MODIFIED: Migrate TestLevelCalculation
```

**Structure Decision**: Follows existing dual-architecture pattern. Frappe DocTypes in `memora_admin/memora_admin/doctype/`, sync hooks in `memora_admin/events/`, FastAPI modules in `fastapi_app/core/`. No new directories created beyond the two DocType folders.

## Complexity Tracking

No constitution violations to justify.
