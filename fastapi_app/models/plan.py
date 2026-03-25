"""Pydantic models for Plan JSON structures."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PlanSubject(BaseModel):
	"""Subject entry in plan manifest."""

	id: str = Field(..., description="Subject document ID")
	title: str = Field(..., description="Subject title")
	alias_title: Optional[str] = Field(None, description="Alternative title")
	image: Optional[str] = Field(None, description="Subject image URL")
	language: str = Field("ar", description="Subject language code (ar, en)")
	total_lessons: int = Field(0, description="Total lesson count")
	total_tracks: int = Field(0, description="Total track count")
	is_premium: bool = Field(False, description="Requires subscription")
	is_free_preview: bool = Field(False, description="Has free preview content")


class PlanManifest(BaseModel):
	"""Plan manifest JSON structure."""

	schema_version: int = Field(1, description="Schema version number")
	version: int = Field(..., description="Content version timestamp")
	generated_at: datetime = Field(..., description="Generation timestamp")
	plan_id: str = Field(..., description="Plan document ID")
	title: str = Field(..., description="Plan name")
	grade_id: Optional[str] = Field(None, description="Grade document ID")
	grade_title: str = Field("", description="Grade display title")
	major_id: Optional[str] = Field(None, description="Major document ID")
	major_title: str = Field("", description="Major display title")
	season_id: Optional[str] = Field(None, description="Season document ID")
	subjects: list[PlanSubject] = Field(default_factory=list, description="Plan subjects")

	class Config:
		json_schema_extra = {
			"example": {
				"schema_version": 1,
				"version": 1706275200,
				"generated_at": "2026-02-03T14:30:00Z",
				"plan_id": "PLAN-00001",
				"title": "High School 3 - Scientific",
				"grade_id": "GRD-00001",
				"grade_title": "Grade 12",
				"major_id": "MJR-00001",
				"major_title": "Scientific",
				"season_id": "SEASON-00001",
				"subjects": [],
			}
		}
