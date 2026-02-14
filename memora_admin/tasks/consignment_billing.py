"""Monthly consignment billing: invoice redeemed consignment cards from previous month.

Runs on the 1st of each month at 02:00 (cron: 0 2 1 * *). Queries all cards with
status='Redeemed', sale_model='Consignment', and no sales_invoice link that were
redeemed in the previous calendar month. Groups by library, creates one Sales Invoice
per library (with per-batch line items), and marks each card with its invoice to
prevent double-invoicing.

Transaction isolation: each library is processed independently so one failure
does not roll back another library's invoice.
"""

from itertools import groupby
from operator import itemgetter

import frappe
from frappe.utils import add_months, get_first_day, get_last_day, nowdate

from memora_admin.memora_admin.services.voucher.commission import (
	calculate_commission,
	resolve_commission,
)
from memora_admin.memora_admin.services.voucher.invoice import create_voucher_invoice


def generate_monthly_invoices():
	"""Generate invoices for redeemed consignment cards from the previous month.

	- Queries cards: status=Redeemed, sale_model=Consignment, no sales_invoice,
	  redeemed_at within previous calendar month.
	- Groups by library, sub-groups by batch.
	- Creates one Sales Invoice per library with per-batch line items.
	- Marks each card with sales_invoice to prevent double-invoicing.
	- Per-library transaction isolation: commit after each library.
	"""
	today = nowdate()
	prev_month_start = str(get_first_day(add_months(today, -1)))
	prev_month_end = str(get_last_day(add_months(today, -1)))
	month_label = f"{prev_month_start} to {prev_month_end}"

	# Query redeemed consignment cards not yet invoiced in the previous month
	cards = frappe.db.sql(
		"""
		SELECT c.name, c.batch, c.library, c.redeemed_at,
			b.face_value, b.batch_name
		FROM `tabMemora Voucher Card` c
		JOIN `tabMemora Voucher Batch` b ON c.batch = b.name
		WHERE c.status = 'Redeemed'
			AND c.sale_model = 'Consignment'
			AND (c.sales_invoice IS NULL OR c.sales_invoice = '')
			AND c.redeemed_at >= %s
			AND c.redeemed_at <= %s
		ORDER BY c.library, c.batch
		""",
		(prev_month_start, prev_month_end + " 23:59:59"),
		as_dict=True,
	)

	if not cards:
		frappe.logger().info(f"Consignment billing {month_label}: No consignment cards to invoice")
		return

	frappe.logger().info(
		f"Consignment billing {month_label}: Found {len(cards)} card(s) to invoice"
	)

	invoiced_count = 0
	error_count = 0

	# Group by library
	for library, library_cards_iter in groupby(cards, key=itemgetter("library")):
		library_cards = list(library_cards_iter)

		try:
			items = []
			all_card_names = []
			batch_count = 0

			# Sub-group by batch within library
			for batch, batch_cards_iter in groupby(library_cards, key=itemgetter("batch")):
				batch_cards = list(batch_cards_iter)
				card_count = len(batch_cards)
				batch_name = batch_cards[0]["batch_name"]
				face_value = str(batch_cards[0]["face_value"])

				# Resolve commission via priority chain
				commission_type, commission_value = resolve_commission(batch, library)

				# Calculate amounts with Decimal precision
				result = calculate_commission(
					face_value=face_value,
					quantity=card_count,
					commission_type=commission_type,
					commission_value=commission_value,
				)

				items.append({
					"description": f"Consignment - Batch {batch_name} ({month_label})",
					"qty": card_count,
					"rate": result["net_per_card"],
				})

				all_card_names.extend([c["name"] for c in batch_cards])
				batch_count += 1

			total_cards = len(all_card_names)

			# Create one invoice per library
			invoice_name = create_voucher_invoice(
				customer=library,
				items=items,
				remarks=(
					f"Monthly consignment billing {month_label} | "
					f"{total_cards} cards from {batch_count} batch(es)"
				),
				posting_date=str(get_first_day(today)),
			)

			# Mark all cards with their invoice to prevent double-invoicing
			placeholders = ", ".join(["%s"] * len(all_card_names))
			frappe.db.sql(
				f"""UPDATE `tabMemora Voucher Card`
				SET sales_invoice = %s, modified = NOW(), modified_by = %s
				WHERE name IN ({placeholders})""",
				[invoice_name, frappe.session.user] + all_card_names,
			)

			frappe.db.commit()
			invoiced_count += total_cards

			frappe.logger().info(
				f"Consignment billing: Invoice {invoice_name} created for {library} "
				f"({total_cards} cards, {batch_count} batch(es))"
			)

		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Consignment billing failed for {library}")
			error_count += 1

	frappe.logger().info(
		f"Consignment billing {month_label} complete: "
		f"{invoiced_count} card(s) invoiced, {error_count} library error(s)"
	)
