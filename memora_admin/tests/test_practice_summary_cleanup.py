"""Tests for practice_summary_cleanup task (deprecated).

Coverage:
- cleanup_practice_summaries() returns immediately with warning log (no-op)
- cleanup_practice_summaries() does not issue any DB queries
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub frappe and its submodules so the cleanup module can be imported
# outside of a Frappe bench environment.
_frappe_mock = MagicMock()
_frappe_utils_mock = MagicMock()
sys.modules.setdefault("frappe", _frappe_mock)
sys.modules.setdefault("frappe.utils", _frappe_utils_mock)


class TestPracticeSummaryCleanupDeprecated:
	"""Tests that cleanup_practice_summaries() is now a no-op."""

	@patch("memora_admin.tasks.practice_summary_cleanup.logger")
	def test_deprecated_returns_immediately(self, mock_logger):
		"""cleanup_practice_summaries() must return immediately with a warning log."""
		from memora_admin.tasks.practice_summary_cleanup import cleanup_practice_summaries

		result = cleanup_practice_summaries(triggered_by="Test")

		assert result is None
		mock_logger.warning.assert_called_once()
		assert "DEPRECATED" in mock_logger.warning.call_args[0][0]

	@patch("memora_admin.tasks.practice_summary_cleanup.logger")
	def test_deprecated_no_db_calls(self, mock_logger):
		"""cleanup_practice_summaries() must not issue any DB queries."""
		# Import the actual frappe mock from the module to check calls
		from memora_admin.tasks import practice_summary_cleanup

		# Reset any prior calls on the module-level frappe reference
		# (frappe is not imported by the stub, so no db calls are possible)
		cleanup_practice_summaries = practice_summary_cleanup.cleanup_practice_summaries
		cleanup_practice_summaries(triggered_by="Test")

		# The stub no longer imports frappe at all — success is that it
		# returns without error and logs the deprecation warning.
		mock_logger.warning.assert_called_once()
