"""Fixture factories for voucher system tests.

Provides factory functions to create valid, saved test documents with sensible defaults.
Each factory function returns a saved Frappe Document and can be called multiple times
to produce unique, non-colliding documents.
"""

import frappe
from frappe.utils import today, add_days, random_string


# ─────────────────────────────────────────────────────────────────────────────
# T003: make_season() factory
# ─────────────────────────────────────────────────────────────────────────────

def make_season(
	season_title: str | None = None,
	season_seq: int | None = None,
	start_date: str | None = None,
	end_date: str | None = None,
	is_published: bool = True,
):
	"""Create a Memora Season document.

	Args:
		season_title: Auto-generated if None
		season_seq: Sequence number (auto-generated if None)
		start_date: Defaults to today()
		end_date: Defaults to today() + 365 days
		is_published: Published status (default True)

	Returns:
		Saved Memora Season document.
	"""
	if season_title is None:
		season_title = f"Test Season {random_string(8)}"

	if season_seq is None:
		# Generate unique sequence number to avoid constraint violations
		import hashlib
		import time
		unique_suffix = hashlib.md5(f"{time.time()}{random_string(8)}".encode()).hexdigest()[:6]
		season_seq = int(unique_suffix, 16) % 10000 + 1

	if start_date is None:
		start_date = today()

	if end_date is None:
		end_date = add_days(start_date, 365)

	doc = frappe.get_doc({
		"doctype": "Memora Season",
		"season_title": season_title,
		"season_seq": season_seq,
		"start_date": start_date,
		"end_date": end_date,
		"is_published": 1 if is_published else 0,
	})
	doc.insert(ignore_permissions=True)
	return doc


# ─────────────────────────────────────────────────────────────────────────────
# T004: Internal helpers _make_grade() and _make_major()
# ─────────────────────────────────────────────────────────────────────────────

def _make_grade(grade_title: str | None = None):
	"""Create a Memora Grade document (internal helper).

	Args:
		grade_title: Auto-generated if None

	Returns:
		Saved Memora Grade document.
	"""
	if grade_title is None:
		grade_title = f"Test Grade {random_string(8)}"

	doc = frappe.get_doc({
		"doctype": "Memora Grade",
		"grade_title": grade_title,
	})
	doc.insert(ignore_permissions=True)
	return doc


def _make_major(major_title: str | None = None):
	"""Create a Memora Major document (internal helper).

	Args:
		major_title: Auto-generated if None

	Returns:
		Saved Memora Major document.
	"""
	if major_title is None:
		major_title = f"Test Major {random_string(8)}"

	doc = frappe.get_doc({
		"doctype": "Memora Major",
		"major_title": major_title,
	})
	doc.insert(ignore_permissions=True)
	return doc


# ─────────────────────────────────────────────────────────────────────────────
# T005: Internal helper _make_plan()
# ─────────────────────────────────────────────────────────────────────────────

def _make_plan(grade: str | None = None, season: str | None = None):
	"""Create a Memora Academic Plan document (internal helper).

	Creates dependencies if not provided:
	- If grade is None, creates via _make_grade()
	- If season is None, creates via make_season()

	Args:
		grade: Grade name (str). Auto-created if None.
		season: Season name (str). Auto-created if None.

	Returns:
		Saved Memora Academic Plan document.
	"""
	if grade is None:
		grade = _make_grade().name

	if season is None:
		season = make_season().name

	doc = frappe.get_doc({
		"doctype": "Memora Academic Plan",
		"grade": grade,
		"season": season,
	})
	doc.insert(ignore_permissions=True)
	return doc


# ─────────────────────────────────────────────────────────────────────────────
# T006: make_product_grant() factory
# ─────────────────────────────────────────────────────────────────────────────

def make_product_grant(
	item_code: str = "MEMORA-VOUCHER-CARD",
	plan: str | None = None,
	is_published: bool = True,
	season: str | None = None,
	grade: str | None = None,
):
	"""Create a Memora Product Grant document.

	If plan is not provided, creates one via _make_plan() with optional
	grade and season parameters.

	Args:
		item_code: Item code (default "MEMORA-VOUCHER-CARD")
		plan: Plan name (str). Auto-created if None.
		is_published: Published status (default True)
		season: Used when auto-creating plan (optional)
		grade: Used when auto-creating plan (optional)

	Returns:
		Saved Memora Product Grant document.
	"""
	if plan is None:
		plan = _make_plan(grade=grade, season=season).name

	doc = frappe.get_doc({
		"doctype": "Memora Product Grant",
		"item_code": item_code,
		"plan": plan,
		"is_published": 1 if is_published else 0,
	})
	doc.insert(ignore_permissions=True)
	return doc


# ─────────────────────────────────────────────────────────────────────────────
# T007: make_customer() factory
# ─────────────────────────────────────────────────────────────────────────────

def make_customer(
	customer_name: str | None = None,
	requires_approval: bool = False,
	commission_type: str | None = None,
	commission_value: str | None = None,
):
	"""Create a Customer document with voucher-specific custom fields.

	Args:
		customer_name: Auto-generated if None
		requires_approval: Requires approval status (default False)
		commission_type: "Percentage" or "Fixed Amount" (optional)
		commission_value: Commission value (optional)

	Returns:
		Saved Customer document.
	"""
	if customer_name is None:
		customer_name = f"Test Library {random_string(8)}"

	# Create Customer doc with standard fields
	doc = frappe.get_doc({
		"doctype": "Customer",
		"customer_name": customer_name,
		"customer_type": "Company",
	})
	doc.insert(ignore_permissions=True)

	# Set voucher-specific custom fields via db.set_value()
	frappe.db.set_value(
		"Customer",
		doc.name,
		"voucher_requires_approval",
		1 if requires_approval else 0,
	)

	if commission_type is not None:
		frappe.db.set_value(
			"Customer",
			doc.name,
			"voucher_commission_type",
			commission_type,
		)

	if commission_value is not None:
		frappe.db.set_value(
			"Customer",
			doc.name,
			"voucher_commission_value",
			commission_value,
		)

	# Reload to get updated values
	doc.reload()
	return doc


# ─────────────────────────────────────────────────────────────────────────────
# T008: make_batch() factory
# ─────────────────────────────────────────────────────────────────────────────

def make_batch(
	batch_name: str | None = None,
	quantity: int = 10,
	pin_length: int = 12,
	face_value: float = 5,
	grants: list[str] | None = None,
	status: str = "Draft",
):
	"""Create a Memora Voucher Batch document.

	If grants are provided, creates Memora Voucher Batch Grant child rows.

	Args:
		batch_name: Auto-generated if None
		quantity: Number of vouchers (default 10)
		pin_length: PIN length (default 12)
		face_value: Voucher face value (default 5)
		grants: List of Product Grant names (optional)
		status: Batch status (default "Draft")

	Returns:
		Saved Memora Voucher Batch document.
	"""
	if batch_name is None:
		batch_name = f"Test Batch {random_string(8)}"

	# Build batch_grants child table rows if grants provided
	batch_grants = []
	if grants:
		for grant_name in grants:
			batch_grants.append({
				"product_grant": grant_name,
			})

	doc = frappe.get_doc({
		"doctype": "Memora Voucher Batch",
		"batch_name": batch_name,
		"quantity": quantity,
		"pin_length": pin_length,
		"face_value": face_value,
		"status": status,
		"batch_grants": batch_grants,
	})
	doc.insert(ignore_permissions=True)
	return doc


# ─────────────────────────────────────────────────────────────────────────────
# T009: make_player() factory
# ─────────────────────────────────────────────────────────────────────────────

def make_player(
	display_name: str | None = None,
	plan: str | None = None,
	grade: str | None = None,
	major: str | None = None,
	season: str | None = None,
):
	"""Create a Memora Player Profile document with all required dependencies.

	Auto-creates dependencies when not provided:
	- If plan is None, creates via _make_plan()
	- If grade is None, creates via _make_grade()
	- If major is None, creates via _make_major()
	- If season is None, creates via make_season()

	Args:
		display_name: Auto-generated if None
		plan: Plan name (str). Auto-created if None.
		grade: Grade name (str). Auto-created if None.
		major: Major name (str). Auto-created if None.
		season: Season name (str). Auto-created if None.

	Returns:
		Saved Memora Player Profile document.
	"""
	if display_name is None:
		display_name = f"Test Player {random_string(8)}"

	# Create dependencies in order
	if season is None:
		season = make_season().name

	if grade is None:
		grade = _make_grade().name

	if major is None:
		major = _make_major().name

	if plan is None:
		plan = _make_plan(grade=grade, season=season).name

	doc = frappe.get_doc({
		"doctype": "Memora Player Profile",
		"display_name": display_name,
		"plan": plan,
		"grade": grade,
		"major": major,
		"season": season,
		"avatar": "pre",
	})
	doc.insert(ignore_permissions=True)
	return doc


# ─────────────────────────────────────────────────────────────────────────────
# T010: make_allocation() factory
# ─────────────────────────────────────────────────────────────────────────────

def make_allocation(
	batch: str,
	customer: str,
	allocation_type: str = "Allocate",
	sale_model: str = "Prepaid",
):
	"""Create a Memora Voucher Allocation document.

	Args:
		batch: Voucher Batch name (required)
		customer: Customer name (required)
		allocation_type: Allocation type (default "Allocate")
		sale_model: Sale model (default "Prepaid")

	Returns:
		Saved Memora Voucher Allocation document in Draft status.
	"""
	doc = frappe.get_doc({
		"doctype": "Memora Voucher Allocation",
		"batch": batch,
		"customer": customer,
		"allocation_type": allocation_type,
		"sale_model": sale_model,
		"status": "Draft",
	})
	doc.insert(ignore_permissions=True)
	return doc


# ─────────────────────────────────────────────────────────────────────────────
# T011: Module exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
	"make_season",
	"make_product_grant",
	"make_customer",
	"make_batch",
	"make_player",
	"make_allocation",
]
