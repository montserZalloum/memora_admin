"""Pydantic request/response models for Live Challenge endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# =============================================================================
# Shared / Nested Models
# =============================================================================


class SubmitAnswerItem(BaseModel):
	"""A single answer in a submission."""

	question_idx: int = Field(..., description="0-based question index")
	selected: Literal["A", "B", "C", "D"] | None = Field(
		..., description="Selected option or null if unanswered"
	)


class CorrectionItem(BaseModel):
	"""A single incorrect answer with the correct one."""

	question_idx: int = Field(..., description="0-based question index")
	selected: Literal["A", "B", "C", "D"] | None = Field(
		..., description="What the student selected"
	)
	correct_answer: Literal["A", "B", "C", "D"] = Field(..., description="The correct option")


class LeaderboardEntryItem(BaseModel):
	"""A single entry in the leaderboard."""

	rank: int = Field(..., description="Standard competition rank")
	player: str = Field(..., description="Player profile ID")
	display_name: str = Field(..., description="Player display name")
	score: float = Field(..., description="Score out of 100")


# =============================================================================
# Response Models
# =============================================================================


class StatusResponse(BaseModel):
	"""Lightweight event status from Redis."""

	status: str = Field(..., description="draft, waiting, active, or ended")
	participant_count: int = Field(0, description="Current participant count")


class EventDetailResponse(BaseModel):
	"""Public event details (no correct answers)."""

	event_id: str = Field(..., description="Event ID")
	event_name: str = Field(..., description="Display name")
	description: str | None = Field(None, description="Rich text description")
	status: str = Field(..., description="Draft, Waiting, Active, or Ended")
	scheduled_start: str = Field(..., description="When waiting room opens")
	exam_start_ts: str | None = Field(None, description="Computed exam start time")
	exam_end_ts: str | None = Field(None, description="Computed exam end time")
	waiting_room_duration: int = Field(..., description="Seconds")
	exam_duration: int = Field(..., description="Minutes")
	enable_question_timer: bool = Field(False, description="Per-question countdown")
	question_time_limit: int = Field(30, description="Seconds per question")
	capacity: int = Field(..., description="Max participants (0 = unlimited)")
	current_count: int = Field(0, description="Current participant count")
	is_paid: bool = Field(False, description="Whether event is paid")
	participation_xp: int = Field(0, description="XP for all submitters")
	first_place_xp: int = Field(0, description="Bonus for rank 1")
	second_place_xp: int = Field(0, description="Bonus for rank 2")
	third_place_xp: int = Field(0, description="Bonus for rank 3")
	default_xp: int = Field(0, description="Bonus for rank 4+")
	question_count: int = Field(0, description="Number of questions")
	eligible_plans: list[str] = Field(default_factory=list, description="Eligible plan IDs")
	has_joined: bool = Field(False, description="Whether current player has joined")
	has_submitted: bool = Field(False, description="Whether current player has submitted")
	top_players: list[LeaderboardEntryItem] | None = Field(
		None, description="Top 3 leaderboard entries (only present when event is Ended)"
	)


class JoinResponse(BaseModel):
	"""Response after joining an event."""

	joined: bool = Field(True, description="Always true on success")
	event_id: str = Field(..., description="Event ID")
	position: int = Field(..., description="Player's position number (1-indexed)")
	waiting_room_duration: int = Field(..., description="Total waiting room seconds")
	countdown_remaining: int = Field(..., description="Seconds until exam starts")
	ws_url: str = Field(..., description="WebSocket URL for start signal")


class SubmitRequest(BaseModel):
	"""Answer submission request."""

	answers: list[SubmitAnswerItem] = Field(..., description="List of answers")


class SubmitResponse(BaseModel):
	"""Immediate scoring response after submission."""

	score: float = Field(..., description="Score out of 100")
	correct_count: int = Field(..., description="Number of correct answers")
	total_questions: int = Field(..., description="Total number of questions")
	submitted_at: str = Field(..., description="Server timestamp of submission")
	corrections: list[CorrectionItem] | None = Field(
		None, description="Incorrect answers with corrections (null if disabled)"
	)


class ResultResponse(BaseModel):
	"""Student's own result and rank."""

	event_id: str = Field(..., description="Event ID")
	event_name: str = Field(..., description="Event display name")
	score: float = Field(..., description="Score out of 100")
	correct_count: int = Field(..., description="Number of correct answers")
	total_questions: int = Field(..., description="Total number of questions")
	rank: int | None = Field(None, description="Rank (null if not computed yet)")
	total_participants: int = Field(0, description="Total participants")
	xp_awarded: int | None = Field(None, description="XP awarded (null if not distributed)")
	submitted_at: str | None = Field(None, description="Submission timestamp")
	corrections: list[CorrectionItem] | None = Field(
		None, description="Corrections (null if disabled)"
	)


class LeaderboardResponse(BaseModel):
	"""Top 20 leaderboard after event ends."""

	event_id: str = Field(..., description="Event ID")
	event_name: str = Field(..., description="Event display name")
	status: str = Field(..., description="Event status")
	leaderboard: list[LeaderboardEntryItem] = Field(
		default_factory=list, description="Top 20 entries"
	)
	my_rank: int | None = Field(None, description="Current player's rank")
	my_score: float | None = Field(None, description="Current player's score")
	total_participants: int = Field(0, description="Total participants")
	exam_end_ts: str | None = Field(None, description="Exam end timestamp")


# =============================================================================
# WebSocket Message Models
# =============================================================================


class WSQuestionItem(BaseModel):
	"""A question sent via WebSocket (no correct_answer)."""

	idx: int = Field(..., description="0-based question index")
	question_text: str = Field(..., description="The question body")
	option_a: str = Field(..., description="Choice A")
	option_b: str = Field(..., description="Choice B")
	option_c: str = Field(..., description="Choice C")
	option_d: str = Field(..., description="Choice D")


class QuestionsResponse(BaseModel):
	"""REST fallback: exam questions without correct answers."""

	event_id: str = Field(..., description="Event ID")
	exam_end_ts: str = Field(..., description="Server-authoritative exam end time")
	total_questions: int = Field(..., description="Number of questions")
	enable_question_timer: bool = Field(False, description="Per-question timer enabled")
	question_time_limit: int = Field(30, description="Seconds per question")
	questions: list[WSQuestionItem] = Field(..., description="Questions without correct answers")


class WSCountdownMessage(BaseModel):
	"""Periodic countdown update during Waiting Room."""

	type: Literal["countdown"] = "countdown"
	remaining: int = Field(..., description="Seconds until exam starts")
	participant_count: int = Field(..., description="Current participant count")


class WSExamStartMessage(BaseModel):
	"""Broadcast when exam begins (Waiting -> Active)."""

	type: Literal["exam_start"] = "exam_start"
	exam_end_ts: str = Field(..., description="Server-authoritative exam end time")
	total_questions: int = Field(..., description="Number of questions")
	enable_question_timer: bool = Field(False, description="Per-question timer enabled")
	question_time_limit: int = Field(30, description="Seconds per question")
	questions: list[WSQuestionItem] = Field(..., description="Questions without correct answers")


class WSEventEndedMessage(BaseModel):
	"""Broadcast when event ends."""

	type: Literal["event_ended"] = "event_ended"
