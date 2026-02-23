"""WebSocket endpoint for real-time player notifications."""

from __future__ import annotations

from typing import TYPE_CHECKING

import jwt
import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from fastapi_app.core.redis_keys import notify_channel
from fastapi_app.core.security import decode_token

if TYPE_CHECKING:
	from fastapi_app.core.ws_manager import ConnectionManager

logger = structlog.get_logger()

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.websocket("/ws")
async def notifications_ws(
	websocket: WebSocket,
	token: str = Query(...),
) -> None:
	"""WebSocket endpoint for real-time notifications.

	Authenticates via JWT query parameter before accepting the connection.
	Subscribes to the user's Redis pub/sub channel on first connection
	and unsubscribes on last disconnect for that user.

	Args:
		websocket: The WebSocket connection.
		token: JWT access token passed as query parameter.
	"""
	# Authenticate BEFORE accepting the WebSocket
	try:
		payload = decode_token(token, verify_type="access")
		user_id = payload["sub"]
		plan_id = payload.get("plan", "")
	except jwt.ExpiredSignatureError:
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
		return
	except jwt.InvalidTokenError:
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
		return
	except Exception:
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
		return

	# Get ConnectionManager from app state
	ws_manager: ConnectionManager = websocket.app.state.ws_manager

	# Connect (accept + register with plan tracking)
	is_first = await ws_manager.connect(user_id, websocket, plan_id=plan_id)

	# If first connection for this user, subscribe to their notification channel
	notify_pubsub = websocket.app.state.notify_pubsub
	if is_first:
		await notify_pubsub.subscribe(notify_channel(user_id))
		logger.info("ws_connected", user_id=user_id, is_first=True)
	else:
		logger.info("ws_connected", user_id=user_id, is_first=False)

	# Keep connection alive with a receive loop
	try:
		while True:
			# Wait for client messages (pings/pongs handled by protocol).
			# We don't expect the client to send data, but must await to detect disconnect.
			await websocket.receive_text()
	except WebSocketDisconnect:
		pass
	finally:
		is_last = await ws_manager.disconnect(user_id, websocket)
		if is_last:
			try:
				await notify_pubsub.unsubscribe(notify_channel(user_id))
			except Exception:
				pass
		logger.info("ws_disconnected", user_id=user_id, is_last=is_last)
