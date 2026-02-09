---
status: resolved
trigger: "When submitting a review completion via POST /reviews/{subject}/submit, the user's XP is being reset to 3 instead of incrementing by 3"
created: 2026-02-09T00:00:00Z
updated: 2026-02-09T16:45:00Z
---

## Current Focus

hypothesis: CONFIRMED AND FIXED
test: Manual hydration test verified HINCRBY increments from correct base after hydration
expecting: N/A
next_action: Archive session

## Symptoms

expected: User XP should increment by 3 (e.g., 100 -> 103 XP)
actual: User XP is being reset to exactly 3 (e.g., 100 -> 3 XP)
errors: No error messages - operation succeeds but with wrong XP value
reproduction: Submit a review completion to POST /reviews/{subject}/submit endpoint
started: First time testing reviews - just discovered during initial testing

## Eliminated

- hypothesis: award_xp uses SET instead of INCREMENT
  evidence: wallet.py line 137 uses HINCRBY (atomic increment). Both sessions and reviews use same HINCRBY pattern.
  timestamp: 2026-02-09T16:10:00Z

- hypothesis: Different Redis keys between session and review endpoints
  evidence: Both use "memora:wallet:{player_id}" pattern. Wallet key prefix is "memora:" in all code paths. Verified in deps.py, sessions.py, wallet.py.
  timestamp: 2026-02-09T16:15:00Z

- hypothesis: Sync task overwrites Redis wallet from MariaDB
  evidence: sync_dirty_wallets only reads FROM Redis and writes TO MariaDB (one-way). No reverse sync exists.
  timestamp: 2026-02-09T16:18:00Z

- hypothesis: Review service submit call affects Redis wallet
  evidence: FrappeClient is pure HTTP - calls Frappe API, doesn't touch Redis wallet. ReviewService.invalidate_overview only deletes overview cache key.
  timestamp: 2026-02-09T16:20:00Z

## Evidence

- timestamp: 2026-02-09T16:10:00Z
  checked: wallet.py award_xp() implementation
  found: Uses self.redis.hincrby(key, "xp", amount) - correct atomic increment
  implication: The HINCRBY command itself is correct; issue must be with initial state

- timestamp: 2026-02-09T16:12:00Z
  checked: Redis wallet key for test user moonzalloum19@gmail.com
  found: HGETALL shows only {xp: 3} - no streak or streak_date fields
  implication: Wallet hash was recently created (missing fields from normal gameplay)

- timestamp: 2026-02-09T16:14:00Z
  checked: MariaDB wallet record for same user
  found: total_xp=3, current_streak=0 - matches Redis (sync already ran)
  implication: The sync task overwrote the correct MariaDB value with the wrong Redis value

- timestamp: 2026-02-09T16:16:00Z
  checked: Redis keys matching memora:progress:*, memora:lb:*, memora:access:*
  found: ALL EMPTY - zero progress bitmaps, zero leaderboard entries, zero access keys
  implication: Redis was flushed at some point. Only recently-accessed keys exist.

- timestamp: 2026-02-09T16:18:00Z
  checked: Total memora:* keys in Redis
  found: Only 11 keys total, all recently created (settings, sessions, devices, 1 wallet)
  implication: Confirms Redis flush. The wallet with xp=3 was created by HINCRBY on non-existent hash after flush.

- timestamp: 2026-02-09T16:22:00Z
  checked: User interaction history
  found: ~20 completed interactions dating back to 2026-02-07. Multiple lesson completions should have awarded significant XP.
  implication: XP should be much higher than 3. Previous XP was lost in Redis flush and never restored.

- timestamp: 2026-02-09T16:40:00Z
  checked: Manual hydration test with Python script
  found: After DEL wallet key, hydrate from MariaDB (xp=3), then HINCRBY +3 = 6 (correct). Without hydration, HINCRBY +3 = 3 (bug).
  implication: Hydration fix works correctly - HINCRBY increments from correct base value.

## Resolution

root_cause: The WalletService had no mechanism to hydrate Redis wallet from MariaDB. When Redis was flushed (bench clear-cache, restart, etc.), the wallet hash disappeared. The next HINCRBY operation (from review XP award of 3) created a new hash starting from 0, resulting in xp=3 instead of incrementing the correct accumulated value. The background sync_dirty_wallets task then overwrote the MariaDB record with this wrong Redis value, compounding the data loss.

fix: Added wallet hydration via ensure_hydrated() method in WalletService. Before any HINCRBY or streak update, the method checks if the wallet hash exists in Redis. If missing, it loads total_xp and current_streak from MariaDB via a new Frappe API endpoint (memora_admin.api.wallet.get_player_wallet) and seeds Redis. This prevents XP reset on cache flush. Applied to: award_xp(), get_wallet(), update_streak(), and the raw pipeline in end_session.

verification: Manual test confirmed hydration restores correct XP base before HINCRBY. DEL key -> hydrate from MariaDB (xp=3) -> HINCRBY +3 = 6 (correct, not 3).

files_changed:
- fastapi_app/services/wallet.py: Added ensure_hydrated() method, FrappeClient support, hydration in award_xp/get_wallet/update_streak
- fastapi_app/api/deps.py: Pass FrappeClient to WalletService in get_wallet_service()
- fastapi_app/api/v1/endpoints/sessions.py: Call ensure_hydrated() before raw pipeline HINCRBY in end_session
- fastapi_app/api/v1/endpoints/auth.py: Pass FrappeClient to WalletService in login
- memora_admin/api/wallet.py: New Frappe API endpoint get_player_wallet() for MariaDB wallet lookup
