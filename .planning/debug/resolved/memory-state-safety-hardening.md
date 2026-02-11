---
status: resolved
trigger: "memory-state-safety-hardening: preventative hardening for range-partitioned 10B-row table"
created: 2026-02-11T00:00:00Z
updated: 2026-02-11T02:00:00Z
---

## Current Focus

hypothesis: N/A -- all 6 tasks complete
test: N/A
expecting: N/A
next_action: Archive session

## Symptoms

expected: All code accessing tabMemora Memory State uses raw SQL only, includes season_seq in WHERE, uses UUID_TO_BIN/BIN_TO_UUID for item_id, protected against accidental schema modifications
actual: Table works but lacks safety guardrails. No protection against bench migrate ALTER TABLE on 10B-row table.
errors: None currently - preventative hardening
reproduction: N/A
started: Table recently created, hardening needed before production data grows

## Eliminated

- hypothesis: Production code may use Frappe ORM on Memory State
  evidence: Full grep search found zero instances of frappe.get_all/get_list/get_doc/db.get_value/db.exists/db.count/db.set_value targeting Memory State DocType. All queries use frappe.db.sql() with raw SQL.
  timestamp: 2026-02-11T00:30:00Z

- hypothesis: Queries may be missing season_seq for partition pruning
  evidence: All 6 query patterns include season_seq in WHERE clause. EXPLAIN PARTITIONS confirms single-partition access for all queries.
  timestamp: 2026-02-11T00:35:00Z

- hypothesis: Queries may be missing UUID_TO_BIN for item_id
  evidence: All queries touching item_id use UUID_TO_BIN() (inserts, lookups) or BIN_TO_UUID() (selects).
  timestamp: 2026-02-11T00:35:00Z

## Evidence

- timestamp: 2026-02-11T00:20:00Z
  checked: All files referencing tabMemora Memory State
  found: Production code in fsrs_processor.py, reviews.py, profile.py all use raw SQL correctly.
  implication: No ORM violations in current code

- timestamp: 2026-02-11T00:25:00Z
  checked: is_virtual handling in Frappe source code
  found: schema.py line 94 skips is_virtual fields. meta.py excludes is_virtual from get_fieldnames_with_value().
  implication: Frappe will NOT try to CREATE/ALTER/DROP item_id during bench migrate.

- timestamp: 2026-02-11T00:30:00Z
  checked: Frappe migration flow
  found: migrate.py: before_migrate hooks -> sync_all (updatedb -> MariaDBTable.alter()) -> after_migrate hooks.
  implication: Monkey-patching updatedb in before_migrate is the cleanest interception point.

- timestamp: 2026-02-11T00:35:00Z
  checked: EXPLAIN PARTITIONS on all 6 query patterns
  found: All queries prune to single partition. Indexes used correctly.
  implication: Partition pruning works correctly.

- timestamp: 2026-02-11T00:40:00Z
  checked: get_memory_mastery index coverage
  found: Before fix: used idx_player_item_season (player prefix). After fix: uses idx_mastery as covering index (Using index).
  implication: idx_mastery provides optimal coverage for mastery aggregation queries.

- timestamp: 2026-02-11T01:30:00Z
  checked: All EXPLAIN PARTITIONS after idx_mastery addition
  found: All 6 query patterns show single-partition access and proper index usage. Q4/Q5 (mastery) now show "Using index" = covering index scan.
  implication: All queries optimally indexed.

## Resolution

root_cause: N/A (preventative hardening, not a bug)

fix: Implemented 6-part safety hardening:
  Task 1: Full codebase audit -- all production code compliant (raw SQL, season_seq, UUID_TO_BIN)
  Task 2: before_migrate hook blocks Frappe schema sync on Memory State via monkey-patched updatedb
  Task 3: Added idx_mastery index for mastery aggregation queries (covering index)
  Task 4: Safety documentation in JSON description, setup.py schema reference, fsrs_processor.py/reviews.py/profile.py docstrings
  Task 5: Verified is_virtual safety via Frappe source (schema.py line 94, meta.py line 558-560)
  Task 6: EXPLAIN PARTITIONS confirms single-partition access for all 6 query patterns

verification: All EXPLAIN PARTITIONS show single-partition pruning. All ruff lint/format checks pass.

files_changed:
  - memora_admin/memora_admin/setup.py (before_migrate guard, schema docs, idx_mastery)
  - memora_admin/hooks.py (added before_migrate hook)
  - memora_admin/memora_admin/doctype/memora_memory_state/memora_memory_state.json (description field)
  - memora_admin/memora_admin/doctype/memora_memory_state/memora_memory_state.py (ORM guard, safety docs)
  - memora_admin/tasks/fsrs_processor.py (safety docstring)
  - memora_admin/api/reviews.py (safety docstring)
  - memora_admin/api/profile.py (safety docstring)
