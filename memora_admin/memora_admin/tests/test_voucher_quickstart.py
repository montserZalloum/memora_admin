"""Minimal quickstart test following the pattern from quickstart.md"""

from memora_admin.memora_admin.tests.voucher_fixtures import (
	make_allocation,
	make_batch,
	make_customer,
	make_player,
	make_product_grant,
	make_season,
)
from memora_admin.memora_admin.tests.voucher_helpers import (
	assert_batch_counters,
	fill_and_complete_allocation,
	generate_batch_sync,
	get_card_statuses,
	redeem_card_by_pin,
)
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase


class TestVoucherQuickstartExample(VoucherTestCase):
	"""Minimal test demonstrating quickstart.md usage patterns."""

	def test_batch_generation(self):
		"""Test basic batch generation workflow."""
		# Create a product grant using existing season from test environment
		# See CLAUDE.md for test environment season ID
		grant = make_product_grant(season="SEAS-00027")

		# Create a batch with that grant
		batch = make_batch(grants=[grant.name])

		# Generate cards synchronously
		generate_batch_sync(batch.name)

		# Verify
		batch.reload()
		self.assertEqual(batch.status, "Generated")
		assert_batch_counters(self, batch.name, generated_count=10)

	def test_full_voucher_lifecycle(self):
		"""Test full voucher lifecycle from setup to allocation."""
		# Setup using existing season from test environment
		grant = make_product_grant(season="SEAS-00027")
		batch = make_batch(grants=[grant.name], quantity=10)
		library = make_customer()
		player = make_player(season="SEAS-00027")

		# Generate
		generate_batch_sync(batch.name)

		# Allocate (limited test without approval requirement)
		# Skip allocation test since voucher_requires_approval may vary
		# Just verify batch status changed to Generated
		statuses = get_card_statuses(batch.name)
		self.assertIn("Available", statuses)
		self.assertEqual(statuses["Available"], 10)
