# Feature Landscape: Gamified Education Platform Backend

**Domain:** Gamified education platform backend (Arabic-speaking students)
**Researched:** 2026-02-01
**Overall Confidence:** MEDIUM-HIGH (based on industry patterns, Memora-specific validation needed)

---

## Table Stakes

Features users expect. Missing = product feels incomplete or unusable.

### Progress Tracking

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Lesson completion tracking** | Core educational value - students need to know what they've done | Low | Memora uses bitmap per subject - O(1) lookups. Already designed. |
| **Completion percentage per subject** | Visual progress motivates continuation | Low | Calculate from bitmap BITCOUNT vs total lessons. |
| **Unit/Track/Topic progress rollup** | Hierarchy navigation requires knowing completion at each level | Medium | Aggregate from lesson bitmaps. Cache at API level. |
| **Session state persistence** | Users close app mid-lesson, must resume | Low | Redis hash with TTL (already in PRD). |
| **Progress sync across devices** | Multi-device usage is expected | Medium | Single source of truth in Redis, MariaDB for persistence. |

### Gamification Core

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **XP accumulation** | Core gamification - every action should feel rewarded | Low | Redis HINCRBY on wallet hash. Already designed. |
| **Streak tracking** | Duolingo has trained users to expect daily streaks | Medium | **High complexity in edge cases** (timezone, DST, midnight). See pitfalls. |
| **Basic leaderboard (weekly)** | Competition is expected in gamified apps | Medium | Redis sorted sets. Design for multiple timeframes. |
| **Wallet/stats display** | Users need to see their XP, streak, total lessons | Low | Single Redis hash read. |

### Access Control

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Subscription validation** | Paid content must be gated | Medium | Double-Gate: season status + player grants. Already designed. |
| **Free preview content** | Users need to try before buying | Low | is_free flag at Unit/Topic level. Check before access gate. |
| **Expiration enforcement** | Subscriptions must expire correctly | Medium | TTL-based or date-based. Check on every access. |
| **Purchase flow integration** | Users must be able to buy access | High | Depends on payment provider. Mock for now. |

### Content Delivery

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Fast content loading** | <50ms expected for modern apps | Medium | CDN + JSON build pipeline (already in PRD). |
| **Content versioning** | Updates must not break client | Medium | Version in filename (_h_v2.json) or ETag. |
| **Offline-capable content structure** | Not required now but design for it | Low | JSON structure should be self-contained. |

### Authentication

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **JWT-based auth** | Standard for mobile APIs | Low | FastAPI + pyjwt. Stateless verification. |
| **Device registration** | Multi-device management expected | Medium | Device DocType exists. Limit enforcement. |
| **Session management** | Logout, session listing | Medium | Redis-backed sessions with TTL. |

---

## Differentiators

Features that set Memora apart. Not universally expected, but create competitive advantage.

### Progress & Gamification Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Bitmap-based progress** | Sub-millisecond completion checks at scale | Medium | Memora's key innovation. O(1) vs O(n) lookup. |
| **Excluded bits for deleted lessons** | Handle content changes without breaking progress | Medium | bit_range + excluded_bits in JSON metadata. Unique to Memora. |
| **Multiple leaderboard timeframes** | Daily, weekly, monthly, all-time - more engagement hooks | Medium | Separate sorted sets per timeframe. Reset logic needed. |
| **Friend streaks** | Social accountability increases retention (Duolingo data: 3.6x engagement) | High | Not in current PRD but worth considering for future. |
| **XP boosts** | Time-limited multipliers after streak milestones | Medium | Increases engagement. Simple multiplier in XP calculation. |
| **Streak freeze** | Forgiveness mechanic reduces churn by 21% (Duolingo data) | Medium | Not in current PRD but high-value. Store freeze count in wallet. |

### Achievement System Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Threshold-based achievements** | Incremental progress toward badges (100 lessons, 7-day streak) | Medium | Achievement DocType already has threshold field. |
| **Subject-specific achievements** | Complete all of Math, etc. | Medium | subject field in Achievement DocType supports this. |
| **Achievement types variety** | lessons_completed, streak_days, total_xp, perfect_lesson, speed_demon | Medium | Already designed in DocType. Evaluation logic needed. |
| **Achievement notifications** | Real-time celebration when unlocked | Medium | Requires pub/sub or polling mechanism. |

### Access Control Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Double-Gate architecture** | Instant season-wide updates + individual grants | Medium | Memora's key access pattern. Season check + player set check. |
| **Plan-based access** | Academic plans grant multiple subjects | Medium | Plan → Subjects mapping. SADD multiple access keys on purchase. |
| **Season-bound expiry** | All subscriptions expire with season | Low | Tied to season end_ts. No per-subscription tracking needed. |
| **Real-time access revocation** | Admin can instantly revoke access | Low | SREM from player access set. No cache delay. |

### Content Delivery Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Build pipeline with debouncing** | Collect changes for 2 min, then build | Medium | Reduces redundant builds during active editing. |
| **Hierarchy JSON separation** | _h.json for navigation, _c.json for content | Low | Smaller payloads for browsing vs actual lessons. |
| **Pub/sub cache invalidation** | FastAPI cache updates on build completion | Medium | Redis pub/sub triggers cache clear. |
| **CDN abstraction layer** | Swap mock for R2 without code changes | Low | Interface-based design. Critical for deployment flexibility. |

### Analytics Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Interaction logging** | Every stage completion logged for analytics | Medium | Buffer in Redis list, batch insert to MariaDB. |
| **Pre-aggregated stats** | Analytics Aggregate DocType for dashboards | Medium | Scheduled rollups. Avoids expensive queries. |

---

## Anti-Features

Features to explicitly NOT build. Common mistakes in this domain.

### Gamification Anti-Patterns

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Points for everything** | Devalues rewards, creates inflation. Users stop caring. | XP only for meaningful completions (lessons, streaks). Not for logging in. |
| **Public leaderboards without opt-in** | Demotivates low performers. Privacy concerns. | League-based competition (like Duolingo). Users compete in cohorts of similar activity level. |
| **Badges without clear meaning** | Zappos failure - users didn't understand badge value. | Clear descriptions, visible unlock criteria, tied to real accomplishment. |
| **Streak-only engagement** | Creates anxiety, eventual burnout and churn. | Multiple engagement hooks: XP, achievements, progress. Streak is one element, not the only one. |
| **Complex reward systems** | If users can't understand how to earn, they disengage. | Simple, predictable: Complete lesson = XP. Maintain streak = streak count. |
| **Instant gratification everywhere** | Rewards too easily earned become meaningless. | Tiered achievements with increasing difficulty. Easy early, harder later. |

### Technical Anti-Patterns

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Server-time streak calculations** | Users in different timezones lose streaks unfairly. | Store user timezone. Calculate streaks in user-local time. |
| **Real-time leaderboard on every request** | Expensive ZREVRANGE on every page load at scale. | Cache leaderboard results. Update every 1-5 minutes, not real-time. |
| **Sync on every action** | Unnecessary database writes for hot data. | Write to Redis, batch sync to MariaDB every 1 minute. |
| **Global cache invalidation** | Rebuilding everything when one lesson changes. | Granular invalidation per subject/unit. Build only what changed. |
| **Monolithic content JSON** | 10MB subject JSON kills mobile performance. | Hierarchical JSON: _h.json (navigation), _c.json (unit content), lesson JSON (stage content). |
| **Polling for notifications** | Battery drain, server load. | WebSocket or push notifications for achievements/updates. (Future - out of scope now) |
| **Per-lesson access checks in DB** | O(n) database queries for progress screens. | Bitmap in Redis: O(1) per lesson, O(1) to count completions. |

### Access Control Anti-Patterns

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Client-side access enforcement** | Easily bypassed. Content can be extracted. | Always validate on server. Client can display locked UI, but server gates content. |
| **Complex entitlement hierarchies** | Hard to debug, hard to explain to support. | Simple access keys: SUB-MATH, PLAN-TAWJIHI-2026. Direct set membership check. |
| **No grace period for expiration** | Users lose access mid-lesson. Angry support tickets. | Check expiration only on new lesson start, not mid-session. |
| **Hardcoded subscription logic** | Every pricing change requires code deployment. | Data-driven: Product Grant DocType defines what each purchase grants. |

### Analytics Anti-Patterns

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Real-time dashboards on raw data** | Expensive queries on hot tables. | Pre-aggregated stats. Hourly/daily rollups. |
| **Storing every event forever** | Storage costs, query performance. | Retention policy. Raw logs for 30 days, aggregates for 2 years. |
| **Analytics blocking user actions** | Logging failure shouldn't break lesson completion. | Async buffer. Fire-and-forget to Redis list. Background sync. |

---

## Feature Dependencies

Critical ordering for implementation.

```
┌─────────────────────────────────────────────────────────────────┐
│                        FOUNDATION LAYER                         │
├─────────────────────────────────────────────────────────────────┤
│  Redis Data Layer (bitmaps, hashes, sets, sorted sets)         │
│       └── Required by: Everything                               │
│  JWT Authentication                                             │
│       └── Required by: All API endpoints                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       ACCESS CONTROL LAYER                      │
├─────────────────────────────────────────────────────────────────┤
│  Season meta sync (Frappe → Redis)                              │
│       └── Required by: Gate 1 (season check)                    │
│  Player access set management                                    │
│       └── Required by: Gate 2 (player grants)                   │
│  Double-Gate access check                                        │
│       └── Required by: Content delivery, progress endpoints     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       PROGRESS LAYER                            │
├─────────────────────────────────────────────────────────────────┤
│  Lesson completion (SETBIT)                                      │
│       └── Required by: Progress endpoints, XP award             │
│  Progress fetch (GETBIT, BITCOUNT)                               │
│       └── Required by: Progress endpoints, unlock state         │
│  Session management (start/end lesson)                           │
│       └── Required by: Lesson flow, interaction logging         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       GAMIFICATION LAYER                        │
├─────────────────────────────────────────────────────────────────┤
│  XP award (HINCRBY)                                              │
│       └── Depends on: Lesson completion                         │
│  Streak update                                                   │
│       └── Depends on: Lesson completion, timezone handling      │
│  Wallet endpoint                                                 │
│       └── Depends on: XP, streak data                           │
│  Leaderboard update (ZINCRBY)                                    │
│       └── Depends on: XP award                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       CONTENT DELIVERY LAYER                    │
├─────────────────────────────────────────────────────────────────┤
│  Build pipeline (JSON generation)                                │
│       └── Depends on: Content DocTypes, Build Queue             │
│  CDN upload (mock → R2)                                          │
│       └── Depends on: Build pipeline                            │
│  Cache invalidation (pub/sub)                                    │
│       └── Depends on: Build completion                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       SYNC & PERSISTENCE LAYER                  │
├─────────────────────────────────────────────────────────────────┤
│  Dirty progress sync (Redis → MariaDB)                           │
│       └── Depends on: Progress tracking                         │
│  Dirty wallet sync (Redis → MariaDB)                             │
│       └── Depends on: XP/streak updates                         │
│  Interaction buffer flush                                        │
│       └── Depends on: Session management                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       ACHIEVEMENT LAYER                         │
├─────────────────────────────────────────────────────────────────┤
│  Achievement evaluation                                          │
│       └── Depends on: Wallet (XP, streak), Progress (lessons)   │
│  Achievement unlock tracking                                     │
│       └── Depends on: Achievement evaluation                    │
└─────────────────────────────────────────────────────────────────┘
```

### Critical Path

1. **Redis Data Layer** - Everything depends on Redis structures being defined and initialized
2. **Authentication** - All endpoints require JWT verification
3. **Access Control** - Must work before any content delivery
4. **Progress + XP** - Core game loop
5. **Leaderboards** - Depends on XP being accumulated
6. **Build Pipeline** - Can be developed in parallel after access control
7. **Sync Mechanisms** - Background processes, can be late in implementation
8. **Achievements** - Polish feature, depends on everything else

---

## MVP Recommendation

### Phase 1: Foundation + Core Loop (Must Have)

| Priority | Feature | Rationale |
|----------|---------|-----------|
| 1 | Redis data layer setup | Foundation for everything |
| 2 | JWT authentication | Gate all endpoints |
| 3 | Double-Gate access control | Content must be protected |
| 4 | Progress tracking (bitmap) | Core educational value |
| 5 | XP award on completion | Core gamification |
| 6 | Streak tracking | Expected table stakes |
| 7 | Wallet endpoint | Show XP and streak |

### Phase 2: Engagement + Delivery (Should Have)

| Priority | Feature | Rationale |
|----------|---------|-----------|
| 8 | Weekly leaderboard | Competition drives engagement |
| 9 | Build pipeline | CDN content delivery |
| 10 | Session management | Resume mid-lesson |
| 11 | Interaction logging | Analytics foundation |
| 12 | Background sync | Data durability |

### Phase 3: Polish (Nice to Have)

| Priority | Feature | Rationale |
|----------|---------|-----------|
| 13 | Achievement evaluation | Adds depth to gamification |
| 14 | Multiple leaderboard timeframes | More engagement variety |
| 15 | Streak freeze | Churn reduction |
| 16 | XP boosts | Engagement spike |

### Defer to Post-MVP

| Feature | Reason to Defer |
|---------|-----------------|
| Friend streaks | Requires social graph (not in current scope) |
| Push notifications | Requires Firebase setup (out of scope) |
| Real-time achievements | WebSocket complexity (polling OK for MVP) |
| League-based competition | Requires more users to form meaningful cohorts |
| Offline support | Requires significant client-side complexity |
| Anti-cheat system | Premature optimization until scale proven |

---

## Sources

### Industry Patterns (MEDIUM confidence - web search verified)
- [Duolingo Gamification Secrets](https://www.orizon.co/blog/duolingos-gamification-secrets) - Streak, XP, leaderboard data
- [Trophy - How to Build Streaks](https://trophy.so/blog/how-to-build-a-streaks-feature) - Timezone edge cases
- [Redis Leaderboards](https://redis.io/solutions/leaderboards/) - Sorted set patterns
- [Redis Bitmaps](https://redis.io/docs/latest/develop/data-types/bitmaps/) - Progress tracking patterns
- [TalentLMS - Gamification Mistakes](https://www.talentlms.com/blog/common-gamification-mistakes-avoid/) - Anti-patterns

### Technical Patterns (HIGH confidence - official docs)
- Redis official documentation for SETBIT, GETBIT, ZADD, ZRANGE
- Google Play Games Services achievement documentation

### Memora-Specific (HIGH confidence - project documentation)
- PROJECT.md - Existing requirements and constraints
- DocType schemas - Existing data model (31 DocTypes)
