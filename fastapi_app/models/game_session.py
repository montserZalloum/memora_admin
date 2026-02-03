# Copyright (c) 2026, corex and contributors
"""GameSession Pydantic models for lesson flow tracking."""

from pydantic import BaseModel


def _decode_value(value: bytes | str | None) -> str | None:
	"""Decode bytes to string if needed."""
	if value is None:
		return None
	if isinstance(value, bytes):
		return value.decode("utf-8")
	return value


class GameSession(BaseModel):
	"""Core session data stored in Redis hash.

	Per CONTEXT.md:
	- One session per user (new session force-closes existing)
	- TTL of 1 hour for auto-expiry
	- Tracks lesson currently being played
	"""

	session_id: str
	lesson_id: str
	subject_id: str
	device_id: str | None = None
	started_at: str  # ISO timestamp

	@classmethod
	def from_redis_hash(cls, data: dict) -> "GameSession":
		"""Create GameSession from Redis HGETALL result.

		Handles bytes/str conversion from Redis.

		Args:
			data: Dict from Redis HGETALL (may have bytes keys/values)

		Returns:
			GameSession instance
		"""

		# Handle both bytes and str keys from Redis
		def get_field(name: str) -> str | None:
			value = data.get(name) or data.get(name.encode("utf-8"))
			return _decode_value(value)

		return cls(
			session_id=get_field("session_id") or "",
			lesson_id=get_field("lesson_id") or "",
			subject_id=get_field("subject_id") or "",
			device_id=get_field("device_id"),
			started_at=get_field("started_at") or "",
		)


class StageResult(BaseModel):
	"""Stage completion data submitted at lesson end.

	Per CONTEXT.md:
	- Submitted with lesson completion
	- Contains timing and performance data for XP calculation
	"""

	stage_id: str
	time_spent: int  # seconds
	fail_count: int = 0
	completed_at: str  # ISO timestamp
	metadata: dict = {}  # client-provided extra data for analytics


class StartSessionRequest(BaseModel):
	"""Request body for POST /sessions/start."""

	lesson_id: str
	subject_id: str


class StartSessionResponse(BaseModel):
	"""Response from session start."""

	session_id: str
	lesson_id: str


class EndSessionRequest(BaseModel):
	"""Request body for POST /sessions/end."""

	stages: list[StageResult]


class EndSessionResponse(BaseModel):
	"""Response from session end.

	Matches existing CompleteResponse pattern from progress service.
	"""

	success: bool = True
	xp_awarded: int = 0
	is_replay: bool = False
	streak: int = 0
