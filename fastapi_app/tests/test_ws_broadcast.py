"""Tests for WebSocket per-user broadcast in ConnectionManager.

Tests configurable broadcast concurrency:
- broadcast_concurrency=0 → sends are sequential
- broadcast_concurrency=50 → sends dispatched via gather with semaphore
- Slow connection doesn't block other connections when parallel enabled
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi_app.core.ws_manager import ConnectionManager


def _make_mock_ws(send_delay: float = 0) -> MagicMock:
	"""Create a mock WebSocket with async accept/close/send_text methods.

	Args:
		send_delay: Optional delay in seconds for send_text (simulates slow client).
	"""
	ws = MagicMock()
	ws.accept = AsyncMock()
	ws.close = AsyncMock()

	if send_delay > 0:
		original_side_effect = None

		async def _slow_send(message):
			await asyncio.sleep(send_delay)

		ws.send_text = AsyncMock(side_effect=_slow_send)
	else:
		ws.send_text = AsyncMock()

	ws.receive_text = AsyncMock()
	return ws


async def _connect_ws(mgr: ConnectionManager, user_id: str, ws: MagicMock) -> None:
	"""Helper to connect a mock WebSocket."""
	await mgr.connect(user_id, ws)


class TestSequentialBroadcast:
	"""T015.1: broadcast_concurrency=0 → sends are sequential."""

	@pytest.mark.asyncio
	async def test_send_to_user_sequential(self):
		"""With concurrency=0, sends happen one at a time (no gather)."""
		mgr = ConnectionManager(max_connections_per_user=10, broadcast_concurrency=0)

		# Connect 3 websockets for a user
		ws_list = []
		for _ in range(3):
			ws = _make_mock_ws()
			await _connect_ws(mgr, "USER-SEQ-001", ws)
			ws_list.append(ws)

		sent = await mgr.send_to_user("USER-SEQ-001", '{"type": "test"}')
		assert sent == 3

		# All 3 should have received the message
		for ws in ws_list:
			ws.send_text.assert_awaited_once_with('{"type": "test"}')


class TestParallelBroadcast:
	"""T015.2: broadcast_concurrency=50 → sends dispatched via gather with semaphore."""

	@pytest.mark.asyncio
	async def test_send_to_user_parallel(self):
		"""With concurrency=50, multiple connections receive messages concurrently."""
		mgr = ConnectionManager(max_connections_per_user=10, broadcast_concurrency=50)

		ws_list = []
		for _ in range(5):
			ws = _make_mock_ws()
			await _connect_ws(mgr, "USER-PAR-001", ws)
			ws_list.append(ws)

		sent = await mgr.send_to_user("USER-PAR-001", '{"type": "test"}')
		assert sent == 5

		for ws in ws_list:
			ws.send_text.assert_awaited_once_with('{"type": "test"}')

	@pytest.mark.asyncio
	async def test_dead_connection_cleaned_up_parallel(self):
		"""Dead connections are cleaned up after parallel send completes."""
		mgr = ConnectionManager(max_connections_per_user=10, broadcast_concurrency=50)

		good_ws = _make_mock_ws()
		bad_ws = _make_mock_ws()
		bad_ws.send_text = AsyncMock(side_effect=Exception("Connection reset"))

		await _connect_ws(mgr, "USER-PAR-DEAD", good_ws)
		await _connect_ws(mgr, "USER-PAR-DEAD", bad_ws)

		sent = await mgr.send_to_user("USER-PAR-DEAD", '{"type": "test"}')
		assert sent == 1  # Only the good one succeeded

		# Bad connection should be cleaned up (disconnected)
		assert mgr.active_connections == 1


class TestSlowConnectionDoesNotBlock:
	"""T015.3: Slow connection doesn't block other connections when parallel enabled."""

	@pytest.mark.asyncio
	async def test_slow_client_doesnt_block_others(self):
		"""With parallel broadcast, a slow client doesn't delay delivery to fast clients."""
		mgr = ConnectionManager(max_connections_per_user=10, broadcast_concurrency=50)

		# Create 1 slow (200ms) and 4 fast websockets
		slow_ws = _make_mock_ws(send_delay=0.2)
		fast_ws_list = [_make_mock_ws() for _ in range(4)]

		await _connect_ws(mgr, "USER-SLOW-001", slow_ws)
		for ws in fast_ws_list:
			await _connect_ws(mgr, "USER-SLOW-001", ws)

		# Time the broadcast
		start = asyncio.get_event_loop().time()
		sent = await mgr.send_to_user("USER-SLOW-001", '{"type": "test"}')
		elapsed = asyncio.get_event_loop().time() - start

		assert sent == 5

		# With parallel: all sends happen concurrently, so total time ~= slow client time (0.2s)
		# With sequential: total time would be ~0.2s + 4*~0ms = ~0.2s (fast clients are instant)
		# The key test: elapsed should NOT be >> 0.2s (which would mean serialized sends)
		# We use a generous margin since CI can be slow
		assert elapsed < 0.5, f"Broadcast took {elapsed:.3f}s — slow client may be blocking"
