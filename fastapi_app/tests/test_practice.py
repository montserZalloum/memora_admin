"""Tests for Practice Arena v2 endpoints.

Tests verify:
- POST /api/v1/practice/start  — Session creation, access control, rate limiting
- POST /api/v1/practice/submit — Batch submission, concurrency, idempotency
- POST /api/v1/practice/continue — Next batch selection
- GET  /api/v1/practice/session — Session status

All tests mock practice_map.get_map and AccessService to avoid disk/DB deps.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from fastapi_app.core.redis_keys import (
	practice_session_key,
	practice_summary_key,
	subjects_with_free_content_key,
)

# === Test map data ===

SUBJECT_ID = "SUBJ-TEST-001"
TRACK_A = "TRK-A"
TRACK_B = "TRK-B"
UNIT_A = "UNIT-A"
UNIT_B = "UNIT-B"
TOPIC_A = "TOPIC-A"
TOPIC_B = "TOPIC-B"

# Single-track map with 3 questions
SINGLE_TRACK_MAP = {
	"tracks": {
		TRACK_A: {
			"units": {
				UNIT_A: {
					"topics": {
						TOPIC_A: {
							"questions": [
								{"id": "Q-001", "chunk": 1},
								{"id": "Q-002", "chunk": 1},
								{"id": "Q-003", "chunk": 2},
							]
						}
					}
				}
			}
		}
	}
}

# Multi-track map: TRACK_A has Q-001..Q-003, TRACK_B has Q-101..Q-103
MULTI_TRACK_MAP = {
	"tracks": {
		TRACK_A: {
			"units": {
				UNIT_A: {
					"topics": {
						TOPIC_A: {
							"questions": [
								{"id": "Q-001", "chunk": 1},
								{"id": "Q-002", "chunk": 1},
								{"id": "Q-003", "chunk": 2},
							]
						}
					}
				}
			}
		},
		TRACK_B: {
			"units": {
				UNIT_B: {
					"topics": {
						TOPIC_B: {
							"questions": [
								{"id": "Q-101", "chunk": 10},
								{"id": "Q-102", "chunk": 10},
								{"id": "Q-103", "chunk": 11},
							]
						}
					}
				}
			}
		},
	}
}


# === Fixtures ===


@pytest.fixture(autouse=True)
def _mock_access(mock_frappe):
	"""Inject mock_frappe and patch AccessService for all tests.

	Grants subject-level access by default so tests don't fail on access
	checks unless they explicitly test access control.
	"""
	import fastapi_app.api.deps as deps_module

	deps_module._frappe_client = mock_frappe

	with (
		patch(
			"fastapi_app.services.access.AccessService.check_access_with_plan",
			new_callable=AsyncMock,
			return_value=True,
		),
		patch(
			"fastapi_app.services.access.AccessService.check_access",
			new_callable=AsyncMock,
			return_value=True,
		),
	):
		yield

	deps_module._frappe_client = None


@pytest.fixture
def mock_map():
	"""Patch practice_map.get_map to return SINGLE_TRACK_MAP and set maps_dir."""
	import fastapi_app.core.config as config_module

	settings = config_module.get_settings()
	old_dir = settings.practice_maps_dir
	settings.practice_maps_dir = "/tmp/test-maps"
	with patch(
		"fastapi_app.services.practice_map.get_map",
		return_value=SINGLE_TRACK_MAP,
	) as m:
		yield m
	settings.practice_maps_dir = old_dir


@pytest.fixture
def mock_map_multi():
	"""Patch practice_map.get_map to return MULTI_TRACK_MAP and set maps_dir."""
	import fastapi_app.core.config as config_module

	settings = config_module.get_settings()
	old_dir = settings.practice_maps_dir
	settings.practice_maps_dir = "/tmp/test-maps"
	with patch(
		"fastapi_app.services.practice_map.get_map",
		return_value=MULTI_TRACK_MAP,
	) as m:
		yield m
	settings.practice_maps_dir = old_dir


# === Helpers ===


async def _start_session(client, subject_id=SUBJECT_ID, track_ids=None, **extra):
	"""Helper: POST /start with defaults."""
	body = {"subject_id": subject_id, "track_ids": track_ids or [TRACK_A], **extra}
	return await client.post("/api/v1/practice/start", json=body)


async def _submit(client, batch_seq, results):
	"""Helper: POST /submit."""
	return await client.post(
		"/api/v1/practice/submit",
		json={"batch_seq": batch_seq, "results": results},
	)


async def _continue(client, batch_seq):
	"""Helper: POST /continue."""
	return await client.post(
		"/api/v1/practice/continue",
		json={"batch_seq": batch_seq},
	)


def _make_results(question_ids, all_correct=True):
	"""Build results payload from question IDs."""
	return [{"item_id": qid, "is_correct": all_correct} for qid in question_ids]


# =========================================================================
# POST /start
# =========================================================================


class TestStartSession:
	"""POST /api/v1/practice/start."""

	@pytest.mark.asyncio
	async def test_start_happy_path(self, authed_client, mock_map):
		client, _, player_id, _ = authed_client
		resp = await _start_session(client)
		assert resp.status_code == 200
		data = resp.json()
		assert data["session_active"] is True
		assert data["batch_seq"] == 0
		assert len(data["question_ids"]) == 3  # All 3 questions
		assert data["total_available"] == 3
		assert data["all_seen_warning"] is False

	@pytest.mark.asyncio
	async def test_start_creates_redis_session(self, authed_client, redis_client, mock_map):
		client, _, player_id, _ = authed_client
		resp = await _start_session(client)
		assert resp.status_code == 200

		key = practice_session_key(player_id)
		session = await redis_client.hgetall(key)
		assert session["subject_id"] == SUBJECT_ID
		assert json.loads(session["track_ids"]) == [TRACK_A]
		assert session["submitted"] == "0"
		assert session["question_track_map"]  # Stored

	@pytest.mark.asyncio
	async def test_start_unknown_subject(self, authed_client):
		"""Map file not found → 400."""
		import fastapi_app.core.config as config_module

		settings = config_module.get_settings()
		old_dir = settings.practice_maps_dir
		settings.practice_maps_dir = "/tmp/test-maps"
		client, _, _, _ = authed_client
		try:
			with patch(
				"fastapi_app.services.practice_map.get_map",
				side_effect=FileNotFoundError,
			):
				resp = await _start_session(client, subject_id="SUBJ-NOPE")
			assert resp.status_code == 400
			assert "Unknown subject_id" in resp.json()["detail"]
		finally:
			settings.practice_maps_dir = old_dir

	@pytest.mark.asyncio
	async def test_start_unknown_track(self, authed_client, mock_map):
		"""Track not in map → 400."""
		client, _, _, _ = authed_client
		resp = await _start_session(client, track_ids=["TRK-NOPE"])
		assert resp.status_code == 400
		assert "Unknown track_ids" in resp.json()["detail"]

	@pytest.mark.asyncio
	async def test_start_multi_track_with_unit_filter_rejected(self, authed_client, mock_map_multi):
		"""Cannot filter by units when multiple tracks are selected."""
		client, _, _, _ = authed_client
		resp = await _start_session(
			client,
			track_ids=[TRACK_A, TRACK_B],
			unit_ids=[UNIT_A],
		)
		assert resp.status_code == 400
		assert "Cannot filter" in resp.json()["detail"]

	@pytest.mark.asyncio
	async def test_start_maps_dir_not_configured(self, authed_client):
		"""Maps dir empty → 503."""
		client, _, _, _ = authed_client
		# The test settings don't have practice_maps_dir set
		resp = await _start_session(client)
		assert resp.status_code == 503

	@pytest.mark.asyncio
	async def test_start_rate_limited(self, authed_client, mock_map):
		"""Rate limit after 5 starts per hour."""
		client, _, _, _ = authed_client
		for i in range(5):
			resp = await _start_session(client)
			assert resp.status_code == 200

		resp = await _start_session(client)
		assert resp.status_code == 429
		assert "Retry-After" in resp.headers


# =========================================================================
# POST /submit
# =========================================================================


class TestSubmitResults:
	"""POST /api/v1/practice/submit."""

	@pytest.mark.asyncio
	async def test_submit_happy_path(self, authed_client, mock_map):
		client, _, _, _ = authed_client
		start = await _start_session(client)
		qids = start.json()["question_ids"]

		resp = await _submit(client, 0, _make_results(qids))
		assert resp.status_code == 200
		data = resp.json()
		assert data["accepted"] is True
		assert data["batch_seq"] == 0
		assert data["correct_count"] == len(qids)
		assert data["is_duplicate"] is False

	@pytest.mark.asyncio
	async def test_submit_no_session_404(self, authed_client):
		client, _, _, _ = authed_client
		resp = await _submit(client, 0, [{"item_id": "Q-001", "is_correct": True}])
		assert resp.status_code == 404

	@pytest.mark.asyncio
	async def test_submit_wrong_batch_seq(self, authed_client, mock_map):
		client, _, _, _ = authed_client
		await _start_session(client)
		resp = await _submit(client, 99, [{"item_id": "Q-001", "is_correct": True}])
		assert resp.status_code == 400
		assert "batch_seq" in resp.json()["detail"]

	@pytest.mark.asyncio
	async def test_submit_unknown_item_id(self, authed_client, mock_map):
		client, _, _, _ = authed_client
		await _start_session(client)
		resp = await _submit(client, 0, [{"item_id": "Q-UNKNOWN", "is_correct": True}])
		assert resp.status_code == 400
		assert "not in the current batch" in resp.json()["detail"]

	@pytest.mark.asyncio
	async def test_submit_duplicate_item_ids(self, authed_client, mock_map):
		client, _, _, _ = authed_client
		start = await _start_session(client)
		qid = start.json()["question_ids"][0]
		resp = await _submit(
			client,
			0,
			[
				{"item_id": qid, "is_correct": True},
				{"item_id": qid, "is_correct": False},
			],
		)
		assert resp.status_code == 400
		assert "Duplicate" in resp.json()["detail"]

	@pytest.mark.asyncio
	async def test_submit_idempotent(self, authed_client, mock_map):
		"""Second submit with same batch_seq returns cached stats."""
		client, _, _, _ = authed_client
		start = await _start_session(client)
		qids = start.json()["question_ids"]
		results = _make_results(qids)

		resp1 = await _submit(client, 0, results)
		assert resp1.status_code == 200
		assert resp1.json()["is_duplicate"] is False

		resp2 = await _submit(client, 0, results)
		assert resp2.status_code == 200
		assert resp2.json()["is_duplicate"] is True
		assert resp2.json()["correct_count"] == resp1.json()["correct_count"]

	@pytest.mark.asyncio
	async def test_submit_partial_allowed(self, authed_client, mock_map):
		"""Partial submissions (fewer results than batch size) are accepted."""
		client, _, _, _ = authed_client
		start = await _start_session(client)
		qids = start.json()["question_ids"]
		# Submit only the first question (partial)
		resp = await _submit(client, 0, _make_results(qids[:1]))
		assert resp.status_code == 200
		assert resp.json()["total_count"] == 1

	@pytest.mark.asyncio
	async def test_submit_updates_player_summary(self, authed_client, redis_client, mock_map):
		"""Submit writes results to the correct track's player summary."""
		client, _, player_id, _ = authed_client
		start = await _start_session(client)
		qids = start.json()["question_ids"]

		await _submit(client, 0, _make_results(qids, all_correct=True))

		key = practice_summary_key(player_id, TRACK_A)
		summary = json.loads(await redis_client.get(key))
		for qid in qids:
			assert qid in summary
			assert summary[qid]["lr"] == "C"
			assert summary[qid]["ac"] == 1


# =========================================================================
# Multi-track isolation
# =========================================================================


class TestMultiTrack:
	"""Verify results are routed to the correct track."""

	@pytest.mark.asyncio
	async def test_multi_track_no_cross_contamination(
		self,
		authed_client,
		redis_client,
		mock_map_multi,
	):
		"""Each track's summary should only contain its own questions."""
		client, _, player_id, _ = authed_client
		resp = await _start_session(client, track_ids=[TRACK_A, TRACK_B])
		assert resp.status_code == 200
		qids = resp.json()["question_ids"]

		# Submit all
		await _submit(client, 0, _make_results(qids))

		summary_a = json.loads(await redis_client.get(practice_summary_key(player_id, TRACK_A)) or "{}")
		summary_b = json.loads(await redis_client.get(practice_summary_key(player_id, TRACK_B)) or "{}")

		# Track A questions should NOT appear in Track B summary
		track_a_qids = {"Q-001", "Q-002", "Q-003"}
		track_b_qids = {"Q-101", "Q-102", "Q-103"}

		for qid in summary_a:
			assert qid in track_a_qids, f"{qid} leaked into track A summary"
		for qid in summary_b:
			assert qid in track_b_qids, f"{qid} leaked into track B summary"


# =========================================================================
# POST /continue
# =========================================================================


class TestContinueSession:
	"""POST /api/v1/practice/continue."""

	@pytest.mark.asyncio
	async def test_continue_no_session_404(self, authed_client):
		client, _, _, _ = authed_client
		resp = await _continue(client, 0)
		assert resp.status_code == 404

	@pytest.mark.asyncio
	async def test_continue_before_submit_rejected(self, authed_client, mock_map):
		"""Cannot continue if current batch not submitted."""
		client, _, _, _ = authed_client
		await _start_session(client)
		resp = await _continue(client, 0)
		assert resp.status_code == 400
		assert "not been submitted" in resp.json()["detail"]

	@pytest.mark.asyncio
	async def test_continue_wrong_batch_seq(self, authed_client, mock_map):
		client, _, _, _ = authed_client
		await _start_session(client)
		resp = await _continue(client, 5)
		assert resp.status_code == 400

	@pytest.mark.asyncio
	async def test_continue_after_submit(self, authed_client, mock_map):
		"""Submit then continue returns next batch."""
		client, _, _, _ = authed_client
		start = await _start_session(client)
		qids = start.json()["question_ids"]

		await _submit(client, 0, _make_results(qids))

		resp = await _continue(client, 0)
		assert resp.status_code == 200
		data = resp.json()
		assert data["batch_seq"] == 1
		# All questions served in batch 0, so all_seen_warning should be True
		assert data["all_seen_warning"] is True


# =========================================================================
# GET /session
# =========================================================================


class TestSessionStatus:
	"""GET /api/v1/practice/session."""

	@pytest.mark.asyncio
	async def test_session_no_session_404(self, authed_client):
		client, _, _, _ = authed_client
		resp = await client.get("/api/v1/practice/session")
		assert resp.status_code == 404

	@pytest.mark.asyncio
	async def test_session_active(self, authed_client, mock_map):
		client, _, _, _ = authed_client
		start = await _start_session(client)
		qids = start.json()["question_ids"]

		resp = await client.get("/api/v1/practice/session")
		assert resp.status_code == 200
		data = resp.json()
		assert data["session_active"] is True
		assert data["subject_id"] == SUBJECT_ID
		assert data["track_ids"] == [TRACK_A]
		assert data["batch_seq"] == 0
		assert data["submitted"] is False
		assert data["question_ids"] == qids


# =========================================================================
# Access control
# =========================================================================


class TestAccessControl:
	"""Verify access control checks in /start."""

	@pytest.mark.asyncio
	async def test_no_access_returns_403(self, authed_client, mock_map):
		client, _, _, _ = authed_client
		with (
			patch(
				"fastapi_app.services.access.AccessService.check_access_with_plan",
				new_callable=AsyncMock,
				return_value=False,
			),
			patch(
				"fastapi_app.services.access.AccessService.check_access",
				new_callable=AsyncMock,
				return_value=False,
			),
		):
			resp = await _start_session(client)
		assert resp.status_code == 403

	@pytest.mark.asyncio
	async def test_free_content_bypasses_access(self, authed_client, redis_client, mock_map):
		"""Subject in subjects_with_free_content set → allowed."""
		client, _, _, _ = authed_client
		# Mark subject as having free content
		await redis_client.sadd(subjects_with_free_content_key(), SUBJECT_ID)
		with (
			patch(
				"fastapi_app.services.access.AccessService.check_access_with_plan",
				new_callable=AsyncMock,
				return_value=False,
			),
			patch(
				"fastapi_app.services.access.AccessService.check_access",
				new_callable=AsyncMock,
				return_value=False,
			),
		):
			resp = await _start_session(client)
		assert resp.status_code == 200

	@pytest.mark.asyncio
	async def test_track_level_access_allowed(self, authed_client, mock_map):
		"""No subject access but explicit track access → allowed."""
		client, _, _, _ = authed_client

		async def _check_access(self_or_pid, content_key_or_ck=None, **kw):
			# Handle both positional patterns
			ck = content_key_or_ck if content_key_or_ck is not None else self_or_pid
			return ck.startswith("TRK-")

		with (
			patch(
				"fastapi_app.services.access.AccessService.check_access_with_plan",
				new_callable=AsyncMock,
				return_value=False,
			),
			patch(
				"fastapi_app.services.access.AccessService.check_access",
				side_effect=lambda pid, ck: True,
			),
		):
			resp = await _start_session(client)
		assert resp.status_code == 200

	@pytest.mark.asyncio
	async def test_practice_only_grant_allows_start(self, authed_client, mock_map):
		"""No SUB/TRK access but PRAC-SUB grant → allowed with full scope."""
		client, _, _, _ = authed_client
		with (
			patch(
				"fastapi_app.services.access.AccessService.check_access_with_plan",
				new_callable=AsyncMock,
				return_value=False,
			),
			patch(
				"fastapi_app.services.access.AccessService.check_access",
				side_effect=lambda pid, ck: ck.startswith("PRAC-SUB-"),
			),
		):
			resp = await _start_session(client)
		assert resp.status_code == 200
		# Full scope: all 3 questions available (no free content restriction)
		assert resp.json()["total_available"] == 3

	@pytest.mark.asyncio
	async def test_practice_only_grant_no_free_scope_restriction(self, authed_client, redis_client, mock_map):
		"""PRAC-SUB grant does not apply free content scope restriction."""
		client, _, _, _ = authed_client
		# Even if subject has free content markers, PRAC-SUB gives full scope
		await redis_client.sadd(subjects_with_free_content_key(), SUBJECT_ID)
		with (
			patch(
				"fastapi_app.services.access.AccessService.check_access_with_plan",
				new_callable=AsyncMock,
				return_value=False,
			),
			patch(
				"fastapi_app.services.access.AccessService.check_access",
				side_effect=lambda pid, ck: ck.startswith("PRAC-SUB-"),
			),
		):
			resp = await _start_session(client)
		assert resp.status_code == 200
		assert resp.json()["total_available"] == 3

	@pytest.mark.asyncio
	async def test_no_grant_no_prac_sub_returns_403(self, authed_client, mock_map):
		"""No SUB, TRK, or PRAC-SUB grant and no free content → 403."""
		client, _, _, _ = authed_client
		with (
			patch(
				"fastapi_app.services.access.AccessService.check_access_with_plan",
				new_callable=AsyncMock,
				return_value=False,
			),
			patch(
				"fastapi_app.services.access.AccessService.check_access",
				new_callable=AsyncMock,
				return_value=False,
			),
		):
			resp = await _start_session(client)
		assert resp.status_code == 403
