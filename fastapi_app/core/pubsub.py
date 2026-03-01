"""Redis pub/sub listeners for cache invalidation and real-time notifications.

Contains two independent listeners:
1. Cache invalidation listener: Static channel (memora:cache:invalidate) for
   hierarchy/plan/profile/catalog cache invalidation from Frappe build worker.
2. Notification listener: Dynamic per-user channels (memora:notify:{user_id})
   for forwarding subscription notifications to WebSocket clients.
"""

import asyncio
import json
from typing import Any

import structlog

from fastapi_app.core.redis_keys import cache_invalidation_channel

logger = structlog.get_logger()


async def start_pubsub_listener(redis_pool: Any, app_state: Any) -> None:
	"""
	Start Redis pub/sub listener for cache invalidation.

	Subscribes to cache_invalidation_channel() and listens for invalidation messages.
	On message receipt, calls hierarchy_service.invalidate(subject_id).

	Args:
	    redis_pool: Redis connection pool from app.state.redis_pool
	    app_state: FastAPI app.state for accessing hierarchy_service

	Note:
	    This runs as an asyncio background task started in main.py lifespan.
	    Handles CancelledError for clean shutdown.
	"""
	import redis.asyncio as redis

	# Create dedicated client for pub/sub (separate from pool)
	client = redis.Redis(connection_pool=redis_pool)

	try:
		pubsub = client.pubsub()
		await pubsub.subscribe(cache_invalidation_channel())

		logger.info(
			"pubsub_listener_started",
			channel=cache_invalidation_channel(),
		)

		async for message in pubsub.listen():
			if message["type"] == "message":
				await _handle_invalidation(message["data"], app_state)

	except asyncio.CancelledError:
		logger.info("pubsub_listener_cancelled")
		raise

	except Exception as e:
		logger.error("pubsub_listener_error", error=str(e))
		raise

	finally:
		# Clean shutdown
		try:
			await pubsub.unsubscribe(cache_invalidation_channel())
			await client.aclose()
		except Exception as e:
			logger.debug("pubsub_cleanup_error", error=str(e))


async def _handle_invalidation(data: bytes | str, app_state: Any) -> None:
	"""
	Handle cache invalidation message.

	Parses the JSON payload and invalidates hierarchy cache for the subject.

	Args:
	    data: Raw message data (bytes or str)
	    app_state: FastAPI app.state containing hierarchy_service
	"""
	try:
		# Decode bytes if needed
		if isinstance(data, bytes):
			data = data.decode("utf-8")

		# Parse JSON payload
		payload = json.loads(data)

		msg_type = payload.get("type")
		subject_id = payload.get("subject_id")
		plan_id = payload.get("plan_id")
		timestamp = payload.get("timestamp")

		if msg_type == "hierarchy" and subject_id:
			# Get hierarchy service from app state
			hierarchy_service = getattr(app_state, "hierarchy_service", None)

			if hierarchy_service:
				await hierarchy_service.invalidate(subject_id)
				logger.info(
					"hierarchy_cache_invalidated",
					subject_id=subject_id,
					timestamp=timestamp,
				)
			else:
				logger.warning(
					"hierarchy_service_not_available",
					subject_id=subject_id,
				)
		elif msg_type == "plan" and plan_id:
			# Get plan service from app state
			plan_service = getattr(app_state, "plan_service", None)

			if plan_service:
				await plan_service.invalidate(plan_id)
				logger.info(
					"plan_cache_invalidated",
					plan_id=plan_id,
					timestamp=timestamp,
				)
			else:
				logger.warning(
					"plan_service_not_available",
					plan_id=plan_id,
				)
		elif msg_type == "profile" and payload.get("player_id"):
			# Get profile service from app state
			player_id = payload.get("player_id")
			profile_service = getattr(app_state, "profile_service", None)

			if profile_service:
				await profile_service.invalidate(player_id)
				logger.info(
					"profile_cache_invalidated",
					player_id=player_id,
					timestamp=timestamp,
				)
			else:
				logger.warning(
					"profile_service_not_available",
					player_id=player_id,
				)
		elif msg_type == "catalog" and plan_id:
			catalog_service = getattr(app_state, "catalog_service", None)
			if catalog_service:
				await catalog_service.invalidate(plan_id)
				logger.info(
					"catalog_cache_invalidated",
					plan_id=plan_id,
					timestamp=timestamp,
				)
			else:
				logger.warning(
					"catalog_service_not_available",
					plan_id=plan_id,
				)
		elif msg_type == "plan_subjects" and plan_id:
			# Plan-wide fanout is intentionally not supported in-process.
			# If this is needed later, implement it as a dedicated queued feature.
			logger.info(
				"plan_subjects_ignored",
				plan_id=plan_id,
			)
		elif msg_type == "level_config":
			logger.info(
				"level_config_updated",
				timestamp=timestamp,
			)
		elif msg_type == "announcements":
			announcement_service = getattr(app_state, "announcement_service", None)
			if announcement_service:
				await announcement_service.invalidate()
				logger.info(
					"announcements_cache_invalidated",
					timestamp=timestamp,
				)
			else:
				logger.warning("announcement_service_not_available")
		elif msg_type == "subscription_changed" and payload.get("player_id"):
			ws_manager = getattr(app_state, "ws_manager", None)
			if ws_manager:
				player_id = payload["player_id"]
				event = json.dumps({"type": "subscriptions_changed"})
				sent = await ws_manager.send_to_user(player_id, event)
				logger.info(
					"subscription_notification_sent",
					player_id=player_id,
					sent=sent,
				)
		else:
			logger.debug(
				"unknown_invalidation_message",
				msg_type=msg_type,
				payload=payload,
			)

	except json.JSONDecodeError as e:
		logger.error("invalidation_message_parse_error", error=str(e), data=data)

	except Exception as e:
		# Log error but don't crash the listener
		logger.error("invalidation_handler_error", error=str(e))


# --- Notification Pub/Sub Listener ---


async def start_notification_listener(redis_pool: Any, app_state: Any) -> None:
	"""Start dedicated pub/sub listener for per-user notification channels.

	Unlike the cache invalidation listener (static channel), this listener
	dynamically subscribes/unsubscribes to per-user channels as WebSocket
	clients connect and disconnect.

	The pubsub object is stored on app_state.notify_pubsub so the WebSocket
	endpoint can call subscribe/unsubscribe on it.

	Args:
		redis_pool: Redis connection pool from app.state.redis_pool
		app_state: FastAPI app.state for accessing ws_manager
	"""
	import redis.asyncio as redis

	client = redis.Redis(connection_pool=redis_pool)

	try:
		pubsub = client.pubsub()
		app_state.notify_pubsub = pubsub

		logger.info("notification_listener_started")

		async for message in pubsub.listen():
			if message["type"] == "message":
				await _handle_notification(message, app_state)

	except asyncio.CancelledError:
		logger.info("notification_listener_cancelled")
		raise
	except Exception as e:
		logger.error("notification_listener_error", error=str(e))
		raise
	finally:
		try:
			await client.aclose()
		except Exception as e:
			logger.debug("notification_cleanup_error", error=str(e))


async def _handle_notification(message: dict, app_state: Any) -> None:
	"""Handle notification pub/sub message and forward to WebSocket clients.

	Extracts user_id from channel name and sends payload via ConnectionManager.

	Args:
		message: Raw pub/sub message dict with 'channel' and 'data' keys.
		app_state: FastAPI app.state containing ws_manager.
	"""
	try:
		channel = message.get("channel", b"")
		if isinstance(channel, bytes):
			channel = channel.decode("utf-8")

		# Extract user_id from channel: "memora:notify:{user_id}"
		if not channel.startswith("memora:notify:"):
			return

		user_id = channel[len("memora:notify:"):]

		data = message.get("data", b"")
		if isinstance(data, bytes):
			data = data.decode("utf-8")

		# Forward raw JSON to all user's WebSocket connections
		ws_manager = getattr(app_state, "ws_manager", None)
		if ws_manager:
			sent = await ws_manager.send_to_user(user_id, data)
			logger.info("notification_forwarded", user_id=user_id, sent_count=sent)
		else:
			logger.warning("ws_manager_not_available")

	except Exception as e:
		logger.error("notification_handler_error", error=str(e))
