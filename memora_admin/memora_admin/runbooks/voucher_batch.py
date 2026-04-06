# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Voucher Batch lifecycle runbook template."""

from __future__ import annotations

import frappe

from memora_admin.memora_admin.runbooks.base import RunbookTemplate, StepDef
from memora_admin.memora_admin.runbooks.registry import register


def _get_batch(context_name: str) -> dict | None:
	if not context_name:
		return None
	return frappe.db.get_value(
		"Memora Voucher Batch",
		context_name,
		["status", "grant_type", "quantity", "generated_count", "allocated_count"],
		as_dict=True,
	)


# -- Check functions ----------------------------------------------------------


def check_grant_exists(context_name: str, context_data: dict | None) -> bool:
	"""At least one product grant exists."""
	return frappe.db.count("Memora Product Grant") > 0


def check_batch_created(context_name: str, context_data: dict | None) -> bool:
	"""Batch exists (context is set)."""
	if not context_name:
		return False
	return frappe.db.exists("Memora Voucher Batch", context_name)


def check_grants_configured(context_name: str, context_data: dict | None) -> bool:
	"""Grants or eligible plans are configured based on grant_type."""
	batch = _get_batch(context_name)
	if not batch:
		return False

	if batch.grant_type == "product_grant":
		return frappe.db.count("Memora Voucher Batch Grant", {"parent": context_name}) > 0
	elif batch.grant_type == "plan_premium":
		return frappe.db.count("Memora Voucher Batch Eligible Plan", {"parent": context_name}) > 0
	elif batch.grant_type == "live_event_access":
		target = frappe.db.get_value("Memora Voucher Batch", context_name, "target_event")
		return bool(target)
	return False


def check_cards_generated(context_name: str, context_data: dict | None) -> bool:
	"""Cards have been generated (batch is Generated or later)."""
	batch = _get_batch(context_name)
	if not batch:
		return False
	return batch.status in ("Generated", "Active", "Closed")


def check_count_matches(context_name: str, context_data: dict | None) -> bool:
	"""Generated count matches requested quantity."""
	batch = _get_batch(context_name)
	if not batch:
		return False
	return batch.generated_count == batch.quantity and batch.generated_count > 0


def check_allocation_created(context_name: str, context_data: dict | None) -> bool:
	"""At least one allocation exists for this batch."""
	if not context_name:
		return False
	return frappe.db.count("Memora Voucher Allocation", {"batch": context_name}) > 0


def check_allocation_completed(context_name: str, context_data: dict | None) -> bool:
	"""At least one allocation for this batch has been completed."""
	if not context_name:
		return False
	return frappe.db.count(
		"Memora Voucher Allocation", {"batch": context_name, "status": "Completed"}
	) > 0


def check_batch_active(context_name: str, context_data: dict | None) -> bool:
	"""Batch has been activated."""
	batch = _get_batch(context_name)
	if not batch:
		return False
	return batch.status in ("Active", "Closed")


# -- Template -----------------------------------------------------------------

TEMPLATE = RunbookTemplate(
	workflow_id="voucher_batch",
	label="Voucher Batch Lifecycle",
	description="إدارة دورة حياة دفعة القسائم من الإنشاء حتى التفعيل.",
	context_doctype="Memora Voucher Batch",
	steps=[
		StepDef(
			key="create_grant",
			label="Create Product Grant",
			description="Create a product grant that defines what content the voucher unlocks.",
			hint="Skip if reusing an existing grant. Link the grant to a plan, grade, and item code.",
			check=check_grant_exists,
			action_url="/app/memora-product-grant/new",
			optional=True,
			create_doctype="Memora Product Grant",
		),
		StepDef(
			key="batch_created",
			label="Create Voucher Batch",
			description="Create the batch with name, purpose, grant type, quantity, and PIN length.",
			hint="Non-Sale batches must have face_value = 0. Choose PIN length: 12 standard, 14/16 high-security.",
			check=check_batch_created,
			create_doctype="Memora Voucher Batch",
			sets_context=True,
		),
		StepDef(
			key="grants_configured",
			label="Configure Grants / Eligible Plans",
			description="Add Product Grants, Eligible Plans, or set Target Event based on the grant type.",
			hint="product_grant: add rows to Product Grants table. plan_premium: add Eligible Plans. live_event_access: set Target Event.",
			check=check_grants_configured,
			update_context=True,
			action_url="/app/memora-voucher-batch/{context_name}",
		),
		StepDef(
			key="cards_generated",
			label="Generate Cards",
			description="Use Actions > Generate Cards on the batch form. Transitions batch from Draft to Generated.",
			hint="Generation runs in the background. Wait for the notification. Do not edit the batch while generating.",
			check=check_cards_generated,
			action_url="/app/memora-voucher-batch/{context_name}",
		),
		StepDef(
			key="count_verified",
			label="Verify Generated Count",
			description="Confirm that generated_count matches the requested quantity.",
			hint="If counts don't match, check Error Log for generation failures. You may need to void and recreate the batch.",
			check=check_count_matches,
		),
		StepDef(
			key="allocation_created",
			label="Create Allocation",
			description="Create a Voucher Allocation to assign cards to a customer/library.",
			hint="Pick the batch, customer, sale model (Prepaid/Consignment), and add cards to the allocation.",
			check=check_allocation_created,
			create_doctype="Memora Voucher Allocation",
			action_url="/app/memora-voucher-allocation/new?batch={context_name}",
		),
		StepDef(
			key="allocation_completed",
			label="Complete Allocation",
			description="Move the allocation through approval and mark it Completed. Cards become Allocated.",
			hint="Flow: Draft > Pending Approval > Approved > Completed. For Prepaid, a Sales Invoice is auto-created on completion.",
			check=check_allocation_completed,
			action_url="/app/memora-voucher-allocation?batch={context_name}",
		),
		StepDef(
			key="batch_active",
			label="Batch is Active",
			description="Batch auto-transitions to Active after the first completed allocation.",
			hint="If still Generated, check that the allocation reached Completed status.",
			check=check_batch_active,
			action_url="/app/memora-voucher-batch/{context_name}",
		),
	],
)

register(TEMPLATE)
