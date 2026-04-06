# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class StepDef:
	"""Definition of a single runbook step."""

	key: str
	label: str
	description: str = ""
	check: Callable[[str, dict | None], bool] | None = None
	hint: str = ""
	action_url: str | None = None
	optional: bool = False
	create_doctype: str | None = None
	wizard_fields: list[dict] | None = None
	sets_context: bool = False
	update_context: bool = False


@dataclass
class RunbookTemplate:
	"""Definition of a runbook workflow template."""

	workflow_id: str
	label: str
	description: str = ""
	context_doctype: str | None = None
	steps: list[StepDef] = field(default_factory=list)
