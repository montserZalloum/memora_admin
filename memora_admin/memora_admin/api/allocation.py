"""Voucher allocation API.

Provides whitelisted methods for the allocation workflow:
fill_cards, submit_allocation, approve_allocation, reject_allocation.

These are invoked from JS form buttons on the Memora Voucher Allocation form.
"""

import frappe


@frappe.whitelist()
def fill_cards(allocation_name: str, quantity: int = 0) -> dict:
	"""Auto-fill cards from the batch into the allocation's child table.

	For Allocate type: queries Available cards from the batch.
	For Return type: queries Allocated cards belonging to the allocation's library.

	Args:
		allocation_name: The allocation document name.
		quantity: Max number of cards to fill. 0 or negative means all available.

	Returns:
		dict with filled_count.
	"""
	quantity = int(quantity)
	alloc = frappe.get_doc("Memora Voucher Allocation", allocation_name)

	if alloc.status != "Draft":
		frappe.throw(
			f"Can only fill cards in Draft status. Current status: {alloc.status}",
			frappe.ValidationError,
		)

	page_length = quantity if quantity > 0 else 0

	if alloc.allocation_type == "Allocate":
		cards = frappe.db.get_list(
			"Memora Voucher Card",
			filters={"batch": alloc.batch, "status": "Available"},
			fields=["name"],
			order_by="name asc",
			page_length=page_length,
			ignore_permissions=True,
		)
	elif alloc.allocation_type == "Return":
		cards = frappe.db.get_list(
			"Memora Voucher Card",
			filters={
				"batch": alloc.batch,
				"status": "Allocated",
				"library": alloc.customer,
			},
			fields=["name"],
			order_by="name asc",
			page_length=page_length,
			ignore_permissions=True,
		)
	else:
		frappe.throw(
			f"Unknown allocation type: {alloc.allocation_type}",
			frappe.ValidationError,
		)

	# Clear existing child rows and fill with queried cards
	alloc.allocation_cards = []
	for card in cards:
		alloc.append("allocation_cards", {"voucher_card": card.name})

	alloc.save(ignore_permissions=True)
	frappe.db.commit()

	return {"filled_count": len(cards)}


@frappe.whitelist()
def submit_allocation(allocation_name: str) -> dict:
	"""Submit an allocation through the approval workflow.

	If the library requires approval, transitions to Pending Approval.
	Otherwise, auto-approves: Draft -> Approved -> Completed.
	The on_update hook fires on the Completed save and applies card updates.

	Args:
		allocation_name: The allocation document name.

	Returns:
		dict with resulting status.
	"""
	alloc = frappe.get_doc("Memora Voucher Allocation", allocation_name)

	if alloc.status != "Draft":
		frappe.throw(
			f"Allocation must be in Draft status to submit. Current status: {alloc.status}",
			frappe.ValidationError,
		)

	if not alloc.allocation_cards:
		frappe.throw(
			"No cards in allocation. Use Fill Cards first.",
			frappe.ValidationError,
		)

	# Validate all cards belong to the allocation's batch
	card_names = [row.voucher_card for row in alloc.allocation_cards]
	if card_names:
		mismatched = frappe.db.get_all(
			"Memora Voucher Card",
			filters={"name": ["in", card_names], "batch": ["!=", alloc.batch]},
			pluck="name",
		)
		if mismatched:
			frappe.throw(
				f"Cards {', '.join(mismatched)} do not belong to batch {alloc.batch}.",
				frappe.ValidationError,
			)

	requires_approval = frappe.db.get_value(
		"Customer", alloc.customer, "voucher_requires_approval"
	)

	if requires_approval:
		alloc.status = "Pending Approval"
		alloc.save(ignore_permissions=True)
		frappe.db.commit()
		return {"status": "Pending Approval"}

	# Auto-approve: Draft -> Approved -> Completed (two-step per VALID_TRANSITIONS)
	alloc.status = "Approved"
	alloc.save(ignore_permissions=True)

	alloc.status = "Completed"
	alloc.save(ignore_permissions=True)
	frappe.db.commit()

	return {"status": "Completed"}


@frappe.whitelist()
def approve_allocation(allocation_name: str) -> dict:
	"""Approve a pending allocation: Pending Approval -> Approved -> Completed.

	The on_update hook fires on the Completed save and applies card updates.

	Args:
		allocation_name: The allocation document name.

	Returns:
		dict with resulting status.
	"""
	alloc = frappe.get_doc("Memora Voucher Allocation", allocation_name)

	if alloc.status != "Pending Approval":
		frappe.throw(
			f"Can only approve allocations in 'Pending Approval' status. "
			f"Current status: {alloc.status}",
			frappe.ValidationError,
		)

	alloc.status = "Approved"
	alloc.save(ignore_permissions=True)

	alloc.status = "Completed"
	alloc.save(ignore_permissions=True)
	frappe.db.commit()

	return {"status": "Completed"}


@frappe.whitelist()
def reject_allocation(allocation_name: str, reject_reason: str = "") -> dict:
	"""Reject a pending allocation: Pending Approval -> Rejected.

	Args:
		allocation_name: The allocation document name.
		reject_reason: Optional reason for rejection, stored in notes.

	Returns:
		dict with resulting status.
	"""
	alloc = frappe.get_doc("Memora Voucher Allocation", allocation_name)

	if alloc.status != "Pending Approval":
		frappe.throw(
			f"Can only reject allocations in 'Pending Approval' status. "
			f"Current status: {alloc.status}",
			frappe.ValidationError,
		)

	if reject_reason and reject_reason.strip():
		alloc.notes = reject_reason.strip()

	alloc.status = "Rejected"
	alloc.save(ignore_permissions=True)
	frappe.db.commit()

	return {"status": "Rejected"}
