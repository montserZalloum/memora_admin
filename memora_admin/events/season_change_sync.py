"""Season change sync handler for progress reset and session invalidation.

When admin changes a player's season (without changing their plan), wipe
structure progress and invalidate caches so the player looks "new" in the
new season.  When the plan also changes, plan_change_sync already handles
cleanup — this handler yields to avoid double-deleting.
"""

import json
import time

import frappe

from fastapi_app.core.redis_keys import (
	cache_invalidation_channel,
	player_ch_progress_pattern,
	player_progress_pattern,
	player_stats_pattern,
	session_key,
)
from memora_admin.api.utils import invalidate_player_season_seq
from memora_admin.utils.redis_connection import get_memora_redis


def on_player_profile_season_changed(doc, method):
	"""Reset lesson/challenge progress and invalidate session when season changes.

	Guards:
	- Skip if season did not actually change.
	- Skip if plan also changed (plan_change_sync handles that case).
	"""
	if not doc.has_value_changed("season"):
		return

	# Plan change flow already does a full cleanup — avoid double work
	if doc.has_value_changed("plan"):
		return

	player_id = doc.name

	# 1. Delete structure progress records for this player
	frappe.db.delete("Memora Structure Progress", {"player": player_id})

	# 2. Invalidate cached season_seq (Frappe-side cache)
	invalidate_player_season_seq(player_id)

	r = get_memora_redis()

	# 3. SCAN + DEL Redis caches: progress bitmaps, stats, challenge progress
	scan_patterns = [
		player_progress_pattern(player_id),
		player_stats_pattern(player_id),
		player_ch_progress_pattern(player_id),
	]
	for pattern in scan_patterns:
		cursor = 0
		while True:
			cursor, keys = r.scan(cursor, match=pattern, count=200)
			if keys:
				r.delete(*keys)
			if cursor == 0:
				break

	# 4. Invalidate session (force re-login for fresh token with new season)
	r.delete(session_key(player_id))

	# 5. Pubsub: notify FastAPI sidecar
	invalidation_msg = json.dumps(
		{
			"type": "session",
			"player_id": player_id,
			"reason": "season_changed",
			"timestamp": time.time(),
		}
	)
	r.publish(cache_invalidation_channel(), invalidation_msg)

	frappe.logger().info(
		f"Progress reset + session invalidated for {player_id} due to season change"
	)
