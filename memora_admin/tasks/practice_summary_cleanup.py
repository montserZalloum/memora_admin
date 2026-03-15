"""Practice Summary cleanup — DEPRECATED.

Practice Summary cleanup is now handled by the archive purge pipeline.
See archive_executor/purge.py (_purge_player_scope) cleanup_tables for
the replacement.

This module is kept as a stub so any in-flight scheduler references do not crash.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TASK_NAME = "practice_summary_cleanup"


def cleanup_practice_summaries(
	triggered_by: str = "Scheduler",
	batch_size: int = 10_000,
):
	"""DEPRECATED: Practice Summary cleanup is now handled by the archive purge pipeline.

	This function is a no-op. Practice Summary rows are deleted as cleanup_tables
	during the player-scoped archive purge phase. See archive_executor/purge.py
	(_purge_player_scope) for the replacement.

	Kept as a stub so any in-flight scheduler references do not crash.
	"""
	logger.warning(
		f"{TASK_NAME}: DEPRECATED — cleanup is now handled by archive purge pipeline. "
		"This scheduled task should be removed from hooks.py."
	)
	return
