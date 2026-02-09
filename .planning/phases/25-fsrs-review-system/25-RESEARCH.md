# Phase 25: FSRS Review System - Research

**Researched:** 2026-02-09
**Domain:** FSRS spaced repetition review API, MariaDB indexing, Frappe/FastAPI patterns
**Confidence:** HIGH

## Summary

This phase adds a review system on top of the existing FSRS memory state infrastructure. It has three distinct workstreams: (1) fixing two bugs in the existing `fsrs_processor.py` that cause incorrect Memory State creation, (2) adding a Frappe whitelisted API for querying due reviews from MariaDB, and (3) building three new FastAPI endpoints that serve review data and accept review submissions.

The existing codebase already has the FSRS library (v6.3.0) integrated, `Memora Memory State` DocType with fields for player/subject/stage/stability/difficulty/next_review, and the `fsrs_processor.py` background task that creates Memory State records from lesson completion interactions. The review API is a read-heavy workload (GET due counts, GET due stages) with one write path (POST submit) that runs inline FSRS computation.

**Primary recommendation:** Structure as 3 plans: (1) bug fixes in FSRS processor + composite index, (2) Frappe whitelisted API for due queries + Redis cache, (3) FastAPI review endpoints + service + models.

## Standard Stack

The established libraries/tools for this domain:

### Core (already in codebase)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fsrs | 6.3.0 | FSRS-5 spaced repetition algorithm | Already installed, used by `fsrs_processor.py` |
| FastAPI | (installed) | Review API endpoints | Existing sidecar architecture |
| Pydantic | (installed) | Request/response models | Existing pattern across all endpoints |
| redis.asyncio | (installed) | Redis caching for review overview | Existing pattern (wallet, hierarchy, etc.) |
| httpx | (installed) | FrappeClient for whitelisted API calls | Existing pattern (catalog, hierarchy) |

### Supporting (already in codebase)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | (installed) | Structured logging in FastAPI services | All new endpoints/services |
| frappe | v15 | Whitelisted API for MariaDB queries | Due review queries from MariaDB |

### No New Dependencies Required
This phase uses only existing libraries. No new `pip install` needed.

## Architecture Patterns

### Recommended Project Structure
```
fastapi_app/
├── api/v1/endpoints/
│   └── reviews.py           # NEW: 3 review endpoints
├── models/
│   └── review.py            # NEW: Pydantic models for reviews
├── services/
│   └── review.py            # NEW: ReviewService (Redis cache + Frappe calls)
memora_admin/
├── api/
│   └── reviews.py           # NEW: Frappe whitelisted API (MariaDB queries)
├── tasks/
│   └── fsrs_processor.py    # MODIFIED: Bug fixes (is_reviewable, skippable)
├── memora_admin/doctype/
│   └── memora_memory_state/
│       └── memora_memory_state.py  # MODIFIED: (optional) validate hook
```

### Pattern 1: Frappe Whitelisted API for MariaDB Queries (LOCKED decision)
**What:** Due review data is queried via Frappe whitelisted API, not directly from FastAPI.
**When to use:** Review overview (count per subject) and due stages per subject.
**Why:** Design decision locks MariaDB queries through Frappe (not Redis sorted sets). FastAPI calls Frappe via `FrappeClient.call()` -- same pattern used by hierarchy, catalog, settings.

```python
# In memora_admin/api/reviews.py (Frappe side)
@frappe.whitelist(allow_guest=False)
def get_review_overview(player_id: str) -> list[dict]:
    """Get due review counts per subject for a player.

    Uses composite index on (player, subject, next_review).
    """
    today = frappe.utils.today()  # Returns 'YYYY-MM-DD'
    return frappe.db.sql("""
        SELECT subject, COUNT(*) as due_count
        FROM `tabMemora Memory State`
        WHERE player = %(player)s
          AND next_review <= %(today)s
        GROUP BY subject
    """, {"player": player_id, "today": today}, as_dict=True)
```

```python
# In FastAPI service
result = await self.frappe.call(
    "memora_admin.api.reviews.get_review_overview",
    {"player_id": user.sub},
)
```

### Pattern 2: Redis Cache with TTL + Invalidation
**What:** Review overview cached in Redis with 5-min TTL, invalidated on submit.
**Key pattern:** `memora:reviews_overview:{player_id}` -- same pattern as hierarchy cache.

```python
# Cache key
REVIEW_OVERVIEW_KEY = "memora:reviews_overview:{player_id}"
REVIEW_OVERVIEW_TTL = 300  # 5 minutes

# Read: check cache first, then Frappe API
cached = await self.redis.get(key)
if cached:
    return json.loads(cached)
# ... fetch from Frappe, cache result

# Invalidate on submit
await self.redis.delete(f"memora:reviews_overview:{player_id}")
```

### Pattern 3: Inline FSRS Computation on Submit
**What:** FSRS card review happens inline in the FastAPI submit endpoint, not queued.
**Why:** Design decision -- safe at 200K users (~14 req/s peak). FSRS `review_card()` is pure computation (no I/O), takes <1ms per card.

```python
from fsrs import Card, Scheduler, Rating

# Reconstruct card from Memory State
card = Card()
card.stability = memory_state["stability"]
card.difficulty = memory_state["difficulty"]
card.due = memory_state["next_review"]

# Review
scheduler = Scheduler()
card, log = scheduler.review_card(card, rating, now)

# Clamp to date-only midnight, minimum tomorrow
next_review = card.due.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
tomorrow = date.today() + timedelta(days=1)
if next_review.date() < tomorrow:
    next_review = datetime.combine(tomorrow, time.min)
```

### Pattern 4: Batch Submit with XP Award
**What:** Client submits up to 10 reviewed stages at once. Awards 3 XP per session (not per stage).
**Follows:** Same XP award pattern as `sessions.py:end_session()` -- uses `WalletService.award_xp()`.

### Anti-Patterns to Avoid
- **DO NOT query Memora Memory State from FastAPI directly** -- all MariaDB access goes through Frappe whitelisted API via `FrappeClient.call()`
- **DO NOT use Redis ZADD for due date tracking** -- design decision locks MariaDB queries (memory cost too high)
- **DO NOT award XP per stage** -- design decision: 3 XP per review session (batch)
- **DO NOT update streak on review** -- design decision: reviews don't contribute to daily streak
- **DO NOT schedule reviews for same day** -- next_review must be >= tomorrow at midnight

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JWT authentication | Custom auth middleware | `CurrentUser` dependency from `deps.py` | Already handles token validation, expiry |
| Frappe API calls | Direct HTTP requests | `FrappeClient.call()` from `services/frappe_client.py` | Handles auth, error mapping, singleton |
| XP award | Direct Redis HINCRBY | `WalletService.award_xp()` | Handles dirty set marking for sync |
| Redis connection | New connection pool | `get_redis(request)` from `deps.py` | Uses existing app.state.redis_pool |
| FSRS rating mapping | Custom mapping | `_map_rating()` from `fsrs_processor.py` | Already tested, matches FSRS library |
| Composite index | Raw SQL ALTER TABLE | `bench add-database-index` CLI | Creates Property Setter, persists across migrations |
| Datetime clamping | Timezone-aware comparison | Naive datetimes (no tzinfo) for MariaDB | Frappe/MariaDB expects naive datetimes per MEMORY.md |

**Key insight:** The existing codebase already has all the infrastructure pieces (FrappeClient, WalletService, Redis caching patterns, FSRS library). The review system is assembly of existing components, not greenfield.

## Common Pitfalls

### Pitfall 1: Skippable Stage Filtering Bug (SUCCESS CRITERIA #2)
**What goes wrong:** Current `fsrs_processor.py` line 166 compares `stage_id` against `_get_skippable_stages()` which returns `stage_title` values. These are different -- `stage_id` is a random alphanumeric ID (e.g., `aerviq97bb`), while `stage_title` is a human label (e.g., "My Stage Title"). They never match.
**Why it happens:** `_get_skippable_stages()` queries `Memora Lesson Stage Settings` (the global settings DocType) which uses `stage_title`. But interactions store `stage_id` which is the `name` field of `Memora Lesson Stage` (the child table row).
**How to fix:** Look up the stage's `stage_type` from the lesson's child table `Memora Lesson Stage`, then check if that `stage_type` is in the skippable set. The child table has a `stage_type` field (Link to `Memora Lesson Stage Settings`) and an `is_skippable` override field.
**Correct approach:**
```python
# For each interaction, get stage_type from the lesson's child table
stage_row = frappe.db.get_value(
    "Memora Lesson Stage",
    {"parent": lesson, "stage_title": stage_id},  # stage_title field holds the stage identifier
    ["stage_type", "is_skippable"],
    as_dict=True,
)
if stage_row:
    # Check per-stage override first, then fall back to global setting
    if stage_row.is_skippable:
        skip = True
    else:
        skip = stage_row.stage_type in skippable_types
```

**NOTE on field naming:** The child table `Memora Lesson Stage` has `stage_title` as a Data field holding the stage identifier (the same value stored as `stage_id` in interactions), and `stage_type` as a Link to `Memora Lesson Stage Settings`. This naming is confusing but verified from the JSON schema.

### Pitfall 2: is_reviewable Not Checked (SUCCESS CRITERIA #1)
**What goes wrong:** Current `fsrs_processor.py` creates Memory States for ALL lesson completions, including lessons where `is_reviewable=0`.
**Why it happens:** The field `is_reviewable` exists on `Memora Lesson` DocType but `fsrs_processor.py` never checks it.
**How to fix:** After resolving the lesson, check `is_reviewable` before creating/updating Memory State:
```python
is_reviewable = frappe.db.get_value("Memora Lesson", lesson, "is_reviewable")
if not is_reviewable:
    skipped += 1
    continue
```

### Pitfall 3: next_review Must Be Date-Only (SUCCESS CRITERIA #3)
**What goes wrong:** FSRS library returns `card.due` as a precise datetime (e.g., `2026-02-11 10:54:55+00:00`). Storing this raw value means stages could become due mid-day.
**Why it happens:** FSRS computes intervals in fractional days; the library adds exact interval to review time.
**How to fix:** Clamp `next_review` to midnight (00:00:00) and enforce minimum of tomorrow:
```python
from datetime import date, datetime, time, timedelta

# Clamp to midnight
next_date = card.due.date()
# Minimum tomorrow (no same-day reviews)
tomorrow = date.today() + timedelta(days=1)
if next_date < tomorrow:
    next_date = tomorrow
next_review = datetime.combine(next_date, time.min)
```
This applies to BOTH the existing `fsrs_processor.py` AND the new submit endpoint.

### Pitfall 4: Timezone Handling in MariaDB Queries
**What goes wrong:** Comparing `next_review <= NOW()` might miss stages due today if timezone is wrong.
**Why it happens:** MariaDB stores naive datetimes; `frappe.utils.today()` returns date in system timezone.
**How to avoid:** Use `frappe.utils.today()` consistently (returns `YYYY-MM-DD` string). Compare `next_review <= %(today)s 23:59:59` or use `DATE(next_review) <= CURDATE()` for clarity. Since `next_review` is always midnight, `next_review <= %(today)s` with the date string will correctly match stages due today or earlier.

### Pitfall 5: Removed Stages (SUCCESS CRITERIA #10)
**What goes wrong:** Memory State records exist for stages that were removed from a lesson during a rebuild. Returning them in review results causes client confusion.
**Why it happens:** Rebuilds can change lesson structure (add/remove stages), but Memory State records persist.
**How to avoid:** After querying due stages from MariaDB, validate each stage still exists in the lesson's child table before returning. Skip gracefully if not found:
```python
# In the Frappe API or in validation
stages_in_lesson = frappe.get_all(
    "Memora Lesson Stage",
    filters={"parent": lesson_id},
    pluck="stage_title"
)
if stage_id not in stages_in_lesson:
    # Stage removed by rebuild, skip
    continue
```

### Pitfall 6: Naive Datetime Requirement for MariaDB
**What goes wrong:** Saving UTC-aware datetimes to Frappe/MariaDB causes silent failures or incorrect dates.
**Why it happens:** Per MEMORY.md, this exact bug was already fixed once in `fsrs_processor.py`. Easy to re-introduce.
**How to avoid:** Always strip tzinfo before saving to Frappe: `dt.replace(tzinfo=None)`.

## Code Examples

### Example 1: Frappe Whitelisted API -- Get Due Review Overview
```python
# memora_admin/api/reviews.py
@frappe.whitelist(allow_guest=False)
def get_review_overview(player_id: str) -> list[dict]:
    """Get count of due reviews per subject for a player.

    Uses composite index: (player, subject, next_review).
    Returns: [{"subject": "SUBJ-00001", "due_count": 15}, ...]
    """
    today = frappe.utils.today()
    return frappe.db.sql("""
        SELECT subject, COUNT(*) as due_count
        FROM `tabMemora Memory State`
        WHERE player = %(player)s
          AND DATE(next_review) <= %(today)s
        GROUP BY subject
    """, {"player": player_id, "today": today}, as_dict=True)
```

### Example 2: Frappe Whitelisted API -- Get Due Stages for Subject
```python
@frappe.whitelist(allow_guest=False)
def get_due_stages(player_id: str, subject_id: str, limit: int = 10) -> list[dict]:
    """Get up to 10 due stages for a subject, oldest first (FIFO).

    Returns stage_id, lesson_id, and stage_type.
    Validates stages still exist in their lessons.
    """
    today = frappe.utils.today()
    rows = frappe.db.sql("""
        SELECT ms.name, ms.stage_id, ms.lesson, ms.stability, ms.difficulty, ms.next_review
        FROM `tabMemora Memory State` ms
        WHERE ms.player = %(player)s
          AND ms.subject = %(subject)s
          AND DATE(ms.next_review) <= %(today)s
        ORDER BY ms.next_review ASC
        LIMIT %(limit)s
    """, {
        "player": player_id,
        "subject": subject_id,
        "today": today,
        "limit": limit + 5,  # Over-fetch to account for removed stages
    }, as_dict=True)

    # Validate stages still exist in lessons and get stage_type
    result = []
    for row in rows:
        if len(result) >= limit:
            break
        stage_info = frappe.db.get_value(
            "Memora Lesson Stage",
            {"parent": row.lesson, "stage_title": row.stage_id},
            ["stage_type"],
            as_dict=True,
        )
        if stage_info:
            result.append({
                "stage_id": row.stage_id,
                "lesson_id": row.lesson,
                "stage_type": stage_info.stage_type,
                "memory_state_name": row.name,
                "stability": row.stability,
                "difficulty": row.difficulty,
            })

    return result
```

### Example 3: FastAPI Review Endpoint Models
```python
# fastapi_app/models/review.py
from pydantic import BaseModel

class SubjectReviewCount(BaseModel):
    """Due review count for a single subject."""
    subject_id: str
    due_count: int

class ReviewOverviewResponse(BaseModel):
    """All subjects with due review counts."""
    subjects: list[SubjectReviewCount]

class DueStage(BaseModel):
    """A single stage due for review."""
    stage_id: str
    lesson_id: str
    stage_type: str

class DueStagesResponse(BaseModel):
    """Due stages for a subject."""
    subject_id: str
    stages: list[DueStage]
    has_more: bool

class StageReviewResult(BaseModel):
    """Result of reviewing a single stage."""
    stage_id: str
    fail_count: int = 0

class ReviewSubmitRequest(BaseModel):
    """Batch review submission."""
    stages: list[StageReviewResult]

class ReviewSubmitResponse(BaseModel):
    """Response after submitting reviews."""
    processed: int
    remaining_due: int
    has_more: bool
    xp_awarded: int
```

### Example 4: FastAPI Review Service
```python
# fastapi_app/services/review.py
class ReviewService:
    OVERVIEW_TTL = 300  # 5 minutes

    def __init__(self, redis_client, frappe_client):
        self.redis = redis_client
        self.frappe = frappe_client

    async def get_overview(self, player_id: str) -> list[dict]:
        """Get review overview with Redis caching."""
        key = f"memora:reviews_overview:{player_id}"
        cached = await self.redis.get(key)
        if cached:
            data = cached.decode() if isinstance(cached, bytes) else cached
            return json.loads(data)

        result = await self.frappe.call(
            "memora_admin.api.reviews.get_review_overview",
            {"player_id": player_id},
        )
        subjects = result if isinstance(result, list) else []

        await self.redis.set(key, json.dumps(subjects), ex=self.OVERVIEW_TTL)
        return subjects

    async def invalidate_overview(self, player_id: str):
        """Invalidate cached overview after review submit."""
        await self.redis.delete(f"memora:reviews_overview:{player_id}")
```

### Example 5: Composite Index Creation
```bash
# Run from bench root
bench --site x.conanacademy.com add-database-index \
    --doctype "Memora Memory State" \
    --column player \
    --column subject \
    --column next_review
```
This creates a Property Setter that persists across `bench migrate`.

### Example 6: FSRS Review Card with Date Clamping
```python
from fsrs import Card, Scheduler, Rating
from datetime import date, datetime, time, timedelta, timezone

def compute_next_review(stability, difficulty, fail_count, scheduler=None):
    """Run FSRS review and return clamped next_review (naive datetime)."""
    if scheduler is None:
        scheduler = Scheduler()

    # Reconstruct card
    card = Card()
    card.stability = stability
    card.difficulty = difficulty
    card.due = datetime.now(timezone.utc)

    # Map fail_count to rating
    if fail_count == 0:
        rating = Rating.Good
    elif fail_count == 1:
        rating = Rating.Hard
    else:
        rating = Rating.Again

    # Review
    now = datetime.now(timezone.utc)
    card, _log = scheduler.review_card(card, rating, now)

    # Clamp to date-only (midnight), minimum tomorrow
    next_date = card.due.date()
    tomorrow = date.today() + timedelta(days=1)
    if next_date < tomorrow:
        next_date = tomorrow

    # Return naive datetime for MariaDB
    return datetime.combine(next_date, time.min), card.stability, card.difficulty
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Background FSRS only | Background FSRS + inline review FSRS | Phase 25 | Review submit needs inline FSRS computation |
| No review API | 3 review endpoints | Phase 25 | Client can fetch and submit reviews |
| `next_review` as precise datetime | `next_review` clamped to midnight | Phase 25 | No same-day reviews, cleaner queries |
| `stage_id` compared to `stage_title` for skippable check | `stage_type` looked up from child table | Phase 25 | Skippable filter actually works |

**Deprecated/outdated:**
- The `_get_skippable_stages()` function in `fsrs_processor.py` returns `stage_title` values from the global settings. This must be changed to return the set of skippable stage type names, then compared against the stage's `stage_type` field from the lesson's child table.

## Bug Analysis Detail

### Bug 1: Skippable Stage Filter Never Matches

**Current code** (`fsrs_processor.py:34-44, 166`):
```python
def _get_skippable_stages() -> set[str]:
    stages = frappe.get_all(
        "Memora Lesson Stage Settings",
        filters={"is_skippable": 1},
        fields=["stage_title"],
    )
    return {s.stage_title for s in stages}

# Later (line 166):
if stage_id in skippable:  # stage_id = "aerviq97bb", skippable = {"Video", "Intro", ...}
```

**Why it fails:** `Memora Lesson Stage Settings` is the global settings DocType with entries like "Video", "Intro", "Quiz". Its `stage_title` is a human-readable name. But `stage_id` in interactions comes from the `stage_title` field of the child table `Memora Lesson Stage`, which holds a random ID like `aerviq97bb`.

**The correct approach:** For each interaction's (lesson, stage_id), look up the child row in `Memora Lesson Stage` where `parent=lesson AND stage_title=stage_id`, get its `stage_type` (which links to `Memora Lesson Stage Settings`), then check if that type is skippable either at the type level OR via the per-stage `is_skippable` override.

### Bug 2: is_reviewable Not Checked

**Current code:** `fsrs_processor.py` processes ALL completed interactions regardless of the lesson's `is_reviewable` flag.

**The `Memora Lesson` DocType has:**
- `is_reviewable` (Check, default 0) -- "Is Reviewable (FSRS)"

**Fix:** Add check after resolving the lesson, before processing:
```python
is_reviewable = frappe.db.get_value("Memora Lesson", lesson, "is_reviewable")
if not is_reviewable:
    skipped += 1
    continue
```

## Composite Index Design

### Required Index
**DocType:** `Memora Memory State`
**Columns:** `player, subject, next_review` (in this order)
**MariaDB table:** `tabMemora Memory State`

### Why This Column Order
1. **player** first: All queries filter by player (most selective, narrows to ~200 rows per player)
2. **subject** second: Overview query groups by subject; detail query filters by subject
3. **next_review** third: Both queries filter by `<= today`; ORDER BY `next_review ASC` for FIFO

### Query Coverage
| Query | Uses Index? | How |
|-------|-------------|-----|
| Overview: `WHERE player=? AND DATE(next_review) <= ?` GROUP BY subject | Partial (player prefix) | Index scans player's rows |
| Detail: `WHERE player=? AND subject=? AND DATE(next_review) <= ?` ORDER BY next_review | Full | All three columns used |

**Note:** Using `DATE(next_review)` prevents full index usage for the next_review column. Since next_review is always midnight, `next_review <= '2026-02-09 23:59:59'` would use the index better. However, for the overview query, the GROUP BY subject benefits from the (player, subject) prefix, and at ~200 rows per player the full scan is negligible.

### Performance Estimate at Scale
- 200K players * ~200 stages each = ~40M rows (not 120M yet)
- Index on (player, subject, next_review) narrows to ~200 rows per player
- Query time: <5ms even without partitioning

### Creation Method
```bash
bench --site x.conanacademy.com add-database-index \
    --doctype "Memora Memory State" \
    --column player \
    --column subject \
    --column next_review
```
This persists as a Property Setter document, surviving `bench migrate`.

## FastAPI Endpoint Design

### GET /api/v1/reviews
**Purpose:** Overview of all subjects with due review counts
**Auth:** JWT required (`CurrentUser` dependency)
**Cache:** Redis `memora:reviews_overview:{player}`, 5-min TTL
**Response:**
```json
{
  "subjects": [
    {"subject_id": "SUBJ-00028", "due_count": 15},
    {"subject_id": "SUBJ-00031", "due_count": 8}
  ]
}
```

### GET /api/v1/reviews/{subject}
**Purpose:** Get up to 10 due stages for a specific subject
**Auth:** JWT required
**Cache:** No cache (always fresh, cheap query with index)
**Response:**
```json
{
  "subject_id": "SUBJ-00028",
  "stages": [
    {"stage_id": "aerviq97bb", "lesson_id": "LES-00042", "stage_type": "Quiz"},
    {"stage_id": "bxyz123456", "lesson_id": "LES-00043", "stage_type": "FlashCard"}
  ],
  "has_more": true
}
```

### POST /api/v1/reviews/{subject}/submit
**Purpose:** Submit batch of reviewed stages, run inline FSRS, award XP
**Auth:** JWT required
**Body:**
```json
{
  "stages": [
    {"stage_id": "aerviq97bb", "fail_count": 0},
    {"stage_id": "bxyz123456", "fail_count": 2}
  ]
}
```
**Response:**
```json
{
  "processed": 2,
  "remaining_due": 13,
  "has_more": true,
  "xp_awarded": 3
}
```
**Side effects:**
1. Updates Memory State records in MariaDB (via Frappe API)
2. Awards 3 XP via `WalletService.award_xp()`
3. Invalidates `memora:reviews_overview:{player}` Redis cache

## Dependency Injection Pattern

Follow the existing pattern from `deps.py`:

```python
# In deps.py
async def get_review_service(request: Request) -> ReviewService:
    """Get ReviewService with Redis and FrappeClient."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    frappe_client = await get_frappe_client()
    return ReviewService(redis_client, frappe_client)

ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]
```

## Router Registration

Follow existing pattern in `router.py`:
```python
from fastapi_app.api.v1.endpoints import reviews
router.include_router(reviews.router)
```

## Open Questions

Things that couldn't be fully resolved:

1. **Memory State record naming for submit updates**
   - What we know: Current naming is `{season}-{subject}-{player}-{stage_id}`
   - What's unclear: The submit endpoint needs to update by name. The Frappe API needs the exact name to update via `frappe.db.set_value()`.
   - Recommendation: The GET due stages query should return the `memory_state_name` (or derive it from season+subject+player+stage_id). The Frappe submit API receives stage results and constructs names internally.

2. **Active season resolution in submit flow**
   - What we know: `fsrs_processor.py` uses `_get_active_season()` which queries Memora Season.
   - What's unclear: Should the FastAPI submit endpoint also resolve the active season, or should the Frappe API handle it?
   - Recommendation: Frappe API handles season resolution since it has direct DB access. FastAPI just passes player_id, subject_id, and stage results.

3. **Existing Memory State records with is_reviewable=0 lessons**
   - What we know: Current processor creates Memory States for ALL lessons including non-reviewable ones.
   - What's unclear: Should existing incorrect records be cleaned up?
   - Recommendation: Phase 25 should include a one-time cleanup in the Frappe API or a migration script. Non-reviewable Memory States would pollute review counts.

## Sources

### Primary (HIGH confidence)
- **Codebase inspection** -- All files read directly from the repository:
  - `memora_admin/tasks/fsrs_processor.py` -- Current FSRS processing logic, bugs identified
  - `memora_admin/memora_admin/doctype/memora_memory_state/memora_memory_state.json` -- DocType schema
  - `memora_admin/memora_admin/doctype/memora_lesson/memora_lesson.json` -- `is_reviewable` field confirmed
  - `memora_admin/memora_admin/doctype/memora_lesson_stage/memora_lesson_stage.json` -- `stage_type` (Link to Settings) and `stage_title` (Data) fields confirmed
  - `memora_admin/memora_admin/doctype/memora_lesson_stage_settings/memora_lesson_stage_settings.json` -- `is_skippable` field confirmed
  - `fastapi_app/services/wallet.py` -- XP award pattern
  - `fastapi_app/services/frappe_client.py` -- Frappe API call pattern
  - `fastapi_app/api/deps.py` -- Dependency injection pattern
  - `fastapi_app/api/v1/router.py` -- Router registration pattern
  - `memora_admin/api/hierarchy.py` -- Whitelisted API pattern
  - `memora_admin/hooks.py` -- Scheduler events and doc events pattern

- **FSRS library (v6.3.0)** -- Verified via `pip show fsrs` and runtime testing:
  - `Scheduler.review_card(card, rating, now)` returns `(Card, ReviewLog)`
  - Card has: `stability`, `difficulty`, `due`, `state`, `step`, `last_review`
  - Rating enum: `Again=1`, `Hard=2`, `Good=3`
  - State transitions: Learning(1) -> Review(2) -> Relearning(3)
  - After 2nd Good review, state transitions to Review with multi-day intervals

- **Frappe CLI** -- `bench add-database-index --help` confirmed composite index support

### Secondary (MEDIUM confidence)
- [Frappe Forum: Composite indexes](https://discuss.frappe.io/t/how-to-create-composite-index-on-multiple-columns-in-frappe-erpnext/76070) -- Confirms `bench add-database-index --column col1 --column col2` creates composite index
- [Frappe Docs: MariaDB slow queries](https://docs.frappe.io/cloud/faq/mariadb-slow-queries-in-your-site) -- Confirms Property Setter persistence for indexes

### Tertiary (LOW confidence)
- None -- all findings verified against codebase or official sources

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in codebase, versions verified
- Architecture: HIGH -- follows identical patterns to existing endpoints (catalog, hierarchy, sessions)
- Bug fixes: HIGH -- bugs identified from direct code inspection, root causes confirmed
- Pitfalls: HIGH -- based on codebase patterns and MEMORY.md documented issues
- Index design: HIGH -- verified with `bench add-database-index --help`

**Research date:** 2026-02-09
**Valid until:** 2026-03-09 (stable stack, no moving targets)
