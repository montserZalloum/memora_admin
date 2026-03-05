"""Pydantic request/response models for Practice Arena endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PracticeHierarchyParams(BaseModel):
	subject_id: str
	filter: Literal["all", "completed"] = "all"


class StartPracticeRequest(BaseModel):
	subject_id: str
	filter: Literal["all", "completed"]
	tracks: list[str]
	units: list[str] = Field(default_factory=list)
	topics: list[str] = Field(default_factory=list)


class PracticeResult(BaseModel):
	item_id: str
	is_correct: bool


class SubmitPracticeRequest(BaseModel):
	batch_seq: int = Field(ge=0)
	results: list[PracticeResult]


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PracticeTopicInfo(BaseModel):
	topic_id: str
	topic_title: str
	item_count: int


class PracticeUnitInfo(BaseModel):
	unit_id: str
	unit_title: str
	item_count: int
	topics: list[PracticeTopicInfo]


class PracticeTrackInfo(BaseModel):
	track_id: str
	track_title: str
	has_access: bool
	item_count: int
	units: list[PracticeUnitInfo]


class PracticeHierarchyResponse(BaseModel):
	subject_id: str
	subject_title: str
	tracks: list[PracticeTrackInfo]


class PracticeQuestion(BaseModel):
	item_id: str
	stage_type: str
	question_text: str | None = None
	choices: list[str] = Field(default_factory=list)
	correct_choice: int | None = None
	content_json: dict | None = None


class PracticeBatchResponse(BaseModel):
	session_active: bool
	batch_seq: int
	questions: list[PracticeQuestion]
	total_available: int
	all_seen_warning: bool = False


class PracticeSubmitResponse(BaseModel):
	accepted: bool
	batch_seq: int
	correct_count: int
	total_count: int
	accuracy_percent: float
	is_duplicate: bool = False


class PracticeSubmitAndContinueResponse(BaseModel):
	submit: PracticeSubmitResponse
	next_batch: PracticeBatchResponse
