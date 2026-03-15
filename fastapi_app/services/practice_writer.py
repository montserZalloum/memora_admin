"""Practice Arena — Background write worker (Redis Streams consumer).

Provides:
- ``ensure_consumer_group()`` — async, called once on FastAPI startup
- ``process_write_queue()`` — sync, called by Frappe scheduler every minute
- ``reclaim_stale_messages()`` — sync, reclaims stale PEL entries + dead-letter

The write worker consumes messages from the ``memora:practice:write_queue``
Redis Stream, persists results to ``tabMemora Practice Log`` (idempotent
UPSERT with timestamp guard) and updates ``tabPlayer Practice Summary``
question_history JSON.

All sync functions accept a ``db_conn`` parameter compatible with
``frappe.db`` (``.sql()``, ``.commit()``, ``.rollback()``).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog

from fastapi_app.core.redis_keys import (
    PRACTICE_WRITE_QUEUE_DEAD_KEY,
    PRACTICE_WRITE_QUEUE_KEY,
)

logger = structlog.get_logger()

CONSUMER_GROUP = "practice-writers"

MAX_RETRIES = 5
"""Maximum delivery attempts before a message is moved to dead-letter."""

BACKOFF_BASE_MS = 2000
"""Base backoff in milliseconds for exponential retry (2s)."""

BACKOFF_MAX_MS = 32000
"""Maximum backoff in milliseconds (32s)."""


# ---------------------------------------------------------------------------
# Async — FastAPI startup
# ---------------------------------------------------------------------------


async def ensure_consumer_group(redis_client: aioredis.Redis) -> None:
    """Create the consumer group for the practice write queue (idempotent).

    Runs ``XGROUP CREATE memora:practice:write_queue practice-writers 0 MKSTREAM``.
    If the group already exists (``BUSYGROUP`` error), the call is silently
    ignored.  This should be called once on FastAPI startup.
    """
    try:
        await redis_client.xgroup_create(
            name=PRACTICE_WRITE_QUEUE_KEY,
            groupname=CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )
        logger.info(
            "practice_write_queue_group_created",
            stream=PRACTICE_WRITE_QUEUE_KEY,
            group=CONSUMER_GROUP,
        )
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            logger.debug(
                "practice_write_queue_group_exists",
                stream=PRACTICE_WRITE_QUEUE_KEY,
                group=CONSUMER_GROUP,
            )
        else:
            raise


# ---------------------------------------------------------------------------
# Sync — Frappe scheduler startup
# ---------------------------------------------------------------------------


def ensure_consumer_group_sync(redis_client) -> None:
    """Create the consumer group idempotently (synchronous version).

    Called by the Frappe scheduler task before processing, so the group
    exists even if FastAPI hasn't started yet or Redis was flushed.
    """
    try:
        redis_client.xgroup_create(
            name=PRACTICE_WRITE_QUEUE_KEY,
            groupname=CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )
        logger.info(
            "practice_write_queue_group_created",
            stream=PRACTICE_WRITE_QUEUE_KEY,
            group=CONSUMER_GROUP,
        )
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            pass  # Already exists — expected
        else:
            raise


# ---------------------------------------------------------------------------
# T025 — Write worker core (synchronous — called from Frappe scheduler)
# ---------------------------------------------------------------------------


def log_queue_health(redis_client) -> dict:
    """Log write queue health metrics.

    Emits structured logs with queue depth, pending count, and dead-letter
    count for operational dashboards.

    Returns a dict with the metric values for caller use.
    """
    try:
        queue_depth = redis_client.xlen(PRACTICE_WRITE_QUEUE_KEY)
    except Exception:
        queue_depth = -1

    pending_count = 0
    try:
        summary = redis_client.xpending(PRACTICE_WRITE_QUEUE_KEY, CONSUMER_GROUP)
        if summary:
            pending_count = summary.get("pending", 0) if isinstance(summary, dict) else 0
    except Exception:
        pass

    try:
        dead_letter_count = redis_client.xlen(PRACTICE_WRITE_QUEUE_DEAD_KEY)
    except Exception:
        dead_letter_count = -1

    metrics = {
        "queue_depth": queue_depth,
        "pending_count": pending_count,
        "dead_letter_count": dead_letter_count,
    }

    logger.info("practice_write_queue_health", **metrics)
    return metrics


def process_write_queue(redis_client, db_conn) -> int:
    """Process pending messages from the practice write queue.

    Reads up to 10 messages via ``XREADGROUP``, processes each one
    (Practice Log UPSERT + Player Summary update), and ``XACK``s on
    success.  On failure, the message stays in the PEL for retry.

    Parameters
    ----------
    redis_client
        Synchronous Redis client (``decode_responses=True``).
    db_conn
        Database connection with ``.sql()``, ``.commit()``, ``.rollback()``
        methods (e.g. ``frappe.db``).

    Returns
    -------
    int
        Count of successfully processed messages.
    """
    consumer_name = f"writer-{os.getpid()}"

    response = redis_client.xreadgroup(
        groupname=CONSUMER_GROUP,
        consumername=consumer_name,
        streams={PRACTICE_WRITE_QUEUE_KEY: ">"},
        count=10,
        block=5000,
    )

    if not response:
        return 0

    processed = 0
    errors = 0
    for _stream_name, entries in response:
        for message_id, fields in entries:
            try:
                _process_message(fields, db_conn)
                redis_client.xack(
                    PRACTICE_WRITE_QUEUE_KEY, CONSUMER_GROUP, message_id
                )
                processed += 1
                logger.debug(
                    "practice_write_processed",
                    message_id=message_id,
                    player_id=fields.get("player_id"),
                )
            except Exception:
                errors += 1
                logger.error(
                    "practice_write_failed",
                    message_id=message_id,
                    player_id=fields.get("player_id"),
                    exc_info=True,
                )
                # Don't XACK — message stays in PEL for retry
                try:
                    db_conn.rollback()
                except Exception:
                    pass

    logger.info(
        "practice_write_cycle",
        processed=processed,
        errors=errors,
    )
    return processed


def _to_mysql_datetime(iso_ts: str) -> str:
    """Convert ISO 8601 timestamp (e.g. ``2026-03-15T13:43:11.424067+00:00``)
    to MySQL-compatible UTC format (``2026-03-15 13:43:11.424067``)."""
    dt = datetime.fromisoformat(iso_ts)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def _process_message(fields: dict, db_conn) -> None:
    """Process a single write queue message.

    1. UPSERT each result into ``tabMemora Practice Log`` (idempotent).
    2. Update ``tabPlayer Practice Summary`` question_history JSON.
    3. Commit the transaction.
    """
    player_id = fields["player_id"]
    track_id = fields["track_id"]
    subject_id = fields["subject_id"]
    submitted_at = _to_mysql_datetime(fields["submitted_at"])
    results = json.loads(fields["results"])

    # 1. Practice Log UPSERTs
    for result in results:
        _upsert_practice_log(
            db_conn,
            player_id,
            result["item_id"],
            result["is_correct"],
            submitted_at,
        )

    # 2. Player Summary update
    _update_player_summary(
        db_conn, player_id, track_id, subject_id, submitted_at, results
    )

    db_conn.commit()


def _upsert_practice_log(
    db_conn,
    player_id: str,
    item_id: str,
    is_correct: bool,
    submitted_at: str,
) -> None:
    """Idempotent UPSERT into tabMemora Practice Log with timestamp guard.

    The ``IF(VALUES(last_seen_at) > last_seen_at, ...)`` pattern prevents
    double-counting when the same message is processed multiple times.
    """
    last_result = "Correct" if is_correct else "Incorrect"
    correct_count_val = 1 if is_correct else 0

    db_conn.sql(
        """
        INSERT INTO `tabMemora Practice Log`
            (player_id, item_id, first_seen_at, last_seen_at, last_result,
             attempt_count, correct_count)
        VALUES (%s, %s, %s, %s, %s, 1, %s)
        ON DUPLICATE KEY UPDATE
            last_seen_at   = IF(VALUES(last_seen_at) > last_seen_at,
                                VALUES(last_seen_at), last_seen_at),
            last_result    = IF(VALUES(last_seen_at) > last_seen_at,
                                VALUES(last_result), last_result),
            attempt_count  = IF(VALUES(last_seen_at) > last_seen_at,
                                attempt_count + 1, attempt_count),
            correct_count  = IF(VALUES(last_seen_at) > last_seen_at,
                                correct_count + VALUES(correct_count),
                                correct_count)
        """,
        (
            player_id,
            item_id,
            submitted_at,
            submitted_at,
            last_result,
            correct_count_val,
        ),
    )


def _update_player_summary(
    db_conn,
    player_id: str,
    track_id: str,
    subject_id: str,
    submitted_at: str,
    results: list[dict],
) -> None:
    """Update tabPlayer Practice Summary question_history JSON.

    Reads the current row, merges results with a timestamp guard (skip if
    existing ``ls >= submitted_at``), recomputes ``total_seen`` and
    ``total_correct``, and writes back.  If no row exists, INSERTs a new one.
    """
    rows = db_conn.sql(
        """SELECT question_history, total_seen, total_correct
           FROM `tabPlayer Practice Summary`
           WHERE player_id = %s AND track_id = %s""",
        (player_id, track_id),
        as_dict=True,
    )

    if rows:
        history = json.loads(rows[0]["question_history"] or "{}")
    else:
        history = {}

    changed = False
    for result in results:
        item_id = result["item_id"]
        is_correct = result["is_correct"]
        existing = history.get(item_id)

        # Idempotency guard: skip if already processed with same or later timestamp
        if existing and existing.get("ls", "") >= submitted_at:
            continue

        history[item_id] = {
            "lr": "C" if is_correct else "I",
            "ac": (existing["ac"] + 1) if existing else 1,
            "cc": (
                (existing["cc"] + (1 if is_correct else 0))
                if existing
                else (1 if is_correct else 0)
            ),
            "ls": submitted_at,
        }
        changed = True

    if not changed:
        return

    # Recompute totals from history for accuracy
    total_seen = len(history)
    total_correct = sum(entry.get("cc", 0) for entry in history.values())
    history_json = json.dumps(history)

    if rows:
        db_conn.sql(
            """UPDATE `tabPlayer Practice Summary`
               SET question_history = %s,
                   total_seen = %s,
                   total_correct = %s,
                   last_session_at = %s
               WHERE player_id = %s AND track_id = %s""",
            (history_json, total_seen, total_correct, submitted_at,
             player_id, track_id),
        )
    else:
        db_conn.sql(
            """INSERT INTO `tabPlayer Practice Summary`
                   (player_id, track_id, subject_id, question_history,
                    total_seen, total_correct, last_session_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (player_id, track_id, subject_id, history_json,
             total_seen, total_correct, submitted_at),
        )


# ---------------------------------------------------------------------------
# T026 — Retry, backoff, dead-letter, stale message reclaim
# ---------------------------------------------------------------------------


def reclaim_stale_messages(redis_client, db_conn) -> tuple[int, int]:
    """Reclaim stale messages from crashed consumers and handle dead-letter.

    Inspects the PEL via ``XPENDING``, applies exponential backoff per
    message delivery count, and dead-letters messages exceeding
    ``MAX_RETRIES``.

    Parameters
    ----------
    redis_client
        Synchronous Redis client (``decode_responses=True``).
    db_conn
        Database connection (e.g. ``frappe.db``).

    Returns
    -------
    tuple of (processed_count, dead_lettered_count)
    """
    consumer_name = f"writer-{os.getpid()}"

    try:
        pending = redis_client.xpending_range(
            PRACTICE_WRITE_QUEUE_KEY,
            CONSUMER_GROUP,
            min="-",
            max="+",
            count=20,
        )
    except Exception:
        # No pending entries or group doesn't exist yet
        return 0, 0

    if not pending:
        return 0, 0

    processed = 0
    dead_lettered = 0

    for entry in pending:
        msg_id = entry["message_id"]
        delivery_count = entry["times_delivered"]
        idle_ms = entry["time_since_delivered"]

        # Dead-letter: exceeded max retries
        if delivery_count >= MAX_RETRIES:
            messages = redis_client.xrange(
                PRACTICE_WRITE_QUEUE_KEY, min=msg_id, max=msg_id
            )
            if messages:
                _, fields = messages[0]
                _dead_letter_message(redis_client, msg_id, fields, delivery_count)
            else:
                # Message was trimmed but still in PEL — just ACK it
                redis_client.xack(
                    PRACTICE_WRITE_QUEUE_KEY, CONSUMER_GROUP, msg_id
                )
            dead_lettered += 1
            continue

        # Exponential backoff: min(2^(count-1) * 2s, 32s)
        backoff_ms = min(
            BACKOFF_BASE_MS * (2 ** (delivery_count - 1)),
            BACKOFF_MAX_MS,
        )
        if idle_ms < backoff_ms:
            continue  # Not ready for retry yet

        # Claim the message for this consumer
        claimed = redis_client.xclaim(
            PRACTICE_WRITE_QUEUE_KEY,
            CONSUMER_GROUP,
            consumer_name,
            min_idle_time=int(backoff_ms),
            message_ids=[msg_id],
        )

        if not claimed:
            continue

        for claimed_id, fields in claimed:
            try:
                _process_message(fields, db_conn)
                redis_client.xack(
                    PRACTICE_WRITE_QUEUE_KEY, CONSUMER_GROUP, claimed_id
                )
                processed += 1
            except Exception:
                logger.error(
                    "practice_write_reclaim_failed",
                    message_id=claimed_id,
                    delivery_count=delivery_count,
                    exc_info=True,
                )
                try:
                    db_conn.rollback()
                except Exception:
                    pass

    return processed, dead_lettered


def _dead_letter_message(
    redis_client,
    message_id: str,
    fields: dict,
    delivery_count: int,
    error: str = "Max retries exceeded",
) -> None:
    """Move a message to the dead-letter stream and ACK the original."""
    dead_fields = dict(fields)
    dead_fields["original_id"] = str(message_id)
    dead_fields["error"] = error
    dead_fields["delivery_count"] = str(delivery_count)

    redis_client.xadd(PRACTICE_WRITE_QUEUE_DEAD_KEY, dead_fields)
    redis_client.xack(PRACTICE_WRITE_QUEUE_KEY, CONSUMER_GROUP, message_id)

    logger.warning(
        "practice_write_dead_letter",
        message_id=message_id,
        delivery_count=delivery_count,
        error=error,
        player_id=fields.get("player_id"),
    )


async def replay_dead_letters(redis_client, db_conn) -> int:
    """Re-process all messages in the dead-letter stream.

    For each entry, strips the dead-letter metadata (``original_id``,
    ``error``, ``delivery_count``), calls ``_process_message``, and
    removes the entry from the dead-letter stream on success.

    Returns the count of successfully replayed messages.
    """
    entries = await redis_client.xrange(PRACTICE_WRITE_QUEUE_DEAD_KEY, "-", "+")
    if not entries:
        return 0

    replayed = 0
    for msg_id, fields in entries:
        payload = {
            k: v
            for k, v in fields.items()
            if k not in ("original_id", "error", "delivery_count")
        }
        try:
            _process_message(payload, db_conn)
            await redis_client.xdel(PRACTICE_WRITE_QUEUE_DEAD_KEY, msg_id)
            replayed += 1
            logger.info(
                "practice_dead_letter_replayed",
                message_id=msg_id,
                player_id=payload.get("player_id"),
            )
        except Exception:
            logger.error(
                "practice_dead_letter_replay_failed",
                message_id=msg_id,
                player_id=payload.get("player_id"),
                exc_info=True,
            )
            try:
                db_conn.rollback()
            except Exception:
                pass

    logger.info("practice_dead_letter_replay_complete", replayed=replayed, total=len(entries))
    return replayed
