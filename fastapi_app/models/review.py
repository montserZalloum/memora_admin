"""Pydantic models for the FSRS review API (item-level)."""

from pydantic import BaseModel, Field


class SubjectReviewCount(BaseModel):
	"""Due review count for a single subject."""

	subject_id: str
	due_count: int


class ReviewOverviewResponse(BaseModel):
	"""All subjects with due review counts for a player."""

	subjects: list[SubjectReviewCount]


class DueItem(BaseModel):
	"""A single item due for review, with question content."""

	item_id: str  # UUID string
	stage_id: str
	lesson_id: str
	stage_type: str
	question_text: str | None = None
	choices: list[str] = []
	correct_choice: int | None = None
	content_json: dict | None = None


class DueItemsResponse(BaseModel):
	"""Due items for a specific subject."""

	subject_id: str
	items: list[DueItem]
	has_more: bool


class ItemReviewResult(BaseModel):
	"""Result of reviewing a single item."""

	item_id: str  # UUID string
	fail_count: int = Field(default=0, ge=0)


class ReviewSubmitRequest(BaseModel):
	"""Batch review submission request."""

	items: list[ItemReviewResult] = Field(..., min_length=1, max_length=10)


class ReviewSubmitResponse(BaseModel):
	"""Response after submitting review results."""

	processed: int
	remaining_due: int
	has_more: bool
	xp_awarded: int
