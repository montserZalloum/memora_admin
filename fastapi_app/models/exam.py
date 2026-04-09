"""Pydantic models for Official Exam endpoints."""

from pydantic import BaseModel, Field

# --- Start endpoint ---


class ExamStartResponse(BaseModel):
	has_access: bool


# --- Submit endpoint ---


class QuestionResult(BaseModel):
	question_idx: int = Field(ge=1)
	is_correct: bool


class ExamSubmitRequest(BaseModel):
	results: list[QuestionResult] = Field(min_length=1)
	score: int = Field(ge=0)
	total: int = Field(ge=1)


class ExamSubmitResponse(BaseModel):
	accepted: bool = True
	attempt_count: int
	best_score: int
	best_total: int
	is_new_best: bool
