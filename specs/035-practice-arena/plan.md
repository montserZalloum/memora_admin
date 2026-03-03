# Implementation Plan: Practice Arena (ساحة التدريب)

**Branch**: `035-practice-arena` | **Date**: 2026-03-02 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/035-practice-arena/spec.md`
**Prior Art**: Phase 025 implemented core Practice Arena. This phase addresses specification gaps and refinements.

## Summary

Practice Arena enables student-initiated practice sessions with batched questions from previously completed or all-content filters. Phase 025 delivered the foundational implementation: Review Item extraction, Practice Log (raw SQL), Redis sessions, PracticeService, 4 FastAPI endpoints, Pydantic models, and Frappe API bridges.

Phase 035 refines the specification with 25 FRs and identifies **5 implementation gaps** between the 035 spec and the existing codebase:
1. **FR-002**: Dirty-set pattern for Review Item extraction (currently synchronous on-save)
2. **FR-014**: Proportional topic distribution in question selection (currently simple ORDER BY)
3. **FR-016**: `all_seen_warning` when ANY batch question is a repeat (currently only when ALL items exhausted)
4. **FR-007**: Retry semantics for failed extraction (requires dirty-set)
5. **FR-005**: Content hash dedup must apply within dirty-set batch processing

All other FRs (FR-001, FR-003–004, FR-006, FR-008–013, FR-015, FR-017–025) are already implemented and verified in the existing codebase.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: FastAPI, Pydantic v2, redis.asyncio, structlog, Frappe Framework (ORM for Review Items, raw SQL for Practice Log)
**Storage**: MariaDB via Frappe ORM (Review Items) + raw SQL (Practice Log, ~500M rows), Redis at `redis://127.0.0.1:13001` (practice sessions, hierarchy cache)
**Testing**: pytest 8.4.2, pytest-asyncio, httpx (FastAPI), FrappeTestCase (Frappe-side), real Redis (no mocking)
**Target Platform**: Linux server (FastAPI on port 8002, Frappe bench)
**Project Type**: Dual architecture (Frappe admin + FastAPI sidecar)
**Performance Goals**: <100ms question selection (SC-003), <2s P95 end-to-end (SC-001), <30s sync for 100 lessons (SC-002)
**Constraints**: 100K concurrent users, no impact on daily review system (FR-025), one session per player
**Scale/Scope**: ~200K Review Items, ~500M Practice Log rows, 4 existing endpoints (gap-fix only), 0 new tables

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | **COMPLIANT** | Practice Sessions are Redis-only but intentionally ephemeral (not source-of-truth). Practice Log is MariaDB (source of truth). Review Items use existing Frappe DocType. Dirty-set for extraction follows established `memora:dirty:*` pattern — protected key, no TTL, never evicted. |
| II. Sub-20ms Game API | **DEVIATION JUSTIFIED** | Question selection target is <100ms (not <20ms) due to SQL JOIN complexity (Review Item × Practice Log). This is acceptable: practice is a deliberate user action, not real-time game flow. Hierarchy endpoint uses cached data (<5ms). |
| III. Content Hierarchy Integrity | **COMPLIANT** | Read-only consumer of existing hierarchy. No modifications to bitmap, version, or bit_index structures. |
| IV. Double-Gate Access Control | **COMPLIANT** | Reuses existing `AccessService.check_access_with_plan()`. Access checked at session start only (FR-020/FR-021). Free content bypass maintained (FR-022). |
| V. Cryptographic Voucher Security | **N/A** | No voucher operations. |
| VI. Financial Precision | **N/A** | No monetary calculations. No XP/rewards (FR-025). |
| VII. Auditable State Machines | **COMPLIANT** | Practice sessions are ephemeral (no audit trail needed). Practice Log is append/update only (never transitions backward). Dirty set follows existing sync.py patterns. |
| VIII. Test-First Coverage | **COMPLIANT** | Existing tests from phase 025. New tests needed for gap-fill changes (dirty-set extraction, proportional distribution, all_seen_warning fix). |

### Post-Design Gate

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache | **COMPLIANT** | Dirty-set key `memora:dirty:review_items` is protected (no TTL, no eviction). On processing failure, entries remain in set for auto-retry (FR-007). Session loss = student restarts (by design). |
| II. Sub-20ms Game API | **DEVIATION JUSTIFIED** | Proportional distribution adds per-topic COUNT query (~2ms extra) but stays well under 100ms target. |
| IV. Double-Gate Access Control | **COMPLIANT** | No changes to access flow. Session start validates Season (Gate 1) + content access (Gate 2). Subsequent batches use stored `accessible_lessons`. |

**No violations requiring justification in Complexity Tracking table.**

## Project Structure

### Documentation (this feature)

```text
specs/035-practice-arena/
├── plan.md              # This file
├── spec.md              # Feature specification (035 refined)
├── research.md          # Phase 0: Gap analysis and research
├── data-model.md        # Phase 1: Data model (delta from 025)
├── quickstart.md        # Phase 1: Developer quickstart
├── contracts/
│   └── practice-api.md  # Phase 1: API contracts (unchanged from 025)
└── tasks.md             # Phase 2: Task breakdown (by /speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/
├── memora_admin/
│   ├── events/
│   │   └── review_item_sync.py       # MODIFY: Switch from sync to dirty-set enqueue
│   ├── tasks/
│   │   └── sync.py                   # MODIFY: Add sync_dirty_review_items() consumer
│   └── hooks.py                      # MODIFY: Add scheduler entry for review item sync
├── fastapi_app/
│   ├── services/
│   │   └── practice.py               # MODIFY: Proportional distribution + all_seen_warning fix
│   └── core/
│       └── redis_keys.py             # MODIFY: Add dirty_review_items_key()
└── fastapi_app/tests/
    └── test_practice.py              # MODIFY: Add tests for new behavior
```

**Structure Decision**: No new files needed. All changes are modifications to existing files established in phase 025.

## Complexity Tracking

No violations to justify. All changes follow established patterns:
- Dirty-set: follows `memora:dirty:progress` / `memora:dirty:wallets` pattern exactly
- Proportional distribution: extends existing `_select_questions()` SQL
- all_seen_warning fix: minor logic change in existing method
