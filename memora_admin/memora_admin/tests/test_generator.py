"""Unit tests for voucher generator service functions.

Tests PIN generation, HMAC computation, serial number reservation,
and CSV export building from memora_admin.services.voucher.generator.
"""

import csv
import io
import re
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from memora_admin.memora_admin.services.voucher.generator import (
	PIN_ALPHABET,
	build_export_csv,
	compute_hmac,
	generate_pin,
	reserve_serial_block,
)


class TestGeneratePin(unittest.TestCase):
	"""Test PIN generation correctness, format, and uniqueness."""

	def test_default_pin_length_is_12(self):
		"""FR-001: Default PIN length is 12 characters."""
		pin = generate_pin()
		self.assertEqual(len(pin), 12, "Default PIN should be 12 characters")

	def test_custom_pin_length(self):
		"""FR-002: Custom PIN lengths (14, 16) are honored."""
		pin_14 = generate_pin(14)
		self.assertEqual(len(pin_14), 14, "PIN with length=14 should return 14 characters")

		pin_16 = generate_pin(16)
		self.assertEqual(len(pin_16), 16, "PIN with length=16 should return 16 characters")

	def test_pin_contains_only_safe_characters(self):
		"""FR-003: PIN contains only safe characters (no 0, O, 1, I, L)."""
		pin = generate_pin()

		# All characters must be in PIN_ALPHABET
		for char in pin:
			self.assertIn(
				char,
				PIN_ALPHABET,
				f"PIN character '{char}' not in PIN_ALPHABET: {PIN_ALPHABET}",
			)

		# Ambiguous characters must not appear
		ambiguous = {"0", "O", "1", "I", "L"}
		for char in pin:
			self.assertNotIn(char, ambiguous, f"PIN contains ambiguous character: {char}")

	def test_1000_pins_are_unique(self):
		"""FR-004: 1000 generated PINs are all unique."""
		pins = [generate_pin() for _ in range(1000)]
		unique_pins = set(pins)
		self.assertEqual(len(unique_pins), 1000, "All 1000 generated PINs should be unique")

	def test_minimum_length_pin(self):
		"""FR-002/EC-1: Minimum length PIN (1 character) works."""
		pin = generate_pin(1)
		self.assertEqual(len(pin), 1, "PIN with length=1 should return 1 character")
		self.assertIn(pin, PIN_ALPHABET, "Single-character PIN must be in PIN_ALPHABET")


class TestComputeHmac(unittest.TestCase):
	pass


class TestBuildExportCsv(unittest.TestCase):
	pass


class TestReserveSerialBlock(FrappeTestCase):
	pass
