"""Pydantic models for API request/response schemas."""

from fastapi_app.models.access import SeasonMeta
from fastapi_app.models.auth import (
    FrappeUser,
    LoginRequest,
    RefreshRequest,
    TokenPayload,
    TokenResponse,
)
from fastapi_app.models.plan import PlanManifest, PlanSubject
from fastapi_app.models.progress import (
    CompleteRequest,
    CompleteResponse,
    LessonInfo,
    SubjectHierarchy,
    SubjectProgress,
    SubjectSummary,
    TopicInfo,
    TopicProgress,
    TrackInfo,
    TrackProgress,
    UnitInfo,
    UnitProgress,
)

__all__ = [
    # Access control models
    "SeasonMeta",
    # Auth models
    "FrappeUser",
    "LoginRequest",
    "RefreshRequest",
    "TokenPayload",
    "TokenResponse",
    # Plan models
    "PlanManifest",
    "PlanSubject",
    # Progress models
    "CompleteRequest",
    "CompleteResponse",
    "LessonInfo",
    "SubjectHierarchy",
    "SubjectProgress",
    "SubjectSummary",
    "TopicInfo",
    "TopicProgress",
    "TrackInfo",
    "TrackProgress",
    "UnitInfo",
    "UnitProgress",
]
