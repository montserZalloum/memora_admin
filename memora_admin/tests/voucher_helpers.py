"""Helper functions for voucher system tests.

Provides helper functions to execute common multi-step test operations:
- generate_batch_sync: Generate cards synchronously
- get_card_statuses: Query card counts by status
- fill_and_complete_allocation: Full allocation workflow
- redeem_card_by_pin: Redeem with plaintext PIN (computes HMAC)
- get_pins_from_export: Extract plaintext PINs from batch export
- assert_batch_counters: Assert batch counter fields
"""

import csv
import io
import frappe

from memora_admin.memora_admin.api.voucher import (
	generate_cards_job,
	redeem_voucher,
	export_for_print,
	preview_voucher,
)
from memora_admin.memora_admin.api.allocation import (
	fill_cards,
	submit_allocation,
	approve_allocation,
)
from memora_admin.memora_admin.services.voucher.generator import compute_hmac
from memora_admin.memora_admin.tests.voucher_fixtures import make_allocation


# ─────────────────────────────────────────────────────────────────────────────
# T012: generate_batch_sync() helper
# ─────────────────────────────────────────────────────────────────────────────

def generate_batch_sync(batch_name: str) -> None:
	"""Generate cards synchronously by calling generate_cards_job() directly.

	Bypasses the background queue to enable synchronous test execution.
	All validation and generation logic executes immediately.

	Args:
		batch_name: The Voucher Batch name.

	Preconditions:
		- Batch must be in Draft status
		- Batch must have valid quantity and grants

	Postconditions:
		- Batch transitions to Generated status
		- Cards are created with serials and HMAC-hashed PINs
		- Encrypted export file is attached to batch

	Raises:
		Any exception from generate_cards_job() propagates directly.

	Example:
		batch = make_batch(grants=[grant.name])
		generate_batch_sync(batch.name)
		batch.reload()
		assert batch.status == "Generated"
	"""
	generate_cards_job(batch_name)


# ─────────────────────────────────────────────────────────────────────────────
# T013: get_card_statuses() helper
# ─────────────────────────────────────────────────────────────────────────────

def get_card_statuses(batch_name: str) -> dict[str, int]:
	"""Get card status counts for a batch.

	Queries Memora Voucher Card documents, groups by status,
	and returns a dictionary mapping status to count.

	Args:
		batch_name: The Voucher Batch name.

	Returns:
		Dict mapping status strings to counts, e.g.,
		``{"Available": 8, "Allocated": 2}``.
		Only includes statuses with count > 0.

	Example:
		statuses = get_card_statuses(batch.name)
		assert statuses.get("Available", 0) == 10
	"""
	result = frappe.get_all(
		"Memora Voucher Card",
		filters={"batch": batch_name},
		fields=["status", "count(name) as cnt"],
		group_by="status",
	)

	# Convert list of dicts to status -> count dict
	status_counts = {}
	for row in result:
		status = row.get("status")
		count = row.get("cnt", 0)
		if count > 0:
			status_counts[status] = count

	return status_counts


# ─────────────────────────────────────────────────────────────────────────────
# T014: fill_and_complete_allocation() helper
# ─────────────────────────────────────────────────────────────────────────────

def fill_and_complete_allocation(
	batch_name: str,
	customer_name: str,
	quantity: int = 0,
	sale_model: str = "Prepaid",
):
	"""Create, fill, and complete an allocation in one call.

	Orchestrates the full allocation workflow:
	1. Create Draft allocation via make_allocation()
	2. Call fill_cards() API to populate child rows
	3. Call submit_allocation() API to drive approval workflow
	4. If library requires approval, call approve_allocation()
	5. Return reloaded allocation document

	Args:
		batch_name: The Voucher Batch name.
		customer_name: The Customer (library) name.
		quantity: Max cards to allocate (0 = all available).
		sale_model: "Prepaid" or "Consignment" (default "Prepaid").

	Returns:
		Completed Memora Voucher Allocation document.

	Postconditions:
		- Cards transition to Allocated status
		- Batch may transition to Active
		- If prepaid, Sales Invoice is created
		- Allocation status is Completed

	Example:
		alloc = fill_and_complete_allocation(batch.name, library.name, quantity=5)
		assert alloc.status == "Completed"
	"""
	# Step 1: Create Draft allocation
	alloc = make_allocation(
		batch=batch_name,
		customer=customer_name,
		allocation_type="Allocate",
		sale_model=sale_model,
	)

	# Step 2: Fill cards into allocation
	fill_cards(alloc.name, quantity=quantity)

	# Step 3: Submit allocation (Draft -> Pending Approval or Approved -> Completed)
	submit_allocation(alloc.name)

	# Step 4: Check if library requires approval
	library_doc = frappe.get_doc("Customer", customer_name)
	requires_approval = frappe.db.get_value(
		"Customer",
		customer_name,
		"voucher_requires_approval",
	)
	if requires_approval:
		approve_allocation(alloc.name)

	# Step 5: Reload and return
	alloc.reload()
	return alloc


# ─────────────────────────────────────────────────────────────────────────────
# T014b: get_pins_from_export() helper
# ─────────────────────────────────────────────────────────────────────────────

def get_pins_from_export(batch_name: str) -> dict[str, str]:
	"""Extract serial_no → plaintext PIN mapping from batch export.

	Calls export_for_print() to generate the encrypted CSV export,
	decrypts it, and parses the CSV to extract serial numbers and PINs.

	Args:
		batch_name: The Voucher Batch name.

	Returns:
		Dict mapping serial_no to plaintext PIN, e.g.,
		``{"VCR-001": "ABCD1234EFGH", "VCR-002": "WXYZ9876STUV"}``.

	Preconditions:
		- Batch must be in Generated or Active status
		- Batch must have encrypted export file attached
		- voucher_hmac_secret must be configured in site config

	Example:
		pins = get_pins_from_export(batch.name)
		pin = pins["VCR-001"]
		result = redeem_card_by_pin(pin, player.name, grant.name)
	"""
	# Export requires System Manager role
	frappe.set_user("Administrator")

	# Call export API — writes CSV to frappe.local.response.filecontent
	export_for_print(batch_name)

	# Read and decode CSV content
	csv_content = frappe.local.response.filecontent
	if isinstance(csv_content, bytes):
		csv_content = csv_content.decode("utf-8")

	# Parse CSV and build serial_no → PIN mapping
	reader = csv.DictReader(io.StringIO(csv_content))
	return {row["serial_no"]: row["pin"] for row in reader}


# ─────────────────────────────────────────────────────────────────────────────
# T015: redeem_card_by_pin() helper
# ─────────────────────────────────────────────────────────────────────────────

def redeem_card_by_pin(
	pin: str,
	player_id: str,
	grant_id: str,
	ip_address: str = "",
) -> dict:
	"""Redeem a voucher card using plaintext PIN.

	Computes HMAC-SHA256 from the plaintext PIN and calls the redemption API.

	Args:
		pin: Plaintext PIN (from decrypted export).
		player_id: Memora Player Profile name.
		grant_id: Memora Product Grant name.
		ip_address: Client IP for audit logging (default "").

	Returns:
		Result dict from redeem_voucher():
		- ``{"status": "success", "transaction_id": "..."}`` on success
		- ``{"error": "ERROR_CODE"}`` on failure

	Preconditions:
		- Card must be Allocated
		- Batch must be Active

	Example:
		result = redeem_card_by_pin("ABCD1234EFGH", player.name, grant.name)
		assert result["status"] == "success"
	"""
	hmac_secret = frappe.conf.get("voucher_hmac_secret")
	pin_hmac = compute_hmac(pin, hmac_secret)

	result = redeem_voucher(
		pin_hmac=pin_hmac,
		player_id=player_id,
		product_grant_id=grant_id,
		ip_address=ip_address,
	)

	return result


# ─────────────────────────────────────────────────────────────────────────────
# T015b: preview_card_by_pin() helper
# ─────────────────────────────────────────────────────────────────────────────

def preview_card_by_pin(pin: str, player_id: str) -> dict:
	"""Preview a voucher card using plaintext PIN.

	Computes HMAC-SHA256 from the plaintext PIN and calls the preview API
	to retrieve available grants and face value without consuming the card.

	Args:
		pin: Plaintext PIN (from decrypted export).
		player_id: Memora Player Profile name.

	Returns:
		Result dict from preview_voucher():
		- ``{"face_value": 100, "grants": [...]}`` on success
		- ``{"error": "ERROR_CODE"}`` on failure

	Preconditions:
		- Card must be Allocated
		- Batch must be Active

	Example:
		preview = preview_card_by_pin("ABCD1234EFGH", player.name)
		assert "face_value" in preview
		assert len(preview["grants"]) > 0
	"""
	hmac_secret = frappe.conf.get("voucher_hmac_secret")
	pin_hmac = compute_hmac(pin, hmac_secret)

	result = preview_voucher(
		pin_hmac=pin_hmac,
		player_id=player_id,
	)

	return result


# ─────────────────────────────────────────────────────────────────────────────
# T016: assert_batch_counters() helper
# ─────────────────────────────────────────────────────────────────────────────

def assert_batch_counters(
	test_case,
	batch_name: str,
	generated_count: int | None = None,
	allocated_count: int | None = None,
	redeemed_count: int | None = None,
	voided_count: int | None = None,
	expired_count: int | None = None,
) -> None:
	"""Assert batch counter fields match expected values.

	Reloads the batch from the database and asserts each non-None counter
	using test_case.assertEqual().

	Args:
		test_case: FrappeTestCase instance for assertEqual/fail methods.
		batch_name: The Voucher Batch name.
		generated_count: Expected generated count (or None to skip).
		allocated_count: Expected allocated count (or None to skip).
		redeemed_count: Expected redeemed count (or None to skip).
		voided_count: Expected voided count (or None to skip).
		expired_count: Expected expired count (or None to skip).

	Notes:
		Only asserts counters that are explicitly passed.
		Omitted counters are not checked.

	Example:
		assert_batch_counters(self, batch.name, generated_count=10, allocated_count=5)
		assert_batch_counters(self, batch.name, redeemed_count=1, voided_count=0)
	"""
	batch = frappe.get_doc("Memora Voucher Batch", batch_name)

	if generated_count is not None:
		test_case.assertEqual(
			batch.generated_count,
			generated_count,
			f"Expected generated_count={generated_count}, got {batch.generated_count}",
		)

	if allocated_count is not None:
		test_case.assertEqual(
			batch.allocated_count,
			allocated_count,
			f"Expected allocated_count={allocated_count}, got {batch.allocated_count}",
		)

	if redeemed_count is not None:
		test_case.assertEqual(
			batch.redeemed_count,
			redeemed_count,
			f"Expected redeemed_count={redeemed_count}, got {batch.redeemed_count}",
		)

	if voided_count is not None:
		test_case.assertEqual(
			batch.voided_count,
			voided_count,
			f"Expected voided_count={voided_count}, got {batch.voided_count}",
		)

	if expired_count is not None:
		test_case.assertEqual(
			batch.expired_count,
			expired_count,
			f"Expected expired_count={expired_count}, got {batch.expired_count}",
		)


# ─────────────────────────────────────────────────────────────────────────────
# T017: Module exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
	"generate_batch_sync",
	"get_card_statuses",
	"fill_and_complete_allocation",
	"get_pins_from_export",
	"redeem_card_by_pin",
	"preview_card_by_pin",
	"assert_batch_counters",
]
