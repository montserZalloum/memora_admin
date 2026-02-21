"""Pydantic models for the content report API."""

from typing import Literal

from pydantic import BaseModel, Field


class ContentReportRequest(BaseModel):
	"""Request to submit a content report."""

	report_type: Literal["Bug", "Content Error", "Suggestion", "Other"]
	description: str = Field(..., min_length=1, max_length=2000)
	subject: str | None = None
	lesson: str | None = None
	screenshot_base64: str | None = None
	screenshot_filename: str | None = None


class ContentReportResponse(BaseModel):
	"""Response after creating a content report."""

	name: str
	message: str
