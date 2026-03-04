"""Test voucher_helpers module functionality."""

import frappe
from frappe.tests.utils import FrappeTestCase

from memora_admin.memora_admin.tests.voucher_fixtures import (
	make_batch,
	make_customer,
	make_product_grant,
	make_season,
)
from memora_admin.memora_admin.tests.voucher_helpers import (
	assert_batch_counters,
	generate_batch_sync,
	get_card_statuses,
)
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase


class TestVoucherHelpers(VoucherTestCase):
	"""Test that helpers execute correctly."""

	def test_generate_batch_sync_creates_cards(self):
		"""Test that generate_batch_sync() creates cards synchronously."""
		# Setup: Create batch
		grant = make_product_grant()
		batch = make_batch(grants=[grant.name])

		# Verify batch starts in Draft
		self.assertEqual(batch.status, "Draft")
		self.assertEqual(batch.generated_count, 0)

		# Act: Generate cards synchronously
		generate_batch_sync(batch.name)

		# Assert: Batch should now be Generated with cards
		batch.reload()
		self.assertEqual(batch.status, "Generated")
		self.assertEqual(batch.generated_count, 10)  # Default quantity

	def test_get_card_statuses_returns_counts(self):
		"""Test that get_card_statuses() returns correct counts."""
		# Setup: Create and generate a batch
		grant = make_product_grant()
		batch = make_batch(quantity=5, grants=[grant.name])
		generate_batch_sync(batch.name)

		# Act: Get status counts
		statuses = get_card_statuses(batch.name)

		# Assert: All cards should be Available
		self.assertEqual(statuses.get("Available", 0), 5)
		self.assertNotIn("Allocated", statuses)
		self.assertNotIn("Redeemed", statuses)

	def test_assert_batch_counters_passes_on_match(self):
		"""Test that assert_batch_counters() passes when counts match."""
		# Setup: Create and generate batch
		grant = make_product_grant()
		batch = make_batch(quantity=10, grants=[grant.name])
		generate_batch_sync(batch.name)

		# Act & Assert: Should not raise
		assert_batch_counters(self, batch.name, generated_count=10)

	def test_assert_batch_counters_fails_on_mismatch(self):
		"""Test that assert_batch_counters() fails when counts don't match."""
		# Setup: Create and generate batch
		grant = make_product_grant()
		batch = make_batch(quantity=10, grants=[grant.name])
		generate_batch_sync(batch.name)

		# Act & Assert: Should raise AssertionError
		with self.assertRaises(AssertionError):
			assert_batch_counters(self, batch.name, generated_count=999)

	def test_fixtures_create_unique_documents(self):
		"""Test that fixtures create unique, saved documents."""
		# Create two seasons
		season1 = make_season()
		season2 = make_season()

		# They should be different
		self.assertNotEqual(season1.name, season2.name)

		# Both should exist in DB
		self.assertTrue(
			frappe.db.exists("Memora Season", season1.name),
			"Season 1 should exist in DB",
		)
		self.assertTrue(
			frappe.db.exists("Memora Season", season2.name),
			"Season 2 should exist in DB",
		)
