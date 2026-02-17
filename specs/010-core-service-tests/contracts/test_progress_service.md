# Test Contract: ProgressService

**File**: `fastapi_app/tests/test_progress_service.py`
**Service Under Test**: `fastapi_app/services/progress.py` → `ProgressService`
**Constructor**: `ProgressService(redis_client, key_prefix="memora:", frappe_client=None)`

## Setup Pattern

```python
from fastapi_app.core.constants import DIRTY_PROGRESS_KEY

@pytest.fixture
def progress_service(redis_client, test_prefix, mock_frappe):
    return ProgressService(redis_client, key_prefix=test_prefix, frappe_client=mock_frappe)

@pytest.fixture
def progress_service_no_frappe(redis_client, test_prefix):
    return ProgressService(redis_client, key_prefix=test_prefix, frappe_client=None)
```

## Constants

```python
TEST_USER = "USER-TEST-001"
TEST_SUBJECT = "MATH-G5"
TEST_VERSION = 1
# Redis key: {test_prefix}progress:USER-TEST-001:MATH-G5:v1
# Dirty member: "USER-TEST-001:MATH-G5:v1"
```

## Test Cases (8 total)

### Lesson Completion (Tests 1-3)

| # | Test Name | Setup | Action | Assertion |
|---|-----------|-------|--------|-----------|
| 1 | `test_complete_first_time` | None | `complete_lesson(USER, SUBJ, bit_index=5)` | Returns `False` (not replay). `GETBIT key 5` == 1 |
| 2 | `test_complete_replay` | `SETBIT key 5 1` | `complete_lesson(USER, SUBJ, bit_index=5)` | Returns `True` (replay). Bit still 1 |
| 3 | `test_complete_marks_dirty` | None | `complete_lesson(USER, SUBJ, bit_index=5)` | `SISMEMBER memora:dirty:progress "USER-TEST-001:MATH-G5:v1"` == True |

### Read Operations (Tests 4-6)

| # | Test Name | Setup | Action | Assertion |
|---|-----------|-------|--------|-----------|
| 4 | `test_is_complete_true` | `complete_lesson(USER, SUBJ, 5)` | `is_complete(USER, SUBJ, 5)` | Returns `True` |
| 5 | `test_is_complete_false` | None (empty bitmap) | `is_complete(USER, SUBJ, 5)` | Returns `False` |
| 6 | `test_get_completed_count` | `complete_lesson` for bits 0, 5, 10 | `get_completed_count(USER, SUBJ)` | Returns `3` |

### Hydration (Tests 7-8)

| # | Test Name | Setup | Action | Assertion |
|---|-----------|-------|--------|-----------|
| 7 | `test_hydration_from_hex` | No Redis data. `mock_frappe.call.return_value = {"passed_lessons_bitset": "8001"}` | `ensure_hydrated(USER, SUBJ)` | `GETBIT key 0` == 1, `GETBIT key 15` == 1, `BITCOUNT key` == 2. `mock_frappe.call` called with `("memora_admin.api.subscriptions.get_player_progress", {"player_id": USER, "subject_id": SUBJ})` |
| 8 | `test_hydration_no_client_skips` | No Redis data, use `progress_service_no_frappe` | `ensure_hydrated(USER, SUBJ)` | No crash. `GETBIT key 0` == 0 (nothing hydrated) |

## Dirty Key Cleanup

```python
@pytest.fixture(autouse=True)
async def cleanup_dirty_progress(redis_client):
    yield
    await redis_client.srem(DIRTY_PROGRESS_KEY, "USER-TEST-001:MATH-G5:v1")
```
