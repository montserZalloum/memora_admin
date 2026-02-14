"""Commission calculation and priority chain resolution for voucher invoicing.

Uses decimal.Decimal exclusively for all arithmetic to avoid float precision
issues (decision [33-01]). Commission determines the library's cut; the invoice
charges the NET amount (face_value minus commission).

Priority chain (FIN-03):
1. Product-level override: Memora Voucher Batch Grant child rows
2. Library default: Customer custom fields (voucher_commission_type/value)
3. Zero: No commission applied
"""

from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")


def calculate_commission(
	face_value: str,
	quantity: int,
	commission_type: str | None,
	commission_value: str | None,
) -> dict:
	"""Calculate commission with exact Decimal arithmetic.

	Args:
		face_value: Card face value as string (from Voucher Batch.face_value).
		quantity: Number of cards.
		commission_type: "Percentage" or "Fixed Amount" or None/empty.
		commission_value: Rate/amount as string or None/empty.

	Returns:
		Dict with per_card_commission, total_commission,
		net_per_card, net_total -- all as Decimal objects.
	"""
	fv = Decimal(str(face_value))
	qty = Decimal(str(quantity))

	if not commission_type or not commission_value:
		# No commission -- full face value invoiced
		net_per_card = fv.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
		return {
			"per_card_commission": Decimal("0.00"),
			"total_commission": Decimal("0.00"),
			"net_per_card": net_per_card,
			"net_total": (net_per_card * qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
		}

	cv = Decimal(str(commission_value))

	if commission_type == "Percentage":
		per_card_commission = (fv * cv / Decimal("100")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
	elif commission_type == "Fixed Amount":
		per_card_commission = cv.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
	else:
		per_card_commission = Decimal("0.00")

	net_per_card = (fv - per_card_commission).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
	total_commission = (per_card_commission * qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
	net_total = (net_per_card * qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

	return {
		"per_card_commission": per_card_commission,
		"total_commission": total_commission,
		"net_per_card": net_per_card,
		"net_total": net_total,
	}


def resolve_commission(batch_name: str, library: str) -> tuple[str | None, str | None]:
	"""Resolve commission type and value using the priority chain.

	Priority (FIN-03):
	1. Batch grant-level override (product-level): first grant with commission set
	2. Library (Customer) default: custom fields on Customer DocType
	3. Zero: returns (None, None) for no commission

	Args:
		batch_name: Memora Voucher Batch document name.
		library: Customer (library) document name.

	Returns:
		Tuple of (commission_type, commission_value) or (None, None).
	"""
	import frappe

	# 1. Check batch grant-level override (product-level)
	grants = frappe.get_all(
		"Memora Voucher Batch Grant",
		filters={"parent": batch_name, "commission_type": ["is", "set"]},
		fields=["commission_type", "commission_value"],
		limit=1,
	)
	if grants and grants[0].commission_type:
		return grants[0].commission_type, grants[0].commission_value

	# 2. Check library (Customer) default
	customer = frappe.db.get_value(
		"Customer",
		library,
		["voucher_commission_type", "voucher_commission_value"],
		as_dict=True,
	)
	if customer and customer.voucher_commission_type:
		return customer.voucher_commission_type, customer.voucher_commission_value

	# 3. No commission
	return None, None
