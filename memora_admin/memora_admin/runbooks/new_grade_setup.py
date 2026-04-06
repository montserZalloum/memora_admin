# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""New grade setup runbook template."""

from __future__ import annotations

import frappe

from memora_admin.memora_admin.runbooks.base import RunbookTemplate, StepDef
from memora_admin.memora_admin.runbooks.registry import register


def check_major_exists(context_name: str, context_data: dict | None) -> bool:
	"""At least one major exists."""
	return frappe.db.count("Memora Major") > 0


def check_grade_exists(context_name: str, context_data: dict | None) -> bool:
	"""At least one grade exists."""
	return frappe.db.count("Memora Grade") > 0


def check_season_exists(context_name: str, context_data: dict | None) -> bool:
	"""At least one season exists."""
	return frappe.db.count("Memora Season") > 0


TEMPLATE = RunbookTemplate(
	workflow_id="new_grade_setup",
	label="New Grade Setup",
	description="دليل إضافة صف دراسي جديد وربطه بالتخصصات والموسم.",
	steps=[
		StepDef(
			key="create_major",
			label="Create a Major",
			description="Add a major if the grade requires one (e.g., Scientific, Literary).",
			hint="Skip this step if the grade does not have majors.",
			check=check_major_exists,
			action_url="/app/memora-major/new",
			optional=True,
			create_doctype="Memora Major",
		),
		StepDef(
			key="create_grade",
			label="Create a Grade",
			description="Create a grade level and link it with relevant majors if applicable.",
			hint="Open the grade form and add majors in the Grade Majors table.",
			check=check_grade_exists,
			action_url="/app/memora-grade/new",
			create_doctype="Memora Grade",
		),
		StepDef(
			key="create_season",
			label="Create a Season",
			description="Create a new season if one does not already exist.",
			hint="Skip this step if you want to use an existing season.",
			check=check_season_exists,
			action_url="/app/memora-season/new",
			optional=True,
			create_doctype="Memora Season",
		),
	],
)

register(TEMPLATE)
