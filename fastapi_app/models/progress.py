"""Progress tracking models for completion and hierarchy data."""

from pydantic import BaseModel, computed_field


# Request/Response models for completion endpoint


class CompleteRequest(BaseModel):
    """Request body for lesson completion.

    Per CONTEXT.md: subject + lesson identifier
    """

    subject: str  # e.g., "MATH-G5"
    lesson: str  # e.g., "LESSON-001"


class CompleteResponse(BaseModel):
    """Response for lesson completion.

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


class TopicInfo(BaseModel):
    """Topic containing lessons."""

    topic_id: str
    is_linear: bool = True  # If true, lessons must complete in order
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
