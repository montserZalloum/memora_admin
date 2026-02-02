# Features Research: v1.1 Feature Expansion

**Milestone:** v1.1 Feature Expansion
**Features:** Game Sessions, Leaderboards, Device Management, Scheduled Tasks
**Researched:** 2026-02-02
**Overall Confidence:** MEDIUM-HIGH (based on industry patterns and official documentation)

---

## Executive Summary

This research covers expected behavior for four feature areas in v1.1: game sessions, leaderboards, device management, and scheduled tasks. Key findings:

**Game Sessions:** Session tracking is table stakes for lesson flow (start → stages → end). Modern platforms expect TTL-based sessions with automatic cleanup, crash recovery, and session resumption. Redis hashes with TTL are standard.

**Leaderboards:** Multiple timeframes (daily, weekly, all-time) are now expected. Daily resets at midnight UTC-7 are common. Redis sorted sets are the de facto solution. Critical anti-pattern: avoid public rankings without privacy controls (demotivates low performers).

**Device Management:** 3-device limit is industry standard for educational platforms. Concurrent session detection prevents account sharing. Passwordless and low-friction UX are 2026 priorities. Avoid forcing device deauthorization flows.

**Scheduled Tasks:** Native Redis TTL expiration events (2026 pattern) outperform cron-based cleanup. Streak resets at midnight in user's local timezone. Hourly session cleanup with TTL-based expiration is standard.

---

## Game Sessions

### Table Stakes

Features users expect for session tracking.

| Feature | Description | Complexity | Notes |
|---------|-------------|------------|-------|
| **Start session on lesson begin** | Create session record with metadata (lesson_id, user_id, start_time) | Low | Redis hash: `session:{uuid}` with TTL=3600s |
| **Session TTL** | Auto-expire inactive sessions | Low | Prevents Redis bloat. Standard: 1 hour TTL |
| **Stage completion tracking** | Record each stage interaction in active session | Low | Append to session metadata or separate list |
| **End session on lesson complete** | Finalize session, trigger completion flow (XP, progress) | Low | Delete session key, fire completion logic |
| **Session existence check** | Validate active session before accepting stage completions | Low | EXISTS check on session key |
| **Session metadata** | Store lesson_id, user_id, subject_id, start_time, stages_completed | Low | Enables analytics and recovery |

**Implementation Pattern (Industry Standard):**
```
Session Key: session:{session_id}
Structure: Redis hash
TTL: 3600 seconds (1 hour)
Fields:
  - user_id
  - lesson_id
  - subject_id
  - start_time (Unix timestamp)
  - stages_completed (comma-separated or JSON array)
  - last_activity (timestamp, updated on each stage)
```

### Differentiators

Features that enhance session tracking beyond basics.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Session recovery on crash** | Resume lesson progress if app crashes mid-lesson | Medium | Load session from Redis on reconnect. Duolingo pattern: show "Continue where you left off" |
| **Concurrent session detection** | Prevent simultaneous lesson sessions from same user | Medium | Check for existing session before creating new one. Return error with session_id |
| **Session analytics** | Track drop-off rates, average time per stage | Medium | Flush session data to Interaction Log on end |
| **Idle timeout with warning** | Warn user before session expires, extend TTL on activity | High | Requires WebSocket or polling. Out of scope for v1.1 |
| **Multi-device session sync** | Show active session across devices | High | Complex. Requires pub/sub or polling. Defer to future |

### Anti-Features

Patterns to avoid.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Session state in client only** | Crashes lose progress. No server validation. | Always create server-side session on lesson start |
| **No TTL on sessions** | Redis bloat from abandoned sessions | Set TTL=3600s. Cleanup expired sessions hourly |
| **Blocking on session end** | Slow lesson complete if MariaDB sync delays | Fire-and-forget session cleanup. Background flush to analytics |
| **Session replay without validation** | Security risk: users could fabricate session data | Validate session ownership (user_id in JWT matches session.user_id) |
| **Complex state machine** | Tracking every UI interaction creates fragility | Track only meaningful events: start, stage complete, end |

### Dependencies on Existing Features

| New Feature | Depends On (v1.0) |
|-------------|-------------------|
| Start session endpoint | JWT authentication, Access control (Double-Gate) |
| Stage complete in session | Session existence, Interaction logging buffer |
| End session → lesson complete | Bitmap progress (SETBIT), XP award (HINCRBY), Streak update (Lua script) |
| Session cleanup task | Scheduled task framework (Frappe scheduler) |

---

## Leaderboards

### Table Stakes

Features users expect for competitive rankings.

| Feature | Description | Complexity | Notes |
|---------|-------------|------------|-------|
| **All-time XP leaderboard** | Global ranking by total XP | Low | Single Redis sorted set: ZADD `leaderboard:alltime` |
| **Daily XP leaderboard** | Ranking by XP earned today, resets at midnight | Medium | Separate sorted set: `leaderboard:daily:{YYYY-MM-DD}` |
| **Top N retrieval** | Fetch top 10/50/100 players | Low | ZREVRANGE with limit |
| **User's rank lookup** | Show "You are #47" | Low | ZREVRANK for user's position |
| **User's score in leaderboard** | Show user's XP alongside rank | Low | ZSCORE for user's points |
| **Automatic reset scheduling** | Daily leaderboard resets at midnight UTC | Medium | Scheduled task creates new key, archives old key |

**Implementation Pattern (Industry Standard):**
```
Redis Sorted Sets:
  leaderboard:alltime → ZADD user_id score (never resets)
  leaderboard:daily:{date} → ZADD user_id score (resets daily)
  leaderboard:streak → ZADD user_id streak_count

Daily reset (midnight UTC-7 is common):
  1. Rename leaderboard:daily:{yesterday} → leaderboard:archive:daily:{yesterday}
  2. Expire archive key after 30 days (TTL=2592000)
  3. Create new leaderboard:daily:{today} key

Google Play Games SDK pattern: Automatically creates daily, weekly, all-time versions.
```

### Differentiators

Features that enhance leaderboards.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Multiple timeframes** | Daily, weekly, all-time gives more ways to compete | Medium | 3 sorted sets. Weekly reset on Sunday midnight |
| **Streak leaderboard** | Compete on consistency, not just volume | Low | Separate sorted set from XP. Update on streak change |
| **Cached leaderboard results** | Avoid ZREVRANGE on every request at scale | Medium | Cache top 100 for 5 minutes. Trade-off: freshness vs performance |
| **User + context** | Show rank #43-47 (user at #45, with 2 above/below) | Medium | ZREVRANGE with offset around user's rank |
| **League-based competition** | Group users by activity level (like Duolingo leagues) | High | Prevents demotivation of low performers. Complex cohort logic. Defer to future |
| **Anonymous rankings** | Show ranks without revealing usernames (privacy) | Low | Display "Player #123" instead of real names. GDPR-friendly |

### Anti-Features

Patterns to avoid (critical for educational context).

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Public rankings without privacy** | Demotivates low performers. Privacy concerns. Research shows negative impact on learning. | Opt-in leaderboards OR league-based cohorts OR show only top 10 + user's rank |
| **Real-time update on every XP change** | Expensive ZINCRBY on hot key. Bottleneck at scale. | Batch leaderboard updates every 5-10 minutes OR update on lesson complete only |
| **Single global leaderboard only** | New users can never catch up. Demotivating. | Add daily/weekly resets. Fresh competition every period. |
| **Leaderboard as only engagement** | Creates anxiety, eventual burnout (research-backed). | Multiple engagement hooks: achievements, progress, streaks. Leaderboard is ONE of many. |
| **No explanation of scoring** | Users confused why they're ranked lower despite more lessons. | Clear: "Ranked by XP earned today" or "Ranked by current streak" |

**Research Note (HIGH confidence):** A 2021 study in JMIR Serious Games found that students at the bottom of leaderboards experience demotivation and disengagement. Penn State research (2024) emphasizes balancing competition with individual progress tracking. 2026 trend: gamification focuses on purpose and progression, not superficial PBL (points, badges, leaderboards).

### Dependencies on Existing Features

| New Feature | Depends On (v1.0) |
|-------------|-------------------|
| Leaderboard update on XP gain | XP award (HINCRBY on wallet) |
| User rank lookup | User profile, authentication |
| Daily reset task | Scheduled task framework |
| Streak leaderboard | Streak tracking (Lua script) |

---

## Device Management

### Table Stakes

Features users expect for multi-device access.

| Feature | Description | Complexity | Notes |
|---------|-------------|------------|-------|
| **Device registration on login** | Store device metadata (device_id, name, platform, last_login) | Low | Redis hash: `devices:{user_id}` → HSET device_id metadata |
| **Device limit enforcement** | Maximum N devices per user (typically 3-5) | Low | COUNT keys before adding. Reject if at limit. |
| **Device listing** | Show user their authorized devices | Low | HGETALL `devices:{user_id}` |
| **Device deauthorization** | User can remove a device remotely | Low | HDEL `devices:{user_id}` device_id |
| **Last login timestamp** | Show "iPhone - Last used 2 hours ago" | Low | Update timestamp on every login |
| **Device metadata** | Store platform (iOS/Android/Web), device name, IP (optional) | Low | JSON in hash value |

**Implementation Pattern (Industry Standard):**
```
Redis Hash:
  Key: devices:{user_id}
  Fields: device_id → JSON metadata
    {
      "device_id": "uuid-v4",
      "platform": "iOS",
      "device_name": "iPhone 13",
      "first_login": "2026-02-01T10:00:00Z",
      "last_login": "2026-02-02T15:30:00Z"
    }

Device Limit Enforcement:
  - Free tier: 2 devices
  - Premium tier: 3 devices
  - Family tier: 5 devices

Common pattern: Allow exceeding limit temporarily, force deauthorization on next login.
```

### Differentiators

Features that enhance device management.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Passwordless authentication** | Reduces friction. 2026 UX best practice. | Medium | WebAuthn, biometric, magic link. Out of scope for v1.1 |
| **Concurrent session detection** | Prevent account sharing by detecting simultaneous logins | Medium | Track active session token per device. Reject if 2+ sessions active |
| **Device trust levels** | New device requires 2FA, trusted device skips | High | Requires 2FA implementation. Defer to future |
| **Automatic device cleanup** | Remove devices inactive for 90+ days | Low | Scheduled task checks last_login timestamps |
| **Device fingerprinting** | Detect multiple "devices" from same browser | High | Complex. Privacy concerns. Avoid unless necessary |

### Anti-Features

Patterns to avoid.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Immediate logout on device limit** | Frustrating UX. User mid-lesson gets kicked out. | Allow session to finish. Enforce limit on next login attempt |
| **No way to remove old devices** | User loses phone, buys new one, can't login. Support nightmare. | Always provide self-service device deauthorization |
| **Forcing device deauth flow** | "Choose which device to remove" is friction. Low adoption. | Auto-remove least recently used device (with warning notification) |
| **Device limit too restrictive** | 1-2 devices frustrates legitimate users (phone + tablet). | 3 devices is industry standard. 5 for family plans. |
| **Storing sensitive device data** | IP addresses, exact location raises privacy concerns. | Store minimal metadata. Platform and last login timestamp only. |
| **Client-only device checks** | Easily bypassed by modifying client code. | Always validate device authorization server-side |

**Research Note (HIGH confidence):** 2026 device management best practice is balancing security with user-first UX. MDM platforms that rely on heavy control create friction and low adoption. Passwordless authentication and low-friction flows are priorities.

### Dependencies on Existing Features

| New Feature | Depends On (v1.0) |
|-------------|-------------------|
| Device registration | Login endpoint (JWT issuance) |
| Device limit check | Redis connection, Player profile |
| Concurrent session detection | Session tracking (if implemented) |
| Device cleanup task | Scheduled task framework |

---

## Scheduled Tasks

### Table Stakes

Features needed for background maintenance.

| Feature | Description | Complexity | Notes |
|---------|-------------|------------|-------|
| **Daily streak reset at midnight** | Zero out streaks for users who didn't complete a lesson today | Medium | Runs daily at 00:00 in user's timezone (or UTC) |
| **Session cleanup (expired sessions)** | Delete Redis keys for sessions past TTL | Low | Hourly task. Redis auto-expires with TTL, but cleanup ensures consistency |
| **Leaderboard daily reset** | Archive yesterday's leaderboard, create today's | Low | Runs at midnight UTC-7 (common pattern) |
| **Task scheduling framework** | Cron-like scheduler for recurring jobs | Low | Frappe scheduler already exists (used in v1.0) |
| **Task logging** | Record task execution time, success/failure | Low | Write to Frappe DocType or system log |

**Implementation Pattern (Industry Standard):**
```
Frappe Scheduler (already in use):
  - all: Every event (avoid, too frequent)
  - cron: Cron expression (e.g., "0 0 * * *" for daily midnight)
  - daily: Runs once per day
  - hourly: Runs every hour
  - weekly: Runs once per week

2026 Pattern (Redis-native):
  - Use Redis keyspace notifications for expiration events
  - Subscribe to __keyevent@0__:expired channel
  - React to expired session keys instantly
  - Eliminates polling, reduces redundant calls

Spring Boot example:
  spring.session.redis.cleanup-cron: "0 * * * * *" (every minute)

Modern approach: TTL + expiration events > cron cleanup
```

### Differentiators

Features that enhance scheduled tasks.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Redis expiration events** | Instant reaction to key expiry. No polling overhead. | Medium | Requires enabling keyspace notifications in Redis config |
| **Distributed task locking** | Prevent duplicate execution across multiple servers | Medium | Redis SETNX for lock. Critical in multi-server deployments |
| **Task retry logic** | Auto-retry failed tasks with exponential backoff | Medium | Requires task queue (Celery, RQ, or Frappe's built-in) |
| **Task monitoring dashboard** | View task history, failures, durations | Medium | Frappe provides basic logging. Custom DocType for detailed tracking |
| **Dynamic scheduling** | Adjust task frequency based on load | High | Complex. Not needed for v1.1 |

### Anti-Features

Patterns to avoid.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Polling for expired sessions** | Every server instance queries Redis every minute. Redundant. | Use Redis TTL auto-expiration + keyspace events |
| **Server-time streak reset** | Unfair to users in different timezones. | Store user timezone. Calculate midnight in user's local time |
| **Blocking task execution** | Long-running task blocks API requests. | Run tasks in background worker (Frappe scheduler already does this) |
| **No failure handling** | Task fails silently. No retry, no alert. | Log failures. Retry with backoff. Alert if critical task fails |
| **Hardcoded schedules** | Changing task frequency requires code deployment. | Configuration-driven schedules (Frappe hooks.py supports this) |

**Research Note (HIGH confidence):** Redis keyspace notifications (expire events) are the 2026 best practice for session cleanup. OWASP session management cheatsheet emphasizes TTL on all session keys. Microsoft documentation (2024) recommends cron cleanup as fallback, not primary mechanism.

### Dependencies on Existing Features

| New Feature | Depends On (v1.0) |
|-------------|-------------------|
| Streak reset task | Wallet data (Redis hash), Streak tracking (Lua script) |
| Session cleanup task | Session keys in Redis |
| Leaderboard reset task | Leaderboard sorted sets |
| Task scheduling | Frappe scheduler (already configured in v1.0) |

---

## Cross-Feature Dependencies

Critical ordering for v1.1 implementation.

```
┌─────────────────────────────────────────────────────────────────┐
│                   GAME SESSIONS (implement first)               │
├─────────────────────────────────────────────────────────────────┤
│  Start session endpoint                                         │
│       └── Depends on: JWT auth (v1.0), Access control (v1.0)    │
│  Stage complete in session                                      │
│       └── Depends on: Session existence check                   │
│  End session → lesson complete                                  │
│       └── Depends on: Bitmap progress (v1.0), XP award (v1.0)   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   LEADERBOARDS (implement second)               │
├─────────────────────────────────────────────────────────────────┤
│  Leaderboard update on XP gain                                  │
│       └── Depends on: XP award (v1.0), ZINCRBY on sorted set    │
│  Top N + user rank endpoints                                    │
│       └── Depends on: Leaderboard population                    │
│  Streak leaderboard                                             │
│       └── Depends on: Streak tracking (v1.0)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   DEVICE MANAGEMENT (implement third)           │
├─────────────────────────────────────────────────────────────────┤
│  Device registration on login                                   │
│       └── Depends on: Login endpoint (v1.0)                     │
│  Device limit enforcement                                       │
│       └── Depends on: Device registration                       │
│  Device listing + deauthorization                               │
│       └── Depends on: Device metadata in Redis                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SCHEDULED TASKS (implement last)              │
├─────────────────────────────────────────────────────────────────┤
│  Session cleanup (hourly)                                       │
│       └── Depends on: Game sessions being created               │
│  Daily leaderboard reset (daily)                                │
│       └── Depends on: Leaderboards being populated              │
│  Streak reset (daily)                                           │
│       └── Depends on: Streak tracking (v1.0)                    │
│  Device cleanup (weekly)                                        │
│       └── Depends on: Device registration                       │
└─────────────────────────────────────────────────────────────────┘
```

**Critical Path for v1.1:**

1. **Game Sessions** - Core lesson flow. Must work before leaderboards (sessions feed analytics).
2. **Leaderboards** - Depends on XP being accumulated (v1.0 ready), but adds competitive layer.
3. **Device Management** - Security feature. Can be developed in parallel with sessions.
4. **Scheduled Tasks** - Background maintenance. Implement last after features are stable.

**Parallelization Opportunity:**
- Game Sessions + Device Management can be developed simultaneously (no dependencies).
- Leaderboards depend on XP (v1.0), so can start immediately.
- Scheduled Tasks wait for all features to stabilize.

---

## MVP Recommendation for v1.1

### Must Have (Core v1.1)

| Priority | Feature | Rationale |
|----------|---------|-----------|
| 1 | Start/end session endpoints | Core lesson flow tracking |
| 2 | Session TTL and cleanup | Prevent Redis bloat |
| 3 | All-time XP leaderboard | Table stakes competitive feature |
| 4 | Daily XP leaderboard | Fresh competition every day |
| 5 | Device registration on login | Security against account sharing |
| 6 | Device limit enforcement (3 devices) | Prevent abuse |
| 7 | Daily leaderboard reset task | Maintain daily rankings |
| 8 | Session cleanup task (hourly) | Remove expired sessions |

### Should Have (Enhanced v1.1)

| Priority | Feature | Rationale |
|----------|---------|-----------|
| 9 | Streak leaderboard | Different competition axis |
| 10 | Device listing endpoint | User self-service |
| 11 | Cached leaderboard results | Performance at scale |
| 12 | User rank + context (±2 positions) | Better UX than just "#47" |

### Nice to Have (Polish)

| Priority | Feature | Rationale |
|----------|---------|-----------|
| 13 | Session recovery on crash | Better UX, prevents frustration |
| 14 | Concurrent session detection | Advanced account sharing prevention |
| 15 | Weekly leaderboard | Additional timeframe |
| 16 | Device auto-cleanup (90d inactive) | Automated maintenance |

### Defer to Post-v1.1

| Feature | Reason to Defer |
|---------|-----------------|
| League-based leaderboards | Complex cohort logic. Requires more users for meaningful cohorts. |
| Real-time leaderboard updates | WebSocket complexity. Cached results sufficient for MVP. |
| Redis keyspace notifications | Optimization. Cron-based cleanup works for v1.1 scale. |
| Device fingerprinting | Privacy concerns. Device limit sufficient for now. |
| 2FA for new devices | Adds complexity. Basic device management first. |
| Session idle timeout warnings | Requires WebSocket or polling. TTL expiration sufficient. |

---

## Complexity Assessment

| Feature Area | Overall Complexity | Risk Factors |
|--------------|-------------------|--------------|
| **Game Sessions** | Low-Medium | TTL management, ensuring atomic end-session logic |
| **Leaderboards** | Low | Redis sorted sets are well-understood. Reset logic straightforward. |
| **Device Management** | Low | Simple Redis hash operations. Limit enforcement is basic counting. |
| **Scheduled Tasks** | Low | Frappe scheduler already in use (v1.0). Adding new tasks is incremental. |

**Highest Risk:** Leaderboard anti-pattern (demotivation). Mitigation: Start with opt-in or private rankings.

**Lowest Risk:** Device management. Standard CRUD operations on Redis hash.

---

## Performance Considerations

### Game Sessions
- **Start session:** O(1) - HSET to create hash
- **Stage complete:** O(1) - HSET to update field
- **End session:** O(1) - DEL session key + existing lesson complete logic
- **Session cleanup:** O(n) - SCAN for expired keys (use TTL auto-expiration to minimize)

### Leaderboards
- **Update leaderboard:** O(log N) - ZADD to sorted set
- **Fetch top 100:** O(log N + 100) - ZREVRANGE
- **User rank lookup:** O(log N) - ZREVRANK
- **Daily reset:** O(1) - RENAME + EXPIRE

**Scaling concern:** Global leaderboard at 100K users → 100K members in sorted set. Redis handles this, but ZREVRANGE becomes slower. Mitigation: Cache top 100 for 5 minutes.

### Device Management
- **Register device:** O(1) - HSET
- **List devices:** O(n) - HGETALL (n = devices per user, typically 3-5)
- **Remove device:** O(1) - HDEL
- **Limit check:** O(1) - HLEN

**No scaling concerns.** Per-user hashes are small.

### Scheduled Tasks
- **Streak reset:** O(n) - Iterate all users with dirty streak flag (use dirty set pattern from v1.0)
- **Session cleanup:** O(1) - Redis TTL auto-expires (cron task is just safety net)
- **Leaderboard reset:** O(1) - RENAME + EXPIRE

**Optimization:** Use dirty set pattern for streak reset (only iterate users with recent activity).

---

## Sources

### Game Sessions (MEDIUM-HIGH confidence)
- [Duolingo Session Tracking Patterns](https://medium.com/@salamprem49/duolingo-streak-system-detailed-breakdown-design-flow-886f591c953f) - Session flow, user onboarding
- [Session Management Best Practices - OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) - TTL, security patterns
- [Redis Session Management](https://redis.io/solutions/session-management/) - Official Redis patterns
- [Session Tracking State Management Pitfalls](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/app-state?view=aspnetcore-8.0) - Microsoft ASP.NET Core docs

### Leaderboards (HIGH confidence)
- [Leaderboard Design Principles - JMIR](https://pmc.ncbi.nlm.nih.gov/articles/PMC8097522/) - Academic research on educational leaderboards, demotivation risks
- [Penn State - Leaderboards in Educational Gaming](https://sites.psu.edu/zaczidik/2024/09/15/leaderboards-in-educational-gaming-striking-a-balance-between-motivation-and-meaningful-learning/) - Balance competition with learning
- [Google Play Games Services - Leaderboards](https://developers.google.com/games/services/common/concepts/leaderboards) - Automatic daily/weekly/all-time creation
- [LootLocker - Leaderboard Scheduled Resets](https://lootlocker.com/blog/leaderboard-resets-rewards) - Reset patterns, cron expressions
- [Yukai Chou - PBL Fallacy](https://yukaichou.com/gamification-study/points-badges-and-leaderboards-the-gamification-fallacy/) - Anti-pattern warnings
- [Gamification in 2026 Trends](https://tesseractlearning.com/blogs/view/gamification-in-2026-going-beyond-stars-badges-and-points/) - Purpose over superficial rewards

### Device Management (MEDIUM confidence)
- [How to Prevent Multiple Logins - Rupt](https://www.rupt.dev/blog/how-to-prevent-multiple-user-logins-for-the-same-account) - Device limit patterns
- [Device Management UX Best Practices 2026](https://www.venn.com/learn/byod/mobile-device-management/) - Friction reduction, user-first UX
- [5 MDM Best Practices](https://www.beyondidentity.com/resource/5-mobile-device-management-best-practices) - Passwordless authentication, low friction
- [Microsoft Intune Best Practices 2025](https://windowsmanagementexperts.com/7-microsoft-intune-best-practices/) - Endpoint analytics, performance monitoring

### Scheduled Tasks (HIGH confidence)
- [Redis Key Expiration Events](https://gokhana.medium.com/redis-key-expiration-automating-tasks-with-redis-events-not-cron-jobs-bc403d0beedb) - 2026 pattern: events > cron
- [Spring Boot Redis Session Cleanup](https://runebook.dev/en/articles/spring_boot/application-properties/application-properties.web.spring.session.redis.cleanup-cron) - Cron-based cleanup patterns
- [Redis TTL Command](https://redis.io/commands/ttl/) - Official Redis documentation

### Streak Mechanics (MEDIUM confidence)
- [Duolingo Streak Freeze](https://duoplanet.com/duolingo-streak-freeze/) - Streak protection patterns
- [Microsoft Rewards Streak Protection](https://support.microsoft.com/en-us/topic/microsoft-rewards-streak-protection-bc0753f8-be5b-4284-9fcd-ee93946ec822) - 14-day protection, annual reset
- [Elevate Streak Freeze](https://support.elevateapp.com/hc/en-us/articles/28507604797595-What-is-a-streak-freeze) - Milestone-based freeze grants

### Industry Context (MEDIUM confidence)
- [Top 7 AI Tools for Gamified Learning 2026](https://www.disco.co/blog/ai-tools-for-gamified-learning-2026) - Real-time progress tracking
- [EdApp Leaderboards](https://support.edapp.com/leaderboards) - Admin best practices
- [Growth Engineering - Leaderboards in LMS](https://www.growthengineering.co.uk/gamification-leaderboards-lms/) - Refreshing leaderboards periodically

---

## Research Confidence Assessment

| Area | Confidence | Rationale |
|------|------------|-----------|
| **Game Sessions** | MEDIUM-HIGH | OWASP and Redis official docs (HIGH). Duolingo patterns (MEDIUM, web search). |
| **Leaderboards** | HIGH | Academic research (JMIR, Penn State) + Google Play official docs. Anti-patterns well-documented. |
| **Device Management** | MEDIUM | Industry best practices (web search). Limited official documentation on educational-specific patterns. |
| **Scheduled Tasks** | HIGH | Redis official docs + Spring Boot patterns. 2026 trend (keyspace events) from recent Medium articles. |

**Low Confidence Areas (need validation):**
- None identified. Research covered all four areas with multiple sources.

**Verification Notes:**
- Leaderboard demotivation research: Cross-verified JMIR study (2021) with Penn State article (2024) and 2026 trends.
- Redis session patterns: Verified OWASP cheatsheet with Redis official documentation.
- Device limits: Verified 3-device standard across multiple industry sources.
- Scheduled task patterns: Verified traditional cron approach with emerging 2026 Redis events pattern.

---

*Research complete. Ready for requirements definition and technical planning.*
