"""Practice Arena — Core service layer.

Implements player summary cache, question selection algorithm, and session
management. All functions operate on Redis + in-memory data only — zero
direct DB queries in the hot path.

DB access (for cache-miss hydration) is gated behind an optional
``frappe_client`` parameter and happens at most once per (player, track)
per 2-hour cache window.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog

from fastapi_app.core.redis_keys import (
    PRACTICE_RATE_LIMIT_TTL,
    PRACTICE_SESSION_TTL,
    PRACTICE_SUMMARY_TTL,
    PRACTICE_WRITE_QUEUE_KEY,
    hierarchy_key,
    practice_rate_key,
    practice_summary_key,
    practice_session_key,
)

if TYPE_CHECKING:
    from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# T008 — Player summary cache
# ---------------------------------------------------------------------------


async def get_player_summary(
    redis_client: aioredis.Redis,
    player_id: str,
    track_id: str,
    frappe_client: "FrappeClient | None" = None,
) -> dict:
    """Return the player's question_history dict for *track_id*.

    1. Read from Redis cache (hot path — zero DB).
    2. On cache miss, read from ``tabPlayer Practice Summary`` via
       *frappe_client*.
    3. Populate Redis with the result (even if empty) so subsequent
       calls within the 2-hour window are instant.
    4. Return ``{}`` for new players.
    """
    key = practice_summary_key(player_id, track_id)
    cached = await redis_client.get(key)
    if cached is not None:
        logger.debug("practice_summary_cache_hit", player_id=player_id, track_id=track_id)
        return json.loads(cached)

    # Cache miss — try DB
    logger.info("practice_summary_cache_miss", player_id=player_id, track_id=track_id)
    history: dict = {}

    if frappe_client is not None:
        try:
            result = await frappe_client.call(
                "memora_admin.api.practice_summary.get_player_practice_summary",
                params={"player_id": player_id, "track_id": track_id},
            )
            if result and isinstance(result, dict):
                history = result
        except Exception:
            logger.warning(
                "practice_summary_db_fallback_failed",
                player_id=player_id,
                track_id=track_id,
                exc_info=True,
            )

    # Populate cache (even if empty — prevents repeated DB misses)
    await redis_client.set(key, json.dumps(history), ex=PRACTICE_SUMMARY_TTL)
    return history


async def set_player_summary(
    redis_client: aioredis.Redis,
    player_id: str,
    track_id: str,
    history: dict,
) -> None:
    """Write the player's question_history to Redis cache."""
    key = practice_summary_key(player_id, track_id)
    await redis_client.set(key, json.dumps(history), ex=PRACTICE_SUMMARY_TTL)


# ---------------------------------------------------------------------------
# T009 — Question selection algorithm
# ---------------------------------------------------------------------------


def _extract_scope_questions(
    map_data: dict,
    track_ids: list[str],
    unit_ids: list[str] | None,
    topic_ids: list[str] | None,
) -> list[dict]:
    """Extract all questions matching the requested scope from *map_data*."""
    questions: list[dict] = []
    tracks = map_data.get("tracks", {})

    for track_id in track_ids:
        track = tracks.get(track_id)
        if track is None:
            continue
        for uid, unit in track.get("units", {}).items():
            if unit_ids and uid not in unit_ids:
                continue
            for tid, topic in unit.get("topics", {}).items():
                if topic_ids and tid not in topic_ids:
                    continue
                for q in topic.get("questions", []):
                    questions.append({**q, "_track_id": track_id})

    return questions


def _sort_key(question: dict, player_history: dict) -> tuple:
    """Build a comparison key for priority-based sorting.

    Priority order (ascending — lowest key value = highest priority):
    0. Unseen questions
    1. Last-incorrect questions
    2. Seen-correct — lower correct ratio → higher priority
       → tie-break by oldest last-seen (earlier timestamp = higher priority)

    A random tiebreaker is appended so questions within the same priority
    tier are shuffled instead of returning in deterministic map-file order.
    """
    qid = question["id"]
    h = player_history.get(qid)

    if h is None:
        # Unseen — highest priority
        return (0, 0.0, "", random.random())

    if h.get("lr") == "I":
        # Last-incorrect — second priority; oldest first
        return (1, 0.0, h.get("ls", ""), random.random())

    # Seen-correct: lower ratio = higher priority, older = higher priority
    ac = max(h.get("ac", 1), 1)
    ratio = h.get("cc", 0) / ac
    return (2, ratio, h.get("ls", ""), random.random())


def select_questions(
    map_data: dict,
    track_ids: list[str],
    unit_ids: list[str] | None,
    topic_ids: list[str] | None,
    player_history: dict,
    served_ids: set[str],
    batch_size: int = 20,
) -> tuple[list[str], list[int], int, bool, dict[str, str]]:
    """Select up to *batch_size* questions from *map_data* matching scope.

    Returns
    -------
    tuple of (question_ids, chunk_refs, total_available, all_seen_warning, question_track_map)
        - question_ids: ordered list of selected question UUIDs
        - chunk_refs: deduplicated, sorted chunk IDs for the batch
        - total_available: count of all in-scope questions in the map
        - all_seen_warning: True if wrapping around (all questions seen)
        - question_track_map: dict mapping question_id → track_id
    """
    t0 = time.monotonic()

    candidates = _extract_scope_questions(map_data, track_ids, unit_ids, topic_ids)
    total_available = len(candidates)

    if total_available == 0:
        return [], [], 0, False, {}

    # Exclude already-served questions (in-session repeat avoidance)
    available = [q for q in candidates if q["id"] not in served_ids]

    all_seen_warning = False
    if not available:
        # All in-scope questions have been served — wrap around
        available = list(candidates)
        all_seen_warning = True

    # Sort by priority
    available.sort(key=lambda q: _sort_key(q, player_history))

    # Take top batch_size
    batch = available[:batch_size]
    question_ids = [q["id"] for q in batch]
    chunk_refs = sorted({q["chunk"] for q in batch})
    question_track_map = {q["id"]: q["_track_id"] for q in batch}

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "practice_question_selection",
        total_available=total_available,
        candidates=len(available),
        selected=len(question_ids),
        all_seen_warning=all_seen_warning,
        elapsed_ms=round(elapsed_ms, 2),
    )

    return question_ids, chunk_refs, total_available, all_seen_warning, question_track_map


# ---------------------------------------------------------------------------
# T010 — Session creation
# ---------------------------------------------------------------------------


def compute_scope_hash(
    subject_id: str,
    track_ids: list[str],
    unit_ids: list[str] | None,
    topic_ids: list[str] | None,
) -> str:
    """Deterministic hash of the session scope for validation."""
    payload = json.dumps(
        {"subject_id": subject_id, "track_ids": sorted(track_ids),
         "unit_ids": sorted(unit_ids) if unit_ids else None,
         "topic_ids": sorted(topic_ids) if topic_ids else None},
        sort_keys=True,
    )
    return hashlib.md5(payload.encode()).hexdigest()


async def create_session(
    redis_client: aioredis.Redis,
    player_id: str,
    subject_id: str,
    track_ids: list[str],
    scope_hash: str,
    question_ids: list[str],
    chunk_refs: list[int],
    unit_ids: list[str] | None = None,
    topic_ids: list[str] | None = None,
    question_track_map: dict[str, str] | None = None,
) -> str:
    """Create a new practice session in Redis.

    Any existing session for *player_id* is deleted first (session
    replacement per FR-008).  Returns the generated session UUID.
    """
    key = practice_session_key(player_id)
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Delete existing session (replacement)
    await redis_client.delete(key)

    # Create session hash
    await redis_client.hset(
        key,
        mapping={
            "session_id": session_id,
            "subject_id": subject_id,
            "track_ids": json.dumps(track_ids),
            "unit_ids": json.dumps(unit_ids) if unit_ids else "",
            "topic_ids": json.dumps(topic_ids) if topic_ids else "",
            "scope_hash": scope_hash,
            "batch_seq": "0",
            "current_batch": json.dumps(question_ids),
            "submitted": "0",
            "batch_stats": "",
            "served_ids": json.dumps(question_ids),
            "chunk_refs": json.dumps(chunk_refs),
            "question_track_map": json.dumps(question_track_map or {}),
            "created_at": now,
            "last_activity_at": now,
        },
    )
    await redis_client.expire(key, PRACTICE_SESSION_TTL)

    logger.info(
        "practice_session_created",
        player_id=player_id,
        session_id=session_id,
        subject_id=subject_id,
        track_count=len(track_ids),
        batch_size=len(question_ids),
    )
    return session_id


# ---------------------------------------------------------------------------
# Free-content scope helpers
# ---------------------------------------------------------------------------


async def load_free_content_scope(
    redis_client: aioredis.Redis,
    subject_id: str,
) -> tuple[set[str], set[str]]:
    """Load free_topics and free_units from the hierarchy cache.

    Returns ``(free_topics_set, free_units_set)``.  Both are empty when the
    hierarchy is not cached or contains no free content markers.
    One Redis GET (~0.5 ms).
    """
    raw = await redis_client.get(hierarchy_key(subject_id))
    if not raw:
        return set(), set()
    data = json.loads(raw)
    return set(data.get("free_topics", [])), set(data.get("free_units", []))


def resolve_allowed_free_topics(
    map_data: dict,
    track_ids: list[str],
    free_topics: set[str],
    free_units: set[str],
) -> set[str]:
    """Return topic IDs that are free within the requested tracks.

    A topic is free if its ID is in *free_topics* **or** its parent unit
    ID is in *free_units*.  Pure in-memory set lookups — O(topics in scope).
    """
    result: set[str] = set()
    tracks = map_data.get("tracks", {})
    for track_id in track_ids:
        track = tracks.get(track_id)
        if not track:
            continue
        for uid, unit in track.get("units", {}).items():
            if uid in free_units:
                result.update(unit.get("topics", {}).keys())
            else:
                for tid in unit.get("topics", {}).keys():
                    if tid in free_topics:
                        result.add(tid)
    return result


# ---------------------------------------------------------------------------
# Scope validation helpers (used by endpoints)
# ---------------------------------------------------------------------------


def validate_scope(
    map_data: dict,
    track_ids: list[str],
    unit_ids: list[str] | None,
    topic_ids: list[str] | None,
) -> None:
    """Validate that all requested IDs exist in the map file.

    Raises
    ------
    ValueError
        With a descriptive message if validation fails.
    """
    tracks = map_data.get("tracks", {})

    # Validate track_ids
    missing_tracks = [t for t in track_ids if t not in tracks]
    if missing_tracks:
        raise ValueError(f"Unknown track_ids: {missing_tracks}")

    # Validate unit_ids
    if unit_ids:
        all_units: set[str] = set()
        for tid in track_ids:
            all_units.update(tracks.get(tid, {}).get("units", {}).keys())
        missing_units = [u for u in unit_ids if u not in all_units]
        if missing_units:
            raise ValueError(f"Unknown unit_ids: {missing_units}")

    # Validate topic_ids
    if topic_ids:
        all_topics: set[str] = set()
        for tid in track_ids:
            for unit in tracks.get(tid, {}).get("units", {}).values():
                all_topics.update(unit.get("topics", {}).keys())
        missing_topics = [t for t in topic_ids if t not in all_topics]
        if missing_topics:
            raise ValueError(f"Unknown topic_ids: {missing_topics}")


# ---------------------------------------------------------------------------
# T021 — Rate limiting for session creation
# ---------------------------------------------------------------------------

PRACTICE_RATE_LIMIT_MAX = 30
"""Maximum number of session starts per player per hour (FR-010)."""


async def check_rate_limit(
    redis_client: aioredis.Redis,
    player_id: str,
) -> None:
    """Enforce per-player session creation rate limit.

    INCR the rate counter.  On the first increment (counter == 1) set a
    1-hour TTL so the window auto-resets.  If the counter exceeds
    ``PRACTICE_RATE_LIMIT_MAX``, raise a ``RateLimitExceeded`` with the
    remaining TTL so the endpoint can set a Retry-After header.

    Raises
    ------
    RateLimitExceeded
        When the player has exceeded the maximum sessions per hour.
    """
    key = practice_rate_key(player_id)
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, PRACTICE_RATE_LIMIT_TTL)

    if count > PRACTICE_RATE_LIMIT_MAX:
        ttl = await redis_client.ttl(key)
        retry_after = max(ttl, 1)
        logger.warning(
            "practice_rate_limit_exceeded",
            player_id=player_id,
            count=count,
            retry_after=retry_after,
        )
        raise RateLimitExceeded(retry_after)


class RateLimitExceeded(Exception):
    """Raised when a player exceeds the session creation rate limit."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s")


# ---------------------------------------------------------------------------
# T022 — Session TTL refresh
# ---------------------------------------------------------------------------


async def refresh_session_ttl(
    redis_client: aioredis.Redis,
    player_id: str,
) -> None:
    """Refresh the session TTL and update ``last_activity_at``.

    Called after successful submit and continue to reset the 1-hour
    inactivity timer (FR-009).
    """
    key = practice_session_key(player_id)
    now = datetime.now(timezone.utc).isoformat()
    await redis_client.hset(key, "last_activity_at", now)
    await redis_client.expire(key, PRACTICE_SESSION_TTL)


# ---------------------------------------------------------------------------
# T012 — Submission validation and stats computation
# ---------------------------------------------------------------------------


async def get_session(
    redis_client: aioredis.Redis,
    player_id: str,
) -> dict | None:
    """Read the session hash from Redis. Returns None if no active session."""
    key = practice_session_key(player_id)
    data = await redis_client.hgetall(key)
    if not data:
        return None
    return data


def validate_submission(
    session: dict,
    batch_seq: int,
    results: list[dict],
) -> None:
    """Validate a submission against the current session state.

    Raises ValueError with a descriptive message on validation failure.
    """
    current_batch_seq = int(session["batch_seq"])
    if batch_seq != current_batch_seq:
        raise ValueError(
            f"batch_seq {batch_seq} does not match current batch {current_batch_seq}"
        )

    current_batch = json.loads(session["current_batch"])

    # Check for duplicate item_ids in payload
    item_ids = [r["item_id"] for r in results]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("Duplicate item_ids in submission")

    # Check all item_ids exist in current batch
    batch_set = set(current_batch)
    for item_id in item_ids:
        if item_id not in batch_set:
            raise ValueError(f"item_id '{item_id}' is not in the current batch")

    # Check result count does not exceed batch size (partial submissions OK)
    if len(results) > len(current_batch):
        raise ValueError(
            f"Expected at most {len(current_batch)} results, got {len(results)}"
        )


def compute_stats(results: list[dict]) -> tuple[int, int, float]:
    """Compute accuracy statistics from results.

    Returns (correct_count, total_count, accuracy_percent).
    """
    total_count = len(results)
    correct_count = sum(1 for r in results if r["is_correct"])
    accuracy_percent = (correct_count / total_count * 100.0) if total_count > 0 else 0.0
    return correct_count, total_count, accuracy_percent


# ---------------------------------------------------------------------------
# T013 — Submit results flow
# ---------------------------------------------------------------------------


async def submit_results(
    redis_client: aioredis.Redis,
    player_id: str,
    session: dict,
    batch_seq: int,
    results: list[dict],
) -> dict:
    """Process a batch submission.

    Returns a dict with keys: accepted, batch_seq, correct_count,
    total_count, accuracy_percent, is_duplicate.
    """
    key = practice_session_key(player_id)

    # --- Duplicate detection (FR-019) ---
    if session["submitted"] == "1" and int(session["batch_seq"]) == batch_seq:
        cached_stats = json.loads(session["batch_stats"]) if session.get("batch_stats") else {}
        if cached_stats:
            logger.info("practice_submit_duplicate", player_id=player_id, batch_seq=batch_seq)
            return {
                "accepted": True,
                "batch_seq": batch_seq,
                "correct_count": cached_stats["correct_count"],
                "total_count": cached_stats["total_count"],
                "accuracy_percent": cached_stats["accuracy_percent"],
                "is_duplicate": True,
            }

    # --- Compute stats ---
    correct_count, total_count, accuracy_percent = compute_stats(results)

    # --- Update player summary cache (FR-020) ---
    track_ids = json.loads(session["track_ids"])
    now = datetime.now(timezone.utc).isoformat()

    # Route each result to its owning track to avoid cross-contamination
    question_track_map = json.loads(session.get("question_track_map", "{}"))
    fallback_track = track_ids[0] if track_ids else ""
    track_results: dict[str, list[dict]] = {}
    for r in results:
        tid = question_track_map.get(r["item_id"], fallback_track)
        track_results.setdefault(tid, []).append(r)

    for track_id, track_res in track_results.items():
        history = await get_player_summary(redis_client, player_id, track_id)
        for r in track_res:
            item_id = r["item_id"]
            existing = history.get(item_id)
            history[item_id] = {
                "lr": "C" if r["is_correct"] else "I",
                "ac": (existing["ac"] + 1) if existing else 1,
                "cc": (
                    (existing["cc"] + (1 if r["is_correct"] else 0))
                    if existing
                    else (1 if r["is_correct"] else 0)
                ),
                "ls": now,
            }
        await set_player_summary(redis_client, player_id, track_id, history)

    # --- Enqueue to write queue per track (FR-021) ---
    for track_id, track_res in track_results.items():
        await redis_client.xadd(
            PRACTICE_WRITE_QUEUE_KEY,
            {
                "player_id": player_id,
                "track_id": track_id,
                "subject_id": session["subject_id"],
                "submitted_at": now,
                "batch_seq": str(batch_seq),
                "session_id": session["session_id"],
                "results": json.dumps(
                    [{"item_id": r["item_id"], "is_correct": r["is_correct"]} for r in track_res]
                ),
            },
            maxlen=100000,
            approximate=True,
        )

    # --- Mark session as submitted ---
    stats_json = json.dumps({
        "correct_count": correct_count,
        "total_count": total_count,
        "accuracy_percent": accuracy_percent,
    })
    await redis_client.hset(key, mapping={
        "submitted": "1",
        "batch_stats": stats_json,
    })

    # --- Refresh session TTL (T022) ---
    await refresh_session_ttl(redis_client, player_id)

    logger.info(
        "practice_batch_submitted",
        player_id=player_id,
        batch_seq=batch_seq,
        correct_count=correct_count,
        total_count=total_count,
        accuracy_percent=accuracy_percent,
    )

    return {
        "accepted": True,
        "batch_seq": batch_seq,
        "correct_count": correct_count,
        "total_count": total_count,
        "accuracy_percent": accuracy_percent,
        "is_duplicate": False,
    }


# ---------------------------------------------------------------------------
# T015 — Continue session (next batch)
# ---------------------------------------------------------------------------


async def continue_session(
    redis_client: aioredis.Redis,
    player_id: str,
    session: dict,
    map_data: dict,
    batch_size: int = 20,
) -> dict:
    """Advance the session to the next batch of questions.

    Preconditions (checked by caller or here):
    - Session exists (caller returns 404 otherwise).
    - ``session["submitted"]`` is ``"1"`` (current batch submitted).

    Steps:
    1. Verify current batch is submitted (FR-023).
    2. Load updated player summary from cache (reflects just-submitted
       answers per FR-024).
    3. Call ``select_questions`` with served_ids to avoid repeats (FR-015).
    4. Increment ``batch_seq``, update session hash.

    Returns a dict matching ``BatchResponse`` fields.

    Raises
    ------
    ValueError
        If the current batch has not been submitted yet.
    """
    # 1. Guard: current batch must be submitted
    if session.get("submitted") != "1":
        raise ValueError("Current batch has not been submitted yet")

    # 2. Load updated player summary from Redis cache
    track_ids = json.loads(session["track_ids"])
    combined_history: dict = {}
    for track_id in track_ids:
        history = await get_player_summary(redis_client, player_id, track_id)
        combined_history.update(history)

    # 3. Reconstruct scope filters from session
    unit_ids_raw = session.get("unit_ids", "")
    topic_ids_raw = session.get("topic_ids", "")
    unit_ids: list[str] | None = json.loads(unit_ids_raw) if unit_ids_raw else None
    topic_ids: list[str] | None = json.loads(topic_ids_raw) if topic_ids_raw else None

    served_ids = set(json.loads(session["served_ids"]))

    # 4. Select next batch of questions
    question_ids, chunk_refs, total_available, all_seen_warning, question_track_map = select_questions(
        map_data,
        track_ids,
        unit_ids,
        topic_ids,
        combined_history,
        served_ids,
        batch_size,
    )

    # 5. Update session hash
    new_batch_seq = int(session["batch_seq"]) + 1
    new_served_ids = list(served_ids | set(question_ids))
    now = datetime.now(timezone.utc).isoformat()

    key = practice_session_key(player_id)
    await redis_client.hset(
        key,
        mapping={
            "batch_seq": str(new_batch_seq),
            "current_batch": json.dumps(question_ids),
            "submitted": "0",
            "batch_stats": "",
            "served_ids": json.dumps(new_served_ids),
            "chunk_refs": json.dumps(chunk_refs),
            "question_track_map": json.dumps(question_track_map),
            "last_activity_at": now,
        },
    )

    # --- Refresh session TTL (T022) ---
    await redis_client.expire(key, PRACTICE_SESSION_TTL)

    logger.info(
        "practice_session_continued",
        player_id=player_id,
        batch_seq=new_batch_seq,
        batch_size=len(question_ids),
        total_served=len(new_served_ids),
        all_seen_warning=all_seen_warning,
    )

    return {
        "session_active": True,
        "batch_seq": new_batch_seq,
        "question_ids": question_ids,
        "chunk_refs": chunk_refs,
        "total_available": total_available,
        "all_seen_warning": all_seen_warning,
    }
