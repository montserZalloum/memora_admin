"""Per-user WebSocket connection manager.

Tracks WebSocket connections per user_id with async lock for thread safety.
Supports multi-device connections (multiple WebSockets per user).
Used by the notification system to send targeted messages to specific users.
"""

import asyncio
from collections import defaultdict

import structlog
from fastapi import WebSocket

logger = structlog.get_logger()


class ConnectionManager:
	"""Manages per-user WebSocket connections with thread-safe operations.

	Uses a dict mapping user_id -> set[WebSocket] for O(1) user lookup
	and multi-device support. All mutations are protected by an async lock.

	The connect/disconnect methods return first/last connection indicators
	so callers can manage Redis pub/sub subscriptions lifecycle:
	- First connection for a user -> subscribe to their notification channel
	- Last connection for a user -> unsubscribe from their notification channel
	"""

	def __init__(self, max_connections_per_user: int = 5) -> None:
		self._connections: dict[str, set[WebSocket]] = defaultdict(set)
		self._user_plan: dict[str, str] = {}  # user_id -> plan_id
		self._plan_users: dict[str, set[str]] = defaultdict(set)  # plan_id -> set of user_ids
		self._lock = asyncio.Lock()
		self._max_connections_per_user = max_connections_per_user

	@property
	def active_users(self) -> int:
		"""Return count of users with active connections (for health/metrics)."""
		return len(self._connections)

	@property
	def active_connections(self) -> int:
		"""Return total connection count across all users."""
		return sum(len(ws_set) for ws_set in self._connections.values())

	async def connect(self, user_id: str, websocket: WebSocket, plan_id: str = "") -> bool:
		"""Accept WebSocket and add to user's connection set.

		Args:
			user_id: The user identifier (email/player ID).
			websocket: The WebSocket connection to register.
			plan_id: The player's plan ID (for plan-level broadcasts).

		Returns:
			True if this is the first connection for the user
			(caller should subscribe to pub/sub channel).
			False if this is an additional connection or if rejected.
		"""
		async with self._lock:
			if len(self._connections[user_id]) >= self._max_connections_per_user:
				logger.warning(
					"ws_connection_rejected",
					user_id=user_id,
					current=len(self._connections[user_id]),
					max=self._max_connections_per_user,
				)
				await websocket.close(code=4029, reason="Too many connections")
				return False

			await websocket.accept()
			is_first = len(self._connections[user_id]) == 0
			self._connections[user_id].add(websocket)
			if plan_id:
				self._user_plan[user_id] = plan_id
				self._plan_users[plan_id].add(user_id)

		logger.debug(
			"ws_connected",
			user_id=user_id,
			is_first=is_first,
			plan_id=plan_id,
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
		async with self._lock:
			self._connections[user_id].discard(websocket)
			is_last = len(self._connections[user_id]) == 0
			if is_last:
				del self._connections[user_id]
				plan_id = self._user_plan.pop(user_id, "")
				if plan_id:
					self._plan_users[plan_id].discard(user_id)
					if not self._plan_users[plan_id]:
						del self._plan_users[plan_id]

		logger.debug(
			"ws_disconnected",
			user_id=user_id,
			is_last=is_last,
		)
		return is_last

	async def send_to_user(self, user_id: str, message: str) -> int:
		"""Send text message to ALL WebSocket connections for the user.

		Catches exceptions per-connection so one broken connection
		does not prevent others from receiving the message.

		Args:
			user_id: The user identifier to send to.
			message: The text message (typically JSON) to send.

		Returns:
			Count of successful sends.
		"""
		connections = self._connections.get(user_id, set())
		if not connections:
			return 0

		sent = 0
		dead: list[WebSocket] = []

		for ws in connections:
			try:
				await ws.send_text(message)
				sent += 1
			except Exception as e:
				logger.debug(
					"ws_send_failed",
					user_id=user_id,
					error=str(e),
				)
				dead.append(ws)

		# Clean up dead connections outside the send loop
		for ws in dead:
			await self.disconnect(user_id, ws)

		return sent

	async def send_to_plan(self, plan_id: str, message: str) -> int:
		"""Send message to ALL connected users on the given plan.

		Args:
			plan_id: The plan identifier to broadcast to.
			message: The text message (typically JSON) to send.

		Returns:
			Total count of successful sends across all users.
		"""
		user_ids = list(self._plan_users.get(plan_id, set()))
		if not user_ids:
			return 0

		total = 0
		for user_id in user_ids:
			total += await self.send_to_user(user_id, message)

		logger.info(
			"plan_broadcast_sent",
			plan_id=plan_id,
			users=len(user_ids),
			sent=total,
		)
		return total
