"""Pydantic request/response models for Practice Arena endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StartSessionRequest(BaseModel):
	subject_id: str
	track_ids: list[str] = Field(min_length=1)
	unit_ids: list[str] | None = None
	topic_ids: list[str] | None = None


class ResultItem(BaseModel):
	item_id: str
	is_correct: bool


class SubmitRequest(BaseModel):
	batch_seq: int = Field(ge=0)
	results: list[ResultItem] = Field(min_length=1, max_length=20)


class ContinueRequest(BaseModel):
	batch_seq: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class BatchResponse(BaseModel):
	session_active: bool
	batch_seq: int
	question_ids: list[str]
	chunk_refs: list[int]
	total_available: int
	all_seen_warning: bool


class SubmitResponse(BaseModel):
	accepted: bool
	batch_seq: int
	correct_count: int
	total_count: int
	accuracy_percent: float
	is_duplicate: bool


class SessionStatusResponse(BaseModel):
	session_active: bool
	subject_id: str
	track_ids: list[str]
	batch_seq: int
	submitted: bool
	question_ids: list[str] = Field(default_factory=list)
	chunk_refs: list[int] = Field(default_factory=list)


class ErrorResponse(BaseModel):
	detail: str
