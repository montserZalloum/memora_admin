"""Tests for Practice Arena endpoints.

Tests verify:
- GET /api/v1/practice/hierarchy - Hierarchy browsing with access/counts
- POST /api/v1/practice/start - Session creation, first batch
- POST /api/v1/practice/submit - Batch result submission, idempotency
- POST /api/v1/practice/continue - Next batch, dedup, edge cases

Reference: specs/025-practice-arena/contracts/practice-api.md
"""

import json

import pytest
from uuid import uuid4

import fastapi_app.api.deps as deps_module
from fastapi_app.core.redis_keys import (
	access_key as _access_key_fn,
	practice_hierarchy_meta_key,
	practice_session_key,
)
from fastapi_app.tests.conftest import (
	make_hierarchy_json,
	seed_access_grants,
	seed_hierarchy,
)


@pytest.fixture(autouse=True)
def _inject_mock_frappe(mock_frappe):
	"""Inject mock_frappe as the global FrappeClient singleton.

	Required because get_practice_service() calls get_frappe_client() directly
	(not via Depends), bypassing FastAPI's dependency override system.
	"""
	deps_module._frappe_client = mock_frappe
	yield
	deps_module._frappe_client = None


# === Constants ===

SUBJECT_ID = "SUBJ-TEST-001"
TRACK_ID = "TRK-TEST-001"
UNIT_ID = "UNIT-TEST-001"
TOPIC_ID = "TOPIC-TEST-001"


# === Helpers ===


def _make_practice_meta(subject_id=SUBJECT_ID, topic_id=TOPIC_ID, item_count=10):
	"""Build minimal practice hierarchy meta for Redis cache."""
	return {
		"subject_title": "Test Subject",
		"tracks": {TRACK_ID: {"title": "Test Track"}},
		"units": {UNIT_ID: {"title": "Test Unit", "track": TRACK_ID}},
		"topics": {topic_id: {"title": "Test Topic", "unit": UNIT_ID}},
		"item_counts": {topic_id: item_count},
	}


async def _seed_practice_meta(redis, subject_id=SUBJECT_ID, meta=None):
	"""Seed practice hierarchy metadata in Redis."""
	if meta is None:
		meta = _make_practice_meta(subject_id=subject_id)
	key = practice_hierarchy_meta_key(subject_id)
	await redis.set(key, json.dumps(meta), ex=3600)


def _make_question_rows(count=3, topic_id=TOPIC_ID):
	"""Build question result rows like Frappe SQL returns."""
	return [
		{
			"item_id": str(uuid4()),
			"question_text": f"Question {i}?",
			"choice_1": "A",
			"choice_2": "B",
			"choice_3": "C",
			"choice_4": "D",
			"correct_choice": 1,
			"content_json": None,
			"stage_type": "QUESTION",
			"topic": topic_id,
			"priority": 0,
			"sort_seen": "1970-01-01",
		}
		for i in range(count)
	]


def _make_frappe_handler(questions=None, valid_item_ids=None):
	"""Create mock frappe.call handler for practice tests.

	Args:
		questions: List of question row dicts to return from SELECT queries.
		valid_item_ids: Set of item_ids that still exist (for deleted-item tests).
			If None, all question item_ids are considered valid.
	"""
	if questions is None:
		questions = _make_question_rows()

	all_item_ids = {q["item_id"] for q in questions}
	valid = valid_item_ids if valid_item_ids is not None else all_item_ids

	async def handler(method, params=None):
		if method == "memora_admin.api.practice.execute_practice_query":
			sql = (params or {}).get("sql", "")
			if "COUNT(*)" in sql:
				return [{"cnt": len(questions)}]
			elif "SELECT ri.item_id" in sql:
				return questions
			elif "SELECT item_id" in sql:
				# _get_valid_item_ids check
				return [{"item_id": iid} for iid in valid]
			return []
		elif method == "memora_admin.api.practice.execute_practice_log_upsert":
			return None
		elif method == "memora_admin.api.practice.get_practice_hierarchy_meta":
			return _make_practice_meta()
		# For access hydration and other calls, return None
		return None

	return handler


async def _start_session(client, redis_client, mock_frappe, player_id, questions=None):
	"""Helper: seed data and start a practice session. Returns question list."""
	if questions is None:
		questions = _make_question_rows(3)

	await seed_hierarchy(redis_client, SUBJECT_ID)
	await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
	mock_frappe.call.side_effect = _make_frappe_handler(questions)

	resp = await client.post(
		"/api/v1/practice/start",
		json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
	)
	assert resp.status_code == 200
	return questions


async def _start_and_submit(client, redis_client, mock_frappe, player_id, questions=None):
	"""Helper: start session and submit first batch. Returns question list."""
	qs = await _start_session(client, redis_client, mock_frappe, player_id, questions)
	results = [{"item_id": q["item_id"], "is_correct": True} for q in qs]
	resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
	assert resp.status_code == 200
	return qs


# ==========================================================================
# T035: GET /practice/hierarchy
# ==========================================================================


@pytest.mark.asyncio
class TestPracticeHierarchy:
	"""T035: Hierarchy endpoint with tree structure, access flags, item counts."""

	async def test_hierarchy_success(self, authed_client, redis_client, mock_frappe):
		"""Correct tree structure with item counts and access flags."""
		client, token, player_id, family_id = authed_client

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await _seed_practice_meta(redis_client)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])

		resp = await client.get(f"/api/v1/practice/hierarchy?subject_id={SUBJECT_ID}")

		assert resp.status_code == 200
		data = resp.json()
		assert data["subject_id"] == SUBJECT_ID
		assert data["subject_title"] == "Test Subject"
		assert len(data["tracks"]) == 1

		track = data["tracks"][0]
		assert track["track_id"] == TRACK_ID
		assert track["has_access"] is True
		assert track["item_count"] == 10
		assert len(track["units"]) == 1

		unit = track["units"][0]
		assert unit["unit_id"] == UNIT_ID
		assert unit["item_count"] == 10
		assert len(unit["topics"]) == 1

		topic = unit["topics"][0]
		assert topic["topic_id"] == TOPIC_ID
		assert topic["item_count"] == 10

	async def test_hierarchy_no_access_shows_flag(self, authed_client, redis_client, mock_frappe):
		"""Track without access shows has_access=false and empty units."""
		client, token, player_id, family_id = authed_client

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await _seed_practice_meta(redis_client)
		# No access grants seeded

		resp = await client.get(f"/api/v1/practice/hierarchy?subject_id={SUBJECT_ID}")

		assert resp.status_code == 200
		data = resp.json()
		assert len(data["tracks"]) == 1
		track = data["tracks"][0]
		assert track["has_access"] is False
		assert track["units"] == []

	async def test_hierarchy_404_invalid_subject(self, authed_client, redis_client, mock_frappe):
		"""404 for non-existent subject."""
		client, token, player_id, family_id = authed_client

		resp = await client.get("/api/v1/practice/hierarchy?subject_id=NONEXISTENT")

		assert resp.status_code == 404
		assert resp.json()["detail"] == "SUBJECT_NOT_FOUND"

	async def test_hierarchy_completed_filter_empty(self, authed_client, redis_client, mock_frappe):
		"""filter=completed with no progress returns empty tracks."""
		client, token, player_id, family_id = authed_client

		await seed_hierarchy(redis_client, SUBJECT_ID, lesson_count=3)
		await _seed_practice_meta(redis_client)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])

		resp = await client.get(f"/api/v1/practice/hierarchy?subject_id={SUBJECT_ID}&filter=completed")

		assert resp.status_code == 200
		data = resp.json()
		# No completed lessons → no nodes pass the filter
		assert data["tracks"] == []


# ==========================================================================
# T036: POST /practice/start
# ==========================================================================


@pytest.mark.asyncio
class TestPracticeStart:
	"""T036: Session creation, first batch, validation errors."""

	async def test_start_session_creates_redis_session(self, authed_client, redis_client, mock_frappe):
		"""Start creates Redis session with correct fields and returns first batch."""
		client, token, player_id, family_id = authed_client

		questions = _make_question_rows(5)
		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 200
		data = resp.json()
		assert data["session_active"] is True
		assert data["batch_seq"] == 0
		assert len(data["questions"]) == 5
		assert data["total_available"] == 5
		assert data["all_seen_warning"] is False

		# Verify question structure
		q = data["questions"][0]
		assert "item_id" in q
		assert q["stage_type"] == "QUESTION"
		assert len(q["choices"]) == 4
		assert q["correct_choice"] == 1

		# Verify Redis session populated
		session = await redis_client.hgetall(practice_session_key(player_id))
		assert session["subject_id"] == SUBJECT_ID
		assert session["batch_seq"] == "0"
		accessible = json.loads(session["accessible_lessons"])
		assert len(accessible) > 0

	async def test_start_no_items_completed_filter_422(self, authed_client, redis_client, mock_frappe):
		"""422 NO_ITEMS when filter=completed but no lessons completed."""
		client, token, player_id, family_id = authed_client

		await seed_hierarchy(redis_client, SUBJECT_ID, lesson_count=3)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler([])

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "completed", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 422
		detail = resp.json()["detail"]
		assert detail["code"] == "NO_ITEMS"

	async def test_start_empty_tracks_422(self, authed_client, redis_client, mock_frappe):
		"""422 when tracks array is empty."""
		client, token, player_id, family_id = authed_client

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": []},
		)

		assert resp.status_code == 422

	async def test_start_multi_track_with_units_422(self, authed_client, redis_client, mock_frappe):
		"""422 when multi-track selected with units specified."""
		client, token, player_id, family_id = authed_client

		resp = await client.post(
			"/api/v1/practice/start",
			json={
				"subject_id": SUBJECT_ID,
				"filter": "all",
				"tracks": [TRACK_ID, "TRK-TEST-002"],
				"units": [UNIT_ID],
			},
		)

		assert resp.status_code == 422


# ==========================================================================
# T037: POST /practice/submit
# ==========================================================================


@pytest.mark.asyncio
class TestPracticeSubmit:
	"""T037: Batch submission, idempotency, BATCH_SEQ_MISMATCH."""

	async def test_submit_success_with_accuracy(self, authed_client, redis_client, mock_frappe):
		"""Submit records results and returns accuracy stats."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)

		results = [
			{"item_id": questions[0]["item_id"], "is_correct": True},
			{"item_id": questions[1]["item_id"], "is_correct": False},
			{"item_id": questions[2]["item_id"], "is_correct": True},
		]

		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})

		assert resp.status_code == 200
		data = resp.json()
		assert data["accepted"] is True
		assert data["batch_seq"] == 0
		assert data["correct_count"] == 2
		assert data["total_count"] == 3
		assert data["accuracy_percent"] == pytest.approx(66.7, abs=0.1)
		assert data["is_duplicate"] is False

	async def test_submit_idempotent_duplicate(self, authed_client, redis_client, mock_frappe):
		"""Duplicate batch_seq returns is_duplicate=true without re-writing."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)

		results = [{"item_id": questions[0]["item_id"], "is_correct": True}]

		# First submit
		resp1 = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		assert resp1.status_code == 200
		assert resp1.json()["is_duplicate"] is False

		# Second submit (same batch_seq)
		resp2 = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		assert resp2.status_code == 200
		assert resp2.json()["is_duplicate"] is True

	async def test_submit_batch_seq_mismatch_409(self, authed_client, redis_client, mock_frappe):
		"""409 when batch_seq skips ahead of current."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)

		resp = await client.post(
			"/api/v1/practice/submit",
			json={"batch_seq": 5, "results": [{"item_id": questions[0]["item_id"], "is_correct": True}]},
		)

		assert resp.status_code == 409
		detail = resp.json()["detail"]
		assert detail["code"] == "BATCH_SEQ_MISMATCH"
		assert detail["expected"] == 0
		assert detail["received"] == 5

	async def test_submit_no_session_404(self, authed_client, redis_client, mock_frappe):
		"""404 when no active session exists."""
		client, token, player_id, family_id = authed_client

		resp = await client.post(
			"/api/v1/practice/submit",
			json={"batch_seq": 0, "results": [{"item_id": str(uuid4()), "is_correct": True}]},
		)

		assert resp.status_code == 404
		assert resp.json()["detail"] == "NO_ACTIVE_SESSION"


# ==========================================================================
# T038: POST /practice/continue
# ==========================================================================


@pytest.mark.asyncio
class TestPracticeContinue:
	"""T038: Next batch, dedup, PREVIOUS_BATCH_NOT_SUBMITTED."""

	async def test_continue_success_increments_seq(self, authed_client, redis_client, mock_frappe):
		"""Continue returns next batch with incremented batch_seq."""
		client, token, player_id, family_id = authed_client
		await _start_and_submit(client, redis_client, mock_frappe, player_id)

		resp = await client.post("/api/v1/practice/continue")

		assert resp.status_code == 200
		data = resp.json()
		assert data["session_active"] is True
		assert data["batch_seq"] == 1
		assert isinstance(data["questions"], list)
		assert "total_available" in data
		assert "all_seen_warning" in data

	async def test_continue_previous_not_submitted_422(self, authed_client, redis_client, mock_frappe):
		"""422 when previous batch hasn't been submitted yet."""
		client, token, player_id, family_id = authed_client
		# Start but DON'T submit
		await _start_session(client, redis_client, mock_frappe, player_id)

		resp = await client.post("/api/v1/practice/continue")

		assert resp.status_code == 422
		detail = resp.json()["detail"]
		assert detail["code"] == "PREVIOUS_BATCH_NOT_SUBMITTED"
		assert detail["batch_seq"] == 0

	async def test_continue_no_session_404(self, authed_client, redis_client, mock_frappe):
		"""404 when no active session exists."""
		client, token, player_id, family_id = authed_client

		resp = await client.post("/api/v1/practice/continue")

		assert resp.status_code == 404
		assert resp.json()["detail"] == "NO_ACTIVE_SESSION"

	async def test_continue_updates_served_item_ids(self, authed_client, redis_client, mock_frappe):
		"""Continue appends new item_ids to served_item_ids in session."""
		client, token, player_id, family_id = authed_client
		questions = await _start_and_submit(client, redis_client, mock_frappe, player_id)

		# Check served_item_ids before continue
		session_before = await redis_client.hgetall(practice_session_key(player_id))
		served_before = json.loads(session_before["served_item_ids"])
		assert len(served_before) == 3  # First batch had 3 items

		# Continue
		resp = await client.post("/api/v1/practice/continue")
		assert resp.status_code == 200

		# Verify served_item_ids grew
		session_after = await redis_client.hgetall(practice_session_key(player_id))
		served_after = json.loads(session_after["served_item_ids"])
		assert len(served_after) >= len(served_before)


# ==========================================================================
# T039: Access Control
# ==========================================================================


@pytest.mark.asyncio
class TestPracticeAccessControl:
	"""T039: 403 for paid track, free content bypass, no re-check on continue."""

	async def test_paid_track_no_access_403(self, authed_client, redis_client, mock_frappe):
		"""403 NO_ACCESS for paid track without subscription."""
		client, token, player_id, family_id = authed_client

		# Hierarchy has NO free content (has_free_content=False is default)
		await seed_hierarchy(redis_client, SUBJECT_ID)
		# No access grants seeded
		mock_frappe.call.side_effect = _make_frappe_handler([])

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 403
		detail = resp.json()["detail"]
		assert detail["code"] == "NO_ACCESS"
		assert TRACK_ID in detail["tracks"]

	async def test_free_content_bypass(self, authed_client, redis_client, mock_frappe):
		"""Free content accessible without explicit grants."""
		client, token, player_id, family_id = authed_client

		# Hierarchy with free units/topics
		await seed_hierarchy(redis_client, SUBJECT_ID, has_free_content=True)
		# No access grants — but content is free
		questions = _make_question_rows(3)
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 200
		data = resp.json()
		assert data["session_active"] is True
		assert len(data["questions"]) > 0

	async def test_no_recheck_on_continue(self, authed_client, redis_client, mock_frappe):
		"""Access is not re-checked on continue — uses stored accessible_lessons."""
		client, token, player_id, family_id = authed_client
		await _start_and_submit(client, redis_client, mock_frappe, player_id)

		# Remove access grants AFTER session started
		await redis_client.delete(_access_key_fn(player_id))

		# Continue should still work — uses stored accessible_lessons from session
		resp = await client.post("/api/v1/practice/continue")
		assert resp.status_code == 200
		assert resp.json()["session_active"] is True


# ==========================================================================
# T040: Session TTL Expiry
# ==========================================================================


@pytest.mark.asyncio
class TestPracticeSessionExpiry:
	"""T040: 404 on continue/submit after session expires."""

	async def test_expired_session_submit_404(self, authed_client, redis_client, mock_frappe):
		"""404 on submit after session expires (key deleted)."""
		client, token, player_id, family_id = authed_client
		await _start_session(client, redis_client, mock_frappe, player_id)

		# Simulate TTL expiry
		await redis_client.delete(practice_session_key(player_id))

		resp = await client.post(
			"/api/v1/practice/submit",
			json={"batch_seq": 0, "results": [{"item_id": str(uuid4()), "is_correct": True}]},
		)
		assert resp.status_code == 404
		assert resp.json()["detail"] == "NO_ACTIVE_SESSION"

	async def test_expired_session_continue_404(self, authed_client, redis_client, mock_frappe):
		"""404 on continue after session expires (key deleted)."""
		client, token, player_id, family_id = authed_client
		await _start_session(client, redis_client, mock_frappe, player_id)

		# Simulate TTL expiry
		await redis_client.delete(practice_session_key(player_id))

		resp = await client.post("/api/v1/practice/continue")
		assert resp.status_code == 404
		assert resp.json()["detail"] == "NO_ACTIVE_SESSION"


# ==========================================================================
# T041: Item Deleted During Active Session
# ==========================================================================


@pytest.mark.asyncio
class TestPracticeDeletedItem:
	"""T041: Item deleted mid-session is silently skipped on submit."""

	async def test_deleted_item_skipped_on_submit(self, authed_client, redis_client, mock_frappe):
		"""Submit with deleted item: silently skipped, only valid items counted."""
		client, token, player_id, family_id = authed_client

		questions = _make_question_rows(3)
		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])

		# Items 0 and 1 still exist; item 2 is "deleted"
		valid_ids = {questions[0]["item_id"], questions[1]["item_id"]}

		mock_frappe.call.side_effect = _make_frappe_handler(questions, valid_item_ids=valid_ids)

		# Start session
		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200

		# Submit all 3 items (one deleted)
		results = [
			{"item_id": questions[0]["item_id"], "is_correct": True},
			{"item_id": questions[1]["item_id"], "is_correct": False},
			{"item_id": questions[2]["item_id"], "is_correct": True},  # Deleted
		]

		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})

		assert resp.status_code == 200
		data = resp.json()
		assert data["accepted"] is True
		# Only 2 valid items counted
		assert data["total_count"] == 2
		assert data["correct_count"] == 1
		assert data["is_duplicate"] is False


# ==========================================================================
# T042: SC-003 Performance Validation
# ==========================================================================


@pytest.mark.asyncio
class TestPracticePerformance:
	"""T042: SC-003 question selection performance (<100ms).

	Note: True <100ms validation requires real MariaDB with 5K+ Practice Log rows.
	This test validates the endpoint responds quickly with mocked data.
	Full production performance testing documented in quickstart.md.
	"""

	async def test_start_responds_within_timeout(self, authed_client, redis_client, mock_frappe):
		"""Start endpoint completes without timeout (mocked backend)."""
		import time

		client, token, player_id, family_id = authed_client

		questions = _make_question_rows(20)
		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		start = time.monotonic()
		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		elapsed_ms = (time.monotonic() - start) * 1000

		assert resp.status_code == 200
		# With mocked backend, should be well under 1000ms
		assert elapsed_ms < 1000, f"Start took {elapsed_ms:.0f}ms (expected <1000ms with mocks)"


# ==========================================================================
# T043: SC-005 + SC-006 Cross-Reference Validation
# ==========================================================================


@pytest.mark.asyncio
class TestPracticeSuccessCriteria:
	"""T043: Cross-reference success criteria validation.

	SC-005 (duplicate submission safety): Covered by TestPracticeSubmit.test_submit_idempotent_duplicate
	SC-006 (access control rejects unauthorized): Covered by TestPracticeAccessControl.test_paid_track_no_access_403
	"""

	async def test_sc005_idempotent_submit_preserves_data(self, authed_client, redis_client, mock_frappe):
		"""SC-005: Duplicate batch submit does not corrupt Practice Log."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)

		results = [
			{"item_id": questions[0]["item_id"], "is_correct": True},
			{"item_id": questions[1]["item_id"], "is_correct": False},
		]

		# First submit
		resp1 = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		assert resp1.status_code == 200
		d1 = resp1.json()
		assert d1["is_duplicate"] is False
		assert d1["correct_count"] == 1
		assert d1["total_count"] == 2

		# Duplicate submit — should not alter counts
		resp2 = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		assert resp2.status_code == 200
		d2 = resp2.json()
		assert d2["is_duplicate"] is True
		assert d2["correct_count"] == 1
		assert d2["total_count"] == 2

	async def test_sc006_access_control_rejects_unauthorized(self, authed_client, redis_client, mock_frappe):
		"""SC-006: Unauthorized content access is rejected."""
		client, token, player_id, family_id = authed_client

		await seed_hierarchy(redis_client, SUBJECT_ID)
		mock_frappe.call.side_effect = _make_frappe_handler([])

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 403
		assert resp.json()["detail"]["code"] == "NO_ACCESS"
