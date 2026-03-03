# API Contracts: Progress & Practice Read-Path Performance

**Feature Branch**: `036-read-path-perf`
**Date**: 2026-03-03

## No API Contract Changes

This feature is a pure internal performance optimization. All existing API response shapes, status codes, and business rules are preserved exactly.

### Endpoints Affected (Internal Optimization Only)

| Endpoint | Method | Response Model | Change |
|----------|--------|---------------|--------|
| `/api/v1/progress/` | GET | `list[SubjectSummary]` | Bounded concurrency (internal) |
| `/api/v1/progress/{subject}` | GET | `SubjectProgress` | Stats-first read path (internal) |
| `/api/v1/progress/{subject}/tracks` | GET | `list[TrackSummary]` | Stats-first + HMGET (internal) |
| `/api/v1/progress/{subject}/tracks/{track_id}` | GET | `TrackDetail` | Stats-first + HMGET (internal) |
| `/api/v1/progress/{subject}/tracks/{tid}/units/{uid}` | GET | `UnitDetail` | Stats-first + HMGET (internal) |
| `/api/v1/progress/{subject}/topics/{tid}/lessons` | GET | `TopicLessonsResponse` | No change |
| `/api/v1/practice/hierarchy` | GET | `PracticeHierarchyResponse` | Access hoisting + meta coalescing (internal) |

### Verification

All existing test files MUST continue to pass without modification:
- `fastapi_app/tests/test_progress_endpoints.py`
- `fastapi_app/tests/test_stats_service.py`
- `fastapi_app/tests/test_progress_service.py`
- `fastapi_app/tests/test_content_hash.py`
- `fastapi_app/tests/test_practice_endpoints.py` (if exists)
