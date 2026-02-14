"""Sales Invoice and Credit Note creation for voucher transactions.

Creates ERPNext Sales Invoices via ORM to ensure GL entries, tax calculation,
and JoFotara e-invoicing hooks all fire correctly. Decimal-to-float conversion
happens ONLY at the point of setting rate on the Sales Invoice Item.

Functions:
- create_voucher_invoice: Creates and submits a Sales Invoice
- create_credit_note: Creates and submits a Credit Note (return Sales Invoice)
- create_prepaid_allocation_invoice: Orchestrates invoice for completed prepaid allocation
- create_prepaid_return_credit_note: Orchestrates credit note for prepaid returns
"""

import frappe
from frappe.utils import nowdate

from memora_admin.memora_admin.services.voucher.commission import (
	calculate_commission,
	resolve_commission,
)


def create_voucher_invoice(
	customer: str,
	items: list[dict],
	remarks: str = "",
	posting_date: str | None = None,
) -> str:
	"""Create and submit a Sales Invoice for voucher cards.

	Args:
		customer: Customer (library) document name.
		items: List of dicts with keys: description, qty, rate.
			rate can be Decimal or string -- converted to float for ERPNext.
		remarks: Explanatory text linking to allocation/batch.
		posting_date: Override posting date (for consignment backdating).

	Returns:
		Submitted Sales Invoice name (e.g., "ACC-SINV-2026-00048").
	"""
	si = frappe.new_doc("Sales Invoice")
	si.customer = customer
	si.posting_date = posting_date or nowdate()
	si.remarks = remarks

	for item in items:
		si.append(
			"items",
			{
				"item_code": "MEMORA-VOUCHER-CARD",
				"description": item["description"],
				"qty": item["qty"],
				"rate": float(item["rate"]),  # ERPNext expects float for Currency fields
			},
		)

	si.insert(ignore_permissions=True)
	si.submit()

	return si.name


def create_credit_note(
	customer: str,
	return_against: str,
	items: list[dict],
	remarks: str = "",
	posting_date: str | None = None,
) -> str:
	"""Create and submit a Credit Note (return Sales Invoice).

	A Credit Note is a Sales Invoice with is_return=1, return_against set to
	the original invoice, and negative quantities. ERPNext links it to the
	original invoice for proper accounting offset.

	Args:
		customer: Customer (library) document name.
		return_against: Original Sales Invoice name to return against.
		items: List of dicts with keys: description, qty (positive), rate.
			qty will be negated automatically.
		remarks: Explanatory text.
		posting_date: Override posting date.

	Returns:
		Submitted Credit Note (Sales Invoice) name.

	Raises:
		frappe.ValidationError: If return_against is None or empty.
	"""
	if not return_against:
		frappe.throw(
			"Credit Note requires return_against reference to original invoice",
			frappe.ValidationError,
		)

	si = frappe.new_doc("Sales Invoice")
	si.customer = customer
	si.posting_date = posting_date or nowdate()
	si.remarks = remarks
	si.is_return = 1
	si.return_against = return_against

	for item in items:
		si.append(
			"items",
			{
				"item_code": "MEMORA-VOUCHER-CARD",
				"description": item["description"],
				"qty": -abs(item["qty"]),  # Negative for returns
				"rate": float(item["rate"]),
			},
		)

	si.insert(ignore_permissions=True)
	si.submit()

	return si.name


def create_prepaid_allocation_invoice(allocation_name: str) -> str:
	"""Create a Sales Invoice for a completed prepaid allocation.

	Orchestrates: load docs -> resolve commission -> calculate amounts ->
	create invoice -> link to allocation and cards.

	Args:
		allocation_name: Memora Voucher Allocation document name.

	Returns:
		Submitted Sales Invoice name.
	"""
	allocation = frappe.get_doc("Memora Voucher Allocation", allocation_name)
	batch = frappe.get_doc("Memora Voucher Batch", allocation.batch)
	card_count = len(allocation.allocation_cards)

	# Resolve commission via priority chain
	commission_type, commission_value = resolve_commission(allocation.batch, allocation.customer)

	# Calculate amounts with Decimal precision
	result = calculate_commission(
		face_value=str(batch.face_value),
		quantity=card_count,
		commission_type=commission_type,
		commission_value=commission_value,
	)

	# Create and submit invoice
	si_name = create_voucher_invoice(
		customer=allocation.customer,
		items=[
			{
				"description": f"Memora Voucher Cards - Batch {batch.batch_name}",
				"qty": card_count,
				"rate": result["net_per_card"],
			}
		],
		remarks=(
			f"Voucher allocation {allocation.name} | "
			f"Batch {batch.name} ({batch.batch_name}) | "
			f"{card_count} cards x {batch.face_value} JOD"
		),
	)

	# Link invoice to allocation
	frappe.db.set_value("Memora Voucher Allocation", allocation_name, "sales_invoice", si_name)

	# Link invoice to each card via bulk SQL UPDATE
	card_names = [row.voucher_card for row in allocation.allocation_cards]
	if card_names:
		placeholders = ", ".join(["%s"] * len(card_names))
		frappe.db.sql(
			f"""UPDATE `tabMemora Voucher Card`
			SET sales_invoice = %s, modified = NOW(), modified_by = %s
			WHERE name IN ({placeholders})""",
			[si_name, frappe.session.user] + card_names,
		)

	return si_name


def create_prepaid_return_credit_note(allocation_name: str) -> str | None:
	"""Create Credit Note(s) for a completed prepaid return.

	Groups returned cards by their original sales_invoice and creates one
	credit note per original invoice, each with return_against set correctly.

	Args:
		allocation_name: Memora Voucher Allocation document name (Return type).

	Returns:
		Credit Note name (single) or comma-separated names (multiple),
		or None if no cards had original invoices.
	"""
	allocation = frappe.get_doc("Memora Voucher Allocation", allocation_name)
	batch = frappe.get_doc("Memora Voucher Batch", allocation.batch)
	card_names = [row.voucher_card for row in allocation.allocation_cards]

	if not card_names:
		return None

	# Find original invoices for the returned cards
	placeholders = ", ".join(["%s"] * len(card_names))
	card_invoice_data = frappe.db.sql(
		f"""SELECT name, sales_invoice FROM `tabMemora Voucher Card`
		WHERE name IN ({placeholders}) AND sales_invoice IS NOT NULL AND sales_invoice != ''""",
		card_names,
		as_dict=True,
	)

	if not card_invoice_data:
		frappe.logger().warning(
			f"No original invoices found for return allocation {allocation_name}. "
			f"Skipping credit note creation."
		)
		return None

	# Group cards by original invoice
	invoice_groups: dict[str, list[str]] = {}
	for row in card_invoice_data:
		inv = row["sales_invoice"]
		if inv not in invoice_groups:
			invoice_groups[inv] = []
		invoice_groups[inv].append(row["name"])

	# Resolve commission (same as original)
	commission_type, commission_value = resolve_commission(allocation.batch, allocation.customer)

	credit_note_names = []

	for original_invoice, grouped_card_names in invoice_groups.items():
		group_count = len(grouped_card_names)

		# Calculate amounts for this group
		result = calculate_commission(
			face_value=str(batch.face_value),
			quantity=group_count,
			commission_type=commission_type,
			commission_value=commission_value,
		)

		# Create credit note for this group
		cn_name = create_credit_note(
			customer=allocation.customer,
			return_against=original_invoice,
			items=[
				{
					"description": f"Memora Voucher Cards Return - Batch {batch.batch_name}",
					"qty": group_count,
					"rate": result["net_per_card"],
				}
			],
			remarks=(
				f"Return allocation {allocation.name} | "
				f"Batch {batch.name} ({batch.batch_name}) | "
				f"{group_count} cards returned"
			),
		)
		credit_note_names.append(cn_name)

	# Link credit note to allocation (last one if multiple, or the only one)
	if credit_note_names:
		link_name = credit_note_names[0] if len(credit_note_names) == 1 else credit_note_names[-1]
		frappe.db.set_value("Memora Voucher Allocation", allocation_name, "sales_invoice", link_name)

	if len(credit_note_names) == 1:
		return credit_note_names[0]
	return ", ".join(credit_note_names)
