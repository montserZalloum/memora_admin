"""Base test case for voucher system tests with prerequisite checks."""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase


class VoucherTestCase(FrappeTestCase):
	"""Base class for voucher tests with prerequisite validation.

	Prerequisites checked in setUpClass():
	1. voucher_hmac_secret must be configured in site config
	2. MEMORA-VOUCHER-CARD Item must exist in database

	Tests are skipped with descriptive messages if prerequisites are missing.
	"""

	@classmethod
	def setUpClass(cls) -> None:
		"""Check prerequisites before running voucher tests."""
		super().setUpClass()

		# Check 1: HMAC secret configured
		if not frappe.conf.get("voucher_hmac_secret"):
			raise unittest.SkipTest(
				"voucher_hmac_secret not configured in site config. Run: bench --site <site> set-config voucher_hmac_secret <secret>"
			)

		# Check 2: MEMORA-VOUCHER-CARD Item exists
		if not frappe.db.exists("Item", "MEMORA-VOUCHER-CARD"):
			raise unittest.SkipTest(
				"MEMORA-VOUCHER-CARD Item not found. Create it in the test site before running voucher tests."
			)
