# Copyright (c) 2026, corex and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today, add_days, random_string


class TestMemoraSeason(FrappeTestCase):
	"""Tests for Memora Season auto-increment season_seq behaviour."""

	def _make_season(self, **kwargs):
		"""Helper: create and insert a season, track for cleanup."""
		defaults = {
			"doctype": "Memora Season",
			"season_title": f"Test Season {random_string(8)}",
			"start_date": today(),
			"end_date": add_days(today(), 90),
			"is_published": 0,
		}
		defaults.update(kwargs)
		doc = frappe.get_doc(defaults)
		doc.insert(ignore_permissions=True)
		return doc

	# ── Auto-increment ────────────────────────────────────────────────

	def test_season_seq_auto_assigned_when_blank(self):
		"""season_seq must be auto-assigned by before_insert when left blank."""
		doc = self._make_season()  # no season_seq passed
		self.assertIsNotNone(doc.season_seq)
		self.assertGreater(doc.season_seq, 0)

	def test_season_seq_increments_sequentially(self):
		"""Two consecutive seasons with blank seq must get sequential values."""
		doc1 = self._make_season()
		doc2 = self._make_season()
		self.assertEqual(doc2.season_seq, doc1.season_seq + 1)

	def test_season_seq_explicit_value_respected(self):
		"""Programmatic creation with explicit season_seq must keep that value."""
		# Use a very high number unlikely to collide
		explicit_seq = 99990 + int(random_string(3), 36) % 9
		doc = self._make_season(season_seq=explicit_seq)
		self.assertEqual(doc.season_seq, explicit_seq)

	# ── Uniqueness ────────────────────────────────────────────────────

	def test_season_seq_unique_constraint(self):
		"""Duplicate season_seq must raise a unique-constraint error."""
		doc1 = self._make_season()
		with self.assertRaises(frappe.UniqueValidationError):
			self._make_season(season_seq=doc1.season_seq)

	# ── Immutability ──────────────────────────────────────────────────

	def test_season_seq_cannot_change_after_creation(self):
		"""Changing season_seq on an existing record must throw."""
		doc = self._make_season()
		doc.reload()
		doc.season_seq = doc.season_seq + 100
		with self.assertRaises(frappe.exceptions.ValidationError):
			doc.save()

	# ── DocType field properties ──────────────────────────────────────

	def test_season_seq_field_is_read_only(self):
		"""season_seq must be marked read_only in the DocType schema."""
		meta = frappe.get_meta("Memora Season")
		field = meta.get_field("season_seq")
		self.assertEqual(field.read_only, 1)

	def test_season_seq_field_is_not_mandatory(self):
		"""season_seq must NOT be mandatory (server auto-fills it)."""
		meta = frappe.get_meta("Memora Season")
		field = meta.get_field("season_seq")
		self.assertFalse(field.reqd)
