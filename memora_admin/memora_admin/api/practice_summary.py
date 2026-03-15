"""Practice Arena — Player summary API endpoints.

Provides whitelisted methods for reading player practice summaries
from the database. Used by FastAPI on cache miss to hydrate Redis.
Also contains the one-time backfill script for migrating legacy Practice Log
data into the Player Practice Summary table, and admin utilities for
emergency operations (session expiry, dead-letter reprocessing).
"""

import json
import logging

import frappe

from fastapi_app.core.redis_keys import (
	PRACTICE_SESSION_SCAN_PATTERN,
	PRACTICE_WRITE_QUEUE_DEAD_KEY,
	PRACTICE_WRITE_QUEUE_KEY,
)

logger = logging.getLogger(__name__)


@frappe.whitelist(methods=["POST"])
def get_player_practice_summary(player_id: str, track_id: str) -> dict:
	"""Read a player's question_history from tabPlayer Practice Summary.

	Returns the parsed JSON dict, or empty dict if no row exists.
	Called by FastAPI service on Redis cache miss.
	"""
	result = frappe.db.sql(
		"""
		SELECT question_history
		FROM `tabPlayer Practice Summary`
		WHERE player_id = %s AND track_id = %s
		LIMIT 1
		""",
		(player_id, track_id),
		as_dict=True,
	)
	if result and result[0].get("question_history"):
		return frappe.parse_json(result[0]["question_history"])
	return {}


@frappe.whitelist()
def backfill_player_summaries(batch_size=1000):
	"""One-time backfill: aggregate tabMemora Practice Log into tabPlayer Practice Summary.

	Reads all practice history, JOINs with Review Item to resolve track_id,
	builds question_history JSON per (player, track), and UPSERTs into the
	summary table. Idempotent — safe to re-run (ON DUPLICATE KEY UPDATE).

	Args:
		batch_size: Number of players to process per batch (default 1000).

	Returns:
		dict with total_players and rows_upserted counts.
	"""
	batch_size = int(batch_size)
	logger = frappe.logger("practice_backfill")

	# 1. Get distinct player_ids
	players = frappe.db.sql(
		"SELECT DISTINCT player_id FROM `tabMemora Practice Log` ORDER BY player_id",
		as_list=True,
	)
	player_ids = [row[0] for row in players]
	total_players = len(player_ids)
	logger.info(f"Backfill: {total_players} distinct players to process")

	if total_players == 0:
		logger.info("Backfill: no players found, nothing to do")
		return {"total_players": 0, "rows_upserted": 0}

	rows_upserted = 0

	# 2. Process in batches
	for offset in range(0, total_players, batch_size):
		batch = player_ids[offset : offset + batch_size]
		placeholders = ", ".join(["%s"] * len(batch))

		# 3. Fetch practice log rows joined with Review Item for track/subject
		rows = frappe.db.sql(
			f"""
			SELECT
				pl.player_id,
				ri.track AS track_id,
				ri.subject AS subject_id,
				pl.item_id,
				pl.last_result,
				pl.attempt_count,
				pl.correct_count,
				pl.last_seen_at
			FROM `tabMemora Practice Log` pl
			JOIN `tabMemora Review Item` ri ON pl.item_id = ri.item_id
			WHERE pl.player_id IN ({placeholders})
			ORDER BY pl.player_id, ri.track
			""",
			tuple(batch),
			as_dict=True,
		)

		# 4. Group by (player_id, track_id) and build summaries
		summaries = {}
		for row in rows:
			key = (row["player_id"], row["track_id"])
			if key not in summaries:
				summaries[key] = {
					"subject_id": row["subject_id"],
					"history": {},
					"total_correct": 0,
					"last_session_at": None,
				}

			s = summaries[key]
			lr = "C" if row["last_result"] == "Correct" else "I"
			ls_dt = row["last_seen_at"]
			ls_str = ls_dt.isoformat() if ls_dt else None

			s["history"][row["item_id"]] = {
				"lr": lr,
				"ac": row["attempt_count"],
				"cc": row["correct_count"],
				"ls": ls_str,
			}
			s["total_correct"] += row["correct_count"]

			if ls_dt and (s["last_session_at"] is None or ls_dt > s["last_session_at"]):
				s["last_session_at"] = ls_dt

		# 5. UPSERT into Player Practice Summary
		for (player_id, track_id), s in summaries.items():
			frappe.db.sql(
				"""
				INSERT INTO `tabPlayer Practice Summary`
					(player_id, track_id, subject_id, question_history,
					 total_seen, total_correct, last_session_at)
				VALUES (%s, %s, %s, %s, %s, %s, %s)
				ON DUPLICATE KEY UPDATE
					subject_id = VALUES(subject_id),
					question_history = VALUES(question_history),
					total_seen = VALUES(total_seen),
					total_correct = VALUES(total_correct),
					last_session_at = VALUES(last_session_at)
				""",
				(
					player_id,
					track_id,
					s["subject_id"],
					json.dumps(s["history"]),
					len(s["history"]),
					s["total_correct"],
					s["last_session_at"],
				),
			)
			rows_upserted += 1

		frappe.db.commit()

		processed = min(offset + batch_size, total_players)
		pct = processed * 100 // total_players
		logger.info(
			f"Backfill progress: {processed}/{total_players} players ({pct}%), "
			f"{rows_upserted} rows upserted so far"
		)

	logger.info(
		f"Backfill complete: {total_players} players, {rows_upserted} summary rows upserted"
	)
	return {"total_players": total_players, "rows_upserted": rows_upserted}


# ---------------------------------------------------------------------------
# T029 — Admin utilities for emergency operations
# ---------------------------------------------------------------------------


@frappe.whitelist()
def force_expire_all_practice_sessions():
	"""Emergency: delete ALL active practice sessions from Redis.

	Uses SCAN with pattern ``memora:practice:session:*`` and deletes
	in batches of 100 to avoid blocking Redis.

	Returns:
		dict with count of expired sessions.
	"""
	from memora_admin.utils.redis_connection import get_memora_redis

	r = get_memora_redis()
	total_expired = 0
	cursor = 0

	while True:
		cursor, keys = r.scan(
			cursor,
			match=PRACTICE_SESSION_SCAN_PATTERN,
			count=100,
		)

		if keys:
			r.delete(*keys)
			total_expired += len(keys)

		if cursor == 0:
			break

	logger.info("practice_sessions_force_expired: count=%d", total_expired)
	return {"expired": total_expired}


@frappe.whitelist()
def reprocess_dead_letters():
	"""Re-enqueue dead-lettered practice write messages for reprocessing.

	Reads all messages from the dead-letter stream, re-adds each to the
	main write queue (stripping error/delivery_count metadata), and
	removes the dead-letter entry.

	Returns:
		dict with count of reprocessed messages.
	"""
	from memora_admin.utils.redis_connection import get_memora_redis

	r = get_memora_redis()

	messages = r.xrange(PRACTICE_WRITE_QUEUE_DEAD_KEY)
	if not messages:
		return {"reprocessed": 0}

	reprocessed = 0
	for msg_id, fields in messages:
		# Strip dead-letter metadata before re-enqueueing
		clean_fields = {
			k: v
			for k, v in fields.items()
			if k not in ("original_id", "error", "delivery_count")
		}
		r.xadd(PRACTICE_WRITE_QUEUE_KEY, clean_fields)
		r.xdel(PRACTICE_WRITE_QUEUE_DEAD_KEY, msg_id)
		reprocessed += 1

	logger.info("practice_dead_letters_reprocessed: count=%d", reprocessed)
	return {"reprocessed": reprocessed}
