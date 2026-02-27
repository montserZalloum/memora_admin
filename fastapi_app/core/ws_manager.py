"""Per-user WebSocket connection manager.

Tracks WebSocket connections per user_id with per-user async locks for
concurrency. Supports multi-device connections (multiple WebSockets per user).
Used by the notification system to send targeted messages to specific users.
"""

import asyncio
from collections import defaultdict

import structlog
from fastapi import WebSocket

logger = structlog.get_logger()


class ConnectionManager:
	"""Manages per-user WebSocket connections with per-user lock isolation.

	Uses a dict mapping user_id -> set[WebSocket] for O(1) user lookup
	and multi-device support. Per-user locks eliminate cross-user contention
	that a global lock would cause at scale (100k+ concurrent users).

	The connect/disconnect methods return first/last connection indicators
	so callers can manage Redis pub/sub subscriptions lifecycle:
	- First connection for a user -> subscribe to their notification channel
	- Last connection for a user -> unsubscribe from their notification channel
	"""

	def __init__(self, max_connections_per_user: int = 5, broadcast_concurrency: int = 0) -> None:
		self._connections: dict[str, set[WebSocket]] = defaultdict(set)
		self._user_locks: dict[str, asyncio.Lock] = {}  # Per-user operation locks
		self._lock_guard = asyncio.Lock()  # Guard for lock dict mutations only
		self._max_connections_per_user = max_connections_per_user
		self._broadcast_concurrency = broadcast_concurrency

	async def _get_user_lock(self, user_id: str) -> asyncio.Lock:
		"""Get or create a per-user lock.

		Fast path: return existing lock (no guard needed).
		Slow path: acquire _lock_guard, use setdefault to create lock.
		"""
		lock = self._user_locks.get(user_id)
		if lock is None:
			async with self._lock_guard:
				lock = self._user_locks.setdefault(user_id, asyncio.Lock())
		return lock

	@property
	def active_users(self) -> int:
		"""Return count of users with active connections (for health/metrics)."""
		return len(self._connections)

	@property
	def active_connections(self) -> int:
		"""Return total connection count across all users."""
		return sum(len(ws_set) for ws_set in self._connections.values())

	async def connect(self, user_id: str, websocket: WebSocket) -> bool | None:
		"""Accept WebSocket and add to user's connection set.

		Args:
			user_id: The user identifier (email/player ID).
			websocket: The WebSocket connection to register.

		Returns:
			True if this is the first connection for the user
			(caller should subscribe to pub/sub channel).
			False if this is an additional connection.
			None if the connection was rejected (max limit reached).
		"""
		user_lock = await self._get_user_lock(user_id)
		async with user_lock:
			if len(self._connections[user_id]) >= self._max_connections_per_user:
				logger.warning(
					"ws_connection_rejected",
					user_id=user_id,
					current=len(self._connections[user_id]),
					max=self._max_connections_per_user,
				)
				await websocket.close(code=4029, reason="Too many connections")
				return None

			await websocket.accept()
			is_first = len(self._connections[user_id]) == 0
			self._connections[user_id].add(websocket)

		logger.debug(
			"ws_connected",
			user_id=user_id,
			is_first=is_first,
			user_connections=len(self._connections[user_id]),
		)
		return is_first

	async def disconnect(self, user_id: str, websocket: WebSocket) -> bool:
		"""Remove WebSocket from user's connection set.

		Args:
			user_id: The user identifier (email/player ID).
			websocket: The WebSocket connection to remove.

		Returns:
			True if this was the last connection for the user
			(caller should unsubscribe from pub/sub channel).
		"""
		user_lock = await self._get_user_lock(user_id)
		async with user_lock:
			self._connections[user_id].discard(websocket)
			is_last = len(self._connections[user_id]) == 0
			if is_last:
				del self._connections[user_id]

		# Clean up per-user lock when user has zero connections
		if is_last:
			async with self._lock_guard:
				# Re-check: another connect may have raced
				if user_id not in self._connections:
					self._user_locks.pop(user_id, None)

		logger.debug(
			"ws_disconnected",
			user_id=user_id,
			is_last=is_last,
		)
		return is_last

	async def send_to_user(self, user_id: str, message: str) -> int:
		"""Send text message to ALL WebSocket connections for the user.

		Catches exceptions per-connection so one broken connection
		does not prevent others from receiving the message. When
		broadcast_concurrency > 0, sends are dispatched in parallel
		with a semaphore to limit concurrency.

		Args:
			user_id: The user identifier to send to.
			message: The text message (typically JSON) to send.

		Returns:
			Count of successful sends.
		"""
		# Snapshot connections to avoid mutation during iteration
		connections = list(self._connections.get(user_id, set()))
		if not connections:
			return 0

		dead: list[WebSocket] = []

		if self._broadcast_concurrency > 0 and len(connections) > 1:
			sem = asyncio.Semaphore(self._broadcast_concurrency)

			async def _send(ws: WebSocket) -> bool:
				async with sem:
					try:
						await ws.send_text(message)
						return True
					except Exception as e:
						logger.debug("ws_send_failed", user_id=user_id, error=str(e))
						dead.append(ws)
						return False

			results = await asyncio.gather(*[_send(ws) for ws in connections])
			sent = sum(1 for r in results if r)
		else:
			sent = 0
			for ws in connections:
				try:
					await ws.send_text(message)
					sent += 1
				except Exception as e:
					logger.debug("ws_send_failed", user_id=user_id, error=str(e))
					dead.append(ws)

		# Clean up dead connections outside the send loop
		for ws in dead:
			await self.disconnect(user_id, ws)

		return sent
