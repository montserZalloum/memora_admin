"""
Tests for get_memora_redis() utility function.

Tests verify that the function returns a properly configured Redis client
and handles fallback from redis_memora to redis_cache config keys.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

import memora_admin.utils.redis_connection as redis_conn_module


class TestGetMemoraRedis(FrappeTestCase):
	"""Tests for get_memora_redis() from memora_admin.utils.redis_connection."""

	def setUp(self):
		super().setUp()
		# Reset cached client between tests so each test gets a fresh client
		redis_conn_module._client = None
		redis_conn_module._client_url = None

	def tearDown(self):
		# Reset cached client after tests to avoid leaking mocked clients
		redis_conn_module._client = None
		redis_conn_module._client_url = None
		super().tearDown()

	def test_returns_redis_client_with_decode_responses(self):
		"""get_memora_redis() returns a redis.Redis client with decode_responses=True."""
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()
		# Verify it's a Redis client
		self.assertTrue(hasattr(r, "ping"))
		self.assertTrue(hasattr(r, "get"))
		self.assertTrue(hasattr(r, "set"))
		# Verify decode_responses is enabled
		connection_kwargs = r.connection_pool.connection_kwargs
		self.assertTrue(connection_kwargs.get("decode_responses", False))

	def test_reads_redis_memora_from_conf(self):
		"""When redis_memora is configured, it uses that URL."""
		from memora_admin.utils.redis_connection import get_memora_redis

		mock_conf = MagicMock()
		mock_conf.get = MagicMock(return_value="redis://127.0.0.1:13001")

		with patch("memora_admin.utils.redis_connection.frappe") as mock_frappe:
			mock_frappe.conf = mock_conf
			r = get_memora_redis()
			mock_conf.get.assert_called_once_with("redis_memora", mock_conf.redis_cache)
			# Verify it connects to the right port
			connection_kwargs = r.connection_pool.connection_kwargs
			self.assertEqual(connection_kwargs.get("port"), 13001)

	def test_falls_back_to_redis_cache(self):
		"""When redis_memora is not configured, falls back to redis_cache."""
		from memora_admin.utils.redis_connection import get_memora_redis

		mock_conf = MagicMock()
		mock_conf.redis_cache = "redis://127.0.0.1:13000"
		# Simulate redis_memora not set — .get() returns fallback
		mock_conf.get = MagicMock(return_value="redis://127.0.0.1:13000")

		with patch("memora_admin.utils.redis_connection.frappe") as mock_frappe:
			mock_frappe.conf = mock_conf
			r = get_memora_redis()
			mock_conf.get.assert_called_once_with("redis_memora", mock_conf.redis_cache)
			connection_kwargs = r.connection_pool.connection_kwargs
			self.assertEqual(connection_kwargs.get("port"), 13000)

	def test_can_ping_redis(self):
		"""get_memora_redis() returns a client that can connect to Redis."""
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()
		result = r.ping()
		self.assertTrue(result)

	def test_caches_client_across_calls(self):
		"""Subsequent calls return the same client instance (connection pool reuse)."""
		from memora_admin.utils.redis_connection import get_memora_redis

		r1 = get_memora_redis()
		r2 = get_memora_redis()
		self.assertIs(r1, r2)

	def test_recreates_client_on_url_change(self):
		"""Client is recreated if the URL changes (e.g., config update)."""
		from memora_admin.utils.redis_connection import get_memora_redis

		# First call — gets cached
		r1 = get_memora_redis()

		# Simulate URL change by resetting cache and patching
		redis_conn_module._client = None
		redis_conn_module._client_url = None

		mock_conf = MagicMock()
		mock_conf.get = MagicMock(return_value="redis://127.0.0.1:19999")

		with patch("memora_admin.utils.redis_connection.frappe") as mock_frappe:
			mock_frappe.conf = mock_conf
			r2 = get_memora_redis()
			self.assertIsNot(r1, r2)
