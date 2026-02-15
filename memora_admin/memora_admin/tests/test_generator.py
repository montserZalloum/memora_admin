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
	"""Test HMAC computation correctness, determinism, and format."""

	def test_hmac_is_deterministic(self):
		"""FR-005: HMAC computation is deterministic."""
		pin = "ABCDEF123456"
		secret = "test-secret"
		hmac1 = compute_hmac(pin, secret)
		hmac2 = compute_hmac(pin, secret)
		self.assertEqual(hmac1, hmac2, "Same PIN and secret should produce identical HMACs")

	def test_different_pins_produce_different_hmacs(self):
		"""FR-006: Different PINs produce different HMACs."""
		pin1 = "ABCDEF123456"
		pin2 = "ZYXWVU987654"
		secret = "test-secret"
		hmac1 = compute_hmac(pin1, secret)
		hmac2 = compute_hmac(pin2, secret)
		self.assertNotEqual(hmac1, hmac2, "Different PINs should produce different HMACs")

	def test_different_secrets_produce_different_hmacs(self):
		"""FR-007: Different secrets produce different HMACs."""
		pin = "ABCDEF123456"
		secret1 = "secret-a"
		secret2 = "secret-b"
		hmac1 = compute_hmac(pin, secret1)
		hmac2 = compute_hmac(pin, secret2)
		self.assertNotEqual(hmac1, hmac2, "Different secrets should produce different HMACs")

	def test_hmac_output_format(self):
		"""FR-008: HMAC output is 64-character hex string (SHA-256)."""
		pin = "ABCDEF123456"
		secret = "test-secret"
		hmac_result = compute_hmac(pin, secret)
		self.assertEqual(len(hmac_result), 64, "HMAC should be 64 characters (SHA-256 hex)")
		# Verify it matches hex pattern [0-9a-f]{64}
		self.assertIsNotNone(
			re.match(r"^[0-9a-f]{64}$", hmac_result),
			f"HMAC should match hex pattern, got: {hmac_result}",
		)

	def test_hmac_with_empty_secret(self):
		"""FR-005/EC-3: HMAC works with empty secret."""
		pin = "ABCDEF123456"
		secret = ""
		hmac_result = compute_hmac(pin, secret)
		self.assertEqual(len(hmac_result), 64, "HMAC with empty secret should still be 64 characters")
		self.assertIsNotNone(
			re.match(r"^[0-9a-f]{64}$", hmac_result),
			f"HMAC with empty secret should be valid hex",
		)


class TestBuildExportCsv(unittest.TestCase):
	"""Test CSV export construction and format."""

	def test_csv_header_row(self):
		"""FR-013: CSV header row is [serial_no, pin, product_names, face_value]."""
		cards = [
			{"serial_no": "VCH-000001", "pin": "ABCDEF123456"},
			{"serial_no": "VCH-000002", "pin": "GHJKMN234567"},
		]
		csv_bytes = build_export_csv(cards, "Test Product", "10.00")
		csv_text = csv_bytes.decode("utf-8")
		reader = csv.reader(io.StringIO(csv_text))
		rows = list(reader)
		header = rows[0]
		self.assertEqual(
			header,
			["serial_no", "pin", "product_names", "face_value"],
			"CSV header should match expected columns",
		)

	def test_csv_row_count(self):
		"""FR-014: CSV has 1 header row + N data rows (2 cards = 3 rows total)."""
		cards = [
			{"serial_no": "VCH-000001", "pin": "ABCDEF123456"},
			{"serial_no": "VCH-000002", "pin": "GHJKMN234567"},
		]
		csv_bytes = build_export_csv(cards, "Test Product", "10.00")
		csv_text = csv_bytes.decode("utf-8")
		reader = csv.reader(io.StringIO(csv_text))
		rows = list(reader)
		self.assertEqual(len(rows), 3, "CSV should have 1 header + 2 data rows = 3 total")

	def test_csv_content_matches_input(self):
		"""FR-015: CSV data rows contain correct card data."""
		cards = [
			{"serial_no": "VCH-000001", "pin": "ABCDEF123456"},
			{"serial_no": "VCH-000002", "pin": "GHJKMN234567"},
		]
		product_names = "Test Product"
		face_value = "10.00"
		csv_bytes = build_export_csv(cards, product_names, face_value)
		csv_text = csv_bytes.decode("utf-8")
		reader = csv.reader(io.StringIO(csv_text))
		rows = list(reader)

		# Check first data row
		self.assertEqual(rows[1][0], "VCH-000001", "First card serial should match")
		self.assertEqual(rows[1][1], "ABCDEF123456", "First card PIN should match")
		self.assertEqual(rows[1][2], "Test Product", "Product name should match")
		self.assertEqual(rows[1][3], "10.00", "Face value should match")

		# Check second data row
		self.assertEqual(rows[2][0], "VCH-000002", "Second card serial should match")
		self.assertEqual(rows[2][1], "GHJKMN234567", "Second card PIN should match")

	def test_empty_cards_produces_header_only(self):
		"""FR-014/EC-4: Empty cards list produces header row only."""
		cards = []
		csv_bytes = build_export_csv(cards, "Test Product", "10.00")
		csv_text = csv_bytes.decode("utf-8")
		reader = csv.reader(io.StringIO(csv_text))
		rows = list(reader)
		self.assertEqual(len(rows), 1, "Empty cards should produce only header row")


class TestReserveSerialBlock(FrappeTestCase):
	"""Test serial number reservation correctness and atomicity."""

	def setUp(self):
		"""Clean up series state before each test."""
		frappe.db.sql("DELETE FROM tabSeries WHERE name = 'VCH-SERIAL'")

	def test_first_block_starts_at_one(self):
		"""FR-009: First serial block starts at VCH-000001."""
		serials = reserve_serial_block(3)
		self.assertEqual(len(serials), 3, "Should reserve 3 serials")
		self.assertEqual(serials[0], "VCH-000001", "First serial should be VCH-000001")
		self.assertEqual(serials[1], "VCH-000002", "Second serial should be VCH-000002")
		self.assertEqual(serials[2], "VCH-000003", "Third serial should be VCH-000003")

	def test_consecutive_blocks_are_contiguous(self):
		"""FR-010: Consecutive serial blocks are contiguous (no gaps)."""
		block1 = reserve_serial_block(3)
		block2 = reserve_serial_block(2)

		# First block ends at VCH-000003
		self.assertEqual(block1[-1], "VCH-000003")

		# Second block should start immediately after at VCH-000004
		self.assertEqual(block2[0], "VCH-000004", "Second block should start at VCH-000004")
		self.assertEqual(block2[1], "VCH-000005", "Second block should end at VCH-000005")

	def test_serial_format(self):
		"""FR-011: Serial numbers match format VCH-XXXXXX (6 digits, zero-padded)."""
		serials = reserve_serial_block(5)
		for serial in serials:
			self.assertIsNotNone(
				re.match(r"^VCH-\d{6}$", serial),
				f"Serial '{serial}' does not match format VCH-XXXXXX",
			)

	def test_exact_count_returned(self):
		"""FR-012: reserve_serial_block returns exactly the requested count."""
		serials = reserve_serial_block(5)
		self.assertEqual(len(serials), 5, "Should return exactly 5 serials")

	def test_zero_count_returns_empty_list(self):
		"""FR-012/EC-2: Requesting 0 serials returns empty list."""
		serials = reserve_serial_block(0)
		self.assertEqual(serials, [], "Requesting 0 serials should return empty list")
