"""Centralized Redis connection for Frappe-side Memora code.

All Frappe background tasks, API endpoints, and event handlers that need
to access Memora's dedicated Redis instance should use get_memora_redis().

The function reads the ``redis_memora`` key from Frappe site config.
If not set (e.g., during migration or on older deployments), it falls back
to ``redis_cache`` for backward compatibility.

Uses a module-level cached client to avoid creating a new connection pool
on every call (prevents file descriptor exhaustion under load).
"""

import frappe
import redis

_client: redis.Redis | None = None
_client_url: str | None = None
_raw_client: redis.Redis | None = None
_raw_client_url: str | None = None


def get_memora_redis() -> redis.Redis:
	"""Return a synchronous Redis client connected to Memora's dedicated instance.

	Reads ``redis_memora`` from ``frappe.conf`` (site_config.json).
	Falls back to ``frappe.conf.redis_cache`` when ``redis_memora`` is not configured.

	The client uses ``decode_responses=True`` to match the FastAPI Redis pool
	convention — all values are returned as strings, never bytes.

	The client is cached at module level so all callers share one connection pool.

	Returns:
		redis.Redis: Connected client with decode_responses=True.
	"""
	global _client, _client_url
	url = frappe.conf.get("redis_memora", frappe.conf.redis_cache)
	if _client is None or _client_url != url:
		_client = redis.from_url(url, decode_responses=True)
		_client_url = url
	return _client


def get_memora_redis_raw() -> redis.Redis:
	"""Return a synchronous Redis client WITHOUT decode_responses.

	Use this for operations on binary data (e.g. bitmap GET) where
	the raw bytes are not valid UTF-8 and would fail with decode_responses=True.

	The client is cached at module level so all callers share one connection pool.

	Returns:
		redis.Redis: Connected client that returns bytes, not strings.
	"""
	global _raw_client, _raw_client_url
	url = frappe.conf.get("redis_memora", frappe.conf.redis_cache)
	if _raw_client is None or _raw_client_url != url:
		_raw_client = redis.from_url(url, decode_responses=False)
		_raw_client_url = url
	return _raw_client
