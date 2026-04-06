# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

from __future__ import annotations

from memora_admin.memora_admin.runbooks.base import RunbookTemplate

_REGISTRY: dict[str, RunbookTemplate] = {}


def register(template: RunbookTemplate) -> None:
	_REGISTRY[template.workflow_id] = template


def get_template(workflow_id: str) -> RunbookTemplate | None:
	_ensure_loaded()
	return _REGISTRY.get(workflow_id)


def get_all_templates() -> dict[str, RunbookTemplate]:
	_ensure_loaded()
	return dict(_REGISTRY)


_loaded = False


def _ensure_loaded():
	global _loaded
	if _loaded:
		return
	# Import template modules so they self-register
	import memora_admin.memora_admin.runbooks.academic_plan  # noqa: F401
	import memora_admin.memora_admin.runbooks.new_grade_setup  # noqa: F401
	import memora_admin.memora_admin.runbooks.voucher_batch  # noqa: F401

	_loaded = True
