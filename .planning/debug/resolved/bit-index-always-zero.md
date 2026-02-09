---
status: resolved
trigger: "All lessons show bit_index: 0 in generated JSON files, and Memora Subject last_bit_index never updates"
created: 2026-02-09T00:00:00Z
updated: 2026-02-09T00:03:00Z
---

## Current Focus

hypothesis: CONFIRMED AND FIXED
test: verified DB values, hierarchy API output, and subject counter
expecting: n/a
next_action: archive

## Symptoms

expected: Each lesson should have a unique, sequential bit_index value. Memora Subject should track last_bit_index.
actual: All lessons show bit_index: 0 in generated JSON. last_bit_index on Memora Subject is always 0.
errors: No explicit errors - values simply not set/persisted.
reproduction: Open any generated JSON file for lessons - all bit_index values are 0. Check any Memora Subject - last_bit_index is 0.
started: Persistent issue with bit_index assignment system.

## Eliminated

## Evidence

- timestamp: 2026-02-09T00:00:30Z
  checked: MemoraLesson DocType class (memora_lesson.py)
  found: Class is empty - just `pass`. No before_insert, no validate, no bit_index assignment logic.
  implication: The PRD specifies a before_insert hook to auto-assign bit_index, but it was never implemented.

- timestamp: 2026-02-09T00:00:35Z
  checked: MemoraSubject DocType class (memora_subject.py)
  found: Class is empty - just `pass`. No logic to increment last_bit_index.
  implication: The subject counter is never updated because there's no code to do it.

- timestamp: 2026-02-09T00:00:40Z
  checked: hierarchy.py API
  found: Lines 71, 138, 142 - bit_index is allocated dynamically at RUNTIME (counter starting from 0, incrementing per lesson). This is ephemeral - never written back to DB.
  implication: The hierarchy API works correctly for runtime use but masks the underlying issue that DB values are all 0.

- timestamp: 2026-02-09T00:00:45Z
  checked: generator.py (lines 176, 190, 285)
  found: Generator reads bit_index FROM the lesson document. Since DB values are all 0, all generated JSON shows 0.
  implication: Generator is correct in reading from DB, but DB was never populated.

- timestamp: 2026-02-09T00:00:50Z
  checked: PRD-1.md section 6.3 (lines 1107-1130)
  found: PRD specifies exact implementation - before_insert hook should call assign_bit_index().
  implication: Design was specified but never coded.

- timestamp: 2026-02-09T00:00:55Z
  checked: Memora Subject DocType JSON (field name)
  found: Field is named `last_bit_index` (not `next_bit_index` as in PRD). Label says "Next Bit Index".
  implication: Field exists but naming differs from PRD. Code must use `last_bit_index`.

- timestamp: 2026-02-09T00:02:00Z
  checked: Post-fix verification
  found: DB shows LES-00039=0, LES-00140=1, LES-00316=2. Subject SUBJ-00028 last_bit_index=3. Hierarchy API returns matching values with bit_range=3.
  implication: Fix is working correctly.

## Resolution

root_cause: The bit_index auto-assignment logic specified in PRD-1.md section 6.3 was never implemented. MemoraLesson.py was an empty class with no before_insert hook. When lessons were created, bit_index defaulted to 0. The subject's last_bit_index counter was never incremented. The hierarchy API masked this by computing bit_index dynamically at runtime, but the generator reads from DB where all values were 0.

fix: |
  1. Implemented before_insert hook in MemoraLesson to auto-assign bit_index using subject's last_bit_index as monotonic counter (with row locking for concurrency safety).
  2. Created backfill script to assign correct bit_index values to existing lessons, traversing hierarchy in same order as hierarchy API (Track idx -> Unit idx -> Topic idx -> Lesson idx).
  3. Updated both hierarchy.py files to read bit_index from DB instead of computing dynamically, making DB the single source of truth.
  4. Ran backfill: LES-00039=0, LES-00140=1, LES-00316=2, SUBJ-00028.last_bit_index=3.

verification: |
  - DB query confirms unique sequential bit_index values: 0, 1, 2
  - Subject last_bit_index correctly set to 3 (next available)
  - Hierarchy API returns correct bit_index values from DB
  - bit_range correctly computed as 3
  - Ruff linting passes on all changed files

files_changed:
  - memora_admin/memora_admin/doctype/memora_lesson/memora_lesson.py (added before_insert hook for bit_index auto-assignment)
  - memora_admin/api/hierarchy.py (read bit_index from DB instead of runtime allocation)
  - memora_admin/memora_admin/api/hierarchy.py (same change, inner module copy)
  - memora_admin/memora_admin/api/backfill_bit_index.py (new: one-time backfill script)
