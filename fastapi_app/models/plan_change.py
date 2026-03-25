"""Pydantic request/response models for Plan Change endpoints."""

from __future__ import annotations

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PlanChangeRequest(BaseModel):
	new_plan_id: str


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PlanChangeResponse(BaseModel):
	success: bool = True
	message: str = "Plan changed successfully. Please log in again."
	history_id: str
	previous_plan_id: str
	new_plan_id: str


class PlanChangeErrorResponse(BaseModel):
	error: str
	message: str
	retry_after: str | None = None


class AvailablePlan(BaseModel):
	plan_id: str
	plan_name: str
	grade_id: str
	grade_name: str
	major_id: str | None = None
	major_name: str | None = None
	season_id: str
	season_title: str


class GradePlanGroup(BaseModel):
	grade_id: str
	grade_name: str
	plans: list[AvailablePlan]


class AvailablePlansResponse(BaseModel):
	grades: list[GradePlanGroup]
	total: int


# ---------------------------------------------------------------------------
# Plan-change options (cascading picker: grade → major → plan)
# ---------------------------------------------------------------------------


class OptionPlan(BaseModel):
	id: str
	title: str
	major_id: str | None = None
	major_title: str | None = None
	season_id: str
	season_title: str


class OptionMajor(BaseModel):
	id: str
	title: str


class OptionGrade(BaseModel):
	id: str
	title: str
	sort_order: int = 0
	majors: list[OptionMajor]
	plans: list[OptionPlan]


class PlanChangeOptionsResponse(BaseModel):
	grades: list[OptionGrade]
