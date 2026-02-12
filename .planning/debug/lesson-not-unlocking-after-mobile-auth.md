---
status: fixing
trigger: "After implementing new Auth by mobile for the Player, completing a lesson returns success response but the UI still shows the lesson as not completed and the next lesson stays locked. Page refresh fixes it."
created: 2026-02-12T00:00:00Z
updated: 2026-02-12T20:20:00Z
---

## Current Focus

hypothesis: CONFIRMED - Stats cache created with only completed fields, missing totals
test: Delete stats hash and verify API returns correct data
expecting: After deletion, progress endpoint recomputes from bitmap with correct totals
next_action: Fix end_session to ensure stats hash has totals before HINCRBY

## Symptoms

expected: After completing a lesson, it should show as completed and the next lesson should unlock immediately (without page refresh)
actual: The lesson completion API returns success (200 OK, correct response with xp_awarded etc.), but the completed lesson still looks uncompleted in the UI and the next lesson stays locked. Only a full page refresh fixes it - after refresh everything shows correctly.
errors: No errors - API returns success. The is_replay field works correctly (false for new completions, true for replays).
reproduction: Complete any lesson as a mobile-auth player. The lesson doesn't visually update and next stays locked until page refresh.
started: Broke after mobile auth was deployed. Worked before.

## Eliminated

- hypothesis: User identity mismatch between write and read paths
  evidence: Both end_session (write) and progress endpoints (read) use user.sub from JWT which is consistently PLAYER-##### for all mobile-auth players. Verified by tracing both code paths and checking Redis key patterns.
  timestamp: 2026-02-12T20:00

- hypothesis: Bitmap not being updated correctly
  evidence: Full end-to-end test: cleared bit 0, started session, ended session. get_topic_lessons immediately returned completed: true. Bitmap is atomically updated by Lua script.
  timestamp: 2026-02-12T20:05

- hypothesis: Token refresh changes user identity
  evidence: Refresh token preserves sub claim (PLAYER-#####). New access tokens from refresh use same user_id.
  timestamp: 2026-02-12T20:03

## Evidence

- timestamp: 2026-02-12T19:50
  checked: Redis key format consistency
  found: All Redis keys (progress, session, stats, wallet) consistently use PLAYER-##### format. MariaDB Structure Progress records also use PLAYER-##### format.
  implication: Identity is consistent across all layers

- timestamp: 2026-02-12T19:55
  checked: Subscription identity format
  found: Memora Player Subscription records still use old email format (moonzalloum19@gmail.com) instead of PLAYER-#####. This causes access hydration to return empty results on every request (15ms+ per-request Frappe API call).
  implication: Performance issue but not the root cause (free content bypass still works)

- timestamp: 2026-02-12T20:05
  checked: Bitmap read-after-write consistency
  found: Cleared bit, completed lesson, immediately read via get_topic_lessons - correctly showed completed: true. Bitmap path works correctly.
  implication: The progress bitmap read path is NOT the source of staleness

- timestamp: 2026-02-12T20:08
  checked: Stats cache after end_session when stats hash absent
  found: end_session creates stats hash via HINCRBY with ONLY completed fields. Stats hash: {completed:1, Track:completed:1, UNT:completed:1, TPC:completed:1} - NO total fields. Progress endpoints read this and return total:0, percentage:0% for ALL items.
  implication: ROOT CAUSE FOUND

- timestamp: 2026-02-12T20:12
  checked: API response with broken stats hash vs after stats deletion
  found: With broken stats: {"completed":1,"total":0,"percentage":0.0}. After deleting stats hash: endpoint recomputes from bitmap and returns {"completed":1,"total":1,"percentage":100.0}. This confirms "page refresh fixes it" = stats hash expires/gets deleted, then recomputed correctly.
  implication: Fix must ensure stats hash always has total fields when modified

## Resolution

root_cause: The end_session endpoint updates the stats cache via Redis HINCRBY. If the stats hash doesn't exist (expired 1h TTL, Redis restart, etc.), HINCRBY creates a new hash with ONLY the "completed" fields. The "total" fields are never set. Progress endpoints (get_subject_progress, get_tracks, get_unit_detail) read from this incomplete stats hash and return total:0, percentage:0% for all hierarchy levels. Since the progress endpoints only recompute stats from bitmap when the hash is completely absent (stats is None), a hash with ANY fields is treated as valid, even if totals are missing. "Page refresh fixes it" because the 1h TTL eventually expires the broken hash, and the next read recomputes correctly from the bitmap.

fix: In end_session, before incrementing stats via HINCRBY, check if the stats hash exists. If it doesn't, initialize it from the hierarchy bitmap (compute_stats_from_hierarchy) first, THEN increment. This ensures the hash always has both completed AND total fields.

verification:
files_changed: []
