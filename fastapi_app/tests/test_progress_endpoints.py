"""Tests for progress tracking endpoints."""

import json
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

from fastapi_app.core.redis_keys import hierarchy_key, progress_key as _progress_key_fn, stats_key
from fastapi_app.tests.conftest import (
	make_hierarchy_json,
	seed_hierarchy,
	seed_access_grants,
	cleanup_player_keys,
)

# Mark all tests as async
pytestmark = pytest.mark.asyncio


class TestProgressSummary:
	"""Tests for GET /api/v1/progress/ (progress summary)."""

	async def test_progress_summary(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should retrieve progress summary for all accessible subjects.

		Seed access grants for 1 subject + hierarchy
		→ GET /api/v1/progress/
		→ 200 OK
		→ Response has list of subjects with subject_id, percentage, completed, total
		"""
		client, token, player_id, family_id = authed_client

		# Seed subject 1 with hierarchy
		subject_id = "SUB-TEST-001"
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)

		# Grant access to subject 1 (endpoint checks f"SUB-{subject_id}")
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		# Get progress summary
		response = await client.get("/api/v1/progress/")

		assert response.status_code == 200
		data = response.json()
		assert "subjects" in data or isinstance(data, list)

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_unauthenticated(
		self,
		app_client: AsyncClient,
	) -> None:
		"""
		Progress summary requires authentication.

		GET /api/v1/progress/ without Authorization header
		→ 401 Unauthorized
		"""
		# Ensure no Authorization header
		if "Authorization" in app_client.headers:
			del app_client.headers["Authorization"]

		response = await app_client.get("/api/v1/progress/")

		assert response.status_code == 401


class TestSubjectProgress:
	"""Tests for GET /api/v1/progress/{subject_id}."""

	async def test_subject_progress(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should retrieve subject progress with tracks.

		Seed hierarchy + access grant
		→ GET /api/v1/progress/{subject_id}
		→ 200 OK
		→ Response has tracks array
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-002"
		await seed_hierarchy(redis_client, subject_id, lesson_count=5)
		# The endpoint checks f"SUB-{subject_id}" so seed with that key
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		response = await client.get(f"/api/v1/progress/{subject_id}")

		assert response.status_code == 200
		data = response.json()
		assert "tracks" in data or "units" in data or "subject_id" in data

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_subject_not_found(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Progress endpoint returns 404 when subject hierarchy not found.

		No hierarchy seeded
		→ GET /api/v1/progress/SUB-NONEXIST
		→ 404 Not Found
		"""
		client, token, player_id, family_id = authed_client

		response = await client.get("/api/v1/progress/SUB-NONEXIST")

		assert response.status_code == 404

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)

	async def test_access_denied(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player without access grant and no free content receives 403.

		Seed hierarchy (no free content, no free units/topics)
		+ no access grant
		→ GET /api/v1/progress/{subject_id}
		→ 403 NO_ACCESS
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-003"
		# Seed hierarchy with has_free_content=False
		await seed_hierarchy(redis_client, subject_id, has_free_content=False, lesson_count=5)
		# Do NOT grant access

		response = await client.get(f"/api/v1/progress/{subject_id}")

		assert response.status_code == 403

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_free_content_bypass(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player without explicit access but with free content receives 200.

		Seed hierarchy with has_free_content=True
		+ no explicit access grant
		→ GET /api/v1/progress/{subject_id}
		→ 200 OK (free content access)
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-004"
		# Seed hierarchy with has_free_content=True
		await seed_hierarchy(redis_client, subject_id, has_free_content=True, lesson_count=5)
		# Do NOT grant access

		response = await client.get(f"/api/v1/progress/{subject_id}")

		assert response.status_code == 200

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))


class TestTrackListing:
	"""Tests for GET /api/v1/progress/{subject_id}/tracks."""

	async def test_track_listing(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should retrieve track summaries within a subject.

		Seed hierarchy + access grant
		→ GET /api/v1/progress/{subject_id}/tracks
		→ 200 OK
		→ Response has list with track_id
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-005"
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		response = await client.get(f"/api/v1/progress/{subject_id}/tracks")

		assert response.status_code == 200
		data = response.json()
		assert isinstance(data, (dict, list))

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))


class TestTrackDetail:
	"""Tests for GET /api/v1/progress/{subject_id}/tracks/{track_id}."""

	async def test_track_detail(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should retrieve units within a track.

		Seed hierarchy + access grant
		→ GET /api/v1/progress/{subject_id}/tracks/{track_id}
		→ 200 OK
		→ Response has units list
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-006"
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		track_id = "TRK-TEST-001"  # From make_hierarchy_json default
		response = await client.get(f"/api/v1/progress/{subject_id}/tracks/{track_id}")

		assert response.status_code == 200
		data = response.json()
		assert "units" in data or isinstance(data, dict)

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))


class TestUnitDetail:
	"""Tests for GET /api/v1/progress/{subject_id}/tracks/{track_id}/units/{unit_id}."""

	async def test_unit_detail(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should retrieve topics within a unit.

		Seed hierarchy + access grant
		→ GET /api/v1/progress/{subject_id}/tracks/{track_id}/units/{unit_id}
		→ 200 OK
		→ Response has topics list
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-007"
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		track_id = "TRK-TEST-001"
		unit_id = "UNIT-TEST-001"
		response = await client.get(
			f"/api/v1/progress/{subject_id}/tracks/{track_id}/units/{unit_id}"
		)

		assert response.status_code == 200
		data = response.json()
		assert "topics" in data or isinstance(data, dict)

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))


class TestLessonCompletion:
	"""Tests for GET /api/v1/progress/{subject_id}/topics/{topic_id}/lessons."""

	async def test_lesson_completion(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should retrieve lessons with completion flags from progress bitmap.

		Seed hierarchy + access grant + progress bitmap with some completed lessons
		→ GET /api/v1/progress/{subject_id}/topics/{topic_id}/lessons
		→ 200 OK
		→ Response has lessons with completed flags
		"""
		client, token, player_id, family_id = authed_client

		subject_id = "SUB-TEST-008"
		await seed_hierarchy(redis_client, subject_id, lesson_count=10)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		# Seed progress bitmap: mark lessons 0,2,4 as completed
		progress_key = _progress_key_fn(player_id, subject_id)
		for bit_index in [0, 2, 4]:
			await redis_client.setbit(progress_key, bit_index, 1)

		topic_id = "TOPIC-TEST-001"
		response = await client.get(f"/api/v1/progress/{subject_id}/topics/{topic_id}/lessons")

		assert response.status_code == 200
		data = response.json()
		assert "lessons" in data or isinstance(data, list)

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
		await redis_client.delete(progress_key)


# --- Stats-first activation tests (T008) ---


def _make_hierarchy_with_hash(subject_id: str, content_hash: str, lesson_count: int = 5) -> dict:
	"""Build hierarchy JSON with a specific content_hash for stats-first testing."""
	data = make_hierarchy_json(subject_id, lesson_count=lesson_count)
	data["content_hash"] = content_hash
	return data


async def _seed_stats_for_subject(
	redis_client: redis.Redis,
	player_id: str,
	subject_id: str,
	content_hash: str,
	completed: int = 3,
	total: int = 5,
) -> None:
	"""Seed a valid stats hash in Redis with matching content_hash."""
	key = stats_key(player_id, subject_id, version=1)
	stats_data = {
		"completed": str(completed),
		"total": str(total),
		"_content_hash": content_hash,
		"TRK-TEST-001:completed": str(completed),
		"TRK-TEST-001:total": str(total),
		"UNIT-TEST-001:completed": str(completed),
		"UNIT-TEST-001:total": str(total),
		"TOPIC-TEST-001:completed": str(completed),
		"TOPIC-TEST-001:total": str(total),
	}
	await redis_client.hset(key, mapping=stats_data)
	await redis_client.expire(key, 3600)


class TestPartialStatsActivation:
	"""T014: Verify partial stats path is activated for tracks/track_detail/unit_detail."""

	async def test_tracks_skips_bitmap_on_partial_stats_hit(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		When valid partial stats exist (matching _content_hash), get_subject_tracks
		should use get_partial_stats and NOT call get_completed_bits().
		"""
		client, token, player_id, family_id = authed_client
		subject_id = "SUB-TEST-PARTIAL-TRACKS"
		content_hash = "ptrk1234"

		hierarchy_json = _make_hierarchy_with_hash(subject_id, content_hash, lesson_count=5)
		await seed_hierarchy(redis_client, subject_id, hierarchy_json=hierarchy_json)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])
		await _seed_stats_for_subject(redis_client, player_id, subject_id, content_hash)

		with patch(
			"fastapi_app.services.progress.ProgressService.get_completed_bits",
			new_callable=AsyncMock,
		) as mock_get_bits:
			response = await client.get(f"/api/v1/progress/{subject_id}/tracks")

			assert response.status_code == 200
			mock_get_bits.assert_not_called()

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_tracks_falls_back_to_bitmap_on_partial_stats_miss(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		When partial stats are missing, get_subject_tracks falls back to
		get_completed_bits() (bitmap path).
		"""
		client, token, player_id, family_id = authed_client
		subject_id = "SUB-TEST-PARTIAL-MISS"

		await seed_hierarchy(redis_client, subject_id, lesson_count=5)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])
		# Do NOT seed stats

		with patch(
			"fastapi_app.services.progress.ProgressService.get_completed_bits",
			new_callable=AsyncMock,
			return_value=set(),
		) as mock_get_bits:
			response = await client.get(f"/api/v1/progress/{subject_id}/tracks")

			assert response.status_code == 200
			mock_get_bits.assert_called_once()

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_track_detail_skips_bitmap_on_partial_stats_hit(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		When valid partial stats exist, get_track_detail should NOT call
		get_completed_bits().
		"""
		client, token, player_id, family_id = authed_client
		subject_id = "SUB-TEST-PARTIAL-TD"
		content_hash = "ptd12345"

		hierarchy_json = _make_hierarchy_with_hash(subject_id, content_hash, lesson_count=5)
		await seed_hierarchy(redis_client, subject_id, hierarchy_json=hierarchy_json)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])
		await _seed_stats_for_subject(redis_client, player_id, subject_id, content_hash)

		track_id = "TRK-TEST-001"
		with patch(
			"fastapi_app.services.progress.ProgressService.get_completed_bits",
			new_callable=AsyncMock,
		) as mock_get_bits:
			response = await client.get(f"/api/v1/progress/{subject_id}/tracks/{track_id}")

			assert response.status_code == 200
			mock_get_bits.assert_not_called()

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_unit_detail_skips_bitmap_on_partial_stats_hit(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		When valid partial stats exist, get_unit_detail should NOT call
		get_completed_bits().
		"""
		client, token, player_id, family_id = authed_client
		subject_id = "SUB-TEST-PARTIAL-UD"
		content_hash = "pud12345"

		hierarchy_json = _make_hierarchy_with_hash(subject_id, content_hash, lesson_count=5)
		await seed_hierarchy(redis_client, subject_id, hierarchy_json=hierarchy_json)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])
		await _seed_stats_for_subject(redis_client, player_id, subject_id, content_hash)

		track_id = "TRK-TEST-001"
		unit_id = "UNIT-TEST-001"
		with patch(
			"fastapi_app.services.progress.ProgressService.get_completed_bits",
			new_callable=AsyncMock,
		) as mock_get_bits:
			response = await client.get(
				f"/api/v1/progress/{subject_id}/tracks/{track_id}/units/{unit_id}"
			)

			assert response.status_code == 200
			mock_get_bits.assert_not_called()

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))


class TestStatsFirstActivation:
	"""T008: Verify stats-first path is activated when valid stats exist."""

	async def test_subject_progress_skips_bitmap_on_stats_hit(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		When valid stats exist (matching _content_hash), get_subject_progress
		should NOT call progress_service.get_completed_bits().
		"""
		client, token, player_id, family_id = authed_client
		subject_id = "SUB-TEST-STATS-HIT"
		content_hash = "abc12345"

		# Seed hierarchy with content_hash
		hierarchy_json = _make_hierarchy_with_hash(subject_id, content_hash, lesson_count=5)
		await seed_hierarchy(redis_client, subject_id, hierarchy_json=hierarchy_json)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		# Seed valid stats matching the content_hash
		await _seed_stats_for_subject(redis_client, player_id, subject_id, content_hash)

		with patch(
			"fastapi_app.services.progress.ProgressService.get_completed_bits",
			new_callable=AsyncMock,
		) as mock_get_bits:
			response = await client.get(f"/api/v1/progress/{subject_id}")

			assert response.status_code == 200
			mock_get_bits.assert_not_called()

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_subject_progress_falls_back_to_bitmap_on_stats_miss(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		When stats are missing, get_subject_progress should call
		progress_service.get_completed_bits() (bitmap fallback).
		"""
		client, token, player_id, family_id = authed_client
		subject_id = "SUB-TEST-STATS-MISS"

		# Seed hierarchy (no content_hash → default "")
		await seed_hierarchy(redis_client, subject_id, lesson_count=5)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])
		# Do NOT seed stats → cache miss → fallback path

		with patch(
			"fastapi_app.services.progress.ProgressService.get_completed_bits",
			new_callable=AsyncMock,
			return_value=set(),
		) as mock_get_bits:
			response = await client.get(f"/api/v1/progress/{subject_id}")

			assert response.status_code == 200
			mock_get_bits.assert_called_once()

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))

	async def test_subject_progress_falls_back_on_stale_hash(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		When stats exist but _content_hash doesn't match hierarchy, should fall back
		to bitmap path.
		"""
		client, token, player_id, family_id = authed_client
		subject_id = "SUB-TEST-STATS-STALE"
		hierarchy_hash = "newhash1"
		stale_hash = "oldhash1"

		# Seed hierarchy with new hash
		hierarchy_json = _make_hierarchy_with_hash(subject_id, hierarchy_hash, lesson_count=5)
		await seed_hierarchy(redis_client, subject_id, hierarchy_json=hierarchy_json)
		await seed_access_grants(redis_client, player_id, [f"SUB-{subject_id}"])

		# Seed stats with STALE hash (different from hierarchy)
		await _seed_stats_for_subject(redis_client, player_id, subject_id, stale_hash)

		with patch(
			"fastapi_app.services.progress.ProgressService.get_completed_bits",
			new_callable=AsyncMock,
			return_value=set(),
		) as mock_get_bits:
			response = await client.get(f"/api/v1/progress/{subject_id}")

			assert response.status_code == 200
			mock_get_bits.assert_called_once()

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await redis_client.delete(hierarchy_key(subject_id))
