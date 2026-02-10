# Phase 26: Profile Page API - Research

**Researched:** 2026-02-10
**Domain:** FastAPI endpoint design, Redis data aggregation, leveling system, FSRS memory state classification
**Confidence:** HIGH (all findings from codebase analysis of 74+ shipped plans)

## Summary

Phase 26 provides the backend API for the client profile page. This involves six components: hero section (avatar, username, level, XP progress), subject-filtered stats (streak, items learned, XP), memory mastery breakdown (mature/learning/new from FSRS), weekly activity chart (XP per day), avatar selection, and logout.

The codebase already has most of the underlying data: wallet (XP, streak), leaderboard ZSETs (daily/weekly/per-subject XP), Memory State DocType (FSRS stability/difficulty for mastery classification), profile service (display_name, avatar), stats service (completed lesson counts), and session service (invalidation for logout). The primary NEW work is: (1) defining a leveling system (XP-to-level thresholds), (2) aggregating existing data into profile-shaped responses, (3) classifying Memory States into mature/learning/new buckets, and (4) creating new Frappe whitelisted APIs for data not already accessible from FastAPI.

**Primary recommendation:** Build a single `ProfilePageService` that composes existing services (WalletService, ProfileService, LeaderboardService, StatsService) plus new Frappe APIs for memory mastery and weekly XP. Use Redis caching for the aggregated profile response (5-min TTL) with subject-parameterized cache keys. Define XP levels as a static lookup table in constants (not database-configurable), keeping it simple.

## Standard Stack

### Core (already in project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.109+ | API framework | Already used for all sidecar endpoints |
| redis.asyncio | 4.6+ | Redis operations | Already used for all caching |
| Pydantic v2 | 2.5+ | Request/response models | Already used for all models |
| structlog | 23.2+ | Structured logging | Already used across all services |
| httpx | 0.27+ | FrappeClient HTTP calls | Already used for Frappe API |

### Supporting (already in project)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Frappe v15 | 15.x | MariaDB ORM, whitelisted APIs | Memory mastery queries, profile updates |
| zoneinfo | stdlib | Timezone handling | Weekly activity date boundaries (Asia/Amman) |

### No New Dependencies Required
Phase 26 requires zero new library installations. All needed functionality is available through existing stack.

## Architecture Patterns

### Recommended Endpoint Structure
```
fastapi_app/
├── api/v1/endpoints/
│   └── profile.py           # NEW - profile page endpoints (hero, stats, mastery, activity, avatar, logout)
├── services/
│   ├── profile.py            # EXTEND - add get_full_profile(), update_avatar()
│   └── profile_page.py       # NEW - aggregation service composing existing services
├── models/
│   └── profile.py            # EXTEND - add hero, stats, mastery, activity, avatar response models
memora_admin/
└── api/
    └── profile.py            # EXTEND - add get_memory_mastery(), get_weekly_xp(), get_player_profile_full()
```

### Pattern 1: Single Aggregate Endpoint vs. Multiple Small Endpoints
**Recommendation: Multiple focused endpoints with a convenience aggregate.**

The profile page has distinct data sections with different volatility:
- Hero section: changes rarely (avatar, username, level)
- Stats: changes on lesson/review completion
- Memory mastery: changes on review completion
- Weekly activity: changes on lesson/review completion

**Use multiple endpoints:**
```
GET /api/v1/profile                      # Hero section (avatar, username, level, XP)
GET /api/v1/profile/stats                # Stats grid (streak, items learned, XP) with ?subject=
GET /api/v1/profile/mastery              # Memory mastery breakdown with ?subject=
GET /api/v1/profile/activity             # Weekly XP activity with ?subject=
PUT /api/v1/profile/avatar               # Avatar selection
POST /api/v1/profile/logout              # Session + device invalidation
```

**Why not a single endpoint:** Different cache TTLs, different invalidation triggers, and the client may only need to refresh one section (e.g., after completing a lesson, only stats change).

### Pattern 2: Subject Filter via Query Parameter
**What:** All stats endpoints accept `?subject={subject_id}` query parameter.
**When omitted:** Returns combined/aggregated stats across all subjects.
**Implementation:**
```python
@router.get("/stats")
async def get_stats(
    user: CurrentUser,
    subject: str | None = Query(None, description="Subject ID to filter, or omit for all"),
    ...
):
```

### Pattern 3: XP Level Lookup Table
**What:** Static level thresholds defined in constants.py
**Why not database:** Levels rarely change, and fetching from DB adds latency. Constants are sub-microsecond.
**Structure:**
```python
# XP thresholds: level N requires LEVEL_THRESHOLDS[N-1] total XP
LEVEL_THRESHOLDS = [
    0,      # Level 1: 0 XP
    100,    # Level 2: 100 XP
    300,    # Level 3: 300 XP
    600,    # Level 4: 600 XP
    1000,   # Level 5: 1000 XP
    1500,   # Level 6: 1500 XP
    2100,   # Level 7: 2100 XP
    2800,   # Level 8: 2800 XP
    3600,   # Level 9: 3600 XP
    4500,   # Level 10: 4500 XP
    # ... expandable
]

LEVEL_TITLES = [
    "Beginner",       # Level 1
    "Learner",        # Level 2
    "Explorer",       # Level 3
    "Scholar",        # Level 4
    "Achiever",       # Level 5
    "Expert",         # Level 6
    "Master",         # Level 7
    "Champion",       # Level 8
    "Legend",          # Level 9
    "Grandmaster",    # Level 10
]
```

**Level calculation function:**
```python
def calculate_level(total_xp: int) -> tuple[int, str, int, int]:
    """Returns (level, title, current_xp_in_level, xp_to_next_level)"""
    for i in range(len(LEVEL_THRESHOLDS) - 1, -1, -1):
        if total_xp >= LEVEL_THRESHOLDS[i]:
            level = i + 1
            title = LEVEL_TITLES[min(i, len(LEVEL_TITLES) - 1)]
            current_in_level = total_xp - LEVEL_THRESHOLDS[i]
            if i + 1 < len(LEVEL_THRESHOLDS):
                xp_to_next = LEVEL_THRESHOLDS[i + 1] - total_xp
            else:
                xp_to_next = 0  # Max level
            return level, title, current_in_level, xp_to_next
    return 1, LEVEL_TITLES[0], total_xp, LEVEL_THRESHOLDS[1] - total_xp
```

### Pattern 4: FSRS Memory State Classification
**What:** Classify Memory States into mature/learning/new based on stability thresholds.
**Source:** Memora Memory State DocType has `stability` and `difficulty` fields per stage.
**Thresholds (standard FSRS conventions):**
```
- Mature:   stability >= 21.0 days (card will be retained 90%+ in ~21 days)
- Learning: 0 < stability < 21.0 (card has been reviewed but not yet mature)
- New:      stability == 0 OR no Memory State record (never reviewed)
```

**Query (Frappe whitelisted API):**
```sql
-- Count by category for a player + subject
SELECT
  SUM(CASE WHEN stability >= 21.0 THEN 1 ELSE 0 END) as mature,
  SUM(CASE WHEN stability > 0 AND stability < 21.0 THEN 1 ELSE 0 END) as learning,
  SUM(CASE WHEN stability = 0 THEN 1 ELSE 0 END) as new_items
FROM `tabMemora Memory State`
WHERE player = %(player)s
  AND subject = %(subject)s
```

**For "all subjects" aggregate:** Remove the subject filter.

**Note:** "New" items that have never been reviewed have NO Memory State record at all. To get a total count, we need total stages from the hierarchy minus stages with Memory State records. This can be simplified: the profile page "new" count represents stages with `stability=0` (initial review state). Items without any Memory State are not shown in mastery breakdown (they haven't entered the review system at all -- only stages from lessons with `is_reviewable=true` get Memory States).

### Pattern 5: Weekly Activity from Daily Leaderboard ZSETs
**What:** Read XP per day from existing daily leaderboard keys.
**How:** The leaderboard service already tracks per-day XP via `memora:lb:daily:{YYYY-MM-DD}` (global) and `memora:lb:daily:{YYYY-MM-DD}:subject:{subject_id}` (per-subject).

```python
async def get_weekly_activity(self, player_id: str, subject_id: str | None = None) -> list[dict]:
    """Get XP earned per day for current week (Mon-Sun)."""
    now = datetime.now(AMMAN_TZ)
    # Monday = 0, find this week's Monday
    monday = now - timedelta(days=now.weekday())

    days = []
    pipe = self.redis.pipeline()
    for i in range(7):
        day = monday + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        key = f"memora:lb:daily:{date_str}"
        if subject_id:
            key = f"{key}:subject:{subject_id}"
        pipe.zscore(key, player_id)

    scores = await pipe.execute()

    for i in range(7):
        day = monday + timedelta(days=i)
        xp = int(scores[i]) if scores[i] else 0
        days.append({
            "date": day.strftime("%Y-%m-%d"),
            "day": day.strftime("%a"),  # Mon, Tue, ...
            "xp": xp,
        })

    return days
```

**Key insight:** Daily leaderboard ZSETs use `ZINCRBY`, so ZSCORE returns the total XP earned that day. No new data tracking needed -- the data already exists.

**Caveat:** Daily leaderboard keys may expire (archived by scheduled task). The archival task in Phase 11 renames old keys. For the current week, keys will always be present. Past weeks may not have data. This is acceptable for a "this week" view.

### Pattern 6: Avatar Selection with Predefined Options
**What:** Player chooses from a predefined list of avatar identifiers.
**Current state:** `Memora Player Profile.avatar` is a Select field with options `"avatar 1\navatar 2"`.
**Approach:**
- Expose the available avatar list via a GET endpoint (hardcoded or from settings)
- PUT endpoint updates the avatar field in both Frappe (MariaDB) and Redis cache
- The avatar identifier is sent to client; client constructs the full avatar URL/path

**Avatar update flow:**
```
Client: PUT /api/v1/profile/avatar { "avatar": "avatar 3" }
  -> FastAPI validates avatar is in allowed list
  -> Frappe API: update Memora Player Profile.avatar
  -> Redis: invalidate profile cache (memora:profile:{player_id})
  -> Return success
```

### Anti-Patterns to Avoid
- **Aggregating in endpoint handler:** Business logic (level calculation, stat aggregation) should be in a service, not the endpoint handler. Keep endpoint thin.
- **N+1 queries for weekly activity:** Do NOT query 7 Redis keys individually. Use pipeline (already shown above).
- **Caching the aggregate:** Do NOT cache the entire profile response in a single key. Different sections have different invalidation triggers. Cache at the section level if needed, or rely on the speed of underlying cached services.
- **Querying all subjects in a loop:** For "all subjects combined" stats, use SQL aggregation (SUM/COUNT across all subjects) rather than looping through subjects.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Per-day XP tracking | Custom XP log table | Daily leaderboard ZSETs (`memora:lb:daily:{date}`) | Already tracking per-day XP via ZINCRBY in session end flow |
| Total XP | Custom accumulator | WalletService.get_wallet() | Already tracks total XP atomically |
| Streak | Custom streak tracker | WalletService.get_wallet() | Already tracks streak with Lua atomicity |
| Items learned count | Custom counter | StatsService + ProgressService | Stats hash tracks completed counts per subject |
| Session invalidation | Custom token blacklist | SessionService.invalidate_session() | Already deletes session key, causing 401 on next call |
| Profile caching | Custom cache | ProfileService | Already caches display_name + avatar with 1hr TTL |
| Subject hierarchy | Custom DB query | HierarchyService.get_hierarchy() | Already cached with 1hr TTL |
| Timezone handling | Custom offset math | zoneinfo.ZoneInfo("Asia/Amman") | Already used in WalletService and LeaderboardService |

**Key insight:** Nearly all data sources for the profile page already exist. Phase 26 is primarily an aggregation and presentation layer, not new data infrastructure.

## Common Pitfalls

### Pitfall 1: Per-Subject XP Not Stored in Wallet
**What goes wrong:** Attempting to get per-subject XP from the wallet (which only has total XP).
**Why it happens:** The wallet hash (`memora:wallet:{player_id}`) stores only global `xp` and `streak`. Per-subject XP is only in leaderboard ZSETs.
**How to avoid:** For subject-filtered XP, use `ZSCORE` on `memora:lb:alltime:subject:{subject_id}` to get per-subject total XP. Note: the score is a composite score (XP + inverted timestamp for tie-breaking), so extract the integer part with `int(score)`.
**Warning signs:** Getting 0 XP for a subject when the player has clearly earned XP in it.

### Pitfall 2: Leaderboard Composite Score Contains Timestamp Fraction
**What goes wrong:** Returning the raw ZSCORE as XP amount, which includes fractional tie-breaking data.
**Why it happens:** `compute_composite_score()` in leaderboard.py encodes XP + inverted timestamp as a float.
**How to avoid:** Always `int(score)` when reading XP from leaderboard ZSETs. The integer part IS the XP value.
**Warning signs:** XP values like `1500.287654321` instead of `1500`.

### Pitfall 3: Memory State "New" Count Ambiguity
**What goes wrong:** Confusing "stages with stability=0" (initial FSRS state) with "stages never reviewed" (no Memory State record).
**Why it happens:** Memory States are only created for stages in lessons with `is_reviewable=true` AND after the lesson is first completed. Stages the player hasn't encountered yet have no record.
**How to avoid:** Define "mastery breakdown" as ONLY covering stages that have a Memory State record. The three categories are: mature (stability >= threshold), learning (0 < stability < threshold), new (stability == 0). Stages without any Memory State are excluded (not part of review system yet).
**Warning signs:** "New" count being wildly high (counting all stages in the curriculum).

### Pitfall 4: Daily Leaderboard Key Expiry
**What goes wrong:** Weekly activity chart shows 0 XP for days that had activity, because the daily leaderboard key was archived/expired.
**Why it happens:** The scheduled task `archive_daily_leaderboard` renames old daily keys for archival. Keys older than ~2 days may be renamed.
**How to avoid:** For the current week, daily keys should still be active. If a key returns None from ZSCORE, treat it as 0 (which is correct -- either no activity or key expired). For historical data, would need a separate approach (not needed for current-week view).
**Warning signs:** Gaps in the weekly chart for recent days.

### Pitfall 5: Avatar Validation Against Stale Options
**What goes wrong:** Player sends an avatar identifier that's valid but not in the hardcoded allow-list.
**Why it happens:** The avatar Select field options in the DocType (`"avatar 1\navatar 2"`) may be updated without updating the FastAPI validation list.
**How to avoid:** Fetch valid avatar options from Frappe (or the DocType definition) rather than hardcoding in FastAPI. Or define a shared constant. The safest approach: validate by checking if the value is in the Select field options programmatically.
**Warning signs:** 400 errors from avatar update when the client is sending a valid avatar.

### Pitfall 6: Subject Filter Without Subject Validation
**What goes wrong:** Passing a non-existent subject_id returns empty results with no error.
**Why it happens:** Redis ZSCORE on a non-existent key returns None, which becomes 0.
**How to avoid:** For the profile page, returning 0/empty for an invalid subject is acceptable (no error needed). The client only shows subjects the player is enrolled in. Don't validate subject existence for profile stats -- it adds unnecessary latency.

### Pitfall 7: Logout Requires Device ID
**What goes wrong:** Logout invalidates session but doesn't remove the device, leaving a stale device slot.
**Why it happens:** Session invalidation and device removal are separate operations.
**How to avoid:** Logout should: (1) invalidate session (`SessionService.invalidate_session()`), and (2) optionally remove the device (`DeviceService.remove_device()`) if `X-Device-ID` header is provided. The device removal is optional because the user might want to keep the device registered for re-login convenience.
**Decision needed:** Should logout also remove the device? Recommendation: YES, remove the device on logout. This frees up a device slot and follows mobile app conventions.

## Code Examples

### Hero Section Response Model
```python
class HeroResponse(BaseModel):
    """Profile hero section data."""
    display_name: str
    avatar: str
    level: int
    level_title: str
    current_xp: int          # Total XP
    xp_in_level: int          # XP earned within current level
    xp_for_next_level: int    # XP remaining to reach next level
    xp_level_start: int       # XP threshold for current level
    xp_level_end: int         # XP threshold for next level (0 if max)
```

### Stats Response Model
```python
class StatsResponse(BaseModel):
    """Stats grid data (subject-filtered or combined)."""
    subject: str | None = None  # None = combined across all subjects
    streak: int                  # Consecutive days (global, not per-subject)
    items_learned: int           # Completed lessons count
    total_xp: int                # XP (per-subject from leaderboard, or global from wallet)
```

### Memory Mastery Response Model
```python
class MemoryMasteryResponse(BaseModel):
    """Memory mastery breakdown from FSRS Memory States."""
    subject: str | None = None
    mature: int      # stability >= 21.0 days
    learning: int    # 0 < stability < 21.0 days
    new_items: int   # stability == 0 (initial state)
    total: int       # mature + learning + new_items
```

### Weekly Activity Response Model
```python
class DailyXP(BaseModel):
    date: str         # YYYY-MM-DD
    day_name: str     # Mon, Tue, Wed, ...
    xp: int

class WeeklyActivityResponse(BaseModel):
    subject: str | None = None
    week_start: str    # Monday date (YYYY-MM-DD)
    days: list[DailyXP]
    total_xp: int      # Sum of week's XP
```

### Avatar Update Models
```python
class AvatarUpdateRequest(BaseModel):
    avatar: str  # Avatar identifier from predefined list

class AvatarUpdateResponse(BaseModel):
    avatar: str  # Updated avatar identifier
    success: bool
```

### Logout Models
```python
class LogoutResponse(BaseModel):
    success: bool
    message: str = "Logged out successfully"
```

### Frappe API for Memory Mastery
```python
# memora_admin/api/profile.py (extend existing)
@frappe.whitelist(allow_guest=False)
def get_memory_mastery(player_id: str, subject_id: str | None = None) -> dict:
    """Get memory mastery breakdown for a player.

    Returns counts of mature/learning/new Memory States.
    """
    filters = {"player": player_id}
    if subject_id:
        filters["subject"] = subject_id

    result = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN stability >= 21.0 THEN 1 ELSE 0 END) as mature,
            SUM(CASE WHEN stability > 0 AND stability < 21.0 THEN 1 ELSE 0 END) as learning,
            SUM(CASE WHEN stability = 0 THEN 1 ELSE 0 END) as new_items
        FROM `tabMemora Memory State`
        WHERE player = %(player)s
        {subject_filter}
    """.format(
        subject_filter="AND subject = %(subject)s" if subject_id else ""
    ), {"player": player_id, "subject": subject_id}, as_dict=True)

    row = result[0] if result else {}
    return {
        "mature": int(row.get("mature") or 0),
        "learning": int(row.get("learning") or 0),
        "new_items": int(row.get("new_items") or 0),
    }
```

### Frappe API for Profile Update (Avatar)
```python
@frappe.whitelist(allow_guest=False)
def update_player_avatar(player_id: str, avatar: str) -> dict:
    """Update player's avatar selection."""
    profile = frappe.get_doc("Memora Player Profile", player_id)
    profile.avatar = avatar
    profile.save(ignore_permissions=True)
    frappe.db.commit()
    return {"avatar": avatar, "success": True}
```

### Getting Per-Subject XP from Leaderboard
```python
# In the ProfilePageService
async def get_subject_xp(self, player_id: str, subject_id: str) -> int:
    """Get player's total XP for a specific subject from the all-time leaderboard."""
    key = f"memora:lb:alltime:subject:{subject_id}"
    score = await self.redis.zscore(key, player_id)
    if score is None:
        return 0
    return int(score)  # int() strips the composite score timestamp fraction
```

## Data Source Mapping

Summary of where each profile data point comes from:

| Data Point | Source | Redis Key / Service | Cache? |
|------------|--------|---------------------|--------|
| Display name | Memora Player Profile | `memora:profile:{player_id}` (ProfileService) | 1hr TTL |
| Avatar | Memora Player Profile | `memora:profile:{player_id}` (ProfileService) | 1hr TTL |
| Total XP | Memora Player Wallet (via Redis) | `memora:wallet:{player_id}` hash field `xp` | Persistent until sync |
| Per-subject XP | Leaderboard ZSET | `memora:lb:alltime:subject:{subject_id}` | Persistent (ZADD on earn) |
| Streak | Memora Player Wallet (via Redis) | `memora:wallet:{player_id}` hash field `streak` | Persistent until sync |
| Items learned (global) | Stats hash or BITCOUNT | `memora:stats:{user}:{subj}:v{ver}` field `completed` | 1hr TTL |
| Items learned (per-subject) | Stats hash | `memora:stats:{user}:{subj}:v{ver}` field `completed` | 1hr TTL |
| Level | Computed from total XP | Static lookup table in constants | N/A (computed) |
| Memory mastery | Memora Memory State | Frappe SQL query | Cache in Redis (5-min TTL) |
| Weekly XP (per day) | Leaderboard daily ZSETs | `memora:lb:daily:{YYYY-MM-DD}[:subject:{id}]` | Persistent (key per day) |
| Available avatars | Hardcoded or from DocType | Constants or Frappe API | Static |

## Key Design Decisions

### 1. Level System: Static Table (Recommended)
**Options considered:**
- A) Database-configurable levels (Memora Settings child table) -- flexible but adds latency and complexity
- B) Static constants in code -- simple, fast, easily versioned

**Recommendation: Option B.** Levels are a game design constant, not an admin-configurable parameter. Changing level thresholds mid-game would confuse players. Ship with 10+ levels and expand later if needed. The calculation is pure math -- no I/O.

### 2. Per-Subject XP Source
**Options considered:**
- A) Track per-subject XP in a new Redis hash
- B) Read from existing leaderboard all-time subject ZSETs

**Recommendation: Option B.** The leaderboard service already tracks per-subject XP via ZADD/ZINCRBY at session end. No new data tracking needed. ZSCORE is O(log N) which is well under the 50ms target.

### 3. Memory Mastery Caching
**Options considered:**
- A) No cache -- query Frappe every time
- B) Redis cache with 5-min TTL

**Recommendation: Option B.** Memory mastery queries scan the Memory State table which could have 100K+ rows per player. Cache with 5-min TTL and invalidate on review submit (same pattern as review overview). Cache key: `memora:mastery:{player_id}:{subject_id}` (use `all` for global).

### 4. Items Learned Source
**Options considered:**
- A) BITCOUNT on progress bitmap (counts set bits)
- B) Stats hash `completed` field
- C) Frappe query

**Recommendation: Option B (stats hash) with Option A as fallback.** The stats hash already has `completed` count per subject from the lesson completion pipeline. If the stats hash is missing (cold start), fall back to BITCOUNT on the progress bitmap. For "all subjects combined", sum `completed` across all subjects the player has progress in.

### 5. Streak Scope
**Decision:** Streak is GLOBAL only (not per-subject). The wallet tracks a single streak counter based on daily lesson completion. Per the existing design, reviews do NOT contribute to streak. The stats endpoint returns the global streak regardless of subject filter.

### 6. Logout Behavior
**Recommendation:** Logout invalidates session AND removes device.
- `SessionService.invalidate_session(user_id)` -- kills the session, causing 401 on all subsequent requests
- `DeviceService.remove_device(user_id, device_id)` -- frees up the device slot
- Requires `X-Device-ID` header on the logout request (already required pattern from login)

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No level system | Phase 26 introduces XP-to-level mapping | This phase | Players see progression beyond raw XP number |
| Profile = name + avatar only | Profile page with stats, mastery, activity | This phase | Rich profile experience |
| No memory mastery visibility | FSRS stability-based classification | This phase | Players see their retention progress |

**Key architectural note:** This phase is an aggregation layer. It does NOT introduce new data tracking mechanisms. It composes existing services into profile-shaped responses.

## Open Questions

1. **Level thresholds exact values**
   - What we know: Need 10+ levels with increasing XP gaps
   - What's unclear: Exact XP values depend on game economy (base_lesson_xp=100, replay_xp=25, review session=3 XP)
   - Recommendation: Use a progression curve where early levels are quick (100, 300, 600) and later levels require more (5000, 7000, 10000). Can be tuned post-launch.

2. **FSRS maturity threshold**
   - What we know: FSRS stability represents expected retention interval in days
   - What's unclear: Whether 21 days is the right threshold for "mature" in this educational context
   - Recommendation: Start with 21 days (3 weeks), which is a common FSRS convention. Can be adjusted via Memora Settings if needed later.

3. **Avatar list expansion**
   - What we know: Current DocType has only "avatar 1" and "avatar 2" as options
   - What's unclear: How many avatars the design team wants to offer
   - Recommendation: The API should support any number of avatars. The Select field options in the DocType can be updated by the admin. FastAPI should read valid options from the DocType definition (via Frappe API) rather than hardcoding.

4. **Subject list for "all subjects combined"**
   - What we know: Player has access to subjects via access grants and plan membership
   - What's unclear: Should "all subjects" include only subjects the player has access to, or all subjects they've ever interacted with?
   - Recommendation: For stats/mastery, iterate over subjects where the player has a progress bitmap (has interacted). For XP, use the global wallet total. This avoids the problem of including subjects the player lost access to.

## Sources

### Primary (HIGH confidence)
- Codebase analysis of all 31 DocTypes, 17 FastAPI endpoint files, 20 service files
- `fastapi_app/services/wallet.py` -- WalletService (XP, streak)
- `fastapi_app/services/leaderboard.py` -- LeaderboardService (per-subject/per-day XP)
- `fastapi_app/services/profile.py` -- ProfileService (display_name, avatar cache)
- `fastapi_app/services/stats.py` -- StatsService (lesson completion counts)
- `fastapi_app/services/review.py` -- ReviewService (FSRS review operations)
- `fastapi_app/services/session.py` -- SessionService (session invalidation)
- `fastapi_app/services/device.py` -- DeviceService (device removal)
- `memora_admin/memora_admin/doctype/memora_memory_state/memora_memory_state.json` -- FSRS fields
- `memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.json` -- avatar Select
- `memora_admin/memora_admin/doctype/memora_player_wallet/memora_player_wallet.json` -- XP/streak fields
- `fastapi_app/api/deps.py` -- dependency injection pattern

### Secondary (MEDIUM confidence)
- FSRS stability threshold of 21 days -- based on general FSRS convention, not verified against this project's specific weights

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in use, zero new dependencies
- Architecture: HIGH -- follows established patterns from 74 shipped plans
- Data sources: HIGH -- verified every Redis key and MariaDB table referenced
- FSRS mastery thresholds: MEDIUM -- 21-day threshold is convention, may need tuning
- Level system design: MEDIUM -- thresholds are arbitrary game design, need playtesting

**Research date:** 2026-02-10
**Valid until:** 2026-03-10 (stable -- no external dependencies to go stale)
