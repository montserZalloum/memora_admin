# Access Control Investigation Report

**Date**: 2026-02-08
**Investigation Type**: Code Review & Logic Analysis
**Status**: COMPLETE - NO BUGS FOUND

---

## Executive Summary

A comprehensive investigation of the lesson start endpoint access control logic was conducted to verify correct behavior when users with plan membership access subjects with mixed free/paid content.

**Finding**: The access control implementation is **correct and working as designed**. All components are in place and properly integrated.

---

## Investigation Scope

### Question
When a player with plan membership in a subject containing both free and paid content tries to start:
- A lesson in a **FREE topic** - should be allowed?
- A lesson in a **PAID topic** - should be allowed?

### Answer
- ✓ **FREE lessons**: Always accessible (bypass Gate 2 entirely)
- ✓ **PAID lessons**: Accessible if user has explicit grant OR subject has is_premium=0 in plan membership

---

## Code Analysis

### 1. Session Start Endpoint Logic
**File**: `fastapi_app/api/v1/endpoints/sessions.py:108-187`

```python
# Lines 158-166
if not hierarchy.is_lesson_free(request.lesson_id):
    # Lesson is NOT free - require explicit subject access or plan membership
    content_key = f"SUB-{request.subject_id}"
    has_access = await access_service.check_access_with_plan(user.sub, content_key, user.plan)
    if not has_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, ...)
```

**Verdict**: ✓ CORRECT
- Correctly checks if lesson is free using hierarchy model
- For paid lessons, delegates to access service
- Only raises 403 if access is denied

### 2. Access Service - Plan-Aware Check
**File**: `fastapi_app/services/access.py:122-151`

```python
async def check_access_with_plan(
    self, player_id: str, content_key: str, plan_id: str | None
) -> bool:
    # Check explicit grant first (fast path)
    if await self.check_access(player_id, content_key):
        return True

    # Check plan membership (if plan provided and content is subject-level)
    if plan_id and content_key.startswith("SUB-"):
        subject_id = content_key.replace("SUB-", "")
        if await self.is_subject_free_in_plan(plan_id, subject_id):
            return True

    return False
```

**Verdict**: ✓ CORRECT
- Two-level check: explicit grant OR plan membership
- Fast path for existing grants
- Proper plan validation (checks plan_id exists and key format)

### 3. Plan Membership Check
**File**: `fastapi_app/services/access.py:86-105`

```python
async def is_subject_free_in_plan(self, plan_id: str, subject_id: str) -> bool:
    if not plan_id:
        return False
    key = self._plan_free_subjects_key(plan_id)  # memora:plan:{plan_id}:free_subjects
    result = await self.redis.sismember(key, subject_id)
    return bool(result)
```

**Verdict**: ✓ CORRECT
- Queries correct Redis key
- O(1) set membership check
- Proper null handling

### 4. Hierarchy Free Content Check
**File**: `fastapi_app/models/progress.py:142-181`

```python
def is_lesson_free(self, lesson_id: str) -> bool:
    free_units_set = set(self.free_units)
    free_topics_set = set(self.free_topics)

    for track in self.tracks:
        for unit in track.units:
            if unit.unit_id in free_units_set:
                for topic in unit.topics:
                    for lesson in topic.lessons:
                        if lesson.lesson_id == lesson_id:
                            return True
            else:
                for topic in unit.topics:
                    if topic.topic_id in free_topics_set:
                        for lesson in topic.lessons:
                            if lesson.lesson_id == lesson_id:
                                return True
                    else:
                        for lesson in topic.lessons:
                            if lesson.lesson_id == lesson_id:
                                return topic.is_free
    return False
```

**Verdict**: ✓ CORRECT
- Uses cached free_units/free_topics arrays for O(1) lookups
- Falls back to is_free flags on TopicInfo
- Correctly identifies free vs paid lessons

### 5. Event Handler - Plan Sync
**File**: `memora_admin/events/access_sync.py:145-168`

```python
def on_plan_subject_changed(doc, method):
    plan_id = doc.parent
    subject_id = doc.subject
    redis_key = f"memora:plan:{plan_id}:free_subjects"
    r = get_fastapi_redis()

    if method == "on_trash":
        r.srem(redis_key, subject_id)
    elif not doc.is_premium:
        r.sadd(redis_key, subject_id)  # is_premium=0 → free in plan
    else:
        r.srem(redis_key, subject_id)  # is_premium=1 → premium in plan
```

**Verdict**: ✓ CORRECT
- Checks method to handle deletion
- Correctly maps is_premium=0 to SADD (include in plan)
- Correctly maps is_premium=1 to SREM (exclude from plan)
- Uses correct Redis key format matching access service

### 6. Event Handler Registration
**File**: `memora_admin/hooks.py:202-211`

Event handler is registered for:
- `after_insert` of Memora Plan Subject
- `on_update` of Memora Plan Subject
- `on_trash` of Memora Plan Subject

**Verdict**: ✓ CORRECT
- Hook is properly registered
- Covers all lifecycle events (create, update, delete)

### 7. Data Repair Tools
**File**: `memora_admin/events/access_sync.py:171-191`

```python
def rebuild_plan_free_subjects(plan_id: str):
    subjects = frappe.get_all(
        "Memora Plan Subject",
        filters={"parent": plan_id, "is_premium": 0},
        pluck="subject",
    )
    r = get_fastapi_redis()
    redis_key = f"memora:plan:{plan_id}:free_subjects"
    r.delete(redis_key)
    if subjects:
        r.sadd(redis_key, *subjects)
```

**Verdict**: ✓ CORRECT
- Manual repair function exists
- Queries database for authoritative data
- Rebuilds Redis set from scratch

---

## Test Scenarios

### Scenario 1: User with plan accessing FREE lesson
```
User: moonzallou19@gmail.com (Plan: PLAN-00052)
Subject: SUBJ-00028 (has UNT-00037 with is_free=true)
Lesson: In free unit

Flow:
1. hierarchy.is_lesson_free(lesson_id) = True
2. Skip Gate 2 check
3. Start session ✓

Result: SESSION STARTS
```

### Scenario 2: User with plan accessing PAID lesson
```
User: moonzallou19@gmail.com (Plan: PLAN-00052)
Subject: SUBJ-00028 (has mixed content)
Lesson: In paid topic

Flow:
1. hierarchy.is_lesson_free(lesson_id) = False
2. Call check_access_with_plan(user_sub, "SUB-SUBJ-00028", "PLAN-00052")
3. Check explicit grant: None
4. Check plan membership:
   - SISMEMBER memora:plan:PLAN-00052:free_subjects SUBJ-00028
   - If SUBJ-00028 was added with is_premium=0: Found → True ✓
   - If SUBJ-00028 was added with is_premium=1: Not found → False
5. Return True or raise 403

Result: SESSION STARTS if is_premium=0, 403 if is_premium=1
```

### Scenario 3: User without plan accessing PAID lesson
```
User: test_user@example.com (No plan)
Subject: SUBJ-00028
Lesson: In paid topic

Flow:
1. hierarchy.is_lesson_free(lesson_id) = False
2. Call check_access_with_plan(user_sub, "SUB-SUBJ-00028", None)
3. Check explicit grant: None
4. Check plan membership: plan_id is None, skip
5. Return False
6. Raise 403

Result: 403 NOT FOUND
```

---

## Edge Cases Analysis

| Case | Setup | Result | Verdict |
|------|-------|--------|---------|
| No plan | user.plan=None | Denies access to paid content | ✓ Correct |
| Free lesson | is_free=true | Bypasses access check | ✓ Correct |
| Explicit grant | SUB-* in user grants | Fast-path allow | ✓ Correct |
| Premium subject | is_premium=1 | Requires explicit grant | ✓ Correct |
| Deleted subject | method='on_trash' | Removed from plan | ✓ Correct |
| Wrong key format | TRK-* instead of SUB-* | Plan check skipped | ✓ Correct |
| Mixed plan subjects | X:is_premium=0, Y:is_premium=1 | Independently checked | ✓ Correct |

---

## Potential Issues (Already Handled)

### Issue 1: Event handler didn't exist when plans were created
- **Impact**: Subjects wouldn't be synced to Redis
- **Solution**: `rebuild_plan_free_subjects(plan_id)` utility function
- **Status**: ✓ Implemented

### Issue 2: Redis connection uses wrong REDIS_URL
- **Impact**: Event handler syncs to wrong Redis instance
- **Solution**: `get_fastapi_redis()` loads FastAPI's .env file
- **Status**: ✓ Implemented

### Issue 3: Data drift between DB and Redis
- **Impact**: Subjects updated but not synced
- **Solution**: `plan_sync.sync_all_plan_subjects_to_redis()` periodic task (every 6 hours)
- **Status**: ✓ Implemented

---

## Key Design Decisions Verified

1. **Free content gates**: ✓ Correctly bypass Gate 2
2. **Plan membership model**: ✓ Uses is_premium=0 to indicate free subjects in plan
3. **Redis key naming**: ✓ Consistent between event handler and access service
4. **Access additivity**: ✓ Explicit grant OR plan membership either allows access
5. **Subject-level granularity**: ✓ Subjects are at what permission level plan works

---

## Conclusion

The access control implementation for mixed free/paid subjects in plan memberships is **correct and complete**.

### Components Verified
✓ Endpoint correctly identifies free vs paid content
✓ Access service correctly implements two-level authorization
✓ Plan membership is correctly synced to Redis
✓ Event handlers are properly registered
✓ Repair tools are available for data consistency
✓ All edge cases handled correctly

### No Fixes Needed
The implementation matches the design specification and handles all tested scenarios correctly.

---

## Recommendations

If users report access issues despite having plan membership:

1. **Verify Redis connection**: Ensure FastAPI and Frappe use same Redis instance
2. **Check Plan Subject data**: Query `SELECT * FROM "tabMemora Plan Subject" WHERE parent='PLAN-XX'`
3. **Rebuild if needed**: Run `rebuild_plan_free_subjects('PLAN-XX')` to sync
4. **Monitor periodic sync**: Check logs for `plan_sync.sync_all_plan_subjects_to_redis()` task

---

**Investigation Complete**
