# Unit Tests: Content Lifecycle Events

**File:** `fastapi_app/tests/test_content_lifecycle_events.py`
**Tests:** 83 passing
**Run:** `pytest fastapi_app/tests/test_content_lifecycle_events.py -v`

## What Is Being Tested

When an admin publishes, unpublishes, or deletes content in Frappe, two event handler modules fire:

| Module | Handler | Triggered By |
|--------|---------|-------------|
| `memora_admin/events/build_trigger.py` | `on_content_updated` | Subject, Track, Unit, Topic, Lesson save/delete |
| `memora_admin/events/build_trigger.py` | `on_plan_updated` | Academic Plan save |
| `memora_admin/events/build_trigger.py` | `on_plan_deleted` | Academic Plan delete *(GAP 1 fix)* |
| `memora_admin/events/build_trigger.py` | `on_plan_subject_changed` | Plan Subject add/modify/delete |
| `memora_admin/events/access_sync.py` | `on_season_updated` / `on_season_deleted` | Season save/delete |
| `memora_admin/events/access_sync.py` | `on_subscription_change` / `on_subscription_deleted` | Player Subscription save/delete |
| `memora_admin/events/access_sync.py` | `on_plan_subject_changed` | Plan Subject `is_premium` flag change |
| `memora_admin/events/access_sync.py` | `on_unit_free_changed` | Unit `is_free` flag change |
| `memora_admin/events/access_sync.py` | `on_topic_free_changed` | Topic `is_free` flag change |

## Test Classes & Coverage

### `TestGetSubjectId` (7 tests)

Tests `_get_subject_id(doc)` — the helper that resolves a subject ID from any content DocType.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-GS-01 | Memora Subject | Returns `doc.name` directly |
| TC-GS-02 | Memora Track | Returns `doc.subject` (no ORM call) |
| TC-GS-03 | Memora Unit | Calls `get_cached_value("Memora Track", track, "subject")` |
| TC-GS-04 | Memora Unit with `track=None` | Returns `None` safely |
| TC-GS-05 | Memora Topic | Two `get_cached_value` calls (Unit→Track, Track→Subject) |
| TC-GS-06 | Memora Lesson | Returns `doc.subject` directly |
| TC-GS-07 | Unknown DocType | Returns `None` (defensive) |

### `TestHasIsPremiumChanged` (5 tests)

Tests `_has_is_premium_changed(old_doc, doc)` — detects whether any subject flipped its `is_premium` value.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-HPC-01 | No old doc (new plan) | Treated as changed (triggers rebuild) |
| TC-HPC-02 | Identical `is_premium` values | No change detected |
| TC-HPC-03 | Subject flips `is_premium` 0→1 | Change detected |
| TC-HPC-04 | New subject added to plan | Change detected |
| TC-HPC-05 | Subject removed from plan | Change detected |

### `TestInvalidateHierarchyCache` (3 tests)

Tests `_invalidate_hierarchy_cache(subject_id)` — two-pronged cache invalidation.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-IHC-01 | Normal call | DEL `memora:hierarchy:{subject_id}` |
| TC-IHC-02 | Normal call | Publishes `{"type": "hierarchy", "subject_id": ...}` to cache channel |
| TC-IHC-03 | Redis unavailable | Error logged, no exception raised |

### `TestInvalidateCatalogCache` (3 tests)

Tests `_invalidate_catalog_cache(plan_id)` — same two-pronged pattern for catalog.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-ICC-01 | Normal call | DEL `memora:catalog:{plan_id}` |
| TC-ICC-02 | Normal call | Publishes `{"type": "catalog", "plan_id": ...}` to cache channel |
| TC-ICC-03 | Redis unavailable | Error logged, no exception raised |

### `TestOnContentUpdated` (14 tests)

Tests `on_content_updated(doc, method)` across all content DocTypes.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-OCU-01 | Subject `on_update` | Hierarchy cache for subject deleted |
| TC-OCU-02 | Subject `on_trash` | Hierarchy cache for subject deleted |
| TC-OCU-03 | Track `on_update` | Resolves subject via `doc.subject`, invalidates cache |
| TC-OCU-04 | Unit `on_update` | Resolves subject via `Unit.track → get_cached_value`, invalidates cache |
| TC-OCU-05 | Topic `on_update` | Resolves subject via two ORM hops, invalidates cache |
| TC-OCU-06 | Lesson `on_update` | Resolves subject via `doc.subject`, invalidates cache |
| TC-OCU-07 | Lesson `on_trash` | `_delete_lesson_json(lesson_id)` called (orphaned file cleanup) |
| TC-OCU-08 | Non-lesson `on_trash` | `_delete_lesson_json` NOT called |
| TC-OCU-09 | Unit with `track=None` | `log_error` called; no cache or build ops |
| TC-OCU-10 | Subject in 2 plans | Build queue entry created per plan (2 entries) |
| TC-OCU-11 | Subject in no plans | No build queue entries created |
| TC-OCU-12 | Normal call | Build queue entry has `trigger_reason='content_update'` |
| TC-OCU-13 | Subject `on_trash` | `_remove_subject_from_plan_free_subjects(subject_id)` called *(GAP 2 fix)* |
| TC-OCU-14 | Subject `on_update` | `_remove_subject_from_plan_free_subjects` NOT called |

### `TestOnPlanDeleted` (7 tests)

Tests `on_plan_deleted(doc, method)` — full cleanup when an Academic Plan is deleted (GAP 1 fix).

The handler delegates to two internal helpers:
- `_delete_plan_directory(plan_id)` — lists files first, then deletes `plans/{plan_id}/` from storage, then purges listed files from CDN.
- `_delete_plan_redis_keys(plan_id)` — deletes four Redis keys in one call, then publishes cache invalidation.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-OPD-01 | Normal delete | `storage.delete_directory("plans/{plan_id}")` called |
| TC-OPD-02 | Directory has files | `purge_service.purge_files(file_keys)` called with listed keys |
| TC-OPD-03 | Directory already empty | CDN purge service never instantiated |
| TC-OPD-04 | Normal delete | `catalog_key`, `plan_manifest_key`, `plan_free_subjects_key`, `build_debounce_key` all deleted |
| TC-OPD-05 | Normal delete | Publishes `{"type": "catalog", "plan_id": ...}` to cache invalidation channel |
| TC-OPD-06 | Storage throws | Error logged; Redis cleanup still executes (independent try/except) |
| TC-OPD-07 | Redis throws | Error logged; does not propagate |

### `TestRemoveSubjectFromPlanFreeSubjects` (4 tests)

Tests `_remove_subject_from_plan_free_subjects(subject_id)` — removes a deleted subject from all plan free_subjects Redis sets (GAP 2 fix).

This covers the gap where a Memora Subject is deleted directly (not via Plan Subject child row deletion), leaving stale entries in `memora:plan:{plan_id}:free_subjects` sets.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-RSF-01 | Subject was free in 2 plans | SREM from both `plan_free_subjects_key` sets |
| TC-RSF-02 | Any call | Queries Plan Subject with `filters={"subject": ..., "is_premium": 0}` |
| TC-RSF-03 | No plans have subject as free | `get_memora_redis` never called (early return) |
| TC-RSF-04 | Redis throws | Error logged; does not propagate |

### `TestDebounce` (3 tests)

Tests the 120-second per-plan deduplication preventing build storms.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-DEB-01 | First update (`SET NX EX` succeeds) | Build queue entry created |
| TC-DEB-02 | Second update within 120s (`SET NX EX` → None) | No duplicate entry |
| TC-DEB-03 | Build queue insert fails | Debounce key cleaned up (so next update retries) |

### `TestOnPlanUpdated` (7 tests)

Tests `on_plan_updated(doc, method)` — Academic Plan changes.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-OPU-01 | Any plan update | Catalog cache always invalidated |
| TC-OPU-02 | Any plan update | Build queue with `trigger_reason='plan_update'` |
| TC-OPU-03 | Season field changed | `memora:plan_season_seq:{plan_id}` deleted |
| TC-OPU-04 | Season field unchanged | `plan_season_seq` key NOT touched |
| TC-OPU-05 | `is_premium` flipped on a subject | `rebuild_plan_free_subjects(plan_id)` called |
| TC-OPU-06 | `is_premium` unchanged | `rebuild_plan_free_subjects` NOT called |
| TC-OPU-07 | `is_premium` changed | Pubsub `plan_subjects` event published for plan |

### `TestOnPlanSubjectChanged` (4 tests)

Tests `on_plan_subject_changed(doc, method)` — Plan Subject add/modify/delete.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-OPSC-01 | Plan Subject modified | Hierarchy cache for subject deleted |
| TC-OPSC-02 | Plan Subject modified | Catalog cache for plan deleted |
| TC-OPSC-03 | Plan Subject modified | Build queue with `trigger_reason='plan_subject_change'` |
| TC-OPSC-04 | Doc with `parent=None` | Returns early; no cache or build ops |

### `TestSeasonLifecycle` (5 tests)

Tests `on_season_updated` / `on_season_deleted` — Gate 1 (season validation).

| TC | Scenario | Expected |
|----|----------|----------|
| TC-SEA-01 | Season published (`is_published=1`) | Redis hash stores `is_published='1'` |
| TC-SEA-02 | Season unpublished (`is_published=0`) | Redis hash stores `is_published='0'` |
| TC-SEA-03 | Season updated | Uses `season_key()` builder for Redis key |
| TC-SEA-04 | Season updated | Stores `start_date`, `end_date`, `season_seq` |
| TC-SEA-05 | Season deleted | DEL `memora:season:{id}` |

### `TestSubscriptionLifecycle` (7 tests)

Tests `on_subscription_change` / `on_subscription_deleted` — Gate 2 (access grants).

| TC | Scenario | Expected |
|----|----------|----------|
| TC-SUB-01 | `is_active=1` (grant) | SADD access key to `memora:access:{player}` |
| TC-SUB-02 | Subscription activated | EXPIRE set to `ACCESS_KEY_TTL` (24h) |
| TC-SUB-03 | Subscription changed | Publishes `subscription_changed` event with `player_id` |
| TC-SUB-04 | `is_active=0` (revoke) | SREM access key from set; SADD NOT called |
| TC-SUB-05 | Deactivated, other grants remain | EXPIRE refreshed on remaining set |
| TC-SUB-06 | Subscription deleted | SREM access key from set |
| TC-SUB-07 | Subscription deleted | Publishes `subscription_changed` event |

### `TestPlanSubjectFreeSync` (5 tests)

Tests `access_sync.on_plan_subject_changed` — `memora:plan:{plan}:free_subjects` set maintenance.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-PSF-01 | `on_trash` (any `is_premium`) | SREM subject from `plan_free_subjects` set |
| TC-PSF-02 | `is_premium=0` (free subject) | SADD subject to `plan_free_subjects` set |
| TC-PSF-03 | Subject added as free | EXPIRE set to `PLAN_FREE_SUBJECTS_TTL` (12h) |
| TC-PSF-04 | `is_premium=1` (premium subject) | SREM subject from `plan_free_subjects` set |
| TC-PSF-05 | Any plan_subject change | Publishes `plan_subjects` event for the plan |

### `TestFreeContentSync` (9 tests)

Tests `on_unit_free_changed` / `on_topic_free_changed` — `memora:subjects_with_free_content` set.

| TC | Scenario | Expected |
|----|----------|----------|
| TC-FCS-01 | Unit `is_free=1` | SADD subject to `subjects_with_free_content` |
| TC-FCS-02 | Unit `is_free=0`, no other free content | DB checked; SREM subject |
| TC-FCS-03 | Unit `is_free=0`, other free content exists | DB checked; SADD subject (kept) |
| TC-FCS-04 | Unit `on_trash`, no free content remains | DB checked; SREM subject |
| TC-FCS-05 | Unit `on_trash`, free content remains | DB checked; SADD subject (kept) |
| TC-FCS-06 | Unit with `track=None` | Returns early; no Redis or Frappe ops |
| TC-FCS-07 | Topic `is_free=1` | SADD subject to `subjects_with_free_content` |
| TC-FCS-08 | Topic `on_trash`, no free content remains | DB checked; SREM subject |
| TC-FCS-09 | Topic with `unit=None` | Returns early; no Redis or Frappe ops |

## What Is NOT Tested Here

| Area | Reason |
|------|--------|
| `on_plan_overrider_changed` | Follows identical debounce + build queue pattern; covered by pattern tests |
| `_delete_lesson_json` internals | Storage/CDN operations; pattern is the same as `_delete_plan_directory` which is fully tested |
| `rebuild_plan_free_subjects` internals | Requires real Frappe DB; tested via integration tests |
| `rebuild_subjects_with_free_content` | Bulk rebuild utility; intended for manual repair |
| Plan manifest build execution | Build queue consumer lives in a separate service |
| Pubsub consumer (FastAPI side) | Tested in `test_hierarchy_service.py` (TestInvalidation) |
| Concurrent invalidation races | Tested in load tests |

## How the Mock Strategy Works

These tests mock all external dependencies so they run in the FastAPI pytest
environment with no Frappe database required:

```
@patch("memora_admin.events.build_trigger.frappe")               # Frappe ORM/cache/logger
@patch("memora_admin.utils.redis_connection.get_memora_redis")   # Redis client
@patch("memora_admin.memora_admin.services.build.storage.get_storage_backend")  # Storage (lazy import)
@patch("memora_admin.memora_admin.services.cdn.utils.get_purge_service")        # CDN (lazy import)
```

**Key note on lazy imports:** When a function does `from X import Y` inside its body, Python resolves `X.Y` fresh on each call. Patch at the *source module* (where the function is defined), not at the caller:

```python
# CORRECT — patch where rebuild_plan_free_subjects lives
with patch("memora_admin.events.access_sync.rebuild_plan_free_subjects"):
    ...

# CORRECT — patch where get_storage_backend lives
with patch("memora_admin.memora_admin.services.build.storage.get_storage_backend"):
    ...

# WRONG (attribute doesn't exist on build_trigger module)
with patch("memora_admin.events.build_trigger.rebuild_plan_free_subjects"):
    ...
```

**Decorator argument order:** With multiple `@patch` decorators, the bottom-most decorator produces the first mock argument:

```python
@patch("...frappe")              # → mock_frappe (4th arg)
@patch("...get_memora_redis")    # → mock_get_redis (3rd arg)
@patch("...get_purge_service")   # → mock_get_purge (2nd arg)
@patch("...get_storage_backend") # → mock_get_storage (1st arg)
def test_...(self, mock_get_storage, mock_get_purge, mock_get_redis, mock_frappe):
```

## Key Invariants Confirmed by These Tests

1. **Hierarchy cache invalidated immediately** on any content change — not delayed until plan build completes.
2. **Both hierarchy AND catalog caches** invalidated when a Plan Subject changes (double-cache dependency).
3. **Debounce prevents build storms** — rapid edits produce exactly one build queue entry per plan per 120s.
4. **Lesson JSON cleaned up** on lesson delete — no orphaned files left on storage/CDN.
5. **Season Gate 1** reflects `is_published` state in Redis within the same request.
6. **Subscription Gate 2** is updated atomically (SADD/SREM) with TTL refresh on the access set.
7. **Free content set** (`subjects_with_free_content`) uses DB to make a single correct decision, not a cached assumption.
8. **Best-effort Redis ops** — errors in cache invalidation are logged and swallowed, not propagated.
9. **Plan deletion fully cleans up** — storage directory, CDN cache, and all four Redis keys are removed; storage failure is isolated and does not block Redis cleanup.
10. **Subject deletion removes stale free_subjects entries** — direct Subject deletion is handled separately from Plan Subject child row deletion; only non-premium (`is_premium=0`) records are queried.
