# Domain Pitfalls

**Domain:** Gamified Education Platform Backend (FastAPI + Frappe + Redis)
**Researched:** 2026-02-01
**Overall Confidence:** MEDIUM-HIGH (verified with multiple sources)

---

## Critical Pitfalls

Mistakes that cause data loss, rewrites, or major production issues.

---

### Pitfall 1: Redis Bitmap Memory Explosion from Sparse User IDs

**What goes wrong:** If player IDs are non-contiguous (e.g., UUIDs converted to integers, or starting at 8,000,000), Redis allocates memory up to the highest bit offset. A single user at offset 8,000,000 allocates ~1MB. At offset 2^32-1, allocation takes ~300ms and blocks the server.

**Why it happens:** Redis bitmaps are strings where the Nth bit requires N/8 bytes allocated. Teams use existing user IDs without considering bitmap implications.

**Consequences:**
- Memory usage 100-1000x higher than expected
- Server blocking during large allocations (300ms+ on cold keys)
- Redis OOM kills when scaling to 100K users

**Prevention:**
1. **Use contiguous bitmap offsets:** Create a separate `bitmap_slot` column in `Memora Player Profile` that auto-increments from 0, independent of the Frappe document name
2. **Shard large bitmaps:** Split into 4096-key shards (e.g., `progress:0`, `progress:1`) each holding 32K-bit pages
3. **Memory estimation:** For 100K users with contiguous IDs: ~12.5KB per bitmap. With 100 subjects = 1.25MB total. Budget for this.

**Detection:**
- Redis `MEMORY USAGE` on bitmap keys shows unexpected size
- `INFO memory` shows high `used_memory` vs expected
- Slow SET operations (>50ms on bitmap keys)

**Phase:** Address in Redis Data Layer phase. Design bitmap_slot mapping before implementing SETBIT operations.

**Confidence:** HIGH - verified with [Redis official docs](https://redis.io/docs/latest/develop/data-types/bitmaps/) and [SETBIT documentation](https://redis.io/docs/latest/commands/setbit/)

---

### Pitfall 2: Redis-to-MariaDB Sync Data Loss Window

**What goes wrong:** With "dirty set sync every 1 min" design, up to 1-2 minutes of progress data exists only in Redis. Redis crash (OOM, power failure, process kill) loses this data permanently.

**Why it happens:** Redis is in-memory by default. Even with AOF persistence, default config syncs every 1 second, meaning ~1-2 seconds of data loss risk. Combined with 1-minute app-level sync, the window is larger.

**Consequences:**
- Players lose lesson completions, XP, streak progress
- Inconsistent state between Redis and MariaDB on recovery
- Player complaints, trust loss

**Prevention:**
1. **Enable AOF with `appendfsync everysec`:** Limits Redis data loss to ~1 second
2. **Idempotent sync design:** Track `last_synced_ts` per player; on recovery, re-sync all dirty records
3. **Sync-on-critical-events:** Immediately sync wallet after lesson completion (not just batch)
4. **Write-ahead pattern:** For XP awards, write to MariaDB first (as pending), then update Redis, then mark confirmed

**Detection:**
- Monitor `redis_aof_current_rewrite_time_sec` - long rewrites indicate risk
- Check `INFO persistence` for `aof_last_bgrewrite_status`
- Alert on sync lag: if dirty set age > 3 minutes, investigate

**Phase:** Address in Sync Mechanisms phase. Implement AOF config in Infrastructure phase.

**Confidence:** HIGH - verified with [Redis persistence docs](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)

---

### Pitfall 3: JWT Algorithm Confusion Attack

**What goes wrong:** Attacker modifies JWT header from RS256 to HS256, then signs with the server's public key (which is publicly available). If the server doesn't enforce algorithm, it accepts forged tokens.

**Why it happens:** JWT libraries accept the algorithm from the token header by default. Developers assume "signed = secure" without enforcing which algorithm is expected.

**Consequences:**
- Complete authentication bypass
- Attacker can impersonate any user
- Access to all player data, XP manipulation, subscription bypass

**Prevention:**
1. **Whitelist algorithms explicitly:** Configure JWT library to ONLY accept RS256 (or your chosen algorithm)
2. **Reject "none" algorithm:** Never enable/accept unsigned tokens
3. **Validate claims:** Check `iss`, `aud`, `exp` claims server-side, not just signature
4. **Use library correctly:** Call `verify()` not `decode()` - the latter only decodes without validating

**Detection:**
- Log algorithm from incoming tokens; alert on unexpected values
- Penetration test with algorithm-switching attacks before launch
- Use security scanners that check for JWT vulnerabilities

**Phase:** Address in Authentication API phase. Make algorithm enforcement a blocking requirement.

**Confidence:** HIGH - verified with [JWT Vulnerabilities Guide](https://pentesterlab.com/blog/jwt-vulnerabilities-attacks-guide) and [PortSwigger Web Security Academy](https://portswigger.net/web-security/jwt)

---

### Pitfall 4: Build Pipeline Race Conditions with Debounce

**What goes wrong:** With 2-minute debounce, rapid content edits queue multiple builds. If builds run concurrently, they may:
- Overwrite each other's CDN files
- Generate JSON from partially-updated database state
- Create inconsistent `_h.json` and `_b.json` files

**Why it happens:** Debounce coalesces triggers but doesn't prevent concurrent execution. Multiple workers may pick up builds simultaneously.

**Consequences:**
- Corrupted build artifacts
- Missing lessons in hierarchy JSON
- Bitmap JSON doesn't match hierarchy (bit indexes wrong)
- Students see broken content or incorrect progress

**Prevention:**
1. **Single-writer pattern:** Only one build worker per subject at a time. Use Redis lock: `SETNX build:lock:{subject_id}`
2. **Version stamping:** Include `build_version` in all JSON files; FastAPI rejects stale versions
3. **Atomic CDN updates:** Upload new files with version suffix, then atomically update manifest/pointer
4. **Build queue ordering:** Process builds in FIFO order per subject; skip if newer build queued

**Detection:**
- Check `Memora Build Queue` for overlapping `started_at` times on same subject
- Validate `_h.json` and `_b.json` consistency post-build
- Alert if build duration exceeds expected (indicates contention)

**Phase:** Address in Build Pipeline phase. Implement locking before parallel worker design.

**Confidence:** MEDIUM - based on [GitLab pipeline issues](https://gitlab.com/gitlab-org/gitlab/-/issues/202691) and distributed systems patterns

---

### Pitfall 5: Streak Calculation Timezone Bugs

**What goes wrong:** Streak calculated using server UTC time. User in UTC+3 completes lesson at 11:50 PM local time; server records as 8:50 PM UTC (previous day). Next day at 12:10 AM local (9:10 PM UTC same day), server thinks streak broken because "same UTC day, no activity previous UTC day."

**Why it happens:** Streaks require "one action per day" but "day" boundary differs per timezone. Developers use server time without considering user location.

**Consequences:**
- Players lose streaks despite legitimate activity
- Unfair advantage for users in certain timezones
- Player frustration and churn
- Support tickets about "broken streaks"

**Prevention:**
1. **Store player timezone:** Add `timezone` field to `Memora Player Profile` (e.g., "Asia/Riyadh")
2. **Calculate in user-local time:** Convert streak calculations to player's timezone
3. **Grace period:** Allow 3-6 hour buffer after midnight before breaking streak
4. **DST handling:** Use proper timezone library (pytz, zoneinfo) that handles 23/25-hour days

**Detection:**
- Monitor streak breaks by user timezone; look for clusters near UTC midnight
- Log streak calculation inputs (last_activity_ts, user_tz, current_ts)
- A/B test grace period vs strict midnight

**Phase:** Address in Wallet/Streak logic within FastAPI Game API phase.

**Confidence:** HIGH - verified with [Trophy.so timezone handling guide](https://trophy.so/blog/handling-time-zones-gamification) and community reports

---

## Moderate Pitfalls

Mistakes that cause delays, technical debt, or degraded performance.

---

### Pitfall 6: Leaderboard Hot Key Problem at Scale

**What goes wrong:** Redis sorted set for leaderboard (e.g., `leaderboard:weekly`) becomes a hot key. At 100K concurrent users, all score updates hit the same Redis key on the same shard. Single shard becomes bottleneck.

**Why it happens:** Leaderboard is inherently centralized - everyone writes to one ranked list. Redis cluster shards by key, not by operation.

**Consequences:**
- Leaderboard update latency spikes (100ms+)
- Redis shard CPU saturation
- Score updates dropped or delayed under load

**Prevention:**
1. **Sharded leaderboards:** Split into N shards (e.g., `leaderboard:weekly:0` through `leaderboard:weekly:15`). Merge on read.
2. **Batch score updates:** Buffer XP changes locally, flush to Redis every 5-10 seconds
3. **Approximate rankings:** For ranks > 1000, show "Top 5%" instead of exact position
4. **Separate read/write:** Maintain authoritative in Redis, cache top N in application memory

**Detection:**
- Monitor Redis `SLOWLOG`; look for ZADD/ZINCRBY on leaderboard keys
- Track leaderboard operation latency as separate metric
- Redis `HOTKEYS` command (if available) shows hot spots

**Phase:** Address in Leaderboard Endpoints phase. Design sharding before implementing ZINCRBY.

**Confidence:** MEDIUM - verified with [Redis leaderboard solution page](https://redis.io/solutions/leaderboards/) and [systemdesign.one](https://systemdesign.one/leaderboard-system-design/)

---

### Pitfall 7: Excluded Bits Pattern Index Drift

**What goes wrong:** Lesson deleted, its bit index added to `excluded_bits`. New lesson created, reuses the same index. Old players' progress now shows incorrect completion (their bit was for deleted lesson, now means new lesson).

**Why it happens:** `excluded_bits` prevents false positives but doesn't prevent index reuse. Over time, as lessons are added/deleted, bit indexes drift from lesson meaning.

**Consequences:**
- Progress data corruption (silent, hard to detect)
- Incorrect completion percentages
- Players see completed lessons they never did

**Prevention:**
1. **Never reuse bit indexes:** Maintain a `max_bit_index` counter per subject; only increment, never decrement
2. **Version your bitmaps:** Include `bitmap_version` in `_b.json`; when rebuilding, increment version and reset old players' bitmaps on first access
3. **Soft delete only:** Mark lessons as `is_deleted=True` but keep their bit index reserved
4. **Audit trail:** Log all bit index assignments in `Memora Sync Log` for forensic analysis

**Detection:**
- Compare `Memora Structure Progress.completed_bits` count vs actual lesson completions in `Memora Interaction Log`
- Alert if `excluded_bits` list grows beyond threshold (indicates churn problem)
- Validate `_b.json` bit_range matches lesson count on every build

**Phase:** Address in Build Pipeline phase when implementing `_b.json` generation.

**Confidence:** MEDIUM - domain-specific to Memora's excluded_bits pattern

---

### Pitfall 8: Device Limit Bypass via Rapid Login

**What goes wrong:** Player logs in on device A, B, and C in quick succession (within seconds). All three requests pass the "max 3 devices" check because none have finished registering yet. Player ends up with 4+ devices.

**Why it happens:** Device count check and device registration are not atomic. Race condition window exists between check and insert.

**Consequences:**
- Device limit exceeded
- Subscription sharing enabled
- Revenue loss from account sharing

**Prevention:**
1. **Atomic check-and-add:** Use Redis transaction: `WATCH device_count:{user_id}` + `MULTI` + `INCR` + `EXEC`. Retry on abort.
2. **Pessimistic lock:** Redis `SETNX device_lock:{user_id}` before device check; release after registration complete
3. **Eventual consistency with correction:** Allow temporary excess, background job prunes oldest devices to limit
4. **Device fingerprinting:** Detect same device on different browsers; count as single device

**Detection:**
- Alert if any user has > 3 devices in `Memora Player Device` table
- Log device count at login time; monitor for concurrent login attempts
- Rate limit login endpoint per user (max 1 login per 5 seconds)

**Phase:** Address in Authentication API phase with device registration.

**Confidence:** MEDIUM - based on [FusionAuth device limiting guide](https://fusionauth.io/docs/extend/examples/device-limiting)

---

### Pitfall 9: FastAPI Frappe Session Isolation

**What goes wrong:** FastAPI sidecar tries to call Frappe ORM directly (e.g., `frappe.get_doc()`). Either fails because Frappe context not initialized, or creates session leaks between concurrent requests.

**Why it happens:** Frappe's ORM assumes single-threaded request context with `frappe.local`. FastAPI is async and multi-threaded; context variables don't transfer.

**Consequences:**
- Random crashes in production
- Data leaks between requests (wrong user's data returned)
- Impossible-to-debug intermittent failures

**Prevention:**
1. **Clear boundary:** FastAPI NEVER imports `frappe`. Uses Redis/HTTP only.
2. **Frappe API for writes:** FastAPI calls Frappe REST API (over HTTP) for MariaDB writes
3. **Read-through cache:** FastAPI reads from Redis; Frappe background jobs populate Redis
4. **Message queue:** For complex operations, FastAPI pushes to Redis queue; Frappe worker processes

**Detection:**
- CI check: Fail if any FastAPI file imports `frappe`
- Grep codebase for `from frappe import` in `/api/` directory
- Runtime: unexpected `AttributeError` on `frappe.local` indicates context leak

**Phase:** Address in FastAPI Game API phase. Establish boundary in Architecture Design.

**Confidence:** MEDIUM - based on [Frappe forum discussion](https://discuss.frappe.io/t/fastapi-vs-werkzeug/72785) and async framework patterns

---

### Pitfall 10: AOF Rewrite Disk Space Exhaustion

**What goes wrong:** Redis AOF rewrite runs while application is under heavy write load. Rewrite process needs temporary disk space equal to current AOF size. If disk fills, rewrite fails, Redis starts rejecting writes.

**Why it happens:** AOF rewrite is triggered automatically when file grows. Production servers often have limited disk provisioned.

**Consequences:**
- Redis rejects all writes
- Progress tracking fails
- Cascading failures across application

**Prevention:**
1. **Disk monitoring:** Alert when disk usage > 70%; Redis needs 2x AOF size for rewrite
2. **Scheduled rewrites:** Run `BGREWRITEAOF` during low-traffic periods with disk space check
3. **Hybrid persistence:** Use RDB + AOF; RDB for snapshots, AOF for durability
4. **Separate volume:** Put Redis data on dedicated volume with guaranteed space

**Detection:**
- `INFO persistence` shows `aof_last_bgrewrite_status:err`
- Disk usage alerts from infrastructure monitoring
- Redis `SLOWLOG` shows long BGREWRITEAOF times

**Phase:** Address in Infrastructure phase. Configure Redis persistence before production.

**Confidence:** HIGH - verified with [Redis persistence deep dive](https://medium.com/@sohail_saifi/how-redis-persistence-actually-works-and-when-it-fails-c3715d11529f)

---

## Edge Cases

Scenarios that break common assumptions.

---

### Edge Case 1: Season Expiry During Active Lesson

**Scenario:** Player starts lesson at 11:55 PM. Season expires at midnight. Player completes at 12:05 AM.

**Broken assumption:** Access check at lesson start is sufficient.

**What happens:** Stage completion calls fail with "season expired" error mid-lesson. Progress partially saved; confusing UX.

**Handling:**
1. **Grace period on completion:** If lesson was started before expiry, allow completion within 30 minutes
2. **Pre-check remaining time:** Warn if season expires within 1 hour; block lesson start within 30 minutes
3. **Session-bound access:** Store `access_valid_until` in Redis session; check against session, not current season

**Phase:** Access Control (Double-Gate) phase.

---

### Edge Case 2: Wallet Sync During Streak Break

**Scenario:** Player's last activity was yesterday. At midnight, streak should break. But wallet sync runs at 00:01 AM, reading old Redis value before streak reset job runs at 00:05 AM.

**Broken assumption:** Sync and reset jobs run in correct order.

**What happens:** MariaDB wallet shows streak=15 while Redis shows streak=0. Inconsistent state.

**Handling:**
1. **Streak reset before sync:** Schedule streak reset at 00:00, wallet sync at 00:05
2. **Atomic streak calculation:** Streak is calculated, not stored. Derive from `last_streak_activity_date`.
3. **Sync includes calculation:** Wallet sync re-calculates streak before writing, doesn't just copy Redis value

**Phase:** Scheduled Tasks phase.

---

### Edge Case 3: Multiple Concurrent Stage Completions

**Scenario:** Flaky network; player's app sends same stage completion 3 times within 100ms.

**Broken assumption:** Each API call represents unique action.

**What happens:** XP awarded 3 times; interaction logged 3 times; streak extended incorrectly.

**Handling:**
1. **Idempotency key:** Client sends unique `completion_id`; server dedupes within 5-minute window
2. **Bitmap is naturally idempotent:** `SETBIT` to 1 when already 1 is no-op
3. **XP check:** Compare `xp_before` vs expected; reject if mismatch indicates already processed
4. **Session state machine:** Track stage state in Redis session; only `in_progress` -> `completed` transition allowed

**Phase:** FastAPI Game API phase (Stage Complete endpoint).

---

### Edge Case 4: Build Triggered on Unpublished Content

**Scenario:** Editor creates draft lesson, saves, triggers build. Build includes unpublished content in CDN JSON.

**Broken assumption:** All saved content should be built.

**What happens:** Students see incomplete/draft lessons in app.

**Handling:**
1. **Status filter:** Build only processes lessons with `is_published=True`
2. **Preview vs production:** Separate build targets; preview includes drafts, production excludes
3. **Validation gate:** Build fails if any included lesson has `status != "Published"`

**Phase:** Build Pipeline phase.

---

### Edge Case 5: Token Refresh Race with Multiple Devices

**Scenario:** User has 3 devices. Device A's access token expires. Device A and B both try to refresh using the same refresh token simultaneously.

**Broken assumption:** Refresh token can be used multiple times.

**What happens:** If using rotation, one device gets new tokens, other gets "invalid refresh token" error. User logged out unexpectedly on one device.

**Handling:**
1. **Reuse window:** Allow refresh token reuse within 30-second window (concurrent requests same token)
2. **Device-bound refresh tokens:** Each device has own refresh token; no shared rotation
3. **Graceful degradation:** On invalid refresh, prompt re-auth instead of hard logout
4. **Token family tracking:** Detect if old refresh token used after rotation; revoke entire family (security breach)

**Phase:** Authentication API phase.

---

### Edge Case 6: Bitmap Overflow on Subject with 10,000+ Lessons

**Scenario:** Mega-subject has 10,000 lessons (perhaps auto-generated). Bitmap requires 10,000 / 8 = 1,250 bytes per user. With 100K users = 125MB for one subject.

**Broken assumption:** Subject sizes are bounded.

**What happens:** Memory explosion; performance degradation.

**Handling:**
1. **Subject size limits:** Enforce max 1,000 lessons per subject in validation
2. **Hierarchical bitmaps:** Use Unit-level bitmaps for large subjects; aggregate for subject progress
3. **Different storage for mega-subjects:** Switch to Redis hash with sparse lesson IDs instead of bitmap

**Phase:** Build Pipeline phase (validation) + Redis Data Layer phase (storage design).

---

## Prevention Strategies Summary

### By Phase

| Phase | Key Pitfalls | Prevention Actions |
|-------|--------------|-------------------|
| **Infrastructure** | AOF disk exhaustion, persistence config | Configure AOF everysec, monitor disk, separate volume |
| **Redis Data Layer** | Bitmap memory explosion, hot keys | Contiguous bitmap_slot, sharded leaderboards |
| **Authentication API** | Algorithm confusion, device bypass | Whitelist algorithms, atomic device limit |
| **Access Control** | Season expiry mid-lesson | Grace periods, session-bound access |
| **FastAPI Game API** | Frappe isolation, streak timezone, duplicate completions | Clear boundary, user timezone, idempotency keys |
| **Build Pipeline** | Race conditions, excluded_bits drift | Single-writer lock, never reuse indexes |
| **Sync Mechanisms** | Data loss window, sync ordering | AOF config, sync-on-critical, calculated streaks |
| **Scheduled Tasks** | Job ordering dependencies | Explicit ordering, derived values over stored |

### Technology-Specific

| Technology | Key Pitfall | Prevention |
|------------|-------------|------------|
| Redis Bitmap | Sparse ID memory explosion | Use contiguous bitmap_slot, shard large bitmaps |
| Redis Sorted Set | Hot key at scale | Shard leaderboards, batch updates |
| Redis Persistence | Data loss on crash | AOF everysec, test recovery procedures |
| JWT | Algorithm confusion | Whitelist algorithms, verify not just decode |
| Frappe + FastAPI | Session isolation | Clear boundary, HTTP-only communication |
| Build Pipeline | Race conditions | Single-writer locks, version stamping |
| Timezone | Streak breaks | User timezone storage, grace periods |

---

## Sources

### HIGH Confidence (Official Documentation)
- [Redis Bitmaps Documentation](https://redis.io/docs/latest/develop/data-types/bitmaps/)
- [Redis SETBIT Command](https://redis.io/docs/latest/commands/setbit/)
- [Redis Persistence Documentation](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
- [Redis Memory Optimization](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/memory-optimization/)
- [Redis Transactions (WATCH)](https://redis.io/docs/latest/develop/using-commands/transactions/)

### MEDIUM Confidence (Verified with Multiple Sources)
- [JWT Vulnerabilities Guide - PentesterLab](https://pentesterlab.com/blog/jwt-vulnerabilities-attacks-guide)
- [PortSwigger JWT Attacks](https://portswigger.net/web-security/jwt)
- [JWT Security Best Practices - 42Crunch](https://42crunch.com/7-ways-to-avoid-jwt-pitfalls/)
- [Trophy.so Timezone Handling](https://trophy.so/blog/handling-time-zones-gamification)
- [Trophy.so Streaks Feature](https://trophy.so/blog/how-to-build-a-streaks-feature)
- [Redis Leaderboard Solutions](https://redis.io/solutions/leaderboards/)
- [Leaderboard System Design](https://systemdesign.one/leaderboard-system-design/)
- [FusionAuth Device Limiting](https://fusionauth.io/docs/extend/examples/device-limiting)
- [Frappe REST API Documentation](https://docs.frappe.io/framework/user/en/api/rest)
- [Distributed Debounce Patterns - Inngest](https://www.inngest.com/blog/debouncing-in-queuing-systems-optimizing-efficiency-in-async-workflows)
- [JWT Token Revocation Strategies](https://medium.com/@ahmedosamaft/understanding-jwt-revocation-strategies-allowlist-denylist-and-jti-matcher-9d298893f8a1)

### LOW Confidence (Single Source / Community)
- [Frappe FastAPI Discussion](https://discuss.frappe.io/t/fastapi-vs-werkzeug/72785)
- [Redis Persistence Failure Scenarios](https://medium.com/@sohail_saifi/how-redis-persistence-actually-works-and-when-it-fails-c3715d11529f)
- [GitLab Pipeline Race Conditions](https://gitlab.com/gitlab-org/gitlab/-/issues/202691)

---

*Pitfalls research: 2026-02-01*
