"""Batch counter recount and auto-close helper for voucher subsystem.

Provides a shared helper that recounts all batch counters from actual card states
and auto-closes batches when all cards reach terminal states.

This centralizes the counter update logic to prevent drift across multiple call sites
(redeem_voucher, void_card, expire_season_cards).
"""


def recount_and_maybe_close(batch_name: str) -> dict:
	"""Recount all batch counters from actual card states and auto-close if eligible.

	Implements the counter recount pattern: query actual card states, update counters
	via a single UPDATE, then conditionally transition batch to Closed if all cards
	are in terminal states (Redeemed, Void, or Expired).

	Args:
		batch_name: The name of the Memora Voucher Batch to update.

	Returns:
		dict with keys:
			allocated_count (int): Current count of Allocated cards
			redeemed_count (int): Current count of Redeemed cards
			voided_count (int): Current count of Void cards
			expired_count (int): Current count of Expired cards
			closed (bool): Whether the batch was auto-closed by this call

	Notes:
		- Does NOT call frappe.db.commit() — caller is responsible for transaction boundaries
		- Does NOT update generated_count — this is set once during generation and never changes
		- Does NOT modify void_reason — auto-closed batches are distinguished by absence of void_reason
		- Auto-close only triggers for Active batches (not Draft or Generated)
		- Idempotent: calling multiple times for the same batch produces identical results
	"""
	import frappe

	# Phase 1: Recount all 4 counter fields from actual card states
	allocated_count = frappe.db.count(
		"Memora Voucher Card",
		{"batch": batch_name, "status": "Allocated"}
	)
	redeemed_count = frappe.db.count(
		"Memora Voucher Card",
		{"batch": batch_name, "status": "Redeemed"}
	)
	voided_count = frappe.db.count(
		"Memora Voucher Card",
		{"batch": batch_name, "status": "Void"}
	)
	expired_count = frappe.db.count(
		"Memora Voucher Card",
		{"batch": batch_name, "status": "Expired"}
	)

	# Update all 4 counter fields on the batch via single call
	frappe.db.set_value(
		"Memora Voucher Batch",
		batch_name,
		{
			"allocated_count": allocated_count,
			"redeemed_count": redeemed_count,
			"voided_count": voided_count,
			"expired_count": expired_count,
		},
		update_modified=True,
	)

	# Phase 2: Check auto-close condition
	# Read current batch status and check if zero non-terminal cards remain
	batch = frappe.get_doc("Memora Voucher Batch", batch_name)
	closed = False

	if batch.status == "Active":
		# Count cards with non-terminal statuses (Available or Allocated)
		non_terminal_count = frappe.db.count(
			"Memora Voucher Card",
			{"batch": batch_name, "status": ["in", ["Available", "Allocated"]]}
		)

		# If all cards are terminal (no Available or Allocated), transition to Closed
		if non_terminal_count == 0:
			frappe.db.set_value(
				"Memora Voucher Batch",
				batch_name,
				"status",
				"Closed",
				update_modified=True,
			)
			closed = True

	return {
		"allocated_count": allocated_count,
		"redeemed_count": redeemed_count,
		"voided_count": voided_count,
		"expired_count": expired_count,
		"closed": closed,
	}
