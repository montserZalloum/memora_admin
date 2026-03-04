# Copyright (c) 2026, corex and contributors
"""Tests for SessionService — authentication session management with family_id."""

import json

import pytest
import redis.asyncio as redis

from fastapi_app.core.redis_keys import session_key
from fastapi_app.services.session import SessionService

# Test constants
TEST_USER = "USER-001"
TEST_PLAN = "PLAN-001"


@pytest.fixture
async def session_service(redis_client: redis.Redis, test_prefix: str) -> SessionService:
	"""Create SessionService with test prefix for isolation."""
	return SessionService(redis_client)


class TestSessionManagement:
	"""Test session management with family_id supersession."""

	async def test_tc_ss_01_create_stores_json_with_ttl(
		self, session_service: SessionService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-SS-01: Create stores JSON {fid, plan} with 30-day TTL."""
		family_id = await session_service.create_session(TEST_USER, TEST_PLAN, ttl_days=30)

		# Verify family_id is returned
		assert family_id, "family_id should not be empty"
		assert len(family_id) == 36, "family_id should be UUID (36 chars)"

		# Verify Redis stores JSON
		key = session_key(TEST_USER)
		raw = await redis_client.get(key)
		assert raw is not None, "Session key should exist"

		data = json.loads(raw) if isinstance(raw, bytes) else json.loads(raw)
		assert data["fid"] == family_id, "fid should match returned family_id"
		assert data["plan"] == TEST_PLAN, "plan should match"

		# Verify TTL is approximately 30 days (2,592,000 seconds)
		ttl = await redis_client.ttl(key)
		assert ttl > 2591000 and ttl <= 2592000, f"TTL should be ~2,592,000s (30 days), got {ttl}"

	async def test_tc_ss_02_validate_matching_family_id_returns_true_and_plan(
		self, session_service: SessionService
	):
		"""TC-SS-02: Validate matching family_id returns (True, plan)."""
		family_id = await session_service.create_session(TEST_USER, TEST_PLAN)

		is_valid, plan, season = await session_service.validate_session(TEST_USER, family_id)

		assert is_valid is True, "Should return True for matching family_id"
		assert plan == TEST_PLAN, "Should return correct plan_id"
		assert season is None, "Should return None for session without season"

	async def test_tc_ss_03_validate_mismatched_returns_false_and_none(self, session_service: SessionService):
		"""TC-SS-03: Validate mismatched family_id returns (False, None)."""
		# Create a session
		await session_service.create_session(TEST_USER, TEST_PLAN)

		# Validate with different family_id
		is_valid, plan, season = await session_service.validate_session(TEST_USER, "wrong-family-id")

		assert is_valid is False, "Should return False for mismatched family_id"
		assert plan is None, "Should return None on mismatch"
		assert season is None, "Should return None on mismatch"

	async def test_tc_ss_04_invalidate_deletes_key(
		self, session_service: SessionService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-SS-04: Invalidate deletes key."""
		# Create a session
		await session_service.create_session(TEST_USER, TEST_PLAN)

		# Invalidate
		deleted = await session_service.invalidate_session(TEST_USER)

		assert deleted is True, "Should return True when deleting existing session"

		# Verify key is deleted
		key = session_key(TEST_USER)
		exists = await redis_client.exists(key)
		assert exists == 0, "Session key should be deleted"

	async def test_tc_ss_05_create_overwrites_previous_session(
		self, session_service: SessionService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-SS-05: Create overwrites previous session (family_id supersession)."""
		# Create first session
		family_id_1 = await session_service.create_session(TEST_USER, "PLAN-A")

		# Create second session (overwrites first)
		family_id_2 = await session_service.create_session(TEST_USER, "PLAN-B")

		# Verify family_ids are different
		assert family_id_1 != family_id_2, "New session should have different family_id"

		# Verify first family_id is no longer valid
		is_valid, plan, season = await session_service.validate_session(TEST_USER, family_id_1)
		assert is_valid is False, "Old family_id should be invalidated"

		# Verify second family_id is valid
		is_valid, plan, season = await session_service.validate_session(TEST_USER, family_id_2)
		assert is_valid is True, "New family_id should be valid"
		assert plan == "PLAN-B", "New plan should be returned"
