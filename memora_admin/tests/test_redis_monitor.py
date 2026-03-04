"""
Tests for redis_monitor.py monitoring task.

Tests:
- INFO log with all metrics on every run
- WARNING log when memory exceeds 80%
- WARNING log when dirty set count exceeds 1000
- CRITICAL log when buffer exceeds 10000
- Handles Redis connection errors gracefully

Uses unittest.mock.patch for Redis client and frappe.logger().
"""

from unittest.mock import MagicMock, call, patch

import frappe
from frappe.tests.utils import FrappeTestCase


def _make_mock_redis(
	used_memory=50 * 1024 * 1024,
	maxmemory=128 * 1024 * 1024,
	connected_clients=5,
	aof_enabled=1,
	uptime_in_seconds=86400,
	buffer_len=100,
	dirty_wallets=10,
	dirty_progress=20,
	total_keys=5000,
):
	"""Build a mock Redis client with configurable INFO responses."""
	mock_r = MagicMock()

	def info_side_effect(section=None):
		if section == "memory":
			return {
				"used_memory": used_memory,
				"maxmemory": maxmemory,
			}
		elif section == "clients":
			return {"connected_clients": connected_clients}
		elif section == "persistence":
			return {"aof_enabled": aof_enabled}
		elif section == "server":
			return {"uptime_in_seconds": uptime_in_seconds}
		return {}

	mock_r.info = MagicMock(side_effect=info_side_effect)
	mock_r.llen = MagicMock(return_value=buffer_len)
	mock_r.scard = MagicMock(
		side_effect=lambda key: {
			"memora:dirty:wallets": dirty_wallets,
			"memora:dirty:progress": dirty_progress,
		}.get(key, 0)
	)
	mock_r.dbsize = MagicMock(return_value=total_keys)

	return mock_r


class TestRedisMonitor(FrappeTestCase):
	"""Tests for monitor_redis_health() task."""

	@patch("memora_admin.tasks.redis_monitor.get_memora_redis")
	def test_logs_info_on_every_run(self, mock_get_redis):
		"""monitor_redis_health() always logs INFO with all metrics."""
		mock_r = _make_mock_redis()
		mock_get_redis.return_value = mock_r

		mock_logger = MagicMock()
		with patch("memora_admin.tasks.redis_monitor.logger", mock_logger):
			from memora_admin.tasks.redis_monitor import monitor_redis_health

			monitor_redis_health()

		# Verify INFO was called with metrics in the message string
		mock_logger.info.assert_called_once()
		info_call_args = mock_logger.info.call_args
		# First positional arg is the format string containing metric names
		assert "redis_monitor" in info_call_args[0][0]
		assert "used_mb" in info_call_args[0][0]
		assert "buffer" in info_call_args[0][0]

	@patch("memora_admin.tasks.redis_monitor.get_memora_redis")
	def test_warning_on_memory_above_80_percent(self, mock_get_redis):
		"""WARNING logged when memory usage exceeds 80%."""
		# 110MB of 128MB = 85.9%
		mock_r = _make_mock_redis(used_memory=110 * 1024 * 1024, maxmemory=128 * 1024 * 1024)
		mock_get_redis.return_value = mock_r

		mock_logger = MagicMock()
		with patch("memora_admin.tasks.redis_monitor.logger", mock_logger):
			from memora_admin.tasks.redis_monitor import monitor_redis_health

			monitor_redis_health()

		# Should have a WARNING call about memory
		mock_logger.warning.assert_called()
		warning_fmts = [c[0][0] for c in mock_logger.warning.call_args_list]
		assert any("redis_memory_high" in f for f in warning_fmts)

	@patch("memora_admin.tasks.redis_monitor.get_memora_redis")
	def test_warning_on_dirty_wallets_above_1000(self, mock_get_redis):
		"""WARNING logged when dirty wallet count exceeds 1000."""
		mock_r = _make_mock_redis(dirty_wallets=1500)
		mock_get_redis.return_value = mock_r

		mock_logger = MagicMock()
		with patch("memora_admin.tasks.redis_monitor.logger", mock_logger):
			from memora_admin.tasks.redis_monitor import monitor_redis_health

			monitor_redis_health()

		mock_logger.warning.assert_called()
		warning_fmts = [c[0][0] for c in mock_logger.warning.call_args_list]
		assert any("redis_sync_falling_behind" in f for f in warning_fmts)

	@patch("memora_admin.tasks.redis_monitor.get_memora_redis")
	def test_warning_on_dirty_progress_above_1000(self, mock_get_redis):
		"""WARNING logged when dirty progress count exceeds 1000."""
		mock_r = _make_mock_redis(dirty_progress=2000)
		mock_get_redis.return_value = mock_r

		mock_logger = MagicMock()
		with patch("memora_admin.tasks.redis_monitor.logger", mock_logger):
			from memora_admin.tasks.redis_monitor import monitor_redis_health

			monitor_redis_health()

		mock_logger.warning.assert_called()
		warning_fmts = [c[0][0] for c in mock_logger.warning.call_args_list]
		assert any("redis_sync_falling_behind" in f for f in warning_fmts)

	@patch("memora_admin.tasks.redis_monitor.get_memora_redis")
	def test_critical_on_buffer_above_10000(self, mock_get_redis):
		"""CRITICAL logged when interaction buffer exceeds 10000."""
		mock_r = _make_mock_redis(buffer_len=15000)
		mock_get_redis.return_value = mock_r

		mock_logger = MagicMock()
		with patch("memora_admin.tasks.redis_monitor.logger", mock_logger):
			from memora_admin.tasks.redis_monitor import monitor_redis_health

			monitor_redis_health()

		mock_logger.critical.assert_called()
		critical_fmts = [c[0][0] for c in mock_logger.critical.call_args_list]
		assert any("redis_buffer_backlog" in f for f in critical_fmts)

	@patch("memora_admin.tasks.redis_monitor.get_memora_redis")
	def test_no_warnings_when_healthy(self, mock_get_redis):
		"""No WARNING or CRITICAL when all metrics are within thresholds."""
		mock_r = _make_mock_redis()
		mock_get_redis.return_value = mock_r

		mock_logger = MagicMock()
		with patch("memora_admin.tasks.redis_monitor.logger", mock_logger):
			from memora_admin.tasks.redis_monitor import monitor_redis_health

			monitor_redis_health()

		mock_logger.warning.assert_not_called()
		mock_logger.critical.assert_not_called()

	@patch("memora_admin.tasks.redis_monitor.get_memora_redis")
	def test_handles_redis_connection_error(self, mock_get_redis):
		"""monitor_redis_health() handles Redis connection errors gracefully."""
		from redis.exceptions import ConnectionError

		mock_get_redis.side_effect = ConnectionError("Connection refused")

		mock_logger = MagicMock()
		with patch("memora_admin.tasks.redis_monitor.logger", mock_logger):
			from memora_admin.tasks.redis_monitor import monitor_redis_health

			# Should not raise
			monitor_redis_health()

		mock_logger.exception.assert_called()
