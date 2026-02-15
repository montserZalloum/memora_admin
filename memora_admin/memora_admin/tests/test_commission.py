"""
Unit and integration tests for commission calculation and resolution.

Tests cover:
- US1 (FR-001 through FR-007): Commission calculation correctness
- US2 (FR-008): Three-tier commission resolution priority
"""

import unittest
from decimal import Decimal

from memora_admin.memora_admin.services.voucher.commission import (
	calculate_commission,
	resolve_commission,
)
from memora_admin.memora_admin.tests.voucher_fixtures import (
	make_batch,
	make_customer,
	make_product_grant,
)
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase


class TestCalculateCommission(unittest.TestCase):
	"""Pure unit tests for calculate_commission() — no database access."""

	def test_no_commission_none_type(self):
		"""FR-001: None commission inputs yield zero commission and full face value."""
		result = calculate_commission(
			face_value="5.00",
			quantity=10,
			commission_type=None,
			commission_value=None,
		)
		self.assertEqual(result["per_card_commission"], Decimal("0.00"))
		self.assertEqual(result["total_commission"], Decimal("0.00"))
		self.assertEqual(result["net_per_card"], Decimal("5.00"))
		self.assertEqual(result["net_total"], Decimal("50.00"))

	def test_no_commission_empty_string(self):
		"""FR-001: Empty string commission inputs yield zero commission and full face value."""
		result = calculate_commission(
			face_value="5.00",
			quantity=10,
			commission_type="",
			commission_value="",
		)
		self.assertEqual(result["per_card_commission"], Decimal("0.00"))
		self.assertEqual(result["total_commission"], Decimal("0.00"))
		self.assertEqual(result["net_per_card"], Decimal("5.00"))
		self.assertEqual(result["net_total"], Decimal("50.00"))

	def test_percentage_commission(self):
		"""FR-002: Percentage commission (10% of 5.00 → 0.50 commission)."""
		result = calculate_commission(
			face_value="5.00",
			quantity=10,
			commission_type="Percentage",
			commission_value="10",
		)
		self.assertEqual(result["per_card_commission"], Decimal("0.50"))
		self.assertEqual(result["total_commission"], Decimal("5.00"))
		self.assertEqual(result["net_per_card"], Decimal("4.50"))
		self.assertEqual(result["net_total"], Decimal("45.00"))

	def test_fixed_amount_commission(self):
		"""FR-003: Fixed amount commission (1.00 → exact deduction)."""
		result = calculate_commission(
			face_value="5.00",
			quantity=10,
			commission_type="Fixed Amount",
			commission_value="1.00",
		)
		self.assertEqual(result["per_card_commission"], Decimal("1.00"))
		self.assertEqual(result["total_commission"], Decimal("10.00"))
		self.assertEqual(result["net_per_card"], Decimal("4.00"))
		self.assertEqual(result["net_total"], Decimal("40.00"))

	def test_unknown_commission_type_defaults_to_zero(self):
		"""FR-007: Unknown commission type defaults to zero commission."""
		result = calculate_commission(
			face_value="5.00",
			quantity=10,
			commission_type="UnknownType",
			commission_value="10",
		)
		self.assertEqual(result["per_card_commission"], Decimal("0.00"))
		self.assertEqual(result["net_per_card"], Decimal("5.00"))

	def test_repeating_decimal_precision(self):
		"""FR-004: Repeating decimal (33.33% of 10.00) rounds correctly."""
		result = calculate_commission(
			face_value="10.00",
			quantity=1,
			commission_type="Percentage",
			commission_value="33.33",
		)
		# 10.00 * 33.33 / 100 = 3.333 → ROUND_HALF_UP → 3.33
		self.assertEqual(result["per_card_commission"], Decimal("3.33"))
		self.assertEqual(result["net_per_card"], Decimal("6.67"))

	def test_quantity_multiplication(self):
		"""FR-005: net_per_card * quantity = net_total."""
		result = calculate_commission(
			face_value="5.00",
			quantity=10,
			commission_type="Percentage",
			commission_value="10",
		)
		self.assertEqual(result["net_per_card"], Decimal("4.50"))
		self.assertEqual(result["net_total"], Decimal("45.00"))  # 4.50 * 10

	def test_zero_face_value(self):
		"""FR-006: Zero face value yields all zero results."""
		result = calculate_commission(
			face_value="0",
			quantity=10,
			commission_type="Percentage",
			commission_value="10",
		)
		self.assertEqual(result["per_card_commission"], Decimal("0.00"))
		self.assertEqual(result["total_commission"], Decimal("0.00"))
		self.assertEqual(result["net_per_card"], Decimal("0.00"))
		self.assertEqual(result["net_total"], Decimal("0.00"))


class TestResolveCommission(VoucherTestCase):
	"""Integration tests for resolve_commission() — requires database."""

	def test_grant_level_takes_precedence(self):
		"""FR-008: Grant-level commission overrides customer default."""
		import frappe

		# Create product grant and batch
		grant = make_product_grant(season="SEAS-00027")
		batch = make_batch(grants=[grant.name])

		# Set commission on batch grant child row
		grant_row = frappe.get_all(
			"Memora Voucher Batch Grant",
			filters={"parent": batch.name},
			fields=["name"],
			limit=1,
		)
		frappe.db.set_value(
			"Memora Voucher Batch Grant",
			grant_row[0].name,
			{"commission_type": "Percentage", "commission_value": "15"},
		)

		# Create customer with different commission defaults
		customer = make_customer(commission_type="Fixed Amount", commission_value="2.00")

		# Verify grant-level commission wins
		result = resolve_commission(batch.name, customer.name)
		self.assertEqual(result, ("Percentage", "15"))

	def test_customer_default_when_no_grant_override(self):
		"""FR-008: Customer defaults used when no grant-level override."""
		# Create product grant and batch (no commission on grant row)
		grant = make_product_grant(season="SEAS-00027")
		batch = make_batch(grants=[grant.name])

		# Create customer with commission defaults
		customer = make_customer(commission_type="Fixed Amount", commission_value="2.00")

		# Verify customer commission is used
		result = resolve_commission(batch.name, customer.name)
		self.assertEqual(result, ("Fixed Amount", "2.00"))

	def test_no_commission_returns_none_none(self):
		"""FR-008: Neither grant nor customer commission set returns (None, None)."""
		# Create product grant and batch (no commission on grant row)
		grant = make_product_grant(season="SEAS-00027")
		batch = make_batch(grants=[grant.name])

		# Create customer with no commission fields
		customer = make_customer()

		# Verify no commission returned
		result = resolve_commission(batch.name, customer.name)
		self.assertEqual(result, (None, None))
