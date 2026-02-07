---
phase: 20
plan: 02
subsystem: fastapi-progress
tags: [legacy-removal, time-spent, deprecation, endpoint-cleanup]
requires:
  - "20-01 (hierarchy enrichment with base_xp, max_hearts)"
provides:
  - "StageResult.time_spent documented as milliseconds"
  - "Legacy POST /progress/complete endpoint removed"
  - "CompleteRequest/CompleteResponse deprecated but retained"
affects:
  - "20-03 (Lua hot path uses millisecond time_spent)"
  - "20-04 (FSRS interprets time_spent as milliseconds)"
tech-stack:
  added: []
  patterns:
    - "Session-only completion path (POST /sessions/end)"
key-files:
  modified:
    - fastapi_app/models/game_session.py
    - fastapi_app/api/v1/endpoints/progress.py
    - fastapi_app/models/progress.py
key-decisions:
  - decision: "Keep CompleteRequest/CompleteResponse models with DEPRECATED comments"
    reason: "Referenced in models/__init__.py and potentially by tests"
  - decision: "Remove calculate_xp_award from progress.py (duplicated in sessions.py)"
    reason: "Sessions.py already has _calculate_xp_award; no need for two copies"
  - decision: "Remove is_lesson_unlocked import from progress.py"
    reason: "Only used by the removed complete_lesson endpoint"
duration: "3m"
completed: "2026-02-07"
---

# Phase 20 Plan 02: StageResult time_spent + Legacy Endpoint Removal Summary

**One-liner:** Changed StageResult.time_spent to milliseconds and removed legacy POST /progress/complete endpoint, making POST /sessions/end the sole completion path.

## Performance

- **Duration:** ~3 minutes
- **Started:** 2026-02-07T09:39:44Z
- **Completed:** 2026-02-07T09:42:40Z
- **Tasks:** 2/2 completed
- **Files modified:** 3

## Accomplishments

1. **StageResult.time_spent contract change**: Updated docstring and inline comment from "seconds" to "milliseconds". No logic change -- the field remains `int` and is passed through without transformation. FSRS task (Plan 04) will interpret it as milliseconds.

2. **Legacy endpoint removal**: Removed `POST /progress/complete` endpoint, the `calculate_xp_award` helper function (~27 lines), and all unused imports (`GameSessionServiceDep`, `WalletServiceDep`, `SettingsServiceDep`, `CompleteRequest`, `CompleteResponse`, `is_lesson_unlocked`). This removed approximately 170 lines of code.

3. **Model deprecation**: Added DEPRECATED comments to `CompleteRequest` and `CompleteResponse` models in `models/progress.py` pointing users to `EndSessionRequest`/`EndSessionResponse` instead.

4. **All 7 GET progress endpoints preserved**: Summary, SSE stream, tracks, track detail, unit detail, topic lessons, and subject progress endpoints all remain functional and registered.

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Change StageResult.time_spent to milliseconds | b70a6d4 | fastapi_app/models/game_session.py |
| 2 | Remove legacy POST /progress/complete endpoint | 62514ae | fastapi_app/api/v1/endpoints/progress.py, fastapi_app/models/progress.py |

## Files Modified

- `fastapi_app/models/game_session.py` -- StageResult docstring and time_spent comment updated
- `fastapi_app/api/v1/endpoints/progress.py` -- Removed complete_lesson endpoint, calculate_xp_award, unused imports
- `fastapi_app/models/progress.py` -- Added DEPRECATED comments to CompleteRequest/CompleteResponse

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Keep CompleteRequest/CompleteResponse with deprecation comments | Referenced by models/__init__.py re-exports; removing would break public API surface |
| Remove calculate_xp_award from progress.py | Duplicated in sessions.py as _calculate_xp_award; single source of truth |
| Remove is_lesson_unlocked import | Only used by complete_lesson; sessions.py has its own unlock check |
| time_spent stays int (no validator) | Pass-through field; FSRS task will consume as milliseconds |

## Deviations from Plan

None -- plan executed exactly as written.

## Issues

None.

## Verification Results

| Check | Result |
|-------|--------|
| StageResult.time_spent is integer field | PASS -- model_json_schema confirms type: integer |
| POST /progress/complete returns 404/405 | PASS -- returns 405 Method Not Allowed |
| All 7 GET progress endpoints registered | PASS -- route listing shows all GET routes |
| FastAPI health check | PASS -- {"status":"alive","api_version":"v1"} |
| No new ruff lint errors | PASS -- only 2 pre-existing warnings (F841, B905) |

## Next Phase Readiness

**Ready for 20-03** (Lua session_complete script + pipeline hot path + hearts XP):
- StageResult.time_spent is now documented as milliseconds for FSRS integration
- Legacy endpoint removed; only POST /sessions/end handles completions
- No blockers for Lua script implementation

**Ready for 20-04** (FSRS background task):
- time_spent in milliseconds is the expected input format
- Stage data flows through interaction buffer unchanged

## Self-Check: PASSED
