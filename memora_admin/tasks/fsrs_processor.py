"""
FSRS spaced repetition processor.

Processes recent interactions to compute and persist FSRS memory state at item level.
Each sub-element (question, matching pair, word, etc.) gets its own Memory State record.
Excludes skippable stages (from Memora Lesson Stage Settings).

Scheduled via hooks.py: every 1 minute.

FSRS Rating Mapping (from research):
- fail_count == 0 -> Rating.Good (3)
- fail_count == 1 -> Rating.Hard (2)
- fail_count >= 2 -> Rating.Again (1)

Memory State storage:
- PK: BIGINT autoincrement via frappe.db.get_next_sequence_val
- item_id: BINARY(16) via UUID_TO_BIN polyfill
- season_seq: INT for RANGE partition routing
- Lookup: (player, item_id, season_seq) unique index

IMPORTANT -- RAW SQL ONLY:
  Memora Memory State is a RANGE-partitioned table designed for 10+ billion rows.
  Frappe ORM (get_doc, get_all, get_list, db.get_value, etc.) is FORBIDDEN because:
  1. Frappe ORM cannot handle BINARY(16) columns (item_id).
  2. Frappe ORM does not include season_seq in WHERE, breaking partition pruning.
  3. ORM-generated queries may cause full table scans on a 10B-row table.
  All queries MUST use frappe.db.sql() with:
  - season_seq in every WHERE clause (partition pruning)
  - UUID_TO_BIN() for item_id writes, BIN_TO_UUID() for reads
  See setup.py for full schema reference and safety rules.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone

import frappe
import redis

logger = logging.getLogger(__name__)

FSRS_PROCESSED_KEY = "memora:fsrs:last_processed"


def get_redis():
	"""Get Redis connection using Frappe site config."""
	return redis.from_url(frappe.conf.redis_cache)


def _get_skippable_stage_types() -> set[str]:
	"""Get set of stage type names (from Memora Lesson Stage Settings) where is_skippable=1."""
	stages = frappe.get_all(
		"Memora Lesson Stage Settings",
		filters={"is_skippable": 1},
		fields=["stage_title"],  # stage_title is the name/primary key of Settings
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


def _get_active_season() -> tuple[str, int] | None:
	"""Get the currently active season ID and seq number.

	Returns (season_name, season_seq) or None if no active season.
	"""
	today = date.today()
	result = frappe.db.get_value(
		"Memora Season",
		{
			"is_published": 1,
			"start_date": ["<=", today],
			"end_date": [">=", today],
		},
		["name", "season_seq"],
		as_dict=True,
	)
	if result and result.season_seq is not None:
		return (result.name, result.season_seq)
	return None


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


def _lookup_memory_state(player: str, item_id: str, season_seq: int) -> dict | None:
	"""Look up existing Memory State by (player, item_id, season_seq) using raw SQL.

	Uses UUID_TO_BIN polyfill because item_id is stored as BINARY(16).
	Returns dict with name, stability, difficulty, next_review or None.
	"""
	rows = frappe.db.sql(
		"""
		SELECT name, stability, difficulty, next_review
		FROM `tabMemora Memory State`
		WHERE player = %(player)s
			AND item_id = UUID_TO_BIN(%(item_id)s)
			AND season_seq = %(season_seq)s
		LIMIT 1
		""",
		{
			"player": player,
			"item_id": item_id,
			"season_seq": season_seq,
		},
		as_dict=True,
	)
	return rows[0] if rows else None


def _update_memory_state(
	name: int, season_seq: int, stability: float, difficulty: float, next_review: date
) -> None:
	"""Update existing Memory State via raw SQL for partition-aware queries."""
	frappe.db.sql(
		"""
		UPDATE `tabMemora Memory State`
		SET stability = %(stability)s,
			difficulty = %(difficulty)s,
			next_review = %(next_review)s,
			modified = NOW(6)
		WHERE name = %(name)s
			AND season_seq = %(season_seq)s
		""",
		{
			"name": name,
			"season_seq": season_seq,
			"stability": stability,
			"difficulty": difficulty,
			"next_review": next_review,
		},
	)


def _insert_memory_state(
	season_seq: int,
	subject: str,
	player: str,
	item_id: str,
	stage_id: str,
	lesson: str,
	stability: float,
	difficulty: float,
	next_review: date,
) -> int:
	"""Insert new Memory State via raw SQL with BIGINT sequence PK and UUID_TO_BIN.

	Returns the new record name (BIGINT).
	"""
	next_name = frappe.db.get_next_sequence_val("Memora Memory State")
	frappe.db.sql(
		"""
		INSERT INTO `tabMemora Memory State`
		(name, season_seq, subject, player, item_id, stage_id, lesson,
		 stability, difficulty, next_review,
		 creation, modified, owner, modified_by, docstatus, idx)
		VALUES
		(%(name)s, %(season_seq)s, %(subject)s, %(player)s,
		 UUID_TO_BIN(%(item_id)s), %(stage_id)s, %(lesson)s,
		 %(stability)s, %(difficulty)s, %(next_review)s,
		 NOW(6), NOW(6), 'Administrator', 'Administrator', 0, 0)
		""",
		{
			"name": next_name,
			"season_seq": season_seq,
			"subject": subject,
			"player": player,
			"item_id": item_id,
			"stage_id": stage_id,
			"lesson": lesson,
			"stability": stability,
			"difficulty": difficulty,
			"next_review": next_review,
		},
	)
	return next_name


def process_fsrs_reviews():
	"""Process recent interactions for FSRS spaced repetition at item level.

	Flow:
	1. Get recent interactions from Memora Interaction Log (last 10 minutes)
	2. Filter out skippable stages and non-reviewable lessons
	3. For each interaction:
	   a. Determine item_id (from interaction or deterministic UUID from stage_id for legacy)
	   b. Look up or create FSRS Card from Memora Memory State via raw SQL
	   c. Apply review with mapped rating
	   d. Persist via raw SQL INSERT/UPDATE (BINARY item_id + BIGINT PK + season_seq partition)
	   e. Cache state in Redis for fast access

	Scheduled: every 1 minute via hooks.py
	"""
	r = get_redis()

	# Get active season (needed for Memory State records)
	season_info = _get_active_season()
	if not season_info:
		logger.warning("No active season found, skipping FSRS processing")
		return

	active_season, active_season_seq = season_info
	logger.info(f"FSRS: Active season = {active_season} (seq={active_season_seq})")

	# Get skippable stage types to exclude
	skippable_types = _get_skippable_stage_types()

	# Get FSRS scheduler
	scheduler = _get_fsrs_scheduler()

	# Query recent interactions (last 10 minutes to account for scheduler delays and processing time)
	from frappe.utils import now_datetime

	cutoff = now_datetime() - timedelta(minutes=10)
	interactions = frappe.get_all(
		"Memora Interaction Log",
		filters={
			"event_type": "Completed",
			"creation": [">=", cutoff],
		},
		fields=["player", "lesson", "stage_id", "item_id", "errors_count", "time_spent", "creation"],
		order_by="creation asc",
		limit_page_length=500,
	)

	logger.info(f"FSRS: Found {len(interactions)} recent interactions (cutoff: {cutoff})")
	if not interactions:
		logger.debug("No recent interactions for FSRS processing")
		return

	processed = 0
	skipped = 0
	errors_list = []

	from fsrs import Card

	for interaction in interactions:
		stage_id = interaction.stage_id
		player = interaction.player
		lesson = interaction.lesson

		# Look up stage_type from the lesson's child table (Memora Lesson Stage)
		# stage_id is the child table row name, not stage_title
		stage_row = frappe.db.get_value(
			"Memora Lesson Stage",
			{"name": stage_id, "parent": lesson},
			["stage_type", "is_skippable"],
			as_dict=True,
		)

		if stage_row:
			# Per-stage override takes priority over global setting
			if stage_row.is_skippable:
				skipped += 1
				continue
			# Fall back to global setting from stage type
			if stage_row.stage_type in skippable_types:
				skipped += 1
				continue

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

		# Check if lesson is reviewable before creating Memory State
		is_reviewable = frappe.db.get_value("Memora Lesson", lesson, "is_reviewable")
		if not is_reviewable:
			skipped += 1
			continue

		# Determine item_id: use from interaction if present, else deterministic UUID from stage_id
		raw_item_id = interaction.item_id
		if raw_item_id and str(raw_item_id).strip():
			item_id = str(raw_item_id).strip()
		else:
			# Legacy interaction without item_id -- generate deterministic UUID from stage_id
			item_id = str(uuid.uuid5(uuid.NAMESPACE_OID, stage_id))

		try:
			# Check for idempotency -- skip if already processed
			idem_key = f"memora:fsrs:processed:{player}:{item_id}:{interaction.creation}"
			if r.exists(idem_key):
				skipped += 1
				continue

			# Look up existing Memory State via raw SQL (BINARY item_id requires UUID_TO_BIN)
			existing = _lookup_memory_state(player, item_id, active_season_seq)

			# Map fail_count to FSRS rating
			rating = _map_rating(interaction.errors_count or 0)

			# Use UTC-aware datetime for FSRS calculations
			now = datetime.now(timezone.utc)

			if existing and existing.stability and existing.stability > 0:
				# Existing card -- reconstruct and review
				card = Card()
				card.stability = existing.stability
				card.difficulty = existing.difficulty
				if existing.next_review:
					# next_review is a date, convert to datetime for FSRS
					if isinstance(existing.next_review, date) and not isinstance(
						existing.next_review, datetime
					):
						card.due = datetime.combine(existing.next_review, time.min, tzinfo=timezone.utc)
					else:
						card.due = existing.next_review
				else:
					card.due = now

				card, _review_log = scheduler.review_card(card, rating, now)
			else:
				# New card -- first review
				card = Card()
				card, _review_log = scheduler.review_card(card, rating, now)

			# Clamp next_review to date-only (midnight), minimum tomorrow
			next_date = card.due.date()
			tomorrow = date.today() + timedelta(days=1)
			if next_date < tomorrow:
				next_date = tomorrow
			next_review_date = next_date

			# Persist to Memora Memory State via raw SQL
			if existing:
				_update_memory_state(
					name=existing.name,
					season_seq=active_season_seq,
					stability=card.stability,
					difficulty=card.difficulty,
					next_review=next_review_date,
				)
			else:
				_insert_memory_state(
					season_seq=active_season_seq,
					subject=subject,
					player=player,
					item_id=item_id,
					stage_id=stage_id,
					lesson=lesson,
					stability=card.stability,
					difficulty=card.difficulty,
					next_review=next_review_date,
				)

			# Cache in Redis for fast access (keyed by item_id, not stage_id)
			redis_key = f"memora:fsrs:{player}:{item_id}"
			fsrs_data = json.dumps(
				{
					"stability": card.stability,
					"difficulty": card.difficulty,
					"next_review": next_review_date.isoformat(),
					"lesson": lesson,
					"stage_id": stage_id,
				}
			)
			r.setex(redis_key, 86400, fsrs_data)  # 24hr TTL

			# Mark as processed (idempotency key, 5 min TTL)
			r.setex(idem_key, 300, "1")

			processed += 1

		except Exception as e:
			errors_list.append(f"{player}/{item_id}: {e!s}")
			logger.error(f"FSRS processing failed for {player}/{item_id}: {e}")

	# Commit all DB changes
	if processed > 0:
		frappe.db.commit()

	logger.info(f"FSRS processing: {processed} processed, {skipped} skipped, {len(errors_list)} errors")
