# Copyright (c) 2026, corex and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, random_string, today

TEST_PREFIX = "ZTest Season "


def _cleanup_test_seasons():
	"""Delete all test seasons by prefix. Uses raw SQL to bypass ORM side effects."""
	names = frappe.db.sql_list(
		"SELECT name FROM `tabMemora Season` WHERE season_title LIKE %s",
		f"{TEST_PREFIX}%",
	)
	for name in names:
		frappe.delete_doc("Memora Season", name, force=True, ignore_permissions=True)
	if names:
		frappe.db.commit()


class TestMemoraSeason(FrappeTestCase):
	"""Tests for Memora Season auto-increment season_seq behaviour."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup_test_seasons()

	@classmethod
	def tearDownClass(cls):
		_cleanup_test_seasons()
		super().tearDownClass()

	def _make_season(self, **kwargs):
		"""Create a season with partition creation mocked out (DDL can't rollback)."""
		defaults = {
			"doctype": "Memora Season",
			"season_title": f"{TEST_PREFIX}{random_string(8)}",
			"start_date": today(),
			"end_date": add_days(today(), 90),
			"is_published": 0,
		}
		defaults.update(kwargs)
		doc = frappe.get_doc(defaults)
		with patch.object(type(doc), "_ensure_memory_state_partition"):
			doc.insert(ignore_permissions=True)
		return doc

	# ── Auto-increment ────────────────────────────────────────────────

	def test_season_seq_auto_assigned_when_blank(self):
		"""season_seq must be auto-assigned by before_insert when left blank."""
		doc = self._make_season()
		self.assertIsNotNone(doc.season_seq)
		self.assertGreater(doc.season_seq, 0)

	def test_season_seq_increments_sequentially(self):
		"""Two consecutive seasons with blank seq must get sequential values."""
		doc1 = self._make_season()
		doc2 = self._make_season()
		self.assertEqual(doc2.season_seq, doc1.season_seq + 1)

	def test_season_seq_explicit_value_respected(self):
		"""Programmatic creation with explicit season_seq must keep that value."""
		from memora_admin.memora_admin.doctype.memora_season.memora_season import _get_next_season_seq

		next_seq = _get_next_season_seq()
		explicit_seq = next_seq + 2
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
