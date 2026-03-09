"""Pydantic models for Challenge Hub endpoints."""

from pydantic import BaseModel, Field


# =============================================================================
# Request Models
# =============================================================================


class QuestionDetail(BaseModel):
	"""Per-question result in an attempt submission."""

	item_id: str
	correct: bool
	time_spent: int = Field(ge=0)
	chosen_answer: int = Field(ge=1, le=4)


class AttemptRequest(BaseModel):
	"""Request body for POST /challenge/attempt."""

	subject_id: str
	topic_id: str
	attempt_key: str = Field(min_length=1, max_length=128)
	total_questions: int = Field(ge=1)
	time_spent: int = Field(ge=0)
	questions: list[QuestionDetail] = Field(min_length=1)


# =============================================================================
# Response Models
# =============================================================================


class NextTopicInfo(BaseModel):
	"""Info about the next topic unlocked by a stamp."""

	topic_id: str
	state: str


class AttemptResponse(BaseModel):
	"""Response for POST /challenge/attempt."""

	attempt_number: int
	score_pct: float
	passed: bool
	stamped: bool
	xp_earned: int
	total_topic_xp: int
	best_score_pct: float
	best_passing_pct: float | None
	is_new_best: bool
	next_topic: NextTopicInfo | None = None


class TopicState(BaseModel):
	"""Topic state within the challenge hierarchy."""

	topic_id: str
	topic_name: str
	state: str  # "locked", "open", "stamped"
	mcq_count: int
	best_score_pct: float | None = None
	best_passing_pct: float | None = None
	total_xp: int = 0
	attempt_count: int = 0
	normal_path_complete: bool = False
	has_access: bool = False
	lock_reason: str | None = None


class UnitState(BaseModel):
	"""Unit containing topics in challenge hierarchy."""

	unit_id: str
	unit_name: str
	topics: list[TopicState] = []


class TrackState(BaseModel):
	"""Track containing units in challenge hierarchy."""

	track_id: str
	track_name: str
	has_access: bool = False
	units: list[UnitState] = []


class ChallengeHierarchyResponse(BaseModel):
	"""Response for GET /challenge/hierarchy/{subject_id}."""

	subject_id: str
	tracks: list[TrackState] = []


class ChallengeSubjectSummary(BaseModel):
	"""Per-subject summary for the challenge hub landing page."""

	subject_id: str
	subject_name: str
	total_topics: int = 0
	stamped_topics: int = 0
	total_challenge_xp: int = 0


class LeaderboardEntry(BaseModel):
	"""Single entry in the challenge leaderboard."""

	rank: int
	player_id: str
	display_name: str
	xp: int
	avatar: str | None = None
	is_me: bool = False


class LeaderboardResponse(BaseModel):
	"""Response for GET /challenge/leaderboard."""

	subject_id: str | None = None
	entries: list[LeaderboardEntry] = []
	total_players: int = 0


class MyRankResponse(BaseModel):
	"""Response for GET /challenge/leaderboard/me."""

	rank: int | None = None
	xp: int = 0
	xp_to_next: int | None = None
	neighbors: list[LeaderboardEntry] = []
	total_players: int = 0
