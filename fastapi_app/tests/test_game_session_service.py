# Copyright (c) 2026, corex and contributors
"""Tests for GameSessionService — session lifecycle with Lua scripts."""

import json

import pytest
import redis.asyncio as redis

from fastapi_app.core.constants import DIRTY_PROGRESS_KEY, GAME_SESSION_TTL, INTERACTION_BUFFER_KEY
from fastapi_app.core.redis_keys import game_session_key, progress_key
from fastapi_app.models.game_session import GameSession
from fastapi_app.services.game_session import GameSessionService

# Test constants
TEST_USER = "USER-001"
TEST_SUBJECT = "MATH-G5"
TEST_VERSION = 1
TEST_LESSON = "LESSON-001"
TEST_DEVICE = "device-abc"


@pytest.fixture
async def game_session_service(redis_client: redis.Redis, test_prefix: str) -> GameSessionService:
	"""Create GameSessionService with test prefix for isolation."""
	return GameSessionService(redis_client)


@pytest.fixture(autouse=True)
async def cleanup_global_keys(redis_client: redis.Redis):
	"""Clean global keys (not prefixed) that GameSessionService uses globally."""
	yield
	# Remove dirty progress member
	await redis_client.srem(DIRTY_PROGRESS_KEY, f"{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}")
	# Drain interaction buffer (pop all entries)
	while await redis_client.lpop(INTERACTION_BUFFER_KEY):
		pass


class TestStartSession:
	"""Test session startup with force-close atomicity."""

	async def test_tc_gs_01_start_creates_hash_with_ttl(
		self, game_session_service: GameSessionService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-GS-01: Start session creates Redis hash with TTL=3600."""
		session_id = await game_session_service.start_session(
			user_id=TEST_USER,
			lesson_id=TEST_LESSON,
			subject_id=TEST_SUBJECT,
			device_id=TEST_DEVICE,
		)

		# Verify returned session_id is a UUID string
		assert session_id, "session_id should not be empty"
		assert len(session_id) > 20, "session_id should be UUID-like"

		# Verify Redis hash exists with 5 fields
		key = game_session_key(TEST_USER)
		hash_data = await redis_client.hgetall(key)
		assert len(hash_data) == 5, "Session hash should have 5 fields"
		assert hash_data.get(b"session_id") or hash_data.get("session_id"), "session_id field missing"
		assert hash_data.get(b"lesson_id") or hash_data.get("lesson_id"), "lesson_id field missing"

		# Verify TTL is set
		ttl = await redis_client.ttl(key)
		assert ttl == GAME_SESSION_TTL, f"TTL should be {GAME_SESSION_TTL}, got {ttl}"

	async def test_tc_gs_02_start_force_closes_existing(
		self, game_session_service: GameSessionService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-GS-02: Start session force-closes existing session atomically."""
		# Create first session
		session_id_a = await game_session_service.start_session(
			user_id=TEST_USER,
			lesson_id="LESSON-A",
			subject_id=TEST_SUBJECT,
		)

		# Create second session (force-closes first)
		session_id_b = await game_session_service.start_session(
			user_id=TEST_USER,
			lesson_id="LESSON-B",
			subject_id=TEST_SUBJECT,
		)

		# Verify session IDs are different
		assert session_id_a != session_id_b, "Session IDs should differ for new session"

		# Verify only second session exists
		key = game_session_key(TEST_USER)
		hash_data = await redis_client.hgetall(key)
		stored_session_id = hash_data.get(b"session_id") or hash_data.get("session_id")
		stored_session_id_str = (
			stored_session_id.decode() if isinstance(stored_session_id, bytes) else stored_session_id
		)
		assert stored_session_id_str == session_id_b, "Only second session should exist"

	async def test_tc_gs_03_get_returns_game_session(self, game_session_service: GameSessionService):
		"""TC-GS-03: Get active session returns GameSession model."""
		# Start a session
		session_id = await game_session_service.start_session(
			user_id=TEST_USER,
			lesson_id=TEST_LESSON,
			subject_id=TEST_SUBJECT,
			device_id=TEST_DEVICE,
		)

		# Get the session
		session = await game_session_service.get_active_session(TEST_USER)

		assert session is not None, "Session should exist"
		assert isinstance(session, GameSession), "Should return GameSession model"
		assert session.session_id == session_id, "session_id should match"
		assert session.lesson_id == TEST_LESSON, "lesson_id should match"
		assert session.subject_id == TEST_SUBJECT, "subject_id should match"
		assert session.device_id == TEST_DEVICE, "device_id should match"

	async def test_tc_gs_04_get_returns_none_when_no_session(self, game_session_service: GameSessionService):
		"""TC-GS-04: Get active session returns None when no session exists."""
		session = await game_session_service.get_active_session("USER-NONEXISTENT")
		assert session is None, "Should return None for non-existent user"


class TestCompleteSession:
	"""Test session completion with progress tracking."""

	async def test_tc_gs_05_end_returns_data_and_deletes(
		self, game_session_service: GameSessionService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-GS-05: End session returns data and deletes hash."""
		# Start a session
		session_id = await game_session_service.start_session(
			user_id=TEST_USER,
			lesson_id=TEST_LESSON,
			subject_id=TEST_SUBJECT,
		)

		# End the session
		ended_session = await game_session_service.end_session(TEST_USER)

		assert ended_session is not None, "Should return session data"
		assert ended_session.session_id == session_id, "Returned session should match started session"
		assert ended_session.lesson_id == TEST_LESSON, "lesson_id should be preserved"

		# Verify hash is deleted from Redis
		key = game_session_key(TEST_USER)
		exists = await redis_client.exists(key)
		assert exists == 0, "Session hash should be deleted"

	async def test_tc_gs_06_complete_sets_progress_bit_first_completion(
		self, game_session_service: GameSessionService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-GS-06: Complete session sets progress bit (first completion)."""
		# Start a session
		await game_session_service.start_session(
			user_id=TEST_USER,
			lesson_id=TEST_LESSON,
			subject_id=TEST_SUBJECT,
		)

		# Complete the session
		success, is_replay, session = await game_session_service.complete_session(
			user_id=TEST_USER,
			bit_index=5,
			subject_id=TEST_SUBJECT,
			version=TEST_VERSION,
			interaction_jsons=['{"stage":"S1"}'],
		)

		# Verify success
		assert success is True, "Completion should succeed"
		assert is_replay is False, "First completion should not be marked as replay"
		assert session is not None, "Should return session data"

		# Verify progress bit is set
		pkey = progress_key(TEST_USER, TEST_SUBJECT, TEST_VERSION)
		bit_value = await redis_client.getbit(pkey, 5)
		assert bit_value == 1, "Progress bit at index 5 should be set"

		# Verify dirty set contains member
		is_dirty = await redis_client.sismember(
			DIRTY_PROGRESS_KEY, f"{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}"
		)
		assert is_dirty == 1, "Dirty set should contain progress member"

		# Verify interaction buffer received the JSON
		interactions = await redis_client.lrange(INTERACTION_BUFFER_KEY, 0, -1)
		assert len(interactions) >= 1, "Interaction buffer should contain at least one entry"

	async def test_tc_gs_07_complete_detects_replay(
		self, game_session_service: GameSessionService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-GS-07: Complete session detects replay."""
		# First completion
		await game_session_service.start_session(
			user_id=TEST_USER,
			lesson_id=TEST_LESSON,
			subject_id=TEST_SUBJECT,
		)
		success1, is_replay1, _ = await game_session_service.complete_session(
			user_id=TEST_USER,
			bit_index=5,
			subject_id=TEST_SUBJECT,
			version=TEST_VERSION,
			interaction_jsons=[],
		)
		assert is_replay1 is False, "First completion should not be replay"

		# Clear the dirty set and interaction buffer for the next test
		await redis_client.srem(DIRTY_PROGRESS_KEY, f"{TEST_USER}:{TEST_SUBJECT}:v{TEST_VERSION}")
		while await redis_client.lpop(INTERACTION_BUFFER_KEY):
			pass

		# Start a new session for the same lesson
		await game_session_service.start_session(
			user_id=TEST_USER,
			lesson_id=TEST_LESSON,
			subject_id=TEST_SUBJECT,
		)

		# Second completion (replay because bit was already set)
		success2, is_replay2, _ = await game_session_service.complete_session(
			user_id=TEST_USER,
			bit_index=5,  # Same bit index
			subject_id=TEST_SUBJECT,
			version=TEST_VERSION,
			interaction_jsons=[],
		)
		assert is_replay2 is True, "Second completion should be marked as replay"

	async def test_tc_gs_08_complete_with_interaction_buffer(
		self, game_session_service: GameSessionService, redis_client: redis.Redis
	):
		"""TC-GS-08: Complete session pushes interactions to buffer."""
		# Start a session
		await game_session_service.start_session(
			user_id=TEST_USER,
			lesson_id=TEST_LESSON,
			subject_id=TEST_SUBJECT,
		)

		# Complete with multiple interactions
		interaction_jsons = ['{"stage":"S1"}', '{"stage":"S2"}']
		success, is_replay, session = await game_session_service.complete_session(
			user_id=TEST_USER,
			bit_index=5,
			subject_id=TEST_SUBJECT,
			version=TEST_VERSION,
			interaction_jsons=interaction_jsons,
		)

		assert success is True, "Completion should succeed"

		# Verify both interactions are in the buffer
		interactions = await redis_client.lrange(INTERACTION_BUFFER_KEY, 0, -1)
		assert len(interactions) == 2, "Should have 2 interactions in buffer"

		# Verify interaction content
		interaction_strs = [i.decode() if isinstance(i, bytes) else i for i in interactions]
		assert '{"stage":"S1"}' in interaction_strs, "S1 interaction should be in buffer"
		assert '{"stage":"S2"}' in interaction_strs, "S2 interaction should be in buffer"
