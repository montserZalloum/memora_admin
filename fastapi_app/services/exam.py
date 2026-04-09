"""Official Exam service — orchestrates CDN reads, access checks, and DB operations."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from fastapi_app.core.redis_keys import access_key as _access_key_fn, premium_key
from fastapi_app.services.access import AccessService

if TYPE_CHECKING:
	import redis.asyncio as redis

	from fastapi_app.services.frappe_client import FrappeClient

logger = structlog.get_logger()


class ExamService:
	"""Business logic for Official Exam endpoints."""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
		cdn_base_path: str,
		cdn_base_url: str,
		access_service: AccessService | None = None,
	):
		self.redis = redis_client
		self.frappe = frappe_client
		self.cdn_base = Path(cdn_base_path).resolve()
		self.cdn_base_url = cdn_base_url.rstrip("/")
		self.access_service = access_service

	# ------------------------------------------------------------------
	# Access check
	# ------------------------------------------------------------------

	async def has_exam_access(self, player_id: str, plan_id: str) -> bool:
		"""Check if player has exam access: EXAM-PLAN-{plan_id} grant or active premium."""
		if self.access_service:
			await self.access_service.ensure_hydrated(player_id)
		access_key = _access_key_fn(player_id)
		has_grant = await self.redis.sismember(access_key, f"EXAM-PLAN-{plan_id}")
		if has_grant:
			return True
		raw = await self.redis.hget(premium_key(player_id, plan_id), "usable")
		return raw == "1"

	# ------------------------------------------------------------------
	# Start exam
	# ------------------------------------------------------------------

	async def start_exam(self, player_id: str, plan_id: str, subject_id: str, exam_id: str) -> dict:
		"""Validate exam exists and player holds an EXAM-PLAN-{plan_id} grant.

		Raises ValueError with code if exam not found or access denied.
		"""
		exam_entry = await asyncio.to_thread(self._get_exam_from_index, plan_id, subject_id, exam_id)
		if exam_entry is None:
			raise ValueError("EXAM_NOT_FOUND")
		if not await self.has_exam_access(player_id, plan_id):
			raise ValueError("NO_EXAM_ACCESS")
		return {"has_access": True}

	# ------------------------------------------------------------------
	# Submit exam
	# ------------------------------------------------------------------

	async def submit_exam(
		self,
		player_id: str,
		exam_id: str,
		plan_id: str,
		subject_id: str,
		score: int,
		total: int,
		results: list[dict],
	) -> dict:
		"""Record exam attempt results via Frappe API.

		Raises ValueError with code on validation failure.
		"""
		exam_entry = await asyncio.to_thread(self._get_exam_from_index, plan_id, subject_id, exam_id)
		if exam_entry is None:
			raise ValueError("EXAM_NOT_FOUND")

		if not await self.has_exam_access(player_id, plan_id):
			raise ValueError("NO_EXAM_ACCESS")

		expected_count = exam_entry["question_count"]
		if len(results) != expected_count:
			raise ValueError("RESULT_COUNT_MISMATCH")
		if total != expected_count:
			raise ValueError("TOTAL_MISMATCH")

		correct_count = sum(1 for r in results if r.get("is_correct"))
		if score != correct_count:
			raise ValueError("SCORE_MISMATCH")

		# Submit via Frappe API
		result = await self.frappe.call(
			"memora_admin.memora_admin.api.exam.submit_exam_attempt",
			{
				"player_id": player_id,
				"exam_id": exam_id,
				"score": score,
				"total": total,
				"results": json.dumps(results),
			},
		)

		if not result:
			return {
				"accepted": True,
				"attempt_count": 1,
				"best_score": score,
				"best_total": total,
				"is_new_best": True,
			}

		return {
			"accepted": True,
			"attempt_count": result["attempt_count"],
			"best_score": result["best_score"],
			"best_total": result["best_total"],
			"is_new_best": result["is_new_best"],
		}

	# ------------------------------------------------------------------
	# Internal helpers
	# ------------------------------------------------------------------

	def _safe_cdn_path(self, *parts: str) -> Path | None:
		"""Resolve a CDN path and verify it stays within cdn_base."""
		try:
			target = self.cdn_base.joinpath(*parts).resolve()
			if not target.is_relative_to(self.cdn_base):
				return None
			return target
		except (ValueError, OSError):
			return None

	def _read_index_json(self, subject_id: str, plan_id: str) -> dict | None:
		"""Read _index.json from CDN (sync, call via to_thread)."""
		index_path = self._safe_cdn_path("exams", plan_id, subject_id, "_index.json")
		if index_path is None or not index_path.exists():
			return None
		raw = index_path.read_text(encoding="utf-8")
		return json.loads(raw)

	def _get_exam_from_index(self, plan_id: str, subject_id: str, exam_id: str) -> dict | None:
		"""Return the index entry for a specific exam, or None if not published."""
		index_data = self._read_index_json(subject_id, plan_id)
		if index_data is None:
			return None
		for e in index_data.get("exams", []):
			if e["exam_id"] == exam_id:
				return e
		return None

