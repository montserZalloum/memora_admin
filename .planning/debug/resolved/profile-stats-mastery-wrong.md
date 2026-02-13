---
status: resolved
trigger: "Player PLAYER-00003 has 6 valid records in Memora Memory State DocType, but the profile APIs return incorrect data."
created: 2026-02-13T00:00:00Z
updated: 2026-02-13T00:02:00Z
---

## Current Focus

hypothesis: TWO root causes confirmed and fixed
test: All three endpoints verified via curl with JWT for PLAYER-00003
expecting: N/A - verified
next_action: Archive session

## Symptoms

expected:
- `/api/v1/profile/stats` should return items_learned matching Memora Memory State records
- `/api/v1/profile/mastery` should return non-zero values reflecting memory states
- `/api/v1/profile/activity` should return correct activity data

actual:
- `/api/v1/profile/stats` returns items_learned=3, not 7
- `/api/v1/profile/mastery` returns all zeros (mature=0, learning=0, new_items=0, total=0)
- `/api/v1/profile/activity` was actually correct (reads from daily leaderboard ZSETs)

errors: No error messages -- APIs return 200 but with wrong data
reproduction: Hit these APIs as PLAYER-00003
started: Unknown -- likely always been wrong

## Eliminated

## Evidence

- timestamp: 2026-02-13T00:00:30Z
  checked: MariaDB Memory State records for PLAYER-00003
  found: 7 records, ALL with season_seq=1, ALL with stability > 0 (mature)
  implication: Mastery should show 7 mature items

- timestamp: 2026-02-13T00:00:35Z
  checked: Active seasons in Memora Season
  found: TWO active seasons: SEAS-00623 (seq=5, Feb 1-28) and SEAS-00027 (seq=1, Feb 1 - Jan 1 2027)
  implication: get_memory_mastery picks seq=5 via date filter, but records have seq=1

- timestamp: 2026-02-13T00:00:40Z
  checked: _get_player_season_seq("PLAYER-00003") from reviews.py
  found: Returns 1 (resolves via Player Profile -> Academic Plan -> Season -> season_seq)
  implication: The correct approach is player-plan-based, not date-range-based

- timestamp: 2026-02-13T00:00:45Z
  checked: Redis stats keys for PLAYER-00003
  found: memora:stats:PLAYER-00003:SUBJ-00448:v1 has completed=1, SUBJ-00028:v1 has completed=2, total=3
  implication: "completed" counts bitmap lesson completions, not Memory State items

- timestamp: 2026-02-13T00:00:50Z
  checked: profile_page.py get_stats method
  found: Reads Redis stats hash "completed" field (lesson completions from bitmap) and returns it as items_learned
  implication: items_learned should count Memory State records, not bitmap lesson completions

- timestamp: 2026-02-13T00:00:55Z
  checked: Direct Frappe API call get_memory_mastery(player_id="PLAYER-00003")
  found: Returns {"mature": 0, "learning": 0, "new_items": 0, "total": 0}
  implication: Confirmed -- Frappe function itself returns zeros (not a FastAPI caching issue)

- timestamp: 2026-02-13T00:01:30Z
  checked: Post-fix verification of all endpoints
  found: stats={items_learned:7}, mastery={mature:7,total:7}, activity={total_xp:141}
  implication: All fixes verified. Subject filtering also works correctly (SUBJ-00028=5, SUBJ-00448=2, total=7)

## Resolution

root_cause: |
  BUG 1 (mastery all zeros): get_memory_mastery in profile.py resolved season_seq via date-range
  lookup on Memora Season (found seq=5 from SEAS-00623), but PLAYER-00003's records have seq=1.
  The correct approach (used in reviews.py) resolves season_seq via Player Profile -> Academic Plan
  -> Season -> season_seq. The date-range approach fails when multiple seasons are active.

  BUG 2 (items_learned=3): get_stats in profile_page.py read the "completed" field from Redis
  stats hash (memora:stats:{player}:{subject}:v1), which counts bitmap lesson completions (3 total).
  But "items_learned" should count Memory State records (7 total) -- the number of distinct
  SRS items the player has encountered.

fix: |
  1. profile.py: Added _get_player_season_seq() helper that resolves season_seq via
     Player Profile -> Academic Plan -> Season (same as reviews.py). Replaced the
     date-range-based season lookup in get_memory_mastery with this helper.

  2. profile.py: Added get_items_learned_count() whitelisted API that counts Memory State
     records for a player (with season_seq partition pruning and optional subject filter).

  3. profile_page.py: Replaced the Redis stats hash "completed" reading logic in get_stats()
     with a Frappe API call to get_items_learned_count(), with Redis caching (5-min TTL,
     same pattern as mastery cache).

verification: |
  All endpoints tested via curl with JWT for PLAYER-00003:
  - /profile/stats: items_learned=7 (was 3)
  - /profile/mastery: mature=7, total=7 (was all zeros)
  - /profile/activity: total_xp=141 (was already correct)
  - Subject filtering: SUBJ-00028=5 items, SUBJ-00448=2 items (5+2=7 total, consistent)
  - Pre-commit hooks: all pass

files_changed:
  - memora_admin/api/profile.py
  - fastapi_app/services/profile_page.py
