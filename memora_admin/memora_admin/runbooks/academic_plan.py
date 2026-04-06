# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Academic Plan setup runbook template."""

from __future__ import annotations

import frappe
from frappe.utils import getdate, today

from memora_admin.memora_admin.runbooks.base import RunbookTemplate, StepDef
from memora_admin.memora_admin.runbooks.registry import register


def _get_plan(context_name: str) -> dict | None:
	if not context_name:
		return None
	return frappe.db.get_value(
		"Memora Academic Plan",
		context_name,
		["season", "grade", "major", "is_published", "plan_name"],
		as_dict=True,
	)


# ── Check functions ──────────────────────────────────────────────────────


def check_plan_exists(context_name: str, context_data: dict | None) -> bool:
	"""Plan document exists (context is set)."""
	if not context_name:
		return False
	return frappe.db.exists("Memora Academic Plan", context_name)


def check_season_active(context_name: str, context_data: dict | None) -> bool:
	"""Season is published and has not ended."""
	plan = _get_plan(context_name)
	if not plan or not plan.season:
		return False
	season = frappe.db.get_value(
		"Memora Season", plan.season, ["is_published", "end_date"], as_dict=True
	)
	if not season:
		return False
	if not season.is_published:
		return False
	if season.end_date and getdate(season.end_date) < getdate(today()):
		return False
	return True


def check_subjects_added(context_name: str, context_data: dict | None) -> bool:
	"""At least one Plan Subject is added."""
	if not context_name:
		return False
	return frappe.db.count("Memora Plan Subject", {"parent": context_name}) > 0


def check_subjects_have_content(context_name: str, context_data: dict | None) -> bool:
	"""Every linked subject has at least one published lesson."""
	if not context_name:
		return False
	subjects = frappe.get_all(
		"Memora Plan Subject", filters={"parent": context_name}, pluck="subject"
	)
	if not subjects:
		return False
	for subj in subjects:
		lesson_count = frappe.db.count(
			"Memora Lesson", {"subject": subj, "is_published": 1}
		)
		if lesson_count == 0:
			return False
	return True


def check_plan_published(context_name: str, context_data: dict | None) -> bool:
	"""Plan is published."""
	plan = _get_plan(context_name)
	if not plan:
		return False
	return bool(plan.is_published)


# ── Template ─────────────────────────────────────────────────────────────

TEMPLATE = RunbookTemplate(
	workflow_id="academic_plan",
	label="Academic Plan Setup",
	description="إعداد الخطة الدراسية وربطها بالموسم والمواد والمحتوى.",
	context_doctype="Memora Academic Plan",
	steps=[
		StepDef(
			key="create_plan",
			label="Create Academic Plan",
			description="Create the academic plan with a name, grade, season, and optional major.",
			hint="The plan links a grade to a season. If the grade has majors (e.g., Scientific, Literary), pick one.",
			check=check_plan_exists,
			create_doctype="Memora Academic Plan",
			sets_context=True,
		),
		StepDef(
			key="season_active",
			label="Season is Active",
			description="The linked season must be published and not yet ended.",
			hint="Open the season and tick 'Is Published'. Make sure end_date is in the future.",
			check=check_season_active,
			action_url="/app/memora-season",
		),
		StepDef(
			key="add_subjects",
			label="Add Plan Subjects",
			description="Add at least one subject to the plan.",
			hint="Each row links to a Memora Subject. Mark is_premium for paid subjects.",
			check=check_subjects_added,
			update_context=True,
			action_url="/app/memora-academic-plan/{context_name}",
		),
		StepDef(
			key="subjects_have_content",
			label="Subjects Have Published Lessons",
			description="Every subject in the plan must have at least one published lesson.",
			hint="Check each subject's content hierarchy. At least one lesson must have is_published = 1.",
			check=check_subjects_have_content,
		),
		StepDef(
			key="publish_plan",
			label="Publish the Plan",
			description="Publish the plan to make it visible to players.",
			hint="Only publish after all subjects and content are ready.",
			check=check_plan_published,
			update_context=True,
			action_url="/app/memora-academic-plan/{context_name}",
			wizard_fields=[
				{
					"fieldname": "is_published",
					"fieldtype": "Check",
					"label": "Publish this plan now",
					"default": 1,
				},
			],
		),
	],
)

register(TEMPLATE)
