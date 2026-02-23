"""Tests for global API rate limiting (all 3 tiers)."""

import json
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi_app.core.redis_keys import global_ratelimit_key, session_key
from fastapi_app.services.review import ReviewService
from fastapi_app.services.wallet import WalletService

# conftest.py overrides settings before app import


# === Phase 3: User Story 1 — Global Per-IP Rate Limit ===


class TestGlobalRateLimitNormal:
	"""T004: Normal requests under limit pass through."""

	@pytest.mark.asyncio
	async def test_requests_under_limit_pass(self, app_client):
		"""Send N requests (N < global_rate_limit=10), all should return 200."""
		for i in range(8):
			resp = await app_client.get("/api/v1/catalog/")
			# Catalog may return various codes, but NOT 429
			assert resp.status_code != 429, f"Request {i+1} was rate limited"

	@pytest.mark.asyncio
	async def test_rate_limit_headers_present(self, app_client):
		"""Non-exempt responses include X-RateLimit-* headers."""
		resp = await app_client.get("/api/v1/catalog/")
		assert "X-RateLimit-Limit" in resp.headers
		assert "X-RateLimit-Remaining" in resp.headers
		assert "X-RateLimit-Reset" in resp.headers
		assert resp.headers["X-RateLimit-Limit"] == "10"  # test setting

	@pytest.mark.asyncio
	async def test_remaining_decrements(self, app_client):
		"""X-RateLimit-Remaining decrements with each request."""
		resp1 = await app_client.get("/api/v1/catalog/")
		remaining1 = int(resp1.headers["X-RateLimit-Remaining"])

		resp2 = await app_client.get("/api/v1/catalog/")
		remaining2 = int(resp2.headers["X-RateLimit-Remaining"])

		assert remaining2 == remaining1 - 1


class TestGlobalRateLimitExceeded:
	"""T005: 429 returned when limit exceeded."""

	@pytest.mark.asyncio
	async def test_429_on_limit_exceeded(self, app_client):
		"""Send global_rate_limit+1 (11) requests, last returns 429."""
		# Send 10 requests (at limit)
		for i in range(10):
			resp = await app_client.get("/api/v1/catalog/")
			assert resp.status_code != 429, f"Request {i+1} was prematurely rate limited"

		# 11th request should be 429
		resp = await app_client.get("/api/v1/catalog/")
		assert resp.status_code == 429

	@pytest.mark.asyncio
	async def test_429_response_body(self, app_client):
		"""429 response has correct JSON body."""
		for _ in range(10):
			await app_client.get("/api/v1/catalog/")

		resp = await app_client.get("/api/v1/catalog/")
		assert resp.status_code == 429
		body = resp.json()
		assert body["error"] == "RATE_LIMITED"
		assert "retry_after" in body
		assert isinstance(body["retry_after"], int)
		assert body["retry_after"] > 0

	@pytest.mark.asyncio
	async def test_429_retry_after_header(self, app_client):
		"""429 response includes Retry-After header."""
		for _ in range(10):
			await app_client.get("/api/v1/catalog/")

		resp = await app_client.get("/api/v1/catalog/")
		assert resp.status_code == 429
		assert "Retry-After" in resp.headers
		assert int(resp.headers["Retry-After"]) > 0

	@pytest.mark.asyncio
	async def test_429_still_has_rate_limit_headers(self, app_client):
		"""429 response still includes X-RateLimit-* headers."""
		for _ in range(10):
			await app_client.get("/api/v1/catalog/")

		resp = await app_client.get("/api/v1/catalog/")
		assert resp.status_code == 429
		assert resp.headers["X-RateLimit-Remaining"] == "0"


class TestHealthExempt:
	"""T006: Health endpoints exempt from rate limiting."""

	@pytest.mark.asyncio
	async def test_health_live_no_rate_limit_headers(self, app_client):
		"""GET /api/v1/health/live has no X-RateLimit-Limit header."""
		resp = await app_client.get("/api/v1/health/live")
		assert resp.status_code == 200
		assert "X-RateLimit-Limit" not in resp.headers

	@pytest.mark.asyncio
	async def test_health_ready_no_rate_limit_headers(self, app_client):
		"""GET /api/v1/health/ready has no X-RateLimit-Limit header."""
		resp = await app_client.get("/api/v1/health/ready")
		assert "X-RateLimit-Limit" not in resp.headers

	@pytest.mark.asyncio
	async def test_health_not_counted_toward_limit(self, app_client):
		"""Health requests don't consume rate limit quota."""
		# Send many health requests
		for _ in range(20):
			await app_client.get("/api/v1/health/live")

		# Non-exempt request should still work (not 429)
		resp = await app_client.get("/api/v1/catalog/")
		assert resp.status_code != 429


class TestWebhookExempt:
	"""T007: Payment webhook exempt from rate limiting."""

	@pytest.mark.asyncio
	async def test_webhook_payment_no_rate_limit_headers(self, app_client):
		"""POST /api/v1/webhooks/payment has no rate limit headers."""
		resp = await app_client.post(
			"/api/v1/webhooks/payment",
			json={
				"event_id": "EVT-TEST-001",
				"event_type": "payment.completed",
				"transaction_id": "TXN-TEST-001",
				"player_id": "PLAYER-TEST-001",
				"product_grant_id": "GRANT-TEST-001",
			},
		)
		# May return any status, but should NOT have rate limit headers
		assert "X-RateLimit-Limit" not in resp.headers


class TestFailOpen:
	"""T008: Fail-open on Redis unavailable."""

	@pytest.mark.asyncio
	async def test_request_passes_on_redis_error(self, app_client):
		"""When Redis is unavailable, requests pass through without rate limit headers."""
		# Patch the GlobalRateLimiter.check method to raise ConnectionError
		with patch(
			"fastapi_app.middleware.rate_limit.GlobalRateLimiter.check",
			side_effect=Exception("Redis connection refused"),
		):
			resp = await app_client.get("/api/v1/catalog/")
			# Should pass through (not 429, not 500)
			assert resp.status_code != 429
			assert resp.status_code != 500
			# No rate limit headers when Redis is down
			assert "X-RateLimit-Limit" not in resp.headers


# === Phase 4: User Story 2 — Per-Player Rate Limit on Write Endpoints ===

# Valid request bodies for write endpoints
REVIEW_BODY = {"items": [{"item_id": "ITEM-TEST-001", "fail_count": 0}]}
SESSION_START_BODY = {"lesson_id": "LESSON-TEST-001", "subject_id": "SUB-TEST-001"}
SESSION_END_BODY = {"stages": [{"stage_id": "STG-001", "time_spent": 1000, "completed_at": "2026-02-22T00:00:00"}]}


class TestReviewsPerPlayerRateLimit:
	"""T011: Reviews submit allows up to reviews_rate_limit (5) per player then 429."""

	@pytest.mark.asyncio
	async def test_reviews_rate_limited_at_threshold(self, authed_client):
		"""Send reviews_rate_limit+1 (6) requests, 6th returns 429."""
		client, token, player_id, family_id = authed_client

		with (
			patch.object(
				ReviewService,
				"submit_reviews",
				new_callable=AsyncMock,
				return_value={"processed": 1, "remaining_due": 0, "has_more": False},
			),
			patch.object(WalletService, "award_xp", new_callable=AsyncMock),
		):
			# Send 5 requests (at limit) — all should pass rate limit
			for i in range(5):
				resp = await client.post("/api/v1/reviews/SUB-TEST/submit", json=REVIEW_BODY)
				assert resp.status_code != 429, f"Request {i+1} was prematurely rate limited"

			# 6th request should be 429
			resp = await client.post("/api/v1/reviews/SUB-TEST/submit", json=REVIEW_BODY)
			assert resp.status_code == 429

	@pytest.mark.asyncio
	async def test_reviews_429_has_retry_after(self, authed_client):
		"""429 from per-player limit includes Retry-After header and correct body."""
		client, token, player_id, family_id = authed_client

		with (
			patch.object(
				ReviewService,
				"submit_reviews",
				new_callable=AsyncMock,
				return_value={"processed": 1, "remaining_due": 0, "has_more": False},
			),
			patch.object(WalletService, "award_xp", new_callable=AsyncMock),
		):
			for _ in range(5):
				await client.post("/api/v1/reviews/SUB-TEST/submit", json=REVIEW_BODY)

			resp = await client.post("/api/v1/reviews/SUB-TEST/submit", json=REVIEW_BODY)
			assert resp.status_code == 429
			assert "Retry-After" in resp.headers
			body = resp.json()
			assert body["error"] == "RATE_LIMITED"
			assert isinstance(body["retry_after"], int)


class TestSessionStartPerPlayerRateLimit:
	"""T012: Session start allows up to session_rate_limit (3) per player then 429."""

	@pytest.mark.asyncio
	async def test_session_start_rate_limited_at_threshold(self, authed_client):
		"""Send session_rate_limit+1 (4) requests, 4th returns 429."""
		client, token, player_id, family_id = authed_client

		# Requests will fail at endpoint level (404 - no hierarchy) but rate limit counter still increments
		for i in range(3):
			resp = await client.post("/api/v1/sessions/start", json=SESSION_START_BODY)
			assert resp.status_code != 429, f"Request {i+1} was prematurely rate limited"

		# 4th request should be 429
		resp = await client.post("/api/v1/sessions/start", json=SESSION_START_BODY)
		assert resp.status_code == 429


class TestSessionEndPerPlayerRateLimit:
	"""T013: Session end allows up to session_rate_limit (3) per player then 429."""

	@pytest.mark.asyncio
	async def test_session_end_rate_limited_at_threshold(self, authed_client):
		"""Send session_rate_limit+1 (4) requests, 4th returns 429."""
		client, token, player_id, family_id = authed_client

		# Requests will fail at endpoint level (403 - no active session) but rate limit counter still increments
		for i in range(3):
			resp = await client.post("/api/v1/sessions/end", json=SESSION_END_BODY)
			assert resp.status_code != 429, f"Request {i+1} was prematurely rate limited"

		# 4th request should be 429
		resp = await client.post("/api/v1/sessions/end", json=SESSION_END_BODY)
		assert resp.status_code == 429


class TestPerPlayerIndependence:
	"""T014: Per-player limit is independent — different players each get their own counter."""

	@pytest.mark.asyncio
	async def test_different_players_get_separate_counters(self, app_client, redis_client, make_player_token):
		"""Two players from same IP each get their own per-player rate limit counter.

		Uses different X-Forwarded-For headers to avoid global IP limit interference.
		"""
		# Create two players
		token1, fid1 = make_player_token(player_id="PLAYER-RL-001")
		token2, fid2 = make_player_token(player_id="PLAYER-RL-002")

		# Seed sessions for both
		await redis_client.set(session_key("PLAYER-RL-001"), json.dumps({"fid": fid1}))
		await redis_client.set(session_key("PLAYER-RL-002"), json.dumps({"fid": fid2}))

		with (
			patch.object(
				ReviewService,
				"submit_reviews",
				new_callable=AsyncMock,
				return_value={"processed": 1, "remaining_due": 0, "has_more": False},
			),
			patch.object(WalletService, "award_xp", new_callable=AsyncMock),
		):
			# Player 1 exhausts their per-player limit (5 requests)
			for i in range(5):
				resp = await app_client.post(
					"/api/v1/reviews/SUB-TEST/submit",
					json=REVIEW_BODY,
					headers={"Authorization": f"Bearer {token1}", "X-Forwarded-For": "10.0.0.1"},
				)
				assert resp.status_code != 429, f"Player 1 request {i+1} was prematurely rate limited"

			# Player 1 is now rate limited
			resp = await app_client.post(
				"/api/v1/reviews/SUB-TEST/submit",
				json=REVIEW_BODY,
				headers={"Authorization": f"Bearer {token1}", "X-Forwarded-For": "10.0.0.1"},
			)
			assert resp.status_code == 429

			# Player 2 can still send — they have their own per-player counter
			for i in range(5):
				resp = await app_client.post(
					"/api/v1/reviews/SUB-TEST/submit",
					json=REVIEW_BODY,
					headers={"Authorization": f"Bearer {token2}", "X-Forwarded-For": "10.0.0.2"},
				)
				assert resp.status_code != 429, f"Player 2 request {i+1} was incorrectly rate limited"

		# Cleanup
		await redis_client.delete(session_key("PLAYER-RL-001"), session_key("PLAYER-RL-002"))


# === Phase 5: User Story 3 — WebSocket Connection Limiting ===


def _make_mock_ws():
	"""Create a mock WebSocket with async accept/close methods."""
	ws = MagicMock()
	ws.accept = AsyncMock()
	ws.close = AsyncMock()
	ws.send_text = AsyncMock()
	ws.receive_text = AsyncMock()
	return ws


class TestWebSocketConnectionLimit:
	"""T018: Up to ws_max_connections_per_user (3) connections accepted for same player."""

	@pytest.mark.asyncio
	async def test_connections_up_to_limit_accepted(self):
		"""All connections up to max_connections_per_user=3 are accepted."""
		from fastapi_app.core.ws_manager import ConnectionManager

		mgr = ConnectionManager(max_connections_per_user=3)
		accepted = []

		for i in range(3):
			ws = _make_mock_ws()
			is_first = await mgr.connect("PLAYER-WS-001", ws, plan_id="PLAN-001")
			ws.accept.assert_awaited_once()
			ws.close.assert_not_awaited()
			accepted.append(ws)
			if i == 0:
				assert is_first is True
			else:
				assert is_first is False

		assert mgr.active_connections == 3

	@pytest.mark.asyncio
	async def test_connection_limit_per_user_not_global(self):
		"""Different users each get their own connection limit."""
		from fastapi_app.core.ws_manager import ConnectionManager

		mgr = ConnectionManager(max_connections_per_user=3)

		# User A opens 3 connections
		for _ in range(3):
			ws = _make_mock_ws()
			await mgr.connect("PLAYER-WS-A", ws)

		# User B can still open connections (not affected by A's count)
		ws_b = _make_mock_ws()
		is_first = await mgr.connect("PLAYER-WS-B", ws_b)
		ws_b.accept.assert_awaited_once()
		assert is_first is True


class TestWebSocketConnectionRejection:
	"""T019: 4th WebSocket connection rejected with close code 4029."""

	@pytest.mark.asyncio
	async def test_4th_connection_rejected_with_4029(self):
		"""4th connection for same user is rejected with close code 4029 before accept."""
		from fastapi_app.core.ws_manager import ConnectionManager

		mgr = ConnectionManager(max_connections_per_user=3)

		# Fill up to limit
		for _ in range(3):
			ws = _make_mock_ws()
			await mgr.connect("PLAYER-WS-001", ws)

		# 4th connection should be rejected
		ws4 = _make_mock_ws()
		result = await mgr.connect("PLAYER-WS-001", ws4)

		# close() called BEFORE accept()
		ws4.accept.assert_not_awaited()
		ws4.close.assert_awaited_once_with(code=4029, reason="Too many connections")
		# connect() returns False (not a "first" connection, and not actually connected)
		assert result is False
		# Still only 3 connections
		assert mgr.active_connections == 3

	@pytest.mark.asyncio
	async def test_rejected_connection_not_tracked(self):
		"""Rejected connection is not added to the connection set."""
		from fastapi_app.core.ws_manager import ConnectionManager

		mgr = ConnectionManager(max_connections_per_user=3)

		for _ in range(3):
			ws = _make_mock_ws()
			await mgr.connect("PLAYER-WS-001", ws)

		# Reject 4th
		ws4 = _make_mock_ws()
		await mgr.connect("PLAYER-WS-001", ws4)

		# ws4 should NOT be in the connections set
		assert ws4 not in mgr._connections.get("PLAYER-WS-001", set())
		assert mgr.active_users == 1  # still just one user

	@pytest.mark.asyncio
	async def test_slot_freed_after_disconnect(self):
		"""After disconnecting one, a new connection can be accepted."""
		from fastapi_app.core.ws_manager import ConnectionManager

		mgr = ConnectionManager(max_connections_per_user=3)
		first_ws = _make_mock_ws()
		await mgr.connect("PLAYER-WS-001", first_ws)

		for _ in range(2):
			ws = _make_mock_ws()
			await mgr.connect("PLAYER-WS-001", ws)

		# Disconnect the first one
		await mgr.disconnect("PLAYER-WS-001", first_ws)
		assert mgr.active_connections == 2

		# Now a new connection should be accepted
		ws_new = _make_mock_ws()
		result = await mgr.connect("PLAYER-WS-001", ws_new)
		ws_new.accept.assert_awaited_once()
		ws_new.close.assert_not_awaited()
		assert result is False  # not first connection
		assert mgr.active_connections == 3


# === Phase 6: Benchmark ===


class TestRateLimitBenchmark:
	"""T025: Benchmark rate limit check latency — p99 < 2ms (SC-006)."""

	@pytest.mark.asyncio
	async def test_rate_limit_check_p99_under_2ms(self, redis_client):
		"""Measure GlobalRateLimiter.check() directly — single Redis round-trip p99 < 2ms."""
		from fastapi_app.services.global_rate_limit import GlobalRateLimiter

		limiter = GlobalRateLimiter(redis_client)

		# Warmup: register the Lua script
		await limiter.check(global_ratelimit_key("bench-warmup"), 1000, 60)

		latencies = []
		for i in range(100):
			key = global_ratelimit_key(f"bench-{i}")
			start = time.perf_counter()
			await limiter.check(key, 1000, 60)
			elapsed = (time.perf_counter() - start) * 1000  # ms
			latencies.append(elapsed)

		latencies.sort()
		p99 = latencies[int(len(latencies) * 0.99) - 1]
		median = latencies[len(latencies) // 2]

		# Cleanup
		keys = [global_ratelimit_key(f"bench-{i}") for i in range(100)]
		keys.append(global_ratelimit_key("bench-warmup"))
		await redis_client.delete(*keys)

		assert p99 < 2.0, (
			f"Rate limit check p99={p99:.2f}ms exceeds 2ms target "
			f"(median={median:.2f}ms)"
		)
