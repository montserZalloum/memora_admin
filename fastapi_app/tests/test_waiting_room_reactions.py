"""Unit tests for ReactionEngine — Redis-backed global counters, mocked dependencies.

T003 [US1]: tap acceptance (valid/invalid reaction types), Redis counter increment,
flush-and-reset via global flush loop, burst message structure (counts, intensity
tiers, server_ts, room_id), empty window suppression, and reaction_enabled feature
flag bypass.

T008 [US2]: token bucket rate limiting — burst allowance, sustained limit,
token refill after pause, excess taps rejected, no error/disconnect side effects.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from fastapi_app.core.redis_keys import lc_reaction_win_key
from fastapi_app.services.waiting_room_reactions import (
	VALID_REACTIONS,
	ReactionEngine,
	_compute_intensity,
)


def _make_settings(**overrides):
	defaults = {
		"reaction_flush_interval_ms": 50,  # Fast for tests (50ms)
		"reaction_sustained_rate": 3,
		"reaction_burst_allowance": 6,
		"reaction_room_cap_per_sec": 250,
		"reaction_rl_ttl_sec": 5,
		"reaction_enabled": True,
	}
	defaults.update(overrides)
	return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Fake Redis for unit tests — simulates Lua script behavior in-memory
# ---------------------------------------------------------------------------


class _FakeTokenBucket:
	"""Python implementation of the rate limit Lua script for unit testing."""

	def __init__(self):
		self._buckets: dict[str, dict[str, float]] = {}

	async def __call__(self, keys, args):
		key = keys[0]
		max_tokens = float(args[0])
		refill_rate = float(args[1])
		now_ms = float(args[2])

		bucket = self._buckets.get(key)
		if bucket is None:
			self._buckets[key] = {"tokens": max_tokens - 1, "last_ms": now_ms}
			return 1

		elapsed_ms = now_ms - bucket["last_ms"]
		refill = elapsed_ms * refill_rate / 1000.0
		tokens = min(max_tokens, bucket["tokens"] + refill)

		if tokens < 1:
			bucket["last_ms"] = now_ms
			return 0

		tokens -= 1
		bucket["tokens"] = tokens
		bucket["last_ms"] = now_ms
		return 1


class _FakeAcceptTap:
	"""Python implementation of the accept tap Lua script for unit testing."""

	def __init__(self):
		self._room_sec: dict[str, int] = {}
		self._win_hashes: dict[str, dict[str, str]] = {}

	async def __call__(self, keys, args):
		room_sec_key = keys[0]
		win_key = keys[1]
		reaction = args[0]
		room_cap = int(args[1])

		# INCR room_sec
		count = self._room_sec.get(room_sec_key, 0) + 1
		self._room_sec[room_sec_key] = count

		if win_key not in self._win_hashes:
			self._win_hashes[win_key] = {}

		if count > room_cap:
			self._win_hashes[win_key]["_degraded"] = "1"
			return -1

		# HINCRBY reaction
		current = int(self._win_hashes[win_key].get(reaction, "0"))
		self._win_hashes[win_key][reaction] = str(current + 1)
		return 1


class _FakeRedis:
	"""Fake Redis with in-memory scripts and basic commands for unit tests."""

	def __init__(self):
		self._rl_script = _FakeTokenBucket()
		self._tap_script = _FakeAcceptTap()
		self._store: dict[str, str] = {}
		self._hashes: dict[str, dict[str, str]] = {}
		self._published: list[tuple[str, str]] = []
		self._script_registry: list = []

	def register_script(self, script_text):
		if "HMGET" in script_text and "tokens" in script_text:
			return self._rl_script
		return self._tap_script

	async def set(self, key, value, nx=False, px=None, ex=None):
		if nx and key in self._store:
			return None
		self._store[key] = value
		return True

	async def hgetall(self, key):
		# Return from tap script's win_hashes if present
		if key in self._tap_script._win_hashes:
			return dict(self._tap_script._win_hashes[key])
		return dict(self._hashes.get(key, {}))

	async def delete(self, *keys):
		for key in keys:
			self._store.pop(key, None)
			self._hashes.pop(key, None)
			self._tap_script._win_hashes.pop(key, None)

	async def publish(self, channel, message):
		self._published.append((channel, message))
		return 1

	def pubsub(self):
		return _FakePubSub()


class _FakePubSub:
	"""Minimal fake pubsub for unit tests."""

	def __init__(self):
		self._subscriptions: set[str] = set()

	async def subscribe(self, channel):
		self._subscriptions.add(channel)

	async def unsubscribe(self, channel):
		self._subscriptions.discard(channel)

	async def listen(self):
		# Block forever (tests don't use subscriber loop)
		while True:
			await asyncio.sleep(3600)
			yield {"type": "message", "data": b"{}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def broadcast_mock():
	return AsyncMock(return_value=1)


@pytest.fixture
def settings():
	return _make_settings()


@pytest.fixture
def fake_redis():
	return _FakeRedis()


@pytest.fixture
def engine(settings, broadcast_mock, fake_redis):
	eng = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)
	yield eng
	# Cleanup: nothing to cancel — no per-room tasks


@pytest.fixture
def engine_no_redis(settings, broadcast_mock):
	"""Engine without Redis — all taps fail-closed."""
	return ReactionEngine(settings=settings, broadcast=broadcast_mock)


# ---------------------------------------------------------------------------
# Tap acceptance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAcceptTap:
	"""Tap acceptance — valid/invalid reaction types."""

	async def test_valid_reactions_accepted(self, engine):
		for reaction in ("heart", "fire", "clap"):
			result = await engine.accept_tap("E1", "P1", reaction)
			assert result is True

	async def test_invalid_reaction_rejected(self, engine):
		result = await engine.accept_tap("E1", "P1", "thumbsup")
		assert result is False

	async def test_empty_reaction_rejected(self, engine):
		result = await engine.accept_tap("E1", "P1", "")
		assert result is False

	async def test_case_sensitive(self, engine):
		"""Reaction types are case-sensitive — 'Heart' is invalid."""
		result = await engine.accept_tap("E1", "P1", "Heart")
		assert result is False

	async def test_valid_reactions_set(self):
		assert VALID_REACTIONS == {"heart", "fire", "clap"}


# ---------------------------------------------------------------------------
# Redis counter increment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRedisCounterIncrement:
	"""Redis counter increment — verify Lua script effects."""

	async def test_single_tap_increments_redis_counter(self, engine, fake_redis):
		await engine.accept_tap("E1", "P1", "heart")
		# Verify the tap script recorded the counter
		win_hashes = fake_redis._tap_script._win_hashes
		assert any("heart" in h for h in win_hashes.values())

	async def test_multiple_taps_aggregate(self, engine, fake_redis):
		for _ in range(5):
			await engine.accept_tap("E1", "P1", "heart")
		# Find the window hash with heart counts
		win_hashes = fake_redis._tap_script._win_hashes
		for h in win_hashes.values():
			if "heart" in h:
				assert int(h["heart"]) == 5
				break
		else:
			pytest.fail("No window hash with heart counter found")

	async def test_multiple_reaction_types(self, engine, fake_redis):
		await engine.accept_tap("E1", "P1", "heart")
		await engine.accept_tap("E1", "P2", "fire")
		await engine.accept_tap("E1", "P3", "clap")
		win_hashes = fake_redis._tap_script._win_hashes
		for h in win_hashes.values():
			if "heart" in h:
				assert int(h["heart"]) == 1
				assert int(h["fire"]) == 1
				assert int(h["clap"]) == 1
				break

	async def test_rejected_tap_no_counter(self, engine, fake_redis):
		await engine.accept_tap("E1", "P1", "invalid")
		# No window hash should be created for invalid taps
		assert len(fake_redis._tap_script._win_hashes) == 0

	async def test_event_registered_in_active_events(self, engine):
		await engine.accept_tap("E1", "P1", "heart")
		assert "E1" in engine._active_events

	async def test_no_redis_returns_false(self, engine_no_redis):
		"""Without Redis, accept_tap returns False (fail-closed for counters)."""
		result = await engine_no_redis.accept_tap("E1", "P1", "heart")
		assert result is False


# ---------------------------------------------------------------------------
# Flush and burst message structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFlushAndBurst:
	"""Global flush loop, burst message schema."""

	async def test_flush_reads_and_clears_counters(self, engine, fake_redis, broadcast_mock):
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			await engine.accept_tap("E1", "P1", "heart")
			await engine.accept_tap("E1", "P1", "heart")

			now_ms = int(1000.0 * 1000)
			window_id = now_ms // engine._settings.reaction_flush_interval_ms

			await engine._try_flush_window("E1", window_id)

		# Burst was published to Redis
		assert len(fake_redis._published) == 1
		channel, payload = fake_redis._published[0]
		burst = json.loads(payload)
		assert burst["reactions"]["heart"]["count"] == 2

		# Window counters were cleared
		win_key = lc_reaction_win_key("E1", window_id)
		assert win_key not in fake_redis._tap_script._win_hashes

	async def test_burst_message_structure(self, engine, fake_redis, broadcast_mock):
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			await engine.accept_tap("E1", "P1", "heart")
			window_id = int(1000.0 * 1000) // engine._settings.reaction_flush_interval_ms

		await engine._try_flush_window("E1", window_id)

		# Burst was published to Redis
		assert len(fake_redis._published) >= 1
		channel, payload = fake_redis._published[-1]
		import json

		msg = json.loads(payload)
		assert msg["type"] == "waiting_room_reaction_burst"
		assert msg["room_id"] == "E1"
		assert msg["reactions"]["heart"]["count"] >= 1
		assert msg["reactions"]["heart"]["intensity"] == "low"
		assert msg["degraded"] is False
		assert msg["window_duration_ms"] == 50
		assert "server_ts" in msg
		assert msg["server_ts"].endswith("Z")

	async def test_only_nonzero_reactions_in_burst(self, engine, fake_redis):
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			await engine.accept_tap("E1", "P1", "heart")
			window_id = int(1000.0 * 1000) // engine._settings.reaction_flush_interval_ms

		await engine._try_flush_window("E1", window_id)

		import json

		msg = json.loads(fake_redis._published[-1][1])
		assert "heart" in msg["reactions"]
		assert "fire" not in msg["reactions"]
		assert "clap" not in msg["reactions"]

	async def test_empty_window_no_publish(self, engine, fake_redis):
		"""Empty window produces no publish."""
		# No taps — flush should produce nothing
		await engine._try_flush_window("E1", 99999)
		assert len(fake_redis._published) == 0


# ---------------------------------------------------------------------------
# Empty window suppression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEmptyWindowSuppression:
	"""Empty windows produce no broadcast."""

	async def test_no_taps_no_broadcast(self, engine, fake_redis, broadcast_mock):
		# Don't send any taps — flush should be silent
		await engine._try_flush_window("E1", 99999)
		assert len(fake_redis._published) == 0
		broadcast_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Intensity tiers
# ---------------------------------------------------------------------------


class TestIntensityTiers:
	"""Intensity tier thresholds: low (1-10), medium (11-50), high (51+)."""

	def test_low_tier(self):
		for count in (1, 5, 10):
			assert _compute_intensity(count) == "low"

	def test_medium_tier(self):
		for count in (11, 25, 50):
			assert _compute_intensity(count) == "medium"

	def test_high_tier(self):
		for count in (51, 100, 1000):
			assert _compute_intensity(count) == "high"

	def test_boundary_low_medium(self):
		assert _compute_intensity(10) == "low"
		assert _compute_intensity(11) == "medium"

	def test_boundary_medium_high(self):
		assert _compute_intensity(50) == "medium"
		assert _compute_intensity(51) == "high"


@pytest.mark.asyncio
class TestIntensityInBurst:
	"""Intensity tiers reflected in burst messages."""

	async def test_medium_intensity_burst(self, broadcast_mock):
		# Use many players to bypass per-user rate limit
		fake_redis = _FakeRedis()
		settings = _make_settings(reaction_room_cap_per_sec=1000)
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			for i in range(15):
				await engine.accept_tap("E1", f"P{i}", "heart")
			window_id = int(1000.0 * 1000) // settings.reaction_flush_interval_ms

		await engine._try_flush_window("E1", window_id)

		import json

		msg = json.loads(fake_redis._published[-1][1])
		assert msg["reactions"]["heart"]["count"] == 15
		assert msg["reactions"]["heart"]["intensity"] == "medium"

	async def test_high_intensity_burst(self, broadcast_mock):
		# Use many players to bypass per-user rate limit
		fake_redis = _FakeRedis()
		settings = _make_settings(reaction_room_cap_per_sec=1000)
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			for i in range(60):
				await engine.accept_tap("E1", f"P{i}", "fire")
			window_id = int(1000.0 * 1000) // settings.reaction_flush_interval_ms

		await engine._try_flush_window("E1", window_id)

		import json

		msg = json.loads(fake_redis._published[-1][1])
		assert msg["reactions"]["fire"]["count"] == 60
		assert msg["reactions"]["fire"]["intensity"] == "high"


# ---------------------------------------------------------------------------
# Feature flag (reaction_enabled)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFeatureFlag:
	"""reaction_enabled feature flag bypass."""

	async def test_disabled_flag_rejects_tap(self, broadcast_mock, fake_redis):
		settings = _make_settings(reaction_enabled=False)
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)

		result = await engine.accept_tap("E1", "P1", "heart")
		assert result is False
		assert "E1" not in engine._active_events

	async def test_disabled_flag_no_flush(self, broadcast_mock, fake_redis):
		settings = _make_settings(reaction_enabled=False)
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)

		await engine.accept_tap("E1", "P1", "heart")
		assert len(fake_redis._published) == 0

	async def test_enabled_flag_accepts_tap(self, engine):
		result = await engine.accept_tap("E1", "P1", "heart")
		assert result is True


# ---------------------------------------------------------------------------
# T008 [US2]: Per-user rate limiting (token bucket)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine_with_rl(settings, broadcast_mock, fake_redis):
	"""Engine with rate limiting enabled via fake Redis."""
	eng = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)
	return eng


@pytest.mark.asyncio
class TestRateLimiting:
	"""T008 [US2]: Token bucket rate limiting unit tests."""

	async def test_burst_allowance_accepted(self, engine_with_rl):
		"""First 6 rapid taps all accepted (burst_allowance=6)."""
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			for i in range(6):
				result = await engine_with_rl.accept_tap("E1", "P1", "heart")
				assert result is True, f"Tap {i + 1} should be accepted"

	async def test_burst_exceeded_rejected(self, engine_with_rl):
		"""7th rapid tap rejected after burst depleted."""
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			for _ in range(6):
				await engine_with_rl.accept_tap("E1", "P1", "heart")
			result = await engine_with_rl.accept_tap("E1", "P1", "heart")
			assert result is False

	async def test_sustained_rate_after_burst(self, engine_with_rl):
		"""After burst depleted, 1 second refills 3 tokens (sustained_rate=3)."""
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			# Deplete burst at T=1000
			mock_time.time.return_value = 1000.0
			for _ in range(6):
				await engine_with_rl.accept_tap("E1", "P1", "heart")

			# Advance 1 second → 3 tokens refilled
			mock_time.time.return_value = 1001.0
			accepted = 0
			for _ in range(5):
				if await engine_with_rl.accept_tap("E1", "P1", "heart"):
					accepted += 1
			assert accepted == 3

	async def test_token_refill_after_long_pause(self, engine_with_rl):
		"""After depletion, waiting 2s refills to full burst (6)."""
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			for _ in range(6):
				await engine_with_rl.accept_tap("E1", "P1", "heart")

			# Advance 2 seconds → 6 tokens refilled (capped at max)
			mock_time.time.return_value = 1002.0
			for i in range(6):
				result = await engine_with_rl.accept_tap("E1", "P1", "heart")
				assert result is True, f"Tap {i + 1} should be accepted after refill"

	async def test_rejected_tap_no_counter_increment(self, engine_with_rl, fake_redis):
		"""Rate-limited taps don't increment Redis counters."""
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			for _ in range(6):
				await engine_with_rl.accept_tap("E1", "P1", "heart")

			# Count hearts in window hash after burst
			win_hashes = fake_redis._tap_script._win_hashes
			count_after_burst = 0
			for h in win_hashes.values():
				if "heart" in h:
					count_after_burst = int(h["heart"])

			# 7th tap rejected — counter stays the same
			await engine_with_rl.accept_tap("E1", "P1", "heart")
			count_after_reject = 0
			for h in win_hashes.values():
				if "heart" in h:
					count_after_reject = int(h["heart"])
			assert count_after_reject == count_after_burst

	async def test_no_error_on_rejection(self, engine_with_rl):
		"""Rejected taps return False, no exceptions raised."""
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			for _ in range(6):
				await engine_with_rl.accept_tap("E1", "P1", "heart")
			# No exception, just False
			result = await engine_with_rl.accept_tap("E1", "P1", "heart")
			assert result is False

	async def test_rate_limit_per_user(self, engine_with_rl):
		"""Rate limits are per-user — different users have separate buckets."""
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			# Deplete P1's bucket
			for _ in range(6):
				await engine_with_rl.accept_tap("E1", "P1", "heart")
			assert await engine_with_rl.accept_tap("E1", "P1", "heart") is False
			# P2 still has full bucket
			assert await engine_with_rl.accept_tap("E1", "P2", "heart") is True

	async def test_no_disconnect_on_rate_limit(self, engine_with_rl):
		"""Rate limiting doesn't break engine state or other users."""
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			for _ in range(6):
				await engine_with_rl.accept_tap("E1", "P1", "heart")
			# Rejected tap
			await engine_with_rl.accept_tap("E1", "P1", "heart")
			# Event still active and functional
			assert "E1" in engine_with_rl._active_events
			# Other users can still tap
			assert await engine_with_rl.accept_tap("E1", "P2", "fire") is True

	async def test_no_redis_bypasses_rate_limit_but_tap_fails(self, engine_no_redis):
		"""Without Redis, rate limit is bypassed but tap fails (no counters)."""
		result = await engine_no_redis.accept_tap("E1", "P1", "heart")
		assert result is False


# ---------------------------------------------------------------------------
# T012 [US4]: stop_room — immediate cutoff on room transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStopRoom:
	"""T012 [US4]: stop_room() marks room stopped, removes from active events."""

	async def test_active_event_removed(self, engine):
		await engine.accept_tap("E1", "P1", "heart")
		assert "E1" in engine._active_events

		engine.stop_room("E1")
		assert "E1" not in engine._active_events

	async def test_accept_tap_rejected_after_stop(self, engine):
		"""accept_tap returns False for a stopped room."""
		await engine.accept_tap("E1", "P1", "heart")
		engine.stop_room("E1")

		result = await engine.accept_tap("E1", "P1", "heart")
		assert result is False

	async def test_stop_nonexistent_room_no_error(self, engine):
		"""Stopping a room that doesn't exist is a no-op."""
		engine.stop_room("NONEXISTENT")  # should not raise

	async def test_stop_idempotent(self, engine):
		"""Calling stop_room twice is safe."""
		await engine.accept_tap("E1", "P1", "heart")
		engine.stop_room("E1")
		engine.stop_room("E1")  # second call — no error

	async def test_other_rooms_unaffected(self, engine):
		"""Stopping one room doesn't affect other rooms."""
		await engine.accept_tap("E1", "P1", "heart")
		await engine.accept_tap("E2", "P1", "fire")

		engine.stop_room("E1")
		assert "E1" not in engine._active_events
		assert "E2" in engine._active_events

	async def test_stopped_room_in_stopped_set(self, engine):
		engine.stop_room("E1")
		assert "E1" in engine._stopped_rooms


# ---------------------------------------------------------------------------
# T016 [US3]: Room-level degradation under load
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRoomLevelCap:
	"""T016 [US3]: per-second counter reset, taps dropped above cap, degraded flag."""

	async def test_taps_below_cap_accepted(self, broadcast_mock, fake_redis):
		"""Taps under the room cap are all accepted."""
		settings = _make_settings(reaction_room_cap_per_sec=10)
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			for i in range(10):
				result = await engine.accept_tap("E1", f"P{i}", "heart")
				assert result is True, f"Tap {i + 1} should be accepted (under cap)"

	async def test_taps_above_cap_rejected(self, broadcast_mock):
		"""Taps exceeding the room cap are silently dropped."""
		settings = _make_settings(reaction_room_cap_per_sec=5)
		fake_redis = _FakeRedis()
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			accepted = 0
			for i in range(10):
				if await engine.accept_tap("E1", f"P{i}", "heart"):
					accepted += 1
			assert accepted == 5, f"Expected exactly 5 accepted, got {accepted}"

	async def test_counter_resets_on_new_second(self, broadcast_mock):
		"""Room tap counter resets when a new second begins (different room_sec key)."""
		settings = _make_settings(reaction_room_cap_per_sec=3)
		fake_redis = _FakeRedis()
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			# Fill cap in second 1000
			mock_time.time.return_value = 1000.0
			for i in range(3):
				await engine.accept_tap("E1", f"P{i}", "heart")
			# 4th tap in same second — rejected
			result = await engine.accept_tap("E1", "P99", "heart")
			assert result is False

			# New second — different room_sec key, counter resets, tap accepted
			mock_time.time.return_value = 1001.0
			result = await engine.accept_tap("E1", "P99", "heart")
			assert result is True

	async def test_degraded_flag_set_in_burst(self, broadcast_mock):
		"""When room cap is hit, burst message has degraded=True."""
		settings = _make_settings(reaction_room_cap_per_sec=3, reaction_flush_interval_ms=50)
		fake_redis = _FakeRedis()
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)

		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			# Accept 3 (at cap)
			for i in range(3):
				await engine.accept_tap("E1", f"P{i}", "heart")
			# 4th rejected — cap exceeded, _degraded flag set
			await engine.accept_tap("E1", "P99", "heart")
			window_id = int(1000.0 * 1000) // settings.reaction_flush_interval_ms

		await engine._try_flush_window("E1", window_id)

		import json

		assert len(fake_redis._published) >= 1
		msg = json.loads(fake_redis._published[-1][1])
		assert msg["degraded"] is True

	async def test_degraded_clears_when_volume_drops(self, broadcast_mock):
		"""degraded returns to False when volume drops below cap."""
		settings = _make_settings(reaction_room_cap_per_sec=3, reaction_flush_interval_ms=50)
		fake_redis = _FakeRedis()
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)

		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			# First window: exceed cap → degraded=True
			mock_time.time.return_value = 1000.0
			for i in range(5):
				await engine.accept_tap("E1", f"P{i}", "heart")
			window_id_1 = int(1000.0 * 1000) // settings.reaction_flush_interval_ms

		await engine._try_flush_window("E1", window_id_1)

		import json

		msg1 = json.loads(fake_redis._published[-1][1])
		assert msg1["degraded"] is True

		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			# Second window: below cap → degraded=False
			mock_time.time.return_value = 1002.0
			await engine.accept_tap("E1", "P0", "heart")
			window_id_2 = int(1002.0 * 1000) // settings.reaction_flush_interval_ms

		await engine._try_flush_window("E1", window_id_2)

		msg2 = json.loads(fake_redis._published[-1][1])
		assert msg2["degraded"] is False

	async def test_cap_is_per_room(self, broadcast_mock):
		"""Room cap is enforced independently per room."""
		settings = _make_settings(reaction_room_cap_per_sec=2)
		fake_redis = _FakeRedis()
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			# Fill E1 cap
			await engine.accept_tap("E1", "P1", "heart")
			await engine.accept_tap("E1", "P2", "heart")
			assert await engine.accept_tap("E1", "P3", "heart") is False

			# E2 still has capacity (different room_sec key)
			assert await engine.accept_tap("E2", "P1", "heart") is True

	async def test_cap_counts_all_reaction_types(self, broadcast_mock):
		"""Room cap counts all reaction types together, not per-type."""
		settings = _make_settings(reaction_room_cap_per_sec=3)
		fake_redis = _FakeRedis()
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)
		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			await engine.accept_tap("E1", "P1", "heart")
			await engine.accept_tap("E1", "P2", "fire")
			await engine.accept_tap("E1", "P3", "clap")
			# Cap reached — next tap of any type rejected
			result = await engine.accept_tap("E1", "P4", "heart")
			assert result is False

	async def test_burst_counts_reflect_capped_volume(self, broadcast_mock):
		"""Burst message counts reflect only accepted taps, not total attempts."""
		settings = _make_settings(reaction_room_cap_per_sec=3, reaction_flush_interval_ms=50)
		fake_redis = _FakeRedis()
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)

		with patch("fastapi_app.services.waiting_room_reactions.time") as mock_time:
			mock_time.time.return_value = 1000.0
			# Send 10 taps, only 3 should be accepted
			for i in range(10):
				await engine.accept_tap("E1", f"P{i}", "heart")
			window_id = int(1000.0 * 1000) // settings.reaction_flush_interval_ms

		await engine._try_flush_window("E1", window_id)

		import json

		msg = json.loads(fake_redis._published[-1][1])
		assert msg["reactions"]["heart"]["count"] == 3


# ---------------------------------------------------------------------------
# Dynamic subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDynamicSubscription:
	"""subscribe_event/unsubscribe_event channel management."""

	async def test_subscribe_event(self, engine, fake_redis):
		engine._pubsub = fake_redis.pubsub()
		await engine.subscribe_event("E1")
		assert "memora:lc_burst:E1" in engine._subscribed_channels
		assert "memora:lc_burst:E1" in engine._pubsub._subscriptions

	async def test_subscribe_idempotent(self, engine, fake_redis):
		engine._pubsub = fake_redis.pubsub()
		await engine.subscribe_event("E1")
		await engine.subscribe_event("E1")
		assert len(engine._subscribed_channels) == 1

	async def test_unsubscribe_event(self, engine, fake_redis):
		engine._pubsub = fake_redis.pubsub()
		await engine.subscribe_event("E1")
		await engine.unsubscribe_event("E1")
		assert "memora:lc_burst:E1" not in engine._subscribed_channels
		assert "memora:lc_burst:E1" not in engine._pubsub._subscriptions

	async def test_unsubscribe_nonexistent_no_error(self, engine, fake_redis):
		engine._pubsub = fake_redis.pubsub()
		await engine.unsubscribe_event("E1")  # no-op

	async def test_no_pubsub_no_error(self, engine):
		"""subscribe/unsubscribe with no pubsub object is a no-op."""
		engine._pubsub = None
		await engine.subscribe_event("E1")
		await engine.unsubscribe_event("E1")


# ---------------------------------------------------------------------------
# T020 [US5]: Error isolation — resilience to backend failure
# ---------------------------------------------------------------------------


class _ErrorRedis:
	"""Fake Redis that raises RedisError on any script call."""

	def register_script(self, script_text):
		return self._error_script

	@staticmethod
	async def _error_script(keys, args):
		from redis.exceptions import RedisError

		raise RedisError("simulated Redis failure")


@pytest.fixture
def error_redis():
	return _ErrorRedis()


@pytest.fixture
def engine_with_error_redis(settings, broadcast_mock, error_redis):
	"""Engine with Redis that always raises errors on scripts."""
	return ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=error_redis)


@pytest.mark.asyncio
class TestErrorIsolation:
	"""T020 [US5]: Error isolation — Redis failure, engine error, flush loop error."""

	async def test_redis_error_in_accept_tap_returns_false(self, engine_with_error_redis):
		"""Redis error in accept_tap → fail-closed, returns False."""
		result = await engine_with_error_redis.accept_tap("E1", "P1", "heart")
		assert result is False

	async def test_redis_error_no_exception_propagated(self, engine_with_error_redis):
		"""Redis error does not propagate as exception."""
		# Should not raise
		for _ in range(5):
			result = await engine_with_error_redis.accept_tap("E1", "P1", "heart")
			assert result is False

	async def test_accept_tap_internal_error_returns_false(self, broadcast_mock, fake_redis):
		"""Internal error in accept_tap outer boundary → silently returns False."""
		settings = _make_settings()
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)

		# Monkeypatch check_rate_limit to raise a non-Redis error
		async def _explode(*args, **kwargs):
			raise RuntimeError("unexpected internal error")

		engine.check_rate_limit = _explode
		result = await engine.accept_tap("E1", "P1", "heart")
		assert result is False

	async def test_flush_loop_redis_error_continues(self, broadcast_mock, fake_redis):
		"""Flush loop survives errors in _try_flush_window per-event."""
		settings = _make_settings(reaction_flush_interval_ms=50)
		engine = ReactionEngine(settings=settings, broadcast=broadcast_mock, redis=fake_redis)

		# Make hgetall raise for one specific key
		original_hgetall = fake_redis.hgetall

		async def _broken_hgetall(key):
			if "E_BAD" in key:
				raise RuntimeError("simulated flush error")
			return await original_hgetall(key)

		fake_redis.hgetall = _broken_hgetall

		# _try_flush_window propagates the error (flush loop catches it)
		with pytest.raises(RuntimeError, match="simulated flush error"):
			await engine._try_flush_window("E_BAD", 12345)
		# No publish occurred
		assert len(fake_redis._published) == 0

	async def test_stopped_room_after_error_still_rejects(self, engine_with_error_redis):
		"""After error + stop_room, taps are still rejected."""
		await engine_with_error_redis.accept_tap("E1", "P1", "heart")
		engine_with_error_redis.stop_room("E1")
		result = await engine_with_error_redis.accept_tap("E1", "P1", "heart")
		assert result is False
