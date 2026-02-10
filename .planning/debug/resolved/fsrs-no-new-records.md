---
status: resolved
trigger: "FSRS processor not creating new Memora Memory State records for new stage completions"
created: 2026-02-10T00:00:00Z
updated: 2026-02-10T12:35:00Z
---

## Current Focus

hypothesis: CONFIRMED AND FIXED
test: Manual processor run + scheduler execution
expecting: N/A - resolved
next_action: Archive

## Symptoms

expected: When a player completes a stage for the first time, the FSRS processor should create a new Memora Memory State record with autoname format {season}-{subject}-{player}-{stage_id}
actual: Only existing Memora Memory State records get updated. New records are NOT being created for new stages.
errors: No errors - the FSRS processor never runs at all because it was never registered as a Scheduled Job Type
reproduction: Player completes a new stage -> Memora Interaction Log gets a "Completed" event -> FSRS processor never runs -> No new Memora Memory State record is created
started: Since the duplicate key was introduced (likely when build_worker was moved to "* * * * *")

## Eliminated

- hypothesis: FSRS processor has a bug in its create path (frappe.get_doc().insert())
  evidence: Code logic is correct, but processor never runs at all - not registered as Scheduled Job Type
  timestamp: 2026-02-10T12:25:00Z

- hypothesis: Import error preventing registration
  evidence: Both fsrs_processor and sync modules import successfully in bench console
  timestamp: 2026-02-10T12:25:00Z

## Evidence

- timestamp: 2026-02-10T12:20:00Z
  checked: Scheduled Job Type table for FSRS-related jobs
  found: Zero records - FSRS processor is not registered as a scheduled job
  implication: Processor never runs via Frappe scheduler

- timestamp: 2026-02-10T12:20:00Z
  checked: All memora Scheduled Job Types (7 total)
  found: Missing 4 tasks: sync_dirty_progress, sync_dirty_wallets, flush_interaction_buffer, process_fsrs_reviews
  implication: All 4 tasks from first "* * * * *" key are missing - confirms duplicate key problem

- timestamp: 2026-02-10T12:22:00Z
  checked: hooks.py lines 228-237
  found: Two "* * * * *" keys in cron dict - Python dict deduplication drops the first (with 4 tasks)
  implication: ROOT CAUSE - second key overwrites first, only build_worker.process_pending_builds survives

- timestamp: 2026-02-10T12:25:00Z
  checked: frappe.get_hooks("scheduler_events")["cron"]["* * * * *"]
  found: Only contains ["memora_admin.tasks.build_worker.process_pending_builds"]
  implication: Confirms the 4 tasks are completely invisible to Frappe's scheduler

- timestamp: 2026-02-10T12:26:00Z
  checked: Active season and test interaction data
  found: Season SEAS-00027 active, interaction LOG-00514 (stage uustlcf17b, LES-00454) has is_reviewable=1, stage is not skippable, subject=SUBJ-00448
  implication: If processor ran, this interaction WOULD create a new Memory State record

- timestamp: 2026-02-10T12:30:00Z
  checked: Manual execution of process_fsrs_reviews()
  found: Successfully created new Memory State record SEAS-00027-SUBJ-00448-moonzalloum19@gmail.com-uustlcf17b (stab=0.212, diff=6.4133)
  implication: Processor logic is correct - was just never being called

- timestamp: 2026-02-10T12:32:00Z
  checked: Scheduled Job Log after bench restart + 1 minute
  found: FSRS processor ran as "Complete" at 12:32:09. All sync tasks also ran successfully.
  implication: Fix is working - all 5 tasks now execute on the cron schedule

## Resolution

root_cause: hooks.py had duplicate "* * * * *" cron key in scheduler_events dict. Python dicts silently drop duplicate keys (last one wins). The first key contained 4 tasks (process_fsrs_reviews, sync_dirty_progress, sync_dirty_wallets, flush_interaction_buffer). The second key (build_worker.process_pending_builds) overwrote it. As a result, those 4 tasks were never registered as Scheduled Job Type records and never ran.

fix: Merged both "* * * * *" lists into a single key with all 5 tasks. Also fixed fsrs_backfill.py import reference (_get_skippable_stages -> _get_skippable_stage_types). Ran sync_jobs() to register the new Scheduled Job Types. Ran bench restart to activate the scheduler.

verification:
  1. Manual run of process_fsrs_reviews() created new Memory State record (before=1, after=2)
  2. After bench restart, Scheduled Job Log shows "Complete" for fsrs_processor.process_fsrs_reviews at 12:32:09
  3. All 3 sync tasks also showing "Complete" in logs
  4. Total Scheduled Job Types for memora went from 7 to 11 (4 new tasks registered)

files_changed: [memora_admin/hooks.py, memora_admin/tasks/fsrs_backfill.py]
