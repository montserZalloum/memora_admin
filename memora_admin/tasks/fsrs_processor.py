"""
FSRS spaced repetition processor.

Processes recent stage interactions to compute and persist FSRS memory state.
Excludes skippable stages (from Memora Lesson Stage Settings).

Scheduled via hooks.py: every 1 minute.

FSRS Rating Mapping (from research):
- fail_count == 0 -> Rating.Good (3)
- fail_count == 1 -> Rating.Hard (2)
- fail_count >= 2 -> Rating.Again (1)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import frappe
import redis

logger = logging.getLogger(__name__)

FSRS_PROCESSED_KEY = "memora:fsrs:last_processed"


def get_redis():
	"""Get Redis connection using Frappe site config."""
	return redis.from_url(frappe.conf.redis_cache)


def _get_skippable_stages() -> set[str]:
	"""Get set of stage_title values where is_skippable=1.

	Cached for the duration of this task run.
	"""
	stages = frappe.get_all(
		"Memora Lesson Stage Settings",
		filters={"is_skippable": 1},
		fields=["stage_title"],
	)
	return {s.stage_title for s in stages}


def _get_fsrs_scheduler():
	"""Create FSRS scheduler with weights from Memora Settings.

	Returns:
		fsrs.Scheduler instance configured with admin weights (if set)
	"""
	from fsrs import Scheduler

	settings = frappe.get_single("Memora Settings")
	weights_str = settings.fsrs_weights

	if weights_str and weights_str.strip():
		try:
			weights = json.loads(weights_str)
			return Scheduler(parameters=weights)
		except (json.JSONDecodeError, ValueError, TypeError) as e:
			logger.warning(f"Invalid FSRS weights, using defaults: {e}")

	return Scheduler()


def _get_active_season() -> str | None:
	"""Get the currently active season ID.

	Returns the first season with status='Active'.
	"""
	season = frappe.db.get_value(
		"Memora Season",
		{"status": "Active"},
		"name",
	)
	return season


def _map_rating(fail_count: int):
	"""Map fail_count to FSRS Rating.

	Per research:
	- 0 fails = Good (3)
	- 1 fail = Hard (2)
	- 2+ fails = Again (1)
	"""
	from fsrs import Rating

	if fail_count == 0:
		return Rating.Good
	elif fail_count == 1:
		return Rating.Hard
	else:
		return Rating.Again


def process_fsrs_reviews():
	"""Process recent stage interactions for FSRS spaced repetition.

	Flow:
	1. Get recent interactions from Memora Interaction Log (last 2 minutes)
	2. Filter out skippable stages
	3. For each non-skippable stage interaction:
	   a. Load or create FSRS Card from Memora Memory State
	   b. Apply review with mapped rating
	   c. Save updated state to Memora Memory State DocType
	   d. Cache state in Redis for fast access

	Scheduled: every 1 minute via hooks.py
	"""
	r = get_redis()

	# Get active season (needed for Memory State records)
	active_season = _get_active_season()
	if not active_season:
		logger.debug("No active season, skipping FSRS processing")
		return

	# Get skippable stages to exclude
	skippable = _get_skippable_stages()

	# Get FSRS scheduler
	scheduler = _get_fsrs_scheduler()

	# Query recent interactions (last 2 minutes for overlap safety)
	cutoff = datetime.now() - timedelta(minutes=2)
	interactions = frappe.get_all(
		"Memora Interaction Log",
		filters={
			"event_type": "Completed",
			"creation": [">=", cutoff],
		},
		fields=["player", "lesson", "stage_id", "errors_count", "time_spent", "creation"],
		order_by="creation asc",
		limit_page_length=500,
	)

	if not interactions:
		logger.debug("No recent interactions for FSRS processing")
		return

	processed = 0
	skipped = 0
	errors_list = []

	from fsrs import Card

	for interaction in interactions:
		stage_id = interaction.stage_id

		# Skip if stage is skippable
		if stage_id in skippable:
			skipped += 1
			continue

		player = interaction.player
		lesson = interaction.lesson

		# Resolve subject from lesson (direct field on Memora Lesson)
		subject = frappe.db.get_value("Memora Lesson", lesson, "subject")

		if not subject:
			# Safety net: resolve via hierarchy chain
			topic = frappe.db.get_value("Memora Lesson", lesson, "topic")
			if topic:
				unit = frappe.db.get_value("Memora Topic", topic, "unit")
				if unit:
					track = frappe.db.get_value("Memora Unit", unit, "track")
					if track:
						subject = frappe.db.get_value("Memora Track", track, "subject")

		if not subject:
			logger.warning(f"Could not determine subject for lesson {lesson}")
			continue

		try:
			# Check for idempotency -- skip if already processed
			idem_key = f"memora:fsrs:processed:{player}:{stage_id}:{interaction.creation}"
			if r.exists(idem_key):
				skipped += 1
				continue

			# Load existing Memory State or create new Card
			memory_state_name = f"{active_season}-{subject}-{player}-{stage_id}"
			existing = frappe.db.get_value(
				"Memora Memory State",
				memory_state_name,
				["stability", "difficulty", "next_review"],
				as_dict=True,
			)

			# Map fail_count to FSRS rating
			rating = _map_rating(interaction.errors_count or 0)

			now = datetime.now(timezone.utc)

			if existing and existing.stability and existing.stability > 0:
				# Existing card -- reconstruct and review
				card = Card()
				card.stability = existing.stability
				card.difficulty = existing.difficulty
				if existing.next_review:
					card.due = existing.next_review
				else:
					card.due = now

				card, _review_log = scheduler.review_card(card, rating, now)
			else:
				# New card -- first review
				card = Card()
				card, _review_log = scheduler.review_card(card, rating, now)

			# Persist to Memora Memory State DocType
			if frappe.db.exists("Memora Memory State", memory_state_name):
				frappe.db.set_value(
					"Memora Memory State",
					memory_state_name,
					{
						"stability": card.stability,
						"difficulty": card.difficulty,
						"next_review": card.due,
					},
					update_modified=True,
				)
			else:
				frappe.get_doc(
					{
						"doctype": "Memora Memory State",
						"name": memory_state_name,
						"season": active_season,
						"subject": subject,
						"player": player,
						"stage_id": stage_id,
						"lesson": lesson,
						"stability": card.stability,
						"difficulty": card.difficulty,
						"next_review": card.due,
					}
				).insert(ignore_permissions=True)

			# Cache in Redis for fast access
			redis_key = f"memora:fsrs:{player}:{stage_id}"
			fsrs_data = json.dumps(
				{
					"stability": card.stability,
					"difficulty": card.difficulty,
					"next_review": card.due.isoformat() if card.due else None,
					"lesson": lesson,
				}
			)
			r.setex(redis_key, 86400, fsrs_data)  # 24hr TTL

			# Mark as processed (idempotency key, 5 min TTL)
			r.setex(idem_key, 300, "1")

			processed += 1

		except Exception as e:
			errors_list.append(f"{player}/{stage_id}: {e!s}")
			logger.error(f"FSRS processing failed for {player}/{stage_id}: {e}")

	# Commit all DB changes
	if processed > 0:
		frappe.db.commit()

	logger.info(f"FSRS processing: {processed} processed, {skipped} skipped, {len(errors_list)} errors")
