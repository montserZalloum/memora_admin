"""
One-time backfill script to process historical interactions for FSRS Memory State.

Usage:
    bench --site x.conanacademy.com console
    >>> from memora_admin.tasks.fsrs_backfill import backfill_memory_states
    >>> backfill_memory_states(hours=24)  # Process last 24 hours
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import frappe
import redis

from memora_admin.tasks.fsrs_processor import (
	_get_active_season,
	_get_fsrs_scheduler,
	_get_skippable_stages,
	_map_rating,
	get_redis,
)

logger = logging.getLogger(__name__)


def backfill_memory_states(hours: int = 24, player_filter: str | None = None):
	"""Backfill Memory State records from historical interactions.

	Args:
		hours: How many hours back to process (default: 24)
		player_filter: Optional player email to filter by (default: all players)
	"""
	r = get_redis()

	# Get active season
	active_season = _get_active_season()
	print(f"[BACKFILL] Active season = {active_season}")
	if not active_season:
		print("[BACKFILL] No active season found!")
		return

	# Get skippable stages
	skippable = _get_skippable_stages()
	print(f"[BACKFILL] Found {len(skippable)} skippable stages")

	# Get FSRS scheduler
	scheduler = _get_fsrs_scheduler()

	# Query historical interactions
	from frappe.utils import now_datetime

	cutoff = now_datetime() - timedelta(hours=hours)
	print(f"[BACKFILL] Cutoff time: {cutoff} ({hours} hours ago)")

	filters = {
		"event_type": "Completed",
		"creation": [">=", cutoff],
	}
	if player_filter:
		filters["player"] = player_filter

	interactions = frappe.get_all(
		"Memora Interaction Log",
		filters=filters,
		fields=["player", "lesson", "stage_id", "errors_count", "time_spent", "creation"],
		order_by="creation asc",
		limit_page_length=1000,
	)

	print(f"[BACKFILL] Found {len(interactions)} interactions to process")
	if not interactions:
		print("[BACKFILL] No interactions found - exiting")
		return

	processed = 0
	skipped = 0
	errors_list = []

	from fsrs import Card

	for i, interaction in enumerate(interactions, 1):
		print(
			f"[BACKFILL] Processing {i}/{len(interactions)}: {interaction.player} - {interaction.stage_id}"
		)
		stage_id = interaction.stage_id

		# Skip if stage is skippable
		if stage_id in skippable:
			print(f"[BACKFILL]   Stage {stage_id} is skippable, skipping")
			skipped += 1
			continue

		player = interaction.player
		lesson = interaction.lesson

		# Resolve subject from lesson
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
			print(f"[BACKFILL]   Could not determine subject for lesson {lesson}, skipping")
			skipped += 1
			continue

		try:
			# Check if Memory State already exists
			memory_state_name = f"{active_season}-{subject}-{player}-{stage_id}"
			if frappe.db.exists("Memora Memory State", memory_state_name):
				print(f"[BACKFILL]   Memory State {memory_state_name} already exists, skipping")
				skipped += 1
				continue

			# Load existing state or create new Card
			existing = frappe.db.get_value(
				"Memora Memory State",
				memory_state_name,
				["stability", "difficulty", "next_review"],
				as_dict=True,
			)

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
					card.due = existing.next_review
				else:
					card.due = now

				card, _review_log = scheduler.review_card(card, rating, now)
			else:
				# New card -- first review
				card = Card()
				card, _review_log = scheduler.review_card(card, rating, now)

			# Convert card.due from UTC-aware to naive datetime for Frappe/MariaDB
			next_review_naive = card.due.replace(tzinfo=None) if card.due else None

			# Persist to Memora Memory State DocType
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
					"next_review": next_review_naive,
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

			processed += 1
			print(f"[BACKFILL]   ✓ Created Memory State {memory_state_name}")

		except Exception as e:
			errors_list.append(f"{player}/{stage_id}: {e!s}")
			print(f"[BACKFILL]   ✗ Error: {e}")
			logger.error(f"FSRS backfill failed for {player}/{stage_id}: {e}")

	# Commit all DB changes
	if processed > 0:
		frappe.db.commit()
		print(f"\n[BACKFILL] ✓ Committed {processed} new Memory State records")

	print(
		f"\n[BACKFILL] Summary: {processed} processed, {skipped} skipped, {len(errors_list)} errors"
	)
	if errors_list:
		print(f"[BACKFILL] Errors:")
		for err in errors_list:
			print(f"  - {err}")
