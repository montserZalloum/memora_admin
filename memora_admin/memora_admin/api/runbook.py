# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Runbook API — create, validate, and manage workflow runbooks."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from memora_admin.memora_admin.runbooks.registry import get_all_templates, get_template


def _check_permission():
	roles = frappe.get_roles()
	if "System Manager" not in roles and "Memora Admin" not in roles:
		frappe.throw("You don't have permission to manage runbooks", frappe.PermissionError)


@frappe.whitelist()
def get_templates() -> list[dict]:
	"""Return all available workflow templates."""
	_check_permission()
	templates = get_all_templates()
	return [
		{
			"workflow_id": t.workflow_id,
			"label": t.label,
			"description": t.description,
			"context_doctype": t.context_doctype,
			"step_count": len(t.steps),
		}
		for t in templates.values()
	]


@frappe.whitelist()
def create_runbook(workflow_id: str, context_name: str | None = None) -> dict:
	"""Create a new Memora Runbook from a template.

	Args:
		workflow_id: Template key (e.g. "voucher_batch")
		context_name: Name of the context document (e.g. batch name)

	Returns:
		Dict with the created runbook name
	"""
	_check_permission()

	template = get_template(workflow_id)
	if not template:
		frappe.throw(f"Unknown workflow: {workflow_id}")

	# Validate context document exists if provided
	if context_name and template.context_doctype:
		if not frappe.db.exists(template.context_doctype, context_name):
			frappe.throw(f"{template.context_doctype} '{context_name}' does not exist")

	doc = frappe.new_doc("Memora Runbook")
	doc.workflow_id = workflow_id
	doc.workflow_label = template.label
	doc.status = "Not Started"
	if template.context_doctype:
		doc.context_doctype = template.context_doctype
	if context_name:
		doc.context_name = context_name

	for step_def in template.steps:
		action_url = step_def.action_url or ""
		if action_url and context_name:
			action_url = action_url.replace("{context_name}", context_name)
		doc.append("steps", {
			"step_key": step_def.key,
			"label": step_def.label,
			"description": step_def.description,
			"hint": step_def.hint,
			"optional": step_def.optional,
			"action_url": action_url,
			"status": "Pending",
		})

	doc.insert()
	return {"runbook": doc.name, "workflow_id": workflow_id, "steps": len(template.steps)}


@frappe.whitelist()
def validate_steps(runbook_name: str) -> dict:
	"""Run all check functions for a runbook and update step statuses.

	Returns:
		Dict with per-step results and summary counts
	"""
	_check_permission()

	doc = frappe.get_doc("Memora Runbook", runbook_name)
	template = get_template(doc.workflow_id)
	if not template:
		frappe.throw(f"Template '{doc.workflow_id}' not found")

	# Build lookup: step_key -> check function
	checks = {s.key: s.check for s in template.steps if s.check}

	results = []
	for step in doc.steps:
		if step.status in ("Done", "Skipped"):
			results.append({"key": step.step_key, "status": step.status, "changed": False})
			continue

		check_fn = checks.get(step.step_key)
		if not check_fn:
			results.append({"key": step.step_key, "status": step.status, "changed": False})
			continue

		try:
			passed = check_fn(doc.context_name, doc.context_data)
		except Exception:
			passed = False

		if passed:
			step.status = "Done"
			step.completed_by = frappe.session.user
			step.completed_at = now_datetime()
			results.append({"key": step.step_key, "status": "Done", "changed": True})
		elif step.optional:
			step.status = "Skipped"
			step.completed_by = frappe.session.user
			step.completed_at = now_datetime()
			results.append({"key": step.step_key, "status": "Skipped", "changed": True})
		else:
			results.append({"key": step.step_key, "status": step.status, "changed": False})

	# Auto-transition runbook status
	if doc.status == "Not Started":
		doc.status = "In Progress"

	if doc.check_all_done():
		doc.status = "Completed"

	doc.save()

	done = sum(1 for s in doc.steps if s.status == "Done")
	skipped = sum(1 for s in doc.steps if s.status == "Skipped")
	total = len(doc.steps)

	return {
		"steps": results,
		"summary": {"done": done, "skipped": skipped, "pending": total - done - skipped, "total": total},
	}


@frappe.whitelist()
def complete_step(runbook_name: str, step_key: str) -> dict:
	"""Manually mark a step as Done."""
	_check_permission()

	doc = frappe.get_doc("Memora Runbook", runbook_name)
	if doc.status in ("Completed", "Cancelled"):
		frappe.throw(f"Cannot modify steps on a {doc.status} runbook")

	for step in doc.steps:
		if step.step_key == step_key:
			if step.status == "Done":
				return {"step_key": step_key, "status": "Done", "changed": False}
			step.status = "Done"
			step.completed_by = frappe.session.user
			step.completed_at = now_datetime()

			if doc.status == "Not Started":
				doc.status = "In Progress"
			if doc.check_all_done():
				doc.status = "Completed"

			doc.save()
			return {"step_key": step_key, "status": "Done", "changed": True}

	frappe.throw(f"Step '{step_key}' not found in runbook")


@frappe.whitelist()
def skip_step(runbook_name: str, step_key: str) -> dict:
	"""Skip a step."""
	_check_permission()

	doc = frappe.get_doc("Memora Runbook", runbook_name)
	if doc.status in ("Completed", "Cancelled"):
		frappe.throw(f"Cannot modify steps on a {doc.status} runbook")

	for step in doc.steps:
		if step.step_key == step_key:
			if step.status == "Skipped":
				return {"step_key": step_key, "status": "Skipped", "changed": False}
			step.status = "Skipped"
			step.completed_by = frappe.session.user
			step.completed_at = now_datetime()

			if doc.status == "Not Started":
				doc.status = "In Progress"
			if doc.check_all_done():
				doc.status = "Completed"

			doc.save()
			return {"step_key": step_key, "status": "Skipped", "changed": True}

	frappe.throw(f"Step '{step_key}' not found in runbook")


@frappe.whitelist()
def cancel_runbook(runbook_name: str) -> dict:
	"""Cancel a runbook."""
	_check_permission()

	doc = frappe.get_doc("Memora Runbook", runbook_name)
	if doc.status in ("Completed", "Cancelled"):
		frappe.throw(f"Cannot cancel a {doc.status} runbook")

	doc.status = "Cancelled"
	doc.save()
	return {"runbook": doc.name, "status": "Cancelled"}


# ---------------------------------------------------------------------------
# Wizard API
# ---------------------------------------------------------------------------

# DocTypes that wizard templates are allowed to create
_WIZARD_ALLOWED_DOCTYPES: set[str] | None = None


def _get_allowed_doctypes() -> set[str]:
	global _WIZARD_ALLOWED_DOCTYPES
	if _WIZARD_ALLOWED_DOCTYPES is None:
		templates = get_all_templates()
		_WIZARD_ALLOWED_DOCTYPES = set()
		for t in templates.values():
			for s in t.steps:
				if s.create_doctype:
					_WIZARD_ALLOWED_DOCTYPES.add(s.create_doctype)
	return _WIZARD_ALLOWED_DOCTYPES


@frappe.whitelist()
def get_wizard_config(workflow_id: str, runbook_name: str | None = None) -> dict:
	"""Return wizard step config with live check results and field definitions."""
	_check_permission()

	template = get_template(workflow_id)
	if not template:
		frappe.throw(f"Unknown workflow: {workflow_id}")

	context_name = None
	if runbook_name:
		context_name = frappe.db.get_value("Memora Runbook", runbook_name, "context_name")

	steps = []
	for step_def in template.steps:
		passed = False
		if step_def.check:
			try:
				passed = bool(step_def.check(context_name or "", None))
			except Exception:
				passed = False

		steps.append({
			"key": step_def.key,
			"label": step_def.label,
			"description": step_def.description,
			"hint": step_def.hint,
			"optional": step_def.optional,
			"passed": passed,
			"create_doctype": step_def.create_doctype,
			"wizard_fields": step_def.wizard_fields or [],
			"sets_context": step_def.sets_context,
			"update_context": step_def.update_context,
		})

	return {
		"workflow_id": workflow_id,
		"label": template.label,
		"steps": steps,
	}


@frappe.whitelist()
def wizard_create_doc(doctype: str, values: dict | str) -> dict:
	"""Create a document from wizard step values.

	Only DocTypes referenced in runbook templates are allowed.
	"""
	_check_permission()

	if doctype not in _get_allowed_doctypes():
		frappe.throw(f"DocType '{doctype}' is not allowed in wizard creation", frappe.PermissionError)

	if isinstance(values, str):
		import json as _json

		values = _json.loads(values)

	doc = frappe.new_doc(doctype)
	for key, val in values.items():
		if isinstance(val, list):
			for row in val:
				doc.append(key, row)
		else:
			doc.set(key, val)
	doc.insert()

	return {"doctype": doctype, "name": doc.name, "title": doc.get_title()}


@frappe.whitelist()
def wizard_set_context(runbook_name: str, context_name: str) -> dict:
	"""Set the context document on a runbook mid-wizard.

	Called after a ``sets_context`` step creates the main document.
	Also resolves ``{context_name}`` placeholders in step action_urls.
	"""
	_check_permission()

	doc = frappe.get_doc("Memora Runbook", runbook_name)
	doc.context_name = context_name

	for step in doc.steps:
		if step.action_url and "{context_name}" in step.action_url:
			step.action_url = step.action_url.replace("{context_name}", context_name)

	doc.save()
	return {"runbook": doc.name, "context_name": context_name}


@frappe.whitelist()
def wizard_update_doc(runbook_name: str, values: dict | str) -> dict:
	"""Update the runbook's context document with wizard field values.

	Scalar values are set directly. List values are appended as child-table rows.
	"""
	_check_permission()

	doc = frappe.get_doc("Memora Runbook", runbook_name)
	if not doc.context_doctype or not doc.context_name:
		frappe.throw("Runbook has no context document to update")

	if isinstance(values, str):
		import json as _json

		values = _json.loads(values)

	target = frappe.get_doc(doc.context_doctype, doc.context_name)
	for key, val in values.items():
		if isinstance(val, list):
			for row in val:
				target.append(key, row)
		else:
			target.set(key, val)
	target.save()

	return {"doctype": target.doctype, "name": target.name, "title": target.get_title()}
