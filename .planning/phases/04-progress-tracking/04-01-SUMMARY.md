---
phase: "04-progress-tracking"
plan: "01"
subsystem: "progress-models"
tags: ["redis", "bitmap", "pydantic", "progress-tracking"]

dependency_graph:
  requires: ["03-access-control"]
  provides: ["ProgressService", "Progress models", "Hierarchy models"]
  affects: ["04-02", "04-03"]

tech_stack:
  added: []
  patterns: ["redis bitmap O(1) operations", "computed_field for percentages", "hierarchy model nesting"]

key_files:
  created:
    - fastapi_app/models/progress.py
    - fastapi_app/services/progress.py
  modified:
    - fastapi_app/models/__init__.py
    - fastapi_app/services/__init__.py

decisions:
  - id: "04-01-01"
    choice: "Use computed_field decorator for percentage calculation"
    rationale: "Auto-calculates on access, stays in sync with completed/total values"

metrics:
  duration: "2min"
  completed: "2026-02-02"
---

# Phase 04 Plan 01: Progress Models and ProgressService Summary

Redis bitmap-based progress tracking with O(1) SETBIT/GETBIT operations and nested Pydantic hierarchy models for unlock calculation.

## What Was Built

### Progress Request/Response Models

`fastapi_app/models/progress.py`:
- **CompleteRequest**: Lesson completion input with `subject` + `lesson` fields
- **CompleteResponse**: Minimal `{ success: true }` response per CONTEXT.md
- **SubjectSummary**: Lightweight progress listing with percentage

### Hierarchy Models for Unlock Calculation

Nested structure for unlock state calculation:
- **LessonInfo**: `lesson_id`, `bit_index`, `xp`
- **TopicInfo**: Contains lessons, `is_linear` flag
- **UnitInfo**: Contains topics, `is_linear` and `is_free` flags
- **TrackInfo**: Contains units, `is_linear` flag
- **SubjectHierarchy**: Root with `find_lesson()` method, `version`, `bit_range`, `excluded_bits`

### Progress Response Models

Computed percentages at each hierarchy level:
- **TopicProgress**: `completed/total` with `@computed_field percentage`
- **UnitProgress**: Aggregates topics, same computed percentage pattern
- **TrackProgress**: Aggregates units
- **SubjectProgress**: Full breakdown for detailed progress endpoint

### ProgressService

`fastapi_app/services/progress.py`:

| Method | Redis Op | Complexity | Purpose |
|--------|----------|------------|---------|
| `complete_lesson()` | SETBIT | O(1) | Mark lesson complete, returns replay boolean |
| `is_complete()` | GETBIT | O(1) | Check single lesson status |
| `get_completed_count()` | BITCOUNT | O(N) | Total completed lessons |
| `get_completed_bits()` | Pipeline GETBIT | O(N) | Set of completed indexes for unlock calc |

Key pattern: `memora:progress:{user_id}:{subject_id}:v{version}`

## Key Implementation Details

### Replay Detection via SETBIT Return Value

```python
async def complete_lesson(self, user_id, subject_id, bit_index, version=1) -> bool:
    key = self._progress_key(user_id, subject_id, version)
    previous = await self.redis.setbit(key, bit_index, 1)
    return bool(previous)  # True = replay, False = first completion
```

Per CONTEXT.md, SETBIT's return value (previous bit value) naturally provides replay detection without extra queries.

### Safe Division for Percentages

```python
@computed_field
@property
def percentage(self) -> float:
    if self.total == 0:
        return 0.0
    return round(self.completed / self.total * 100, 1)
```

Prevents division-by-zero, rounds to 1 decimal place.

### Recursive Lesson Lookup

```python
def find_lesson(self, lesson_id: str) -> LessonInfo | None:
    for track in self.tracks:
        for unit in track.units:
            for topic in unit.topics:
                for lesson in topic.lessons:
                    if lesson.lesson_id == lesson_id:
                        return lesson
    return None
```

Used by completion endpoint to map lesson ID to bit_index.

## Verification Results

All checks passed:

```
Test 1: Models validate correctly - PASSED
Test 2: ProgressService signatures correct - PASSED
Test 3: Zero-division handled - PASSED
```

## Deviations from Plan

None - plan executed exactly as written.

## Decisions Made

| ID | Decision | Rationale |
|----|----------|-----------|
| 04-01-01 | Use `computed_field` decorator for percentage | Auto-calculates on access, stays synchronized |

## Commits

| Hash | Type | Description |
|------|------|-------------|
| `883f791` | feat | Create progress tracking models |
| `91f9f09` | feat | Create ProgressService for Redis bitmap operations |

## Next Phase Readiness

Ready for 04-02:
- ProgressService ready for endpoint wiring
- Hierarchy models defined for unlock calculation
- Progress response models ready for API responses

Blockers: None
