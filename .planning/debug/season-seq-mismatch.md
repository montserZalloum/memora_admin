---
status: fixing
trigger: "season-seq-mismatch: Memory State records written with season_seq=5 instead of 1 for plan PLAN-00121"
created: 2026-02-11T00:00:00Z
updated: 2026-02-11T00:01:00Z
---

## Current Focus

hypothesis: CONFIRMED - _get_active_season() picks wrong season when multiple seasons overlap
test: n/a - fix applied
expecting: After restart, new Memory State records will use correct per-player season_seq
next_action: User to verify by restarting services and testing

## Symptoms

expected: Memora Memory State records should have season_seq = 1 (matching the plan's season season_seq)
actual: Memora Memory State records have season_seq = 5
errors: No errors - wrong data is written silently
reproduction: Complete a lesson under plan PLAN-00121 and check resulting Memora Memory State records
started: Unknown - user discovered now

## Eliminated

(none needed - root cause found on first hypothesis)

## Evidence

- timestamp: 2026-02-11T00:01
  checked: Plan PLAN-00121 -> season link
  found: PLAN-00121 links to season SEAS-00027 (season_seq=1)
  implication: Plan's correct season_seq is 1

- timestamp: 2026-02-11T00:02
  checked: All seasons in system
  found: Two seasons exist - SEAS-00027 (seq=1, dates 2026-02-01 to 2027-01-01) and SEAS-00623 (seq=5, dates 2026-02-01 to 2026-02-28). BOTH are published and have overlapping active date ranges today.
  implication: Any query for "the active season" by date range will find TWO matches

- timestamp: 2026-02-11T00:03
  checked: Memory State records
  found: All 4 records have season_seq=5 and season=NULL (the `season` column is never written by _insert_memory_state)
  implication: The wrong season_seq is consistently being used

- timestamp: 2026-02-11T00:04
  checked: _get_active_season() in fsrs_processor.py line 84-102
  found: Uses frappe.db.get_value() with date-range filters but NO ordering specified. Returns a single row.
  implication: When two seasons match, which one is returned depends on Frappe's default ordering

- timestamp: 2026-02-11T00:05
  checked: Frappe's get_value default ordering (frappe/database/database.py line 617, query.py line 342)
  found: DefaultOrderBy resolves to "modified" string. In query.py apply_order_by(), when no direction suffix is given, the default is DESC (line 342: `parts[1] if len(parts) > 1 else "desc"`). So the query becomes ORDER BY modified DESC LIMIT 1.
  implication: frappe.db.get_value returns the MOST RECENTLY MODIFIED matching row

- timestamp: 2026-02-11T00:06
  checked: Which season is most recently modified
  found: SEAS-00623 (seq=5) modified 2026-02-11 19:26:47 vs SEAS-00027 (seq=1) modified 2026-02-03 14:23:48
  implication: get_value returns SEAS-00623 (seq=5) because it was modified more recently

- timestamp: 2026-02-11T00:07
  checked: Whether _get_active_season considers the player's plan/subscription at all
  found: It does NOT. It queries seasons by date range only. The player's plan (PLAN-00121) links to SEAS-00027, but this link is never consulted.
  implication: The function has no awareness of which season a player actually belongs to

- timestamp: 2026-02-11T00:08
  checked: _insert_memory_state() in fsrs_processor.py line 172-213
  found: The INSERT SQL sets season_seq but NOT the season (name/FK) column. The `season` column is omitted from the INSERT statement entirely.
  implication: Even if the correct season were identified, only the seq number is stored, not the season reference

- timestamp: 2026-02-11T00:09
  checked: reviews.py _get_active_season_seq() line 30-38
  found: Same bug - uses frappe.db.get_value with same date-range filter, returns single season_seq. Same ambiguity when multiple seasons overlap.
  implication: The review API (get_review_overview, get_due_items, submit_reviews) has the same vulnerability

## Resolution

root_cause: |
  The `_get_active_season()` function in `fsrs_processor.py` and `_get_active_season_seq()`
  in `reviews.py` assume there is only ONE active season at any time. They query
  `Memora Season` by date range and return a single result. With overlapping seasons,
  Frappe's `get_value()` returns the most recently modified row (ORDER BY modified DESC),
  which was SEAS-00623 (season_seq=5) instead of the player's actual season SEAS-00027 (season_seq=1).

fix: |
  Made season resolution plan-aware by resolving through Player Profile -> Academic Plan -> Season.

  **File 1: `memora_admin/tasks/fsrs_processor.py`**
  - Replaced `_get_active_season()` (global date-range query) with `_resolve_player_seasons()`
    (batch JOIN: Player Profile -> Academic Plan -> Season). Single query for all players in batch.
  - Updated `process_fsrs_reviews()` to use per-player (season, season_seq) instead of one global value.
  - Updated `_insert_memory_state()` to also write the `season` column (FK to Memora Season).

  **File 2: `memora_admin/api/reviews.py`**
  - Replaced `_get_active_season_seq()` (global date-range query) with `_get_player_season_seq(player_id)`
    (targeted JOIN: Player Profile -> Academic Plan -> Season -> season_seq). One query per API call.

  **File 3: `memora_admin/events/access_sync.py`**
  - Added `season_seq` to the season Redis hash in `on_season_updated()`, so the mapping is available
    for future Redis-based lookups.

verification: Pending - user to restart services and test
files_changed:
  - memora_admin/tasks/fsrs_processor.py
  - memora_admin/api/reviews.py
  - memora_admin/events/access_sync.py
