"""Pydantic models for the FSRS review API."""

from pydantic import BaseModel, Field


class SubjectReviewCount(BaseModel):
	"""Due review count for a single subject."""

	subject_id: str
	due_count: int


class ReviewOverviewResponse(BaseModel):
	"""All subjects with due review counts for a player."""

	subjects: list[SubjectReviewCount]


class DueStage(BaseModel):
	"""A single stage due for review."""

	stage_id: str
	lesson_id: str
	stage_type: str


class DueStagesResponse(BaseModel):
	"""Due stages for a specific subject."""

	subject_id: str
	stages: list[DueStage]
	has_more: bool


class StageReviewResult(BaseModel):
	"""Result of reviewing a single stage."""

	stage_id: str
	fail_count: int = Field(default=0, ge=0)


class ReviewSubmitRequest(BaseModel):
	"""Batch review submission request."""

	stages: list[StageReviewResult] = Field(..., min_length=1, max_length=10)


class ReviewSubmitResponse(BaseModel):
	"""Response after submitting review results."""

	processed: int
	remaining_due: int
	has_more: bool
	xp_awarded: int
