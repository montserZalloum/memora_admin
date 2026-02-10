"""Progress tracking models for completion and hierarchy data."""

from pydantic import BaseModel, computed_field

# DEPRECATED: These models were used by the legacy POST /progress/complete endpoint
# which was removed in Phase 20. Use POST /sessions/end with EndSessionRequest/EndSessionResponse instead.
# Kept for backward compatibility with tests and external references.


class CompleteRequest(BaseModel):
	"""DEPRECATED: Request body for legacy lesson completion endpoint.

	Removed in Phase 20. Use POST /sessions/end with EndSessionRequest instead.
	Per CONTEXT.md: subject + lesson identifier
	"""

	subject: str  # e.g., "MATH-G5"
	lesson: str  # e.g., "LESSON-001"


class CompleteResponse(BaseModel):
	"""DEPRECATED: Response for legacy lesson completion endpoint.

	Removed in Phase 20. Use POST /sessions/end with EndSessionResponse instead.
	Per CONTEXT.md: Returns completion status plus reward info.
	Per Phase 5: Include XP awarded and replay status.
	"""

	success: bool = True
	xp_awarded: int = 0  # XP awarded this completion
	is_replay: bool = False  # Whether this was a replay
	streak: int = 0  # Current streak after update


# Hierarchy models for unlock calculation


class LessonInfo(BaseModel):
	"""Individual lesson within a topic."""

	lesson_id: str
	bit_index: int  # Position in bitmap
	xp: int = 0  # XP awarded on completion
	max_hearts: int = 5  # Max hearts for this lesson (0 = use default)


class LessonPath(BaseModel):
	"""Path to a lesson in the hierarchy.

	Used for stats updates to identify track/unit/topic for a lesson.
	"""

	track_id: str
	unit_id: str
	topic_id: str
	bit_index: int


class TopicInfo(BaseModel):
	"""Topic containing lessons."""

	topic_id: str
	is_linear: bool = True  # If true, lessons must complete in order
	is_free: bool = False  # If true, bypasses Gate 2
	lessons: list[LessonInfo]


class UnitInfo(BaseModel):
	"""Unit containing topics."""

	unit_id: str
	is_linear: bool = True  # If true, topics must complete in order
	is_free: bool = False  # If true, bypasses Gate 2
	topics: list[TopicInfo]


class TrackInfo(BaseModel):
	"""Track containing units."""

	track_id: str
	is_linear: bool = True  # If true, units must complete in order
	is_sold_separately: bool = False  # If true, track can be purchased individually
	units: list[UnitInfo]


class SubjectHierarchy(BaseModel):
	"""Full subject structure for unlock calculation.

	Contains nested hierarchy: Subject -> Tracks -> Units -> Topics -> Lessons
	Used for calculating unlock states based on is_linear flags.
	"""

	subject_id: str
	version: int = 1  # Bitmap version for structural changes
	bit_range: int  # Total bits allocated in bitmap
	excluded_bits: list[int] = []  # Deleted lessons (for accurate percentage)
	is_linear: bool = True  # If true, tracks must complete in order
	free_units: list[str] = []  # Unit IDs that are marked as free
	free_topics: list[str] = []  # Topic IDs that are marked as free
	tracks: list[TrackInfo]

	def find_lesson(self, lesson_id: str) -> LessonInfo | None:
		"""Recursively search for lesson by ID.

		Args:
		    lesson_id: The lesson identifier to find

		Returns:
		    LessonInfo if found, None otherwise
		"""
		for track in self.tracks:
			for unit in track.units:
				for topic in unit.topics:
					for lesson in topic.lessons:
						if lesson.lesson_id == lesson_id:
							return lesson
		return None

	def find_lesson_path(self, lesson_id: str) -> LessonPath | None:
		"""Find lesson and return its path (track/unit/topic IDs).

		Used for stats updates to identify all parent containers.

		Args:
		    lesson_id: The lesson identifier to find

		Returns:
		    LessonPath with track_id, unit_id, topic_id, bit_index if found
		"""
		for track in self.tracks:
			for unit in track.units:
				for topic in unit.topics:
					for lesson in topic.lessons:
						if lesson.lesson_id == lesson_id:
							return LessonPath(
								track_id=track.track_id,
								unit_id=unit.unit_id,
								topic_id=topic.topic_id,
								bit_index=lesson.bit_index,
							)
		return None

	def is_lesson_free(self, lesson_id: str) -> bool:
		"""Check if lesson is in a free Unit or Topic.

		Per CONTEXT.md: Free content bypasses Gate 2 (player access grant check).
		Unit.is_free=1 or Topic.is_free=1 makes all lessons within free.

		Uses cached free_units/free_topics for O(1) set lookup instead of full tree traversal.

		Args:
		    lesson_id: The lesson identifier to check

		Returns:
		    True if lesson is in a free unit or topic
		"""
		free_units_set = set(self.free_units)
		free_topics_set = set(self.free_topics)

		for track in self.tracks:
			for unit in track.units:
				# Quick O(1) check against cached free units
				if unit.unit_id in free_units_set:
					for topic in unit.topics:
						for lesson in topic.lessons:
							if lesson.lesson_id == lesson_id:
								return True
				else:
					# Only traverse topics if unit isn't free
					for topic in unit.topics:
						# Quick O(1) check against cached free topics
						if topic.topic_id in free_topics_set:
							for lesson in topic.lessons:
								if lesson.lesson_id == lesson_id:
									return True
						else:
							# Fall back to is_free flags as last resort
							for lesson in topic.lessons:
								if lesson.lesson_id == lesson_id:
									return topic.is_free

		return False

	def has_any_free_content(self) -> bool:
		"""Check if subject has any free units or topics.

		Used to determine if a subject should be visible to players
		without explicit grants (for subjects with free samples).

		Uses cached free_units/free_topics for O(1) check.

		Returns:
		    True if any unit or topic is marked as free
		"""
		return bool(self.free_units or self.free_topics)


# Progress response models with computed percentages


class TopicProgress(BaseModel):
	"""Progress for a single topic."""

	topic_id: str
	completed: int
	total: int
	unlocked: bool = True  # Unlock state per CONTEXT.md decision

	@computed_field
	@property
	def percentage(self) -> float:
		"""Calculate completion percentage.

		Safe division: returns 0.0 if total is 0.
		"""
		if self.total == 0:
			return 0.0
		return round(self.completed / self.total * 100, 1)


class UnitProgress(BaseModel):
	"""Progress for a single unit."""

	unit_id: str
	completed: int
	total: int
	topics: list[TopicProgress]
	unlocked: bool = True

	@computed_field
	@property
	def percentage(self) -> float:
		"""Calculate completion percentage."""
		if self.total == 0:
			return 0.0
		return round(self.completed / self.total * 100, 1)


class TrackProgress(BaseModel):
	"""Progress for a single track."""

	track_id: str
	completed: int
	total: int
	units: list[UnitProgress]
	unlocked: bool = True

	@computed_field
	@property
	def percentage(self) -> float:
		"""Calculate completion percentage."""
		if self.total == 0:
			return 0.0
		return round(self.completed / self.total * 100, 1)


class SubjectProgress(BaseModel):
	"""Full progress breakdown for a subject.

	Per CONTEXT.md: Full breakdown includes subject total + each track
	+ each unit + each topic percentages.
	"""

	subject_id: str
	completed: int
	total: int
	tracks: list[TrackProgress]

	@computed_field
	@property
	def percentage(self) -> float:
		"""Calculate completion percentage."""
		if self.total == 0:
			return 0.0
		return round(self.completed / self.total * 100, 1)


class SubjectSummary(BaseModel):
	"""Summary progress for GET /progress listing.

	Used for displaying all subjects a player has progress in.
	"""

	subject_id: str
	subject_name: str
	percentage: float
	completed: int
	total: int


# --- SSE Streaming Models (for documentation) ---


class SSESubjectEvent(BaseModel):
	"""Subject summary event payload for SSE streaming.

	First event sent, arrives within 10ms of request.
	"""

	subject_id: str
	completed: int
	total: int
	percentage: float


class SSETopicData(BaseModel):
	"""Topic data within SSE track event."""

	topic_id: str
	completed: int
	total: int
	percentage: float


class SSEUnitData(BaseModel):
	"""Unit data within SSE track event."""

	unit_id: str
	completed: int
	total: int
	percentage: float
	topics: list[SSETopicData]


class SSETrackEvent(BaseModel):
	"""Track event payload for SSE streaming.

	Sent progressively for each track in subject.
	"""

	track_id: str
	completed: int
	total: int
	percentage: float
	units: list[SSEUnitData]


# --- Granular Endpoint Models (Phase 17.2) ---


class TrackSummary(BaseModel):
	"""Track progress without nested units.

	Used by GET /{subject}/tracks endpoint.
	"""

	track_id: str
	completed: int
	total: int
	unlocked: bool = True

	@computed_field
	@property
	def percentage(self) -> float:
		"""Calculate completion percentage."""
		if self.total == 0:
			return 0.0
		return round(self.completed / self.total * 100, 1)


class UnitSummary(BaseModel):
	"""Unit progress without nested topics.

	Used by GET /{subject}/tracks/{track_id} endpoint.
	"""

	unit_id: str
	completed: int
	total: int
	unlocked: bool = True

	@computed_field
	@property
	def percentage(self) -> float:
		"""Calculate completion percentage."""
		if self.total == 0:
			return 0.0
		return round(self.completed / self.total * 100, 1)


class TrackDetail(BaseModel):
	"""Track with units (no topics).

	Used by GET /{subject}/tracks/{track_id} endpoint.
	"""

	track_id: str
	completed: int
	total: int
	unlocked: bool = True
	units: list[UnitSummary]

	@computed_field
	@property
	def percentage(self) -> float:
		"""Calculate completion percentage."""
		if self.total == 0:
			return 0.0
		return round(self.completed / self.total * 100, 1)


class UnitDetail(BaseModel):
	"""Unit with topics (no lessons).

	Used by GET /{subject}/tracks/{track_id}/units/{unit_id} endpoint.
	"""

	unit_id: str
	completed: int
	total: int
	unlocked: bool = True
	topics: list[TopicProgress]

	@computed_field
	@property
	def percentage(self) -> float:
		"""Calculate completion percentage."""
		if self.total == 0:
			return 0.0
		return round(self.completed / self.total * 100, 1)


# --- Lesson Completion Models (Phase 18) ---


class LessonCompletionStatus(BaseModel):
	"""Completion status for a single lesson.

	Used by GET /{subject}/topics/{topic_id}/lessons endpoint.
	"""

	lesson_id: str
	bit_index: int  # Position in bitmap (useful for debugging/verification)
	completed: bool


class TopicLessonsResponse(BaseModel):
	"""All lessons in a topic with completion status.

	Used by GET /{subject}/topics/{topic_id}/lessons endpoint.
	Performance: <5ms for topics with up to 100 lessons.
	"""

	topic_id: str
	total: int
	completed: int
	lessons: list[LessonCompletionStatus]

	@computed_field
	@property
	def percentage(self) -> float:
		"""Calculate completion percentage."""
		if self.total == 0:
			return 0.0
		return round(self.completed / self.total * 100, 1)
