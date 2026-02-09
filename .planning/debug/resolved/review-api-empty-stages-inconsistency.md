---
status: resolved
trigger: "/api/v1/reviews returns subject with due_count: 1, but /api/v1/reviews/{subject_id} returns empty stages: [] with has_more: true"
created: 2026-02-09T00:00:00Z
updated: 2026-02-09T00:03:00Z
---

## Current Focus

hypothesis: CONFIRMED AND FIXED
test: HTTP endpoint testing via curl
expecting: Consistent data between overview and detail
next_action: Archive and commit

## Symptoms

expected: If /api/v1/reviews/SUBJ-00028 returns no stages, SUBJ-00028 shouldn't appear in summary; if stages is empty, has_more should be false
actual: Summary shows due_count:1 for SUBJ-00028 but detail returns empty stages with has_more:true
errors: None (no HTTP errors, just incorrect data)
reproduction: GET /api/v1/reviews with moonzallou19@gmail.com shows SUBJ-00028 due_count:1, GET /api/v1/reviews/SUBJ-00028 returns empty stages with has_more:true
started: First discovered during initial testing after implementing Phase 25 review API

## Eliminated

- hypothesis: Redis cache inconsistency between endpoints
  evidence: Summary delegates to Frappe API which queries DB directly. Detail also queries DB. Both hit same DB.
  timestamp: 2026-02-09T00:01:00Z

- hypothesis: Pagination offset bug
  evidence: No offset/pagination parameters are used.
  timestamp: 2026-02-09T00:01:00Z

- hypothesis: Orphaned/removed stages in Memory State
  evidence: The stage DOES exist in Memora Lesson Stage (name='aerviq97bb'). The lookup was using the wrong field (stage_title instead of name).
  timestamp: 2026-02-09T00:02:00Z

## Evidence

- timestamp: 2026-02-09T00:00:30Z
  checked: reviews.py get_review_overview (line 24-35)
  found: Counted ALL Memory State records with next_review <= today. No stage validation. No JOIN.
  implication: Overview count included stages that failed detail validation.

- timestamp: 2026-02-09T00:00:30Z
  checked: reviews.py get_due_stages (line 77-83) - old stage validation
  found: Used frappe.db.get_value("Memora Lesson Stage", {"parent": lesson, "stage_title": stage_id}). This looked up by stage_title field.
  implication: stage_id='aerviq97bb' is the child table row NAME, not stage_title. stage_title='1'. Lookup always failed.

- timestamp: 2026-02-09T00:00:30Z
  checked: reviews.py get_due_stages (line 96-105) - old has_more logic
  found: total_due counted raw Memory State records (no validation). has_more = total_due > len(result). With result=[] and total_due=1, has_more=True.
  implication: has_more was ALWAYS wrong when stages were filtered out.

- timestamp: 2026-02-09T00:00:45Z
  checked: Actual data - Memory State for SUBJ-00028
  found: 1 record exists: stage_id='aerviq97bb', lesson='LES-00039'. The Lesson Stage child table has name='aerviq97bb', stage_title='1', stage_type='MATCHING'. Old code looked up stage_title='aerviq97bb' which never matched.
  implication: The stage was never actually orphaned - the lookup was using the wrong field.

- timestamp: 2026-02-09T00:01:00Z
  checked: fsrs_processor.py line 163-168
  found: Same bug - used {"parent": lesson, "stage_title": stage_id} for stage lookup. Should be {"name": stage_id, "parent": lesson}.
  implication: FSRS processor also had wrong field lookup for skippable stage check.

- timestamp: 2026-02-09T00:02:00Z
  checked: Fixed JOIN queries against live data via bench console
  found: INNER JOIN with ls.name = ms.stage_id correctly matches. Overview returns due_count=1, detail returns 1 stage.
  implication: Fix is verified at the SQL level.

- timestamp: 2026-02-09T00:03:00Z
  checked: Full HTTP endpoint testing after supervisorctl restart all
  found: GET /api/v1/reviews returns {"subjects":[{"subject_id":"SUBJ-00028","due_count":1}]}. GET /api/v1/reviews/SUBJ-00028 returns {"subject_id":"SUBJ-00028","stages":[{"stage_id":"aerviq97bb","lesson_id":"LES-00039","stage_type":"MATCHING"}],"has_more":false}. Consistent.
  implication: Fix fully verified end-to-end.

## Resolution

root_cause: Three interconnected bugs in memora_admin/api/reviews.py and memora_admin/tasks/fsrs_processor.py:
  1. FIELD MISMATCH (primary cause): Stage validation used wrong field. stage_id in Memory State stores the Frappe child table row `name` (e.g., 'aerviq97bb'), but lookups compared against `stage_title` (which is a user-facing label field, e.g., '1'). This caused ALL stage lookups to fail, making every stage appear as if it was removed.
  2. INCONSISTENT COUNTING: get_review_overview counted raw Memory State records (no validation JOIN), but get_due_stages filtered stages through the (broken) per-row validation. Overview showed due_count=1 while detail returned 0 valid stages.
  3. WRONG has_more LOGIC: get_due_stages computed has_more from raw DB count (total_due) vs validated result count (len(result)). Since validation always failed (bug 1), has_more was always True whenever Memory State records existed, even when stages=[] was returned.
fix: |
  1. Replaced per-row validation in get_due_stages with SQL INNER JOIN: `INNER JOIN tabMemora Lesson Stage ls ON ls.name = ms.stage_id AND ls.parent = ms.lesson`. This correctly matches stage_id to the child table row name.
  2. Applied same INNER JOIN to get_review_overview so overview counts are consistent with detail results.
  3. Applied same INNER JOIN to submit_reviews remaining_due count.
  4. Changed has_more to use limit+1 fetch pattern: fetch limit+1 rows via SQL, has_more = len(rows) > limit. Eliminates separate COUNT query and ensures accuracy.
  5. Fixed fsrs_processor.py stage lookup from {"parent": lesson, "stage_title": stage_id} to {"name": stage_id, "parent": lesson}.
verification: |
  - All three SQL queries tested via bench console - correct results
  - HTTP endpoint testing via curl after full supervisor restart:
    - GET /api/v1/reviews: {"subjects":[{"subject_id":"SUBJ-00028","due_count":1}]}
    - GET /api/v1/reviews/SUBJ-00028: stages=[{stage_id,lesson_id,stage_type}], has_more=false
    - Overview count (1) matches detail stage count (1) - CONSISTENT
  - Edge case: has_more=false when total due equals returned count
  - Ruff linting passes on both files
files_changed:
  - memora_admin/api/reviews.py
  - memora_admin/tasks/fsrs_processor.py
