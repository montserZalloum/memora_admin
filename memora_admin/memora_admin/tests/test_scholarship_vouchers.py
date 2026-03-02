"""Tests for the Scholarship & Gift Voucher system (feature 034).

Covers:
1. Validation — face_value=0 enforced for non-Sale, batch_purpose immutable after Draft
2. Direct Activate — cards → Allocated with library='Admin-Direct', batch → Active, idempotent
3. Cross-purpose guards — fill_cards/submit_allocation reject non-Sale, direct_activate rejects Sale
4. Report — correct counts, filters, multi-grant correctness
"""

import frappe

from memora_admin.memora_admin.api.allocation import fill_cards, submit_allocation
from memora_admin.memora_admin.api.voucher import direct_activate, void_card
from memora_admin.memora_admin.report.scholarship_gift_grants.scholarship_gift_grants import (
	execute as report_execute,
)
from memora_admin.memora_admin.tests.voucher_fixtures import (
	make_allocation,
	make_batch,
	make_customer,
	make_player,
	make_product_grant,
)
from memora_admin.memora_admin.tests.voucher_helpers import (
	assert_batch_counters,
	generate_batch_sync,
	get_card_statuses,
	get_pins_from_export,
	redeem_card_by_pin,
)
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase


class TestBatchPurposeValidation(VoucherTestCase):
	"""Validation rules for batch_purpose field."""

	def test_non_sale_batch_must_have_zero_face_value(self):
		"""Non-sale batches with face_value > 0 should be rejected."""
		grant = make_product_grant(season="SEAS-00027")
		with self.assertRaises(frappe.ValidationError):
			batch = frappe.get_doc(
				{
					"doctype": "Memora Voucher Batch",
					"batch_name": "Face Value Validation Test",
					"quantity": 5,
					"pin_length": 12,
					"face_value": 10,
					"batch_purpose": "Scholarship",
					"batch_grants": [{"product_grant": grant.name}],
				}
			)
			batch.insert(ignore_permissions=True)

	def test_scholarship_batch_with_zero_face_value_succeeds(self):
		"""Scholarship batch with face_value=0 should be accepted."""
		grant = make_product_grant(season="SEAS-00027")
		batch = make_batch(grants=[grant.name], face_value=0)
		frappe.db.set_value("Memora Voucher Batch", batch.name, "batch_purpose", "Scholarship")
		batch.reload()
		# Re-save to trigger validation
		batch.face_value = 0
		batch.save(ignore_permissions=True)
		self.assertEqual(batch.batch_purpose, "Scholarship")

	def test_batch_purpose_immutable_after_draft(self):
		"""batch_purpose cannot be changed once batch leaves Draft status."""
		grant = make_product_grant(season="SEAS-00027")
		batch = frappe.get_doc(
			{
				"doctype": "Memora Voucher Batch",
				"batch_name": "Immutable Test",
				"quantity": 5,
				"pin_length": 12,
				"face_value": 0,
				"batch_purpose": "Scholarship",
				"batch_grants": [{"product_grant": grant.name}],
			}
		)
		batch.insert(ignore_permissions=True)

		# Generate cards to move batch to Generated status
		generate_batch_sync(batch.name)
		batch.reload()
		self.assertEqual(batch.status, "Generated")

		# Attempt to change batch_purpose after Draft
		batch.batch_purpose = "Gift"
		with self.assertRaises(frappe.ValidationError):
			batch.save(ignore_permissions=True)

	def test_sale_batch_with_face_value_succeeds(self):
		"""Sale batch with face_value > 0 should be accepted (default behavior)."""
		grant = make_product_grant(season="SEAS-00027")
		batch = make_batch(grants=[grant.name], face_value=50)
		self.assertEqual(batch.batch_purpose, "Sale")
		self.assertEqual(batch.face_value, 50)


class TestDirectActivate(VoucherTestCase):
	"""Direct Activate flow for non-Sale batches."""

	def _make_scholarship_batch(self, quantity=5):
		"""Helper to create and generate a Scholarship batch."""
		grant = make_product_grant(season="SEAS-00027")
		batch = frappe.get_doc(
			{
				"doctype": "Memora Voucher Batch",
				"batch_name": "Scholarship DA Test",
				"quantity": quantity,
				"pin_length": 12,
				"face_value": 0,
				"batch_purpose": "Scholarship",
				"batch_grants": [{"product_grant": grant.name}],
			}
		)
		batch.insert(ignore_permissions=True)
		generate_batch_sync(batch.name)
		batch.reload()
		return batch, grant

	def test_direct_activate_transitions_cards(self):
		"""Direct Activate should set all cards to Allocated with library='Admin-Direct'."""
		batch, _ = self._make_scholarship_batch()

		result = direct_activate(batch.name)

		self.assertEqual(result["status"], "activated")
		self.assertEqual(result["activated_count"], 5)

		statuses = get_card_statuses(batch.name)
		self.assertEqual(statuses.get("Allocated", 0), 5)
		self.assertEqual(statuses.get("Available", 0), 0)

		# Verify library sentinel
		cards = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": batch.name},
			fields=["library"],
		)
		for card in cards:
			self.assertEqual(card.library, "Admin-Direct")

	def test_direct_activate_transitions_batch_to_active(self):
		"""Direct Activate should transition batch from Generated to Active."""
		batch, _ = self._make_scholarship_batch()

		direct_activate(batch.name)

		batch.reload()
		self.assertEqual(batch.status, "Active")
		assert_batch_counters(self, batch.name, allocated_count=5)

	def test_direct_activate_idempotent(self):
		"""Second call to Direct Activate returns activated_count=0 (spec: idempotent)."""
		batch, _ = self._make_scholarship_batch()

		# First call activates all 5 cards
		result1 = direct_activate(batch.name)
		self.assertEqual(result1["activated_count"], 5)

		# Second call succeeds with 0 changed (all already Allocated)
		result2 = direct_activate(batch.name)
		self.assertEqual(result2["status"], "activated")
		self.assertEqual(result2["activated_count"], 0)

	def test_direct_activate_rejects_sale_batch(self):
		"""Direct Activate should reject Sale batches."""
		grant = make_product_grant(season="SEAS-00027")
		batch = make_batch(grants=[grant.name], face_value=5)
		generate_batch_sync(batch.name)

		with self.assertRaises(frappe.ValidationError):
			direct_activate(batch.name)

	def test_direct_activate_rejects_draft_batch(self):
		"""Direct Activate should reject batches in Draft status."""
		grant = make_product_grant(season="SEAS-00027")
		batch = frappe.get_doc(
			{
				"doctype": "Memora Voucher Batch",
				"batch_name": "Draft DA Test",
				"quantity": 5,
				"pin_length": 12,
				"face_value": 0,
				"batch_purpose": "Scholarship",
				"batch_grants": [{"product_grant": grant.name}],
			}
		)
		batch.insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			direct_activate(batch.name)

	def test_direct_activate_rejects_closed_batch(self):
		"""Direct Activate should reject Closed batches."""
		batch, _ = self._make_scholarship_batch()
		direct_activate(batch.name)

		# Void the batch to close it
		from memora_admin.memora_admin.api.voucher import void_batch

		void_batch(batch.name, "test closure")
		batch.reload()
		self.assertEqual(batch.status, "Closed")

		with self.assertRaises(frappe.ValidationError):
			direct_activate(batch.name)

	def test_batch_purpose_propagated_to_cards(self):
		"""Cards should inherit batch_purpose from the batch during generation."""
		batch, _ = self._make_scholarship_batch()

		cards = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": batch.name},
			fields=["batch_purpose"],
		)
		for card in cards:
			self.assertEqual(card.batch_purpose, "Scholarship")


class TestCrossPurposeGuards(VoucherTestCase):
	"""Guards preventing cross-purpose misuse."""

	def test_fill_cards_rejects_non_sale_batch(self):
		"""fill_cards() should reject allocation from non-Sale batches."""
		grant = make_product_grant(season="SEAS-00027")
		batch = frappe.get_doc(
			{
				"doctype": "Memora Voucher Batch",
				"batch_name": "Guard Test Fill",
				"quantity": 5,
				"pin_length": 12,
				"face_value": 0,
				"batch_purpose": "Scholarship",
				"batch_grants": [{"product_grant": grant.name}],
			}
		)
		batch.insert(ignore_permissions=True)
		generate_batch_sync(batch.name)

		library = make_customer()
		alloc = make_allocation(batch=batch.name, customer=library.name)

		with self.assertRaises(frappe.ValidationError):
			fill_cards(alloc.name)

	def test_submit_allocation_rejects_non_sale_batch(self):
		"""submit_allocation() should reject allocation from non-Sale batches."""
		grant = make_product_grant(season="SEAS-00027")
		batch = frappe.get_doc(
			{
				"doctype": "Memora Voucher Batch",
				"batch_name": "Guard Test Submit",
				"quantity": 5,
				"pin_length": 12,
				"face_value": 0,
				"batch_purpose": "Scholarship",
				"batch_grants": [{"product_grant": grant.name}],
			}
		)
		batch.insert(ignore_permissions=True)
		generate_batch_sync(batch.name)

		library = make_customer()
		alloc = make_allocation(batch=batch.name, customer=library.name)

		# Manually add a card to bypass fill_cards guard
		card = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": batch.name, "status": "Available"},
			pluck="name",
			limit=1,
		)[0]
		alloc.append("allocation_cards", {"voucher_card": card})
		alloc.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			submit_allocation(alloc.name)

	def test_sale_batch_allocation_succeeds(self):
		"""fill_cards() should succeed for Sale batches (existing flow unchanged)."""
		grant = make_product_grant(season="SEAS-00027")
		batch = make_batch(grants=[grant.name], face_value=5, quantity=5)
		generate_batch_sync(batch.name)

		library = make_customer()
		alloc = make_allocation(batch=batch.name, customer=library.name)

		result = fill_cards(alloc.name)
		self.assertEqual(result["filled_count"], 5)


class TestScholarshipRedemptionFlow(VoucherTestCase):
	"""End-to-end: create Scholarship batch → generate → export → activate → redeem."""

	def test_full_scholarship_lifecycle(self):
		"""Full lifecycle: Scholarship batch → generate → export → activate → redeem."""
		# Grant needs grant_components so get_grant_keys() returns non-empty list
		subject = frappe.get_all("Memora Subject", limit=1, pluck="name")[0]
		grant = make_product_grant(
			season="SEAS-00027",
			grant_components=[{"target_doctype": "Memora Subject", "target_name": subject}],
		)
		player = make_player(season="SEAS-00027")

		batch = frappe.get_doc(
			{
				"doctype": "Memora Voucher Batch",
				"batch_name": "E2E Scholarship Test",
				"quantity": 3,
				"pin_length": 12,
				"face_value": 0,
				"batch_purpose": "Scholarship",
				"batch_grants": [{"product_grant": grant.name}],
			}
		)
		batch.insert(ignore_permissions=True)

		# Generate
		generate_batch_sync(batch.name)
		batch.reload()
		self.assertEqual(batch.status, "Generated")

		# Export PINs (must happen BEFORE Direct Activate)
		pins = get_pins_from_export(batch.name)
		self.assertEqual(len(pins), 3)

		# Direct Activate
		result = direct_activate(batch.name)
		self.assertEqual(result["activated_count"], 3)

		batch.reload()
		self.assertEqual(batch.status, "Active")

		# Redeem one card
		first_serial = next(iter(pins.keys()))
		pin = pins[first_serial]

		frappe.set_user("Administrator")
		redeem_result = redeem_card_by_pin(pin, player.name, grant.name)
		self.assertEqual(redeem_result["status"], "success")

		# Verify card status changed to Redeemed
		card = frappe.get_doc("Memora Voucher Card", first_serial)
		self.assertEqual(card.status, "Redeemed")
		self.assertEqual(card.library, "Admin-Direct")

		# Verify subscription transaction was created
		self.assertTrue(
			frappe.db.exists(
				"Memora Subscription Transaction",
				{"player": player.name, "payment_method": "Voucher"},
			)
		)


class TestScholarshipGiftGrantsReport(VoucherTestCase):
	"""Tests for the Scholarship & Gift Grants Script Report."""

	def _make_activated_batch(self, purpose="Scholarship", quantity=5, grants=None):
		"""Helper to create, generate, and activate a non-Sale batch.

		Args:
			purpose: Batch purpose (default Scholarship).
			quantity: Number of cards (default 5).
			grants: List of Product Grant names. If None, creates one.

		Returns:
			Tuple of (batch, list_of_grant_names).
		"""
		if grants is None:
			grant = make_product_grant(season="SEAS-00027")
			grants = [grant.name]
		batch = frappe.get_doc(
			{
				"doctype": "Memora Voucher Batch",
				"batch_name": f"Report Test {purpose}",
				"quantity": quantity,
				"pin_length": 12,
				"face_value": 0,
				"batch_purpose": purpose,
				"batch_grants": [{"product_grant": g} for g in grants],
			}
		)
		batch.insert(ignore_permissions=True)
		generate_batch_sync(batch.name)
		direct_activate(batch.name)
		batch.reload()
		return batch, grants

	def test_report_excludes_sale_batches(self):
		"""Report should only show non-Sale batches."""
		batch, _ = self._make_activated_batch()

		# Also create a Sale batch
		grant2 = make_product_grant(season="SEAS-00027")
		sale_batch = make_batch(grants=[grant2.name], face_value=5)
		generate_batch_sync(sale_batch.name)

		columns, data, *_ = report_execute(filters=None)

		batch_names = [row.get("batch") for row in data]
		self.assertIn(batch.name, batch_names)
		self.assertNotIn(sale_batch.name, batch_names)

	def test_report_remaining_is_usable_inventory(self):
		"""remaining = total - redeemed - voided (not just Available cards).

		For directly-activated batches, all cards are Allocated, so remaining
		should equal total_cards (not 0).
		"""
		batch, _ = self._make_activated_batch(quantity=5)

		_, data, *_ = report_execute(filters=None)
		row = next((r for r in data if r.get("batch") == batch.name), None)
		self.assertIsNotNone(row)
		self.assertEqual(row["total_cards"], 5)
		self.assertEqual(row["activated"], 5)
		self.assertEqual(row["redeemed"], 0)
		self.assertEqual(row["voided"], 0)
		# remaining = 5 - 0 - 0 = 5 (all usable)
		self.assertEqual(row["remaining"], 5)

	def test_report_remaining_after_redeem_and_void(self):
		"""remaining correctly subtracts redeemed and voided cards."""
		batch, _ = self._make_activated_batch(quantity=5)

		# Void one card
		cards = frappe.get_all(
			"Memora Voucher Card",
			filters={"batch": batch.name, "status": "Allocated"},
			pluck="name",
		)
		void_card(cards[0], "test void")

		_, data, *_ = report_execute(filters=None)
		row = next((r for r in data if r.get("batch") == batch.name), None)
		self.assertIsNotNone(row)
		# 5 total, 0 redeemed, 1 voided → remaining = 4
		self.assertEqual(row["total_cards"], 5)
		self.assertEqual(row["voided"], 1)
		self.assertEqual(row["remaining"], 4)

	def test_report_multi_grant_no_overcounting(self):
		"""Card counts should not inflate when a batch has multiple grants."""
		grant1 = make_product_grant(season="SEAS-00027")
		grant2 = make_product_grant(season="SEAS-00027")

		batch, _ = self._make_activated_batch(quantity=5, grants=[grant1.name, grant2.name])

		_, data, *_ = report_execute(filters=None)
		row = next((r for r in data if r.get("batch") == batch.name), None)
		self.assertIsNotNone(row)
		# Should be exactly 5, not 10 (which would happen with a cartesian product)
		self.assertEqual(row["total_cards"], 5)
		self.assertEqual(row["activated"], 5)
		# Both grants should appear in the product_grant column
		self.assertIn(grant1.name, row["product_grant"])
		self.assertIn(grant2.name, row["product_grant"])

	def test_report_filter_by_purpose(self):
		"""Report should filter by batch_purpose."""
		scholarship_batch, _ = self._make_activated_batch("Scholarship")
		gift_batch, _ = self._make_activated_batch("Gift")

		# Filter Scholarship only
		_, data, *_ = report_execute(filters={"batch_purpose": "Scholarship"})
		batch_names = [row.get("batch") for row in data]
		self.assertIn(scholarship_batch.name, batch_names)
		self.assertNotIn(gift_batch.name, batch_names)

	def test_report_summary(self):
		"""Report summary should include total cards and redemption rate."""
		self._make_activated_batch(quantity=10)

		_, data, _, _, summary = report_execute(filters=None)

		self.assertTrue(len(summary) >= 3)
		total_label = next((s for s in summary if s.get("label") == "Total Cards"), None)
		self.assertIsNotNone(total_label)
		self.assertGreaterEqual(total_label.get("value"), 10)
