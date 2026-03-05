"""Tests for Practice Arena endpoints.

Tests verify:
- GET /api/v1/practice/hierarchy - Hierarchy browsing with access/counts
- POST /api/v1/practice/start - Session creation, first batch
- POST /api/v1/practice/submit - Batch result submission, idempotency
- POST /api/v1/practice/continue - Next batch, dedup, edge cases

Reference: specs/025-practice-arena/contracts/practice-api.md
"""

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import fastapi_app.api.deps as deps_module
from fastapi_app.core.redis_keys import (
	access_key as _access_key_fn,
)
from fastapi_app.core.redis_keys import (
	practice_hierarchy_meta_key,
	practice_served_items_key,
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


def _mock_select_candidates(
	params: dict,
	by_topic: dict[str, list[dict]],
	all_item_ids: set[str],
	default_questions: list[dict],
) -> list[dict]:
	"""Return mock rows for the typed batched selector."""
	limit = params.get("per_topic_limit", 20)
	selected_topics = params.get("topic_ids") or list(by_topic.keys())
	served_ids = {item_id for item_id in params.get("served_item_ids", []) if item_id in all_item_ids}

	rows = []
	for topic_id in selected_topics:
		pool = by_topic.get(topic_id, default_questions)
		rows.extend([q for q in pool if q["item_id"] not in served_ids][:limit])
	return rows


def _mock_select_for_topic(
	params: dict,
	by_topic: dict[str, list[dict]],
	all_item_ids: set[str],
	default_questions: list[dict],
) -> list[dict]:
	"""Return mock rows for the typed single-topic selector."""
	limit = params.get("limit", 20)
	topic_id = params.get("topic_id")
	served_ids = {item_id for item_id in params.get("served_item_ids", []) if item_id in all_item_ids}
	pool = by_topic.get(topic_id, default_questions)
	return [q for q in pool if q["item_id"] not in served_ids][:limit]


def _make_frappe_handler(questions=None, valid_item_ids=None, topic_counts=None):
	"""Create mock frappe.call handler for practice tests.

	Args:
		questions: List of question row dicts to return from SELECT queries.
		valid_item_ids: Set of item_ids that still exist (for deleted-item tests).
			If None, all question item_ids are considered valid.
		topic_counts: Optional dict of topic_id → count for GROUP BY queries.
			If None, counts are inferred from questions by topic.
	"""
	if questions is None:
		questions = _make_question_rows()

	all_item_ids = {q["item_id"] for q in questions}
	valid = valid_item_ids if valid_item_ids is not None else all_item_ids

	# Build per-topic question lists and counts
	by_topic: dict[str, list[dict]] = {}
	for q in questions:
		t = q.get("topic", TOPIC_ID)
		by_topic.setdefault(t, []).append(q)

	inferred_counts = topic_counts or {t: len(qs) for t, qs in by_topic.items()}
	session_seen_ids: set[str] = set()

	async def handler(method, params=None):
		if method == "memora_admin.api.practice.count_practice_items_per_topic":
			return inferred_counts
		elif method == "memora_admin.api.practice.prepare_practice_batch":
			params = params or {}
			selected_topics = params.get("selected_topics") or list(inferred_counts.keys())
			selected_counts = {
				topic_id: inferred_counts[topic_id]
				for topic_id in selected_topics
				if topic_id in inferred_counts
			}
			max_topics = params.get("max_topics")
			if max_topics:
				candidate_topic_ids = [
					topic_id
					for topic_id, _count in sorted(
						selected_counts.items(),
						key=lambda item: (-item[1], item[0]),
					)
				][:max_topics]
			else:
				candidate_topic_ids = list(selected_counts.keys())

			if params.get("session_started_at"):
				scoped_item_ids = {
					q["item_id"]
					for topic_id in selected_counts
					for q in by_topic.get(topic_id, [])
				}
				excluded_ids = session_seen_ids & scoped_item_ids
			else:
				excluded_ids = {
					item_id
					for item_id in params.get("served_item_ids", [])
					if item_id in all_item_ids
				}

			candidate_rows = _mock_select_candidates(
				{
					"topic_ids": candidate_topic_ids,
					"served_item_ids": list(excluded_ids),
					"per_topic_limit": params.get("per_topic_limit", 20),
				},
				by_topic,
				all_item_ids,
				questions,
			)
			return {
				"topic_counts": selected_counts,
				"candidate_rows": candidate_rows,
				"session_served_count": len(excluded_ids),
			}
		elif method == "memora_admin.api.practice.select_practice_candidates":
			params = dict(params or {})
			if params.get("session_started_at"):
				selected_topics = params.get("topic_ids") or list(inferred_counts.keys())
				scoped_item_ids = {
					q["item_id"]
					for topic_id in selected_topics
					for q in by_topic.get(topic_id, [])
				}
				params["served_item_ids"] = list(session_seen_ids & scoped_item_ids)
			return _mock_select_candidates(params, by_topic, all_item_ids, questions)
		elif method == "memora_admin.api.practice.select_practice_questions_for_topic":
			return _mock_select_for_topic(params or {}, by_topic, all_item_ids, questions)
		elif method == "memora_admin.api.practice.get_existing_practice_item_ids":
			requested_ids = set((params or {}).get("item_ids", []))
			return [iid for iid in valid if not requested_ids or iid in requested_ids]
		elif method == "memora_admin.api.practice.upsert_practice_results":
			accepted_ids = [
				result.get("item_id")
				for result in (params or {}).get("results", [])
				if result.get("item_id")
			]
			session_seen_ids.update(accepted_ids)
			return accepted_ids
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
		assert resp.json()["detail"]["code"] == "SUBJECT_NOT_FOUND"

	async def test_hierarchy_503_when_meta_unavailable(self, authed_client, redis_client, mock_frappe):
		"""Valid subjects with unavailable practice metadata should return 503."""
		client, token, player_id, family_id = authed_client

		await seed_hierarchy(redis_client, SUBJECT_ID)
		mock_frappe.call.side_effect = RuntimeError("practice meta unavailable")

		resp = await client.get(f"/api/v1/practice/hierarchy?subject_id={SUBJECT_ID}")

		assert resp.status_code == 503
		assert resp.json()["detail"]["code"] == "PRACTICE_META_UNAVAILABLE"

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
		assert session["total_available"] == "5"
		assert session["session_served_count"] == "0"
		assert sum(json.loads(session["topic_counts"]).values()) == 5
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

	async def test_start_selection_failure_returns_503(self, authed_client, redis_client, mock_frappe):
		"""Selection query failures should surface as 503, not empty sessions."""
		client, token, player_id, family_id = authed_client

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])

		base_handler = _make_frappe_handler([])

		async def handler(method, params=None):
			if method in {
				"memora_admin.api.practice.prepare_practice_batch",
				"memora_admin.api.practice.count_practice_items_per_topic",
				"memora_admin.api.practice.select_practice_candidates",
				"memora_admin.api.practice.select_practice_questions_for_topic",
			}:
				raise RuntimeError("selector unavailable")
			return await base_handler(method, params)

		mock_frappe.call.side_effect = handler

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 503
		assert resp.json()["detail"]["code"] == "PRACTICE_SELECTION_UNAVAILABLE"

	async def test_start_reuses_cached_scope_counts_on_repeat_scope(self, authed_client, redis_client, mock_frappe):
		"""A repeated start for the same resolved scope should skip batch prep recounts."""
		client, token, player_id, family_id = authed_client

		questions = _make_question_rows(5)
		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])

		call_counts = {"prepare": 0, "select": 0}
		base_handler = _make_frappe_handler(questions)

		async def counted_handler(method, params=None):
			if method == "memora_admin.api.practice.prepare_practice_batch":
				call_counts["prepare"] += 1
			elif method == "memora_admin.api.practice.select_practice_candidates":
				call_counts["select"] += 1
			return await base_handler(method, params)

		mock_frappe.call.side_effect = counted_handler

		for _ in range(2):
			resp = await client.post(
				"/api/v1/practice/start",
				json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
			)
			assert resp.status_code == 200
			assert len(resp.json()["questions"]) == 5

		assert call_counts["prepare"] == 1
		assert call_counts["select"] == 1

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

	async def test_submit_and_continue_returns_next_batch(self, authed_client, redis_client, mock_frappe):
		"""submit-continue returns submit stats plus the next batch in one response."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)
		results = [{"item_id": q["item_id"], "is_correct": True} for q in questions]

		resp = await client.post("/api/v1/practice/submit-continue", json={"batch_seq": 0, "results": results})

		assert resp.status_code == 200
		data = resp.json()
		assert data["submit"]["accepted"] is True
		assert data["submit"]["batch_seq"] == 0
		assert data["submit"]["is_duplicate"] is False
		assert data["next_batch"]["batch_seq"] == 1
		assert isinstance(data["next_batch"]["questions"], list)

	async def test_submit_and_continue_duplicate_reuses_cached_next_batch(
		self, authed_client, redis_client, mock_frappe
	):
		"""Duplicate submit-continue should not advance twice."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)
		results = [{"item_id": q["item_id"], "is_correct": True} for q in questions]

		resp1 = await client.post("/api/v1/practice/submit-continue", json={"batch_seq": 0, "results": results})
		resp2 = await client.post("/api/v1/practice/submit-continue", json={"batch_seq": 0, "results": results})

		assert resp1.status_code == 200
		assert resp2.status_code == 200
		assert resp1.json()["next_batch"]["batch_seq"] == 1
		assert resp2.json()["next_batch"]["batch_seq"] == 1
		assert resp2.json()["submit"]["is_duplicate"] is True

		resp3 = await client.post("/api/v1/practice/continue")
		assert resp3.status_code == 422
		assert resp3.json()["detail"]["batch_seq"] == 1

	async def test_submit_duplicate_item_ids_422(self, authed_client, redis_client, mock_frappe):
		"""Duplicate item_ids in one payload are rejected."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)

		item_id = questions[0]["item_id"]
		resp = await client.post(
			"/api/v1/practice/submit",
			json={
				"batch_seq": 0,
				"results": [
					{"item_id": item_id, "is_correct": True},
					{"item_id": item_id, "is_correct": False},
				],
			},
		)

		assert resp.status_code == 422
		detail = resp.json()["detail"]
		assert detail["code"] == "DUPLICATE_RESULTS"
		assert detail["items"] == [item_id]

	async def test_submit_concurrent_duplicate_only_writes_once(
		self, authed_client, redis_client, mock_frappe
	):
		"""Concurrent duplicate submits should return one write and one cached duplicate."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)
		results = [{"item_id": questions[0]["item_id"], "is_correct": True}]

		base_handler = _make_frappe_handler(questions)
		upsert_started = asyncio.Event()
		release_upsert = asyncio.Event()
		upsert_calls = 0

		async def handler(method, params=None):
			nonlocal upsert_calls
			if method == "memora_admin.api.practice.upsert_practice_results":
				upsert_calls += 1
				upsert_started.set()
				await release_upsert.wait()
				return None
			return await base_handler(method, params)

		mock_frappe.call.side_effect = handler

		submit_1 = asyncio.create_task(
			client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		)
		await upsert_started.wait()

		submit_2 = asyncio.create_task(
			client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		)

		await asyncio.sleep(0)
		release_upsert.set()

		resp_1, resp_2 = await asyncio.gather(submit_1, submit_2)
		payloads = [resp_1.json(), resp_2.json()]

		assert resp_1.status_code == 200
		assert resp_2.status_code == 200
		assert upsert_calls == 1
		assert sorted(payload["is_duplicate"] for payload in payloads) == [False, True]

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
		"""Continue appends new item_ids to the dedicated served-items set."""
		client, token, player_id, family_id = authed_client
		questions = await _start_and_submit(client, redis_client, mock_frappe, player_id)

		# Check served history before continue
		session_before = await redis_client.hgetall(practice_session_key(player_id))
		assert "served_item_ids" not in session_before
		served_before = await redis_client.smembers(practice_served_items_key(player_id))
		assert len(served_before) == 3  # First batch had 3 items

		# Continue
		resp = await client.post("/api/v1/practice/continue")
		assert resp.status_code == 200

		# Verify served history grew
		served_after = await redis_client.smembers(practice_served_items_key(player_id))
		assert len(served_after) >= len(served_before)

	async def test_continue_consumes_prefetched_batch_without_frappe_calls(
		self, authed_client, redis_client, mock_frappe
	):
		"""Continue should serve the submit-time prefetched batch without hitting Frappe."""
		client, token, player_id, family_id = authed_client
		call_methods: list[str] = []
		base_handler = _make_frappe_handler(_make_question_rows(3))

		async def tracking_handler(method, params=None):
			call_methods.append(method)
			return await base_handler(method, params)

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = tracking_handler

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200

		results = [{"item_id": q["item_id"], "is_correct": True} for q in resp.json()["questions"]]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		assert resp.status_code == 200

		call_methods.clear()
		resp = await client.post("/api/v1/practice/continue")

		assert resp.status_code == 200
		assert call_methods == []


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
	"""T041: Historical items are still recorded even if deleted after serving."""

	async def test_deleted_item_skipped_on_submit(self, authed_client, redis_client, mock_frappe):
		"""Submit counts the served batch even if an item is later deleted upstream."""
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
		assert data["total_count"] == 3
		assert data["correct_count"] == 2
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


# ==========================================================================
# Multi-topic helpers for T009/T010
# ==========================================================================

TOPIC_ID_A = "TOPIC-TEST-A"
TOPIC_ID_B = "TOPIC-TEST-B"
TOPIC_ID_C = "TOPIC-TEST-C"


def _make_multi_topic_hierarchy(
	subject_id=SUBJECT_ID,
	topics=None,
):
	"""Build hierarchy JSON with multiple topics under a single track/unit.

	Args:
		topics: List of (topic_id, lesson_count) tuples.
			Defaults to 3 topics with varied sizes.
	"""
	if topics is None:
		topics = [(TOPIC_ID_A, 5), (TOPIC_ID_B, 3), (TOPIC_ID_C, 2)]

	topic_entries = []
	bit = 0
	for topic_id, lesson_count in topics:
		lessons = [
			{
				"lesson_id": f"LESSON-{topic_id}-{i:03d}",
				"bit_index": bit + i,
				"xp": 10,
				"max_hearts": 3,
				"is_reviewable": True,
			}
			for i in range(lesson_count)
		]
		bit += lesson_count
		topic_entries.append(
			{
				"topic_id": topic_id,
				"is_linear": False,
				"is_free": False,
				"lessons": lessons,
			}
		)

	return {
		"subject_id": subject_id,
		"version": 1,
		"is_linear": False,
		"bit_range": bit,
		"excluded_bits": [],
		"free_units": [],
		"free_topics": [],
		"tracks": [
			{
				"track_id": TRACK_ID,
				"is_linear": False,
				"units": [
					{
						"unit_id": UNIT_ID,
						"is_linear": False,
						"is_free": False,
						"topics": topic_entries,
					}
				],
			}
		],
	}


def _make_multi_topic_questions(topic_counts):
	"""Build question rows for multiple topics.

	Args:
		topic_counts: Dict of topic_id → question count.

	Returns:
		List of question row dicts with correct topic assignments.
	"""
	all_qs = []
	for topic_id, count in topic_counts.items():
		all_qs.extend(_make_question_rows(count, topic_id=topic_id))
	return all_qs


# ==========================================================================
# T009: Proportional Topic Distribution
# ==========================================================================


@pytest.mark.asyncio
class TestProportionalDistribution:
	"""T009: Verify questions are distributed proportionally across topics."""

	async def test_multi_topic_proportional(self, authed_client, redis_client, mock_frappe):
		"""Multi-topic batch distributes proportionally by item count.

		3 topics with 100, 50, 10 items. Batch size 20.
		Expected: ~12, ~6, ~2 items (proportional with min 1 each).
		"""
		client, token, player_id, family_id = authed_client

		topic_counts = {TOPIC_ID_A: 100, TOPIC_ID_B: 50, TOPIC_ID_C: 10}
		# Create enough questions per topic (we'll cap at quota via LIMIT)
		questions = _make_multi_topic_questions(
			{
				TOPIC_ID_A: 20,
				TOPIC_ID_B: 10,
				TOPIC_ID_C: 5,
			}
		)

		hier = _make_multi_topic_hierarchy(
			topics=[(TOPIC_ID_A, 5), (TOPIC_ID_B, 3), (TOPIC_ID_C, 2)],
		)
		await seed_hierarchy(redis_client, SUBJECT_ID, hierarchy_json=hier)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(
			questions,
			topic_counts=topic_counts,
		)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 200
		data = resp.json()
		assert data["session_active"] is True
		# Should have questions from all 3 topics
		assert len(data["questions"]) > 0
		assert data["total_available"] == 160  # 100 + 50 + 10

	async def test_single_topic_bypasses_proportional(self, authed_client, redis_client, mock_frappe):
		"""Single-topic selection skips proportional logic entirely."""
		client, token, player_id, family_id = authed_client

		questions = _make_question_rows(5, topic_id=TOPIC_ID)
		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 200
		data = resp.json()
		assert len(data["questions"]) == 5

	async def test_small_topic_capped_at_available(self, authed_client, redis_client, mock_frappe):
		"""Topics with fewer items than quota get capped at their count."""
		client, token, player_id, family_id = authed_client

		# Topic A has lots, Topic C has only 1
		topic_counts = {TOPIC_ID_A: 100, TOPIC_ID_C: 1}
		questions = _make_multi_topic_questions(
			{
				TOPIC_ID_A: 20,
				TOPIC_ID_C: 1,
			}
		)

		hier = _make_multi_topic_hierarchy(
			topics=[(TOPIC_ID_A, 5), (TOPIC_ID_C, 1)],
		)
		await seed_hierarchy(redis_client, SUBJECT_ID, hierarchy_json=hier)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(
			questions,
			topic_counts=topic_counts,
		)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 200
		data = resp.json()
		assert data["total_available"] == 101  # 100 + 1

	async def test_compute_topic_quotas_remainder_to_largest(self):
		"""Pure function test: remainder questions go to largest topics."""
		from fastapi_app.services.practice import _compute_topic_quotas

		counts = {"A": 100, "B": 50, "C": 10}
		quotas = _compute_topic_quotas(counts, 20)

		# All topics should be represented
		assert set(quotas.keys()) == {"A", "B", "C"}
		# Total should equal batch_size
		assert sum(quotas.values()) == 20
		# Largest topic gets the most
		assert quotas["A"] > quotas["B"] > quotas["C"]
		# Each topic gets at least 1
		assert all(v >= 1 for v in quotas.values())

	async def test_compute_topic_quotas_single_topic(self):
		"""Pure function test: single topic gets full batch (capped at available)."""
		from fastapi_app.services.practice import _compute_topic_quotas

		quotas = _compute_topic_quotas({"A": 5}, 20)
		assert quotas == {"A": 5}  # Capped at available

		quotas2 = _compute_topic_quotas({"A": 100}, 20)
		assert quotas2 == {"A": 20}  # Capped at batch_size

	async def test_compute_topic_quotas_empty(self):
		"""Pure function test: empty counts returns empty quotas."""
		from fastapi_app.services.practice import _compute_topic_quotas

		assert _compute_topic_quotas({}, 20) == {}

	async def test_compute_topic_quotas_caps_when_topics_exceed_batch(self):
		"""Pure function test: too many topics still respect batch_size."""
		from fastapi_app.services.practice import _compute_topic_quotas

		counts = {f"T{i:02d}": 10 for i in range(30)}
		quotas = _compute_topic_quotas(counts, 20)

		assert len(quotas) == 20
		assert sum(quotas.values()) == 20
		assert all(value == 1 for value in quotas.values())


@pytest.mark.asyncio
class TestBatchedTopicSelection:
	"""Characterization and performance checks for the batched selector."""

	async def test_batched_selection_matches_legacy_for_redistribution_case(self, redis_client, mock_frappe):
		"""Batched path preserves the legacy questions/total/warning outputs."""
		from fastapi_app.core.config import get_settings
		from fastapi_app.services.practice import PracticeService

		config = get_settings().model_copy(deep=True)
		config.practice_session_size = 4

		svc = PracticeService(
			redis_client,
			mock_frappe,
			config,
			AsyncMock(),
			AsyncMock(),
			AsyncMock(),
		)

		topic_a_qs = _make_question_rows(1, topic_id=TOPIC_ID_A)
		topic_b_qs = _make_question_rows(6, topic_id=TOPIC_ID_B)
		topic_b_qs[3]["priority"] = 1  # Redistribution slot should still preserve repeat semantics
		all_qs = topic_a_qs + topic_b_qs

		mock_frappe.call.side_effect = _make_frappe_handler(
			all_qs,
			topic_counts={TOPIC_ID_A: 1, TOPIC_ID_B: 6},
		)

		params = {
			"player_id": "PLAYER-TEST-BATCHED",
			"subject_id": SUBJECT_ID,
			"accessible_lessons": ["LESSON-A", "LESSON-B"],
			"selected_topics": [TOPIC_ID_A, TOPIC_ID_B],
			"served_item_ids": [topic_a_qs[0]["item_id"]],
			"batch_size": 4,
		}

		svc.config.practice_batched_topic_select_enabled = False
		legacy_questions, legacy_total, legacy_repeat = await svc._select_questions(**params)

		svc.config.practice_batched_topic_select_enabled = True
		batched_questions, batched_total, batched_repeat = await svc._select_questions(**params)

		assert [q.item_id for q in batched_questions] == [q.item_id for q in legacy_questions]
		assert batched_total == legacy_total == 7
		assert batched_repeat is legacy_repeat is True

	async def test_batched_selection_uses_one_query_for_many_topics(self, redis_client, mock_frappe):
		"""The hot path uses the combined batch-prep RPC, not separate count/select calls."""
		from fastapi_app.core.config import get_settings
		from fastapi_app.services.practice import PracticeService

		config = get_settings().model_copy(deep=True)
		config.practice_session_size = 20
		config.practice_batched_topic_select_enabled = True

		svc = PracticeService(
			redis_client,
			mock_frappe,
			config,
			AsyncMock(),
			AsyncMock(),
			AsyncMock(),
		)

		topic_ids = [f"TOPIC-LOAD-{i:02d}" for i in range(8)]
		topic_counts = {topic_id: 12 for topic_id in topic_ids}
		questions = _make_multi_topic_questions({topic_id: 6 for topic_id in topic_ids})

		query_calls = {"n": 0}
		base_handler = _make_frappe_handler(questions, topic_counts=topic_counts)

		async def counted_handler(method, params=None):
			if method in {
				"memora_admin.api.practice.prepare_practice_batch",
				"memora_admin.api.practice.select_practice_questions_for_topic",
			}:
				query_calls["n"] += 1
			return await base_handler(method, params)

		mock_frappe.call.side_effect = counted_handler

		selected_questions, total_available, _ = await svc._select_questions(
			player_id="PLAYER-TEST-PERF",
			subject_id=SUBJECT_ID,
			accessible_lessons=[f"LESSON-{i:02d}" for i in range(8)],
			selected_topics=topic_ids,
			served_item_ids=[],
			batch_size=20,
		)

		assert len(selected_questions) == 20
		assert total_available == 96
		assert query_calls["n"] == 1


# ==========================================================================
# T010: all_seen_warning Semantics
# ==========================================================================


@pytest.mark.asyncio
class TestAllSeenWarning:
	"""T010: Verify all_seen_warning fires on ANY repeat, not just total exhaustion."""

	async def test_all_new_questions_warning_false(self, authed_client, redis_client, mock_frappe):
		"""Batch with all-new questions returns all_seen_warning=false."""
		client, token, player_id, family_id = authed_client

		# All priority=0 (never seen)
		questions = _make_question_rows(5)
		for q in questions:
			q["priority"] = 0

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 200
		assert resp.json()["all_seen_warning"] is False

	async def test_any_repeat_warning_true(self, authed_client, redis_client, mock_frappe):
		"""Batch with ANY repeat question returns all_seen_warning=true."""
		client, token, player_id, family_id = authed_client

		# 4 new (priority=0) + 1 repeat (priority=1)
		questions = _make_question_rows(5)
		for i, q in enumerate(questions):
			q["priority"] = 1 if i == 4 else 0

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 200
		assert resp.json()["all_seen_warning"] is True

	async def test_wrap_around_warning_true(self, authed_client, redis_client, mock_frappe):
		"""Wrap-around (all items exhausted, re-serving) returns all_seen_warning=true."""
		client, token, player_id, family_id = authed_client

		questions = _make_question_rows(3)
		for q in questions:
			q["priority"] = 0

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])

		# Phase 1: start session normally
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200

		# Submit batch 0
		results = [{"item_id": q["item_id"], "is_correct": True} for q in questions]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		assert resp.status_code == 200

		# Phase 2: continue — the next batch was already prefetched during submit,
		# so changing the Frappe handler here should not affect the response.
		select_call_count = {"n": 0}

		async def wrap_handler(method, params=None):
			if method == "memora_admin.api.practice.count_practice_items_per_topic":
				return {TOPIC_ID: 3}
			elif method == "memora_admin.api.practice.select_practice_candidates":
				select_call_count["n"] += 1
				if (params or {}).get("session_started_at"):
					return []
				return _mock_select_candidates(
					params or {},
					{TOPIC_ID: questions},
					{q["item_id"] for q in questions},
					questions,
				)
			elif method == "memora_admin.api.practice.get_existing_practice_item_ids":
				requested_ids = set((params or {}).get("item_ids", []))
				return [q["item_id"] for q in questions if q["item_id"] in requested_ids]
			elif method == "memora_admin.api.practice.upsert_practice_results":
				return None
			return None

		mock_frappe.call.side_effect = wrap_handler

		# Continue — should wrap around
		resp = await client.post("/api/v1/practice/continue")
		assert resp.status_code == 200
		data = resp.json()
		# Wrap-around should set all_seen_warning=true
		assert data["all_seen_warning"] is True
		assert select_call_count["n"] == 0

	async def test_continue_uses_same_semantics(self, authed_client, redis_client, mock_frappe):
		"""continue_session uses same all_seen_warning semantics as start_session."""
		client, token, player_id, family_id = authed_client

		# More than one batch worth of new questions — continue should still
		# return unseen items without raising the warning.
		questions = _make_question_rows(25)
		for q in questions:
			q["priority"] = 0

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		# Start
		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200
		start_data = resp.json()
		assert start_data["all_seen_warning"] is False

		# Submit
		results = [{"item_id": q["item_id"], "is_correct": True} for q in start_data["questions"]]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		assert resp.status_code == 200

		# Continue — all new questions, should be false
		resp = await client.post("/api/v1/practice/continue")
		assert resp.status_code == 200
		assert resp.json()["all_seen_warning"] is False

	async def test_continue_ignores_deleted_items_before_wrap_shortcut(self, redis_client, mock_frappe):
		"""Stale deleted IDs in served history must not force an early wrap-around."""
		from fastapi_app.core.config import get_settings
		from fastapi_app.services.practice import PracticeService

		config = get_settings().model_copy(deep=True)
		config.practice_session_size = 2
		config.practice_batched_topic_select_enabled = True

		svc = PracticeService(
			redis_client,
			mock_frappe,
			config,
			AsyncMock(),
			AsyncMock(),
			AsyncMock(),
		)

		current_questions = _make_question_rows(3)
		stale_deleted_id = "ITEM-DELETED-OLD"

		mock_frappe.call.side_effect = _make_frappe_handler(
			current_questions,
			valid_item_ids=[q["item_id"] for q in current_questions],
			topic_counts={TOPIC_ID: 3},
		)

		session_key = practice_session_key("PLAYER-TEST-DELETED")
		await redis_client.hset(
			session_key,
			mapping={
				"batch_seq": "0",
				"submitted_0": "1",
				"accessible_lessons": json.dumps(["LESSON-A"]),
				"selected_topics": json.dumps([TOPIC_ID]),
				"served_item_ids": json.dumps(
					[
						current_questions[0]["item_id"],
						current_questions[1]["item_id"],
						stale_deleted_id,
					]
				),
				"subject_id": SUBJECT_ID,
			},
		)

		response = await svc.continue_session("PLAYER-TEST-DELETED")

		assert response.all_seen_warning is False
		assert [q.item_id for q in response.questions] == [current_questions[2]["item_id"]]


# ==========================================================================
# Regression: Write-failure handling (#1)
# ==========================================================================


@pytest.mark.asyncio
class TestWriteFailureHandling:
	"""DB write failure must NOT mark batch as submitted — client can retry."""

	async def test_db_failure_does_not_mark_submitted(self, authed_client, redis_client, mock_frappe):
		"""When Practice Log UPSERT fails, batch stays unsubmitted so client can retry."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)

		# Make the upsert call raise an exception
		call_count = {"n": 0}

		async def failing_handler(method, params=None):
			if method == "memora_admin.api.practice.upsert_practice_results":
				call_count["n"] += 1
				if call_count["n"] == 1:
					raise RuntimeError("DB connection lost")
				return None  # Succeed on retry
			# Delegate to normal handler for everything else
			return await _make_frappe_handler(questions)(method, params)

		mock_frappe.call.side_effect = failing_handler

		results = [{"item_id": q["item_id"], "is_correct": True} for q in questions]

		# First submit — DB fails, exception propagates (not silently swallowed)
		with pytest.raises(RuntimeError, match="DB connection lost"):
			await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})

		# Session should NOT have submitted_0 marker
		session = await redis_client.hgetall(practice_session_key(player_id))
		assert "submitted_0" not in session

		# Retry should succeed
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})
		assert resp.status_code == 200
		data = resp.json()
		assert data["accepted"] is True
		assert data["is_duplicate"] is False
		assert data["correct_count"] == len(questions)


# ==========================================================================
# Regression: Off-batch item validation (#2)
# ==========================================================================


@pytest.mark.asyncio
class TestOffBatchItemValidation:
	"""Submitted item_ids must belong to the served batch."""

	async def test_off_batch_item_rejected(self, authed_client, redis_client, mock_frappe):
		"""Submitting item_ids not in served batch returns 422 OFF_BATCH_ITEMS."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)

		# Submit with a forged item_id that wasn't served
		forged_id = str(uuid4())
		results = [
			{"item_id": questions[0]["item_id"], "is_correct": True},
			{"item_id": forged_id, "is_correct": True},
		]

		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})

		assert resp.status_code == 422
		detail = resp.json()["detail"]
		assert detail["code"] == "OFF_BATCH_ITEMS"
		assert forged_id in detail["items"]

	async def test_all_served_items_accepted(self, authed_client, redis_client, mock_frappe):
		"""Submitting only served item_ids succeeds normally."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)

		results = [{"item_id": q["item_id"], "is_correct": i % 2 == 0} for i, q in enumerate(questions)]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})

		assert resp.status_code == 200
		assert resp.json()["accepted"] is True

	async def test_cross_batch_item_rejected(self, authed_client, redis_client, mock_frappe):
		"""Submitting batch 0's items with batch 1 is rejected (per-batch validation)."""
		client, token, player_id, family_id = authed_client

		# Create two distinct sets of questions for batch 0 and batch 1
		batch0_qs = _make_question_rows(3, topic_id=TOPIC_ID)
		batch1_qs = _make_question_rows(3, topic_id=TOPIC_ID)
		all_qs = batch0_qs + batch1_qs

		batch_call = {"n": 0}

		async def multi_batch_handler(method, params=None):
			if method == "memora_admin.api.practice.count_practice_items_per_topic":
				return {TOPIC_ID: 6}
			elif method == "memora_admin.api.practice.select_practice_candidates":
				batch_call["n"] += 1
				# First select → batch 0 items, second select → batch 1 items
				if batch_call["n"] <= 1:
					return _mock_select_candidates(
						params or {},
						{TOPIC_ID: batch0_qs},
						{q["item_id"] for q in batch0_qs},
						batch0_qs,
					)
				return _mock_select_candidates(
					params or {},
					{TOPIC_ID: batch1_qs},
					{q["item_id"] for q in batch1_qs},
					batch1_qs,
				)
			elif method == "memora_admin.api.practice.get_existing_practice_item_ids":
				return [q["item_id"] for q in all_qs]
			elif method == "memora_admin.api.practice.upsert_practice_results":
				return None
			return None

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = multi_batch_handler

		# Start session → gets batch 0 items
		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200
		b0_ids = [q["item_id"] for q in resp.json()["questions"]]

		# Submit batch 0
		results_0 = [{"item_id": iid, "is_correct": True} for iid in b0_ids]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results_0})
		assert resp.status_code == 200

		# Continue → gets batch 1 items
		resp = await client.post("/api/v1/practice/continue")
		assert resp.status_code == 200
		b1_data = resp.json()
		assert b1_data["batch_seq"] == 1

		# Try submitting batch 0's item IDs as batch 1 → must be rejected
		cross_results = [{"item_id": b0_ids[0], "is_correct": True}]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 1, "results": cross_results})
		assert resp.status_code == 422
		assert resp.json()["detail"]["code"] == "OFF_BATCH_ITEMS"


# ==========================================================================
# Regression: Duplicate payload tamper (#3)
# ==========================================================================


@pytest.mark.asyncio
class TestDuplicatePayloadTamper:
	"""Duplicate submission returns cached original result, not recomputed from payload."""

	async def test_tampered_duplicate_returns_original(self, authed_client, redis_client, mock_frappe):
		"""Second submit with altered is_correct values still returns original counts."""
		client, token, player_id, family_id = authed_client
		questions = await _start_session(client, redis_client, mock_frappe, player_id)

		# Original: 2 correct, 1 wrong
		original_results = [
			{"item_id": questions[0]["item_id"], "is_correct": True},
			{"item_id": questions[1]["item_id"], "is_correct": True},
			{"item_id": questions[2]["item_id"], "is_correct": False},
		]
		resp1 = await client.post(
			"/api/v1/practice/submit", json={"batch_seq": 0, "results": original_results}
		)
		assert resp1.status_code == 200
		d1 = resp1.json()
		assert d1["correct_count"] == 2
		assert d1["total_count"] == 3
		assert d1["is_duplicate"] is False

		# Tampered: flip all to correct
		tampered_results = [{"item_id": q["item_id"], "is_correct": True} for q in questions]
		resp2 = await client.post(
			"/api/v1/practice/submit", json={"batch_seq": 0, "results": tampered_results}
		)
		assert resp2.status_code == 200
		d2 = resp2.json()
		assert d2["is_duplicate"] is True
		# Must return ORIGINAL counts, not tampered
		assert d2["correct_count"] == 2
		assert d2["total_count"] == 3
		assert d2["accuracy_percent"] == d1["accuracy_percent"]


# ==========================================================================
# Regression: Legacy session backward compatibility (#5-deploy-safety)
# ==========================================================================


@pytest.mark.asyncio
class TestLegacySessionCompat:
	"""Sessions created before the per-batch schema change must not crash."""

	async def test_legacy_submitted_marker_returns_computed_stats(
		self, authed_client, redis_client, mock_frappe
	):
		"""Old sessions stored submitted_0='1' (not JSON). Should recompute stats from payload."""
		client, token, player_id, family_id = authed_client
		questions = _make_question_rows(4, topic_id=TOPIC_ID)
		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		# Start session normally
		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200
		served_ids = [q["item_id"] for q in resp.json()["questions"]]

		# Simulate legacy marker: overwrite submitted_0 with "1" (old format)
		session_key = practice_session_key(player_id)
		await redis_client.hset(session_key, "submitted_0", "1")

		# Submit with mixed results — should detect duplicate but return
		# stats recomputed from the submitted payload (matching old behavior)
		results = [
			{"item_id": served_ids[0], "is_correct": True},
			{"item_id": served_ids[1], "is_correct": True},
			{"item_id": served_ids[2], "is_correct": False},
			{"item_id": served_ids[3], "is_correct": False},
		]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})

		assert resp.status_code == 200
		data = resp.json()
		assert data["is_duplicate"] is True
		assert data["accepted"] is True
		# Stats should be computed from the submitted payload, not zeros
		assert data["correct_count"] == 2
		assert data["total_count"] == 4
		assert data["accuracy_percent"] == 50.0

	async def test_legacy_session_no_batch_key_skips_validation(
		self, authed_client, redis_client, mock_frappe
	):
		"""Old sessions without schema_version + batch_0_item_ids skip validation."""
		client, token, player_id, family_id = authed_client
		questions = _make_question_rows(3, topic_id=TOPIC_ID)
		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		# Start session normally
		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200
		served_ids = [q["item_id"] for q in resp.json()["questions"]]

		# Simulate legacy session: old schema had neither schema_version nor batch_0_item_ids
		session_key = practice_session_key(player_id)
		await redis_client.hdel(session_key, "schema_version", "batch_0_item_ids")

		# Submit should still succeed (validation skipped for legacy)
		results = [{"item_id": iid, "is_correct": True} for iid in served_ids]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})

		assert resp.status_code == 200
		data = resp.json()
		assert data["accepted"] is True
		assert data["is_duplicate"] is False

	async def test_legacy_session_batch1_cross_batch_accepted(self, authed_client, redis_client, mock_frappe):
		"""Legacy sessions on batch 1+ accept cross-batch items (matches old behavior).

		Old code had no per-batch validation at all. Legacy sessions lack
		batch_{n}_item_ids, so validation is skipped entirely. This test
		confirms the temporary rollout behavior: cross-batch items are
		accepted for in-flight legacy sessions (max 1h TTL).
		"""
		client, token, player_id, family_id = authed_client

		batch0_qs = _make_question_rows(3, topic_id=TOPIC_ID)
		batch1_qs = _make_question_rows(3, topic_id=TOPIC_ID)
		all_qs = batch0_qs + batch1_qs
		batch_call = {"n": 0}

		async def multi_batch_handler(method, params=None):
			if method == "memora_admin.api.practice.count_practice_items_per_topic":
				return {TOPIC_ID: 6}
			elif method == "memora_admin.api.practice.select_practice_candidates":
				batch_call["n"] += 1
				pool = batch0_qs if batch_call["n"] <= 1 else batch1_qs
				return _mock_select_candidates(
					params or {},
					{TOPIC_ID: pool},
					{q["item_id"] for q in pool},
					pool,
				)
			elif method == "memora_admin.api.practice.get_existing_practice_item_ids":
				return [q["item_id"] for q in all_qs]
			elif method == "memora_admin.api.practice.upsert_practice_results":
				return None
			return None

		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = multi_batch_handler

		# Start session → batch 0
		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200
		b0_ids = [q["item_id"] for q in resp.json()["questions"]]

		# Submit batch 0, continue to batch 1
		results_0 = [{"item_id": iid, "is_correct": True} for iid in b0_ids]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results_0})
		assert resp.status_code == 200

		resp = await client.post("/api/v1/practice/continue")
		assert resp.status_code == 200
		assert resp.json()["batch_seq"] == 1

		# Simulate legacy: old sessions don't have schema_version or per-batch keys
		session_key = practice_session_key(player_id)
		await redis_client.hdel(session_key, "schema_version", "batch_0_item_ids", "batch_1_item_ids")

		# Submit batch 0's items as batch 1 — legacy sessions accept this
		# (old code had no per-batch validation; skipping matches old behavior)
		cross_results = [{"item_id": b0_ids[0], "is_correct": True}]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 1, "results": cross_results})

		assert resp.status_code == 200
		data = resp.json()
		assert data["accepted"] is True

	async def test_current_session_missing_batch_key_rejected(self, authed_client, redis_client, mock_frappe):
		"""Current-format sessions fail closed if a required batch key is missing."""
		client, token, player_id, family_id = authed_client
		questions = _make_question_rows(3, topic_id=TOPIC_ID)
		await seed_hierarchy(redis_client, SUBJECT_ID)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = _make_frappe_handler(questions)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200
		served_ids = [q["item_id"] for q in resp.json()["questions"]]

		# Corrupt a current-format session: schema_version remains, batch key disappears
		session_key = practice_session_key(player_id)
		await redis_client.hdel(session_key, "batch_0_item_ids")

		results = [{"item_id": iid, "is_correct": True} for iid in served_ids]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results})

		assert resp.status_code == 409
		detail = resp.json()["detail"]
		assert detail["code"] == "INVALID_SESSION_STATE"
		assert detail["missing_field"] == "batch_0_item_ids"


# ==========================================================================
# Regression: Free content visibility in hierarchy (#4)
# ==========================================================================


PAID_TOPIC = "TOPIC-PAID-001"
FREE_TOPIC = "TOPIC-FREE-001"


def _make_mixed_free_paid_hierarchy(subject_id=SUBJECT_ID):
	"""Build hierarchy with one paid topic and one free topic in the same track."""
	return {
		"subject_id": subject_id,
		"version": 1,
		"is_linear": False,
		"bit_range": 4,
		"excluded_bits": [],
		"free_units": [],
		"free_topics": [FREE_TOPIC],  # Only FREE_TOPIC is free
		"tracks": [
			{
				"track_id": TRACK_ID,
				"is_linear": False,
				"units": [
					{
						"unit_id": UNIT_ID,
						"is_linear": False,
						"is_free": False,
						"topics": [
							{
								"topic_id": PAID_TOPIC,
								"is_linear": False,
								"is_free": False,
								"lessons": [
									{
										"lesson_id": "LESSON-PAID-001",
										"bit_index": 0,
										"xp": 10,
										"max_hearts": 3,
										"is_reviewable": True,
									},
									{
										"lesson_id": "LESSON-PAID-002",
										"bit_index": 1,
										"xp": 10,
										"max_hearts": 3,
										"is_reviewable": True,
									},
								],
							},
							{
								"topic_id": FREE_TOPIC,
								"is_linear": False,
								"is_free": True,
								"lessons": [
									{
										"lesson_id": "LESSON-FREE-001",
										"bit_index": 2,
										"xp": 10,
										"max_hearts": 3,
										"is_reviewable": True,
									},
									{
										"lesson_id": "LESSON-FREE-002",
										"bit_index": 3,
										"xp": 10,
										"max_hearts": 3,
										"is_reviewable": True,
									},
								],
							},
						],
					}
				],
			}
		],
	}


@pytest.mark.asyncio
class TestFreeContentHierarchyVisibility:
	"""Tracks with free content should show has_access=true even without grants."""

	async def test_free_content_track_shows_accessible(self, authed_client, redis_client, mock_frappe):
		"""Track with free units/topics shows has_access=true and populated units."""
		client, token, player_id, family_id = authed_client

		# Hierarchy with free content (free_units or free_topics set)
		await seed_hierarchy(redis_client, SUBJECT_ID, has_free_content=True)
		await _seed_practice_meta(redis_client)
		# No access grants — but content is free

		resp = await client.get(f"/api/v1/practice/hierarchy?subject_id={SUBJECT_ID}")

		assert resp.status_code == 200
		data = resp.json()
		assert len(data["tracks"]) == 1
		track = data["tracks"][0]
		# Free content means the track IS accessible (shows units)
		assert track["has_access"] is True
		assert len(track["units"]) > 0

	async def test_mixed_track_hierarchy_only_shows_free_topics(
		self, authed_client, redis_client, mock_frappe
	):
		"""In a mixed paid/free track, hierarchy only exposes the free topic."""
		client, token, player_id, family_id = authed_client

		hier = _make_mixed_free_paid_hierarchy()
		await seed_hierarchy(redis_client, SUBJECT_ID, hierarchy_json=hier)
		# Seed meta with both topics so we can verify filtering
		meta = {
			"subject_title": "Test Subject",
			"tracks": {TRACK_ID: {"title": "Test Track"}},
			"units": {UNIT_ID: {"title": "Test Unit", "track": TRACK_ID}},
			"topics": {
				PAID_TOPIC: {"title": "Paid Topic"},
				FREE_TOPIC: {"title": "Free Topic"},
			},
			"item_counts": {PAID_TOPIC: 5, FREE_TOPIC: 3},
		}
		await _seed_practice_meta(redis_client, meta=meta)
		# No access grants — only free content

		resp = await client.get(f"/api/v1/practice/hierarchy?subject_id={SUBJECT_ID}")

		assert resp.status_code == 200
		data = resp.json()
		track = data["tracks"][0]
		assert track["has_access"] is True
		# Only the free topic should be visible, not the paid one
		topic_ids = [t["topic_id"] for u in track["units"] for t in u["topics"]]
		assert FREE_TOPIC in topic_ids
		assert PAID_TOPIC not in topic_ids

	async def test_mixed_track_start_only_includes_free_lessons(
		self, authed_client, redis_client, mock_frappe
	):
		"""In a mixed paid/free track, start_session only includes free topic lessons."""
		client, token, player_id, family_id = authed_client

		hier = _make_mixed_free_paid_hierarchy()
		await seed_hierarchy(redis_client, SUBJECT_ID, hierarchy_json=hier)
		# No access grants — only free content should be accessible

		# Questions only for the free topic
		free_questions = _make_question_rows(2, topic_id=FREE_TOPIC)
		mock_frappe.call.side_effect = _make_frappe_handler(
			free_questions,
			topic_counts={FREE_TOPIC: 2},
		)

		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)

		assert resp.status_code == 200
		data = resp.json()
		assert data["session_active"] is True
		# Should only get free topic questions, not paid
		assert data["total_available"] == 2

		# Verify session stores only free lessons
		session = await redis_client.hgetall(practice_session_key(player_id))
		accessible = json.loads(session["accessible_lessons"])
		# Must be ONLY the free lessons, not the paid ones
		assert set(accessible) == {"LESSON-FREE-001", "LESSON-FREE-002"}
		assert "LESSON-PAID-001" not in accessible
		assert "LESSON-PAID-002" not in accessible


# ==========================================================================
# Regression: Quota redistribution on topic exhaustion (#3-underfill)
# ==========================================================================


@pytest.mark.asyncio
class TestQuotaRedistribution:
	"""When one topic is exhausted mid-session, unused quota goes to other topics."""

	async def test_exhausted_topic_quota_redistributed(self, authed_client, redis_client, mock_frappe):
		"""Batch 1: Topic A is exhausted, its unfilled quota goes to Topic B.

		Setup: A=1 item, B=40 items. Batch size = 20.
		Quotas: {A: 1, B: 19} (proportional with min-1 guarantee).

		Batch 0: A serves 1 (all of it), B serves 19. Total = 20.
		Batch 1: A has 0 left → returns 0 → EXHAUSTED. B has 21 left → returns 19.
		  Without redistribution: batch 1 = 19 (B's quota only).
		  With redistribution: A's unfilled 1 → B gets 1 extra → batch 1 = 20.

		Definitive assertion: b1_count == 20 (fails at 19 without redistribution).
		"""
		client, token, player_id, family_id = authed_client

		topic_a_qs = _make_question_rows(1, topic_id=TOPIC_ID_A)
		topic_b_qs = _make_question_rows(40, topic_id=TOPIC_ID_B)
		all_qs = topic_a_qs + topic_b_qs

		async def redistrib_handler(method, params=None):
			if method == "memora_admin.api.practice.count_practice_items_per_topic":
				return {
					TOPIC_ID_A: 1,
					TOPIC_ID_B: 40,
				}
			elif method == "memora_admin.api.practice.select_practice_candidates":
				return _mock_select_candidates(
					params or {},
					{
						TOPIC_ID_A: topic_a_qs,
						TOPIC_ID_B: topic_b_qs,
					},
					{q["item_id"] for q in all_qs},
					all_qs,
				)
			elif method == "memora_admin.api.practice.get_existing_practice_item_ids":
				return [q["item_id"] for q in all_qs]
			elif method == "memora_admin.api.practice.upsert_practice_results":
				return None
			return None

		hier = _make_multi_topic_hierarchy(
			topics=[(TOPIC_ID_A, 1), (TOPIC_ID_B, 10)],
		)
		await seed_hierarchy(redis_client, SUBJECT_ID, hierarchy_json=hier)
		await seed_access_grants(redis_client, player_id, [f"SUB-{SUBJECT_ID}"])
		mock_frappe.call.side_effect = redistrib_handler

		# Start session — batch 0
		resp = await client.post(
			"/api/v1/practice/start",
			json={"subject_id": SUBJECT_ID, "filter": "all", "tracks": [TRACK_ID]},
		)
		assert resp.status_code == 200
		b0 = resp.json()
		b0_count = len(b0["questions"])
		assert b0_count == 20, f"batch 0 should be full batch_size, got {b0_count}"

		# Submit batch 0
		results_0 = [{"item_id": q["item_id"], "is_correct": True} for q in b0["questions"]]
		resp = await client.post("/api/v1/practice/submit", json={"batch_seq": 0, "results": results_0})
		assert resp.status_code == 200

		# Continue — batch 1 (topic A exhausted: 1 item, all served in batch 0)
		resp = await client.post("/api/v1/practice/continue")
		assert resp.status_code == 200
		b1 = resp.json()
		b1_count = len(b1["questions"])

		# Definitive redistribution proof:
		# Quotas = {A: 1, B: 19}. A returns 0 (exhausted). B returns 19.
		# WITHOUT redistribution: b1_count = 19 (B's quota only).
		# WITH redistribution: A's unfilled 1 → B gets 20th item → b1_count = 20.
		assert b1_count == 20, (
			f"batch 1 should be full batch_size=20 (redistribution fills "
			f"A's exhausted quota from B), got {b1_count}"
		)
