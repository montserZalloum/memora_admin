# Copyright (c) 2026, corex and Contributors
# See license.txt

from typing import ClassVar

import frappe
from frappe.tests.utils import FrappeTestCase

from memora_admin.memora_admin.api.products import get_grant_keys


def _make_grant(components: list[dict]) -> str:
	"""Create a Memora Product Grant with the given components and return its name."""
	# Use the first available plan
	plan = frappe.get_all("Memora Academic Plan", limit=1, pluck="name")
	if not plan:
		frappe.throw("No Memora Academic Plan found for testing")

	item = frappe.get_all("Item", limit=1, pluck="name")
	if not item:
		frappe.throw("No Item found for testing")

	doc = frappe.get_doc(
		{
			"doctype": "Memora Product Grant",
			"title": f"Test Grant {frappe.generate_hash(length=6)}",
			"plan": plan[0],
			"item_code": item[0],
			"grant_components": components,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


class TestMemoraProductGrant(FrappeTestCase):
	"""Tests for Memora Product Grant and get_grant_keys()."""

	_created_grants: ClassVar[list[str]] = []

	@classmethod
	def tearDownClass(cls):
		for name in cls._created_grants:
			try:
				frappe.delete_doc("Memora Product Grant", name, force=True)
			except Exception:
				pass
		cls._created_grants.clear()
		super().tearDownClass()

	def _create_grant(self, components: list[dict]) -> str:
		name = _make_grant(components)
		self._created_grants.append(name)
		return name

	def test_full_subject_emits_sub_key(self):
		"""key_type=full + Memora Subject → SUB-{name}."""
		subjects = frappe.get_all("Memora Subject", limit=1, pluck="name")
		if not subjects:
			self.skipTest("No Memora Subject found")

		name = self._create_grant(
			[
				{
					"doctype": "Memora Grant Component",
					"target_doctype": "Memora Subject",
					"target_name": subjects[0],
					"key_type": "full",
				}
			]
		)
		keys = get_grant_keys(name)
		self.assertEqual(keys, [f"SUB-{subjects[0]}"])

	def test_practice_subject_emits_prac_sub_key(self):
		"""key_type=practice + Memora Subject → PRAC-SUB-{name}."""
		subjects = frappe.get_all("Memora Subject", limit=1, pluck="name")
		if not subjects:
			self.skipTest("No Memora Subject found")

		name = self._create_grant(
			[
				{
					"doctype": "Memora Grant Component",
					"target_doctype": "Memora Subject",
					"target_name": subjects[0],
					"key_type": "practice",
				}
			]
		)
		keys = get_grant_keys(name)
		self.assertEqual(keys, [f"PRAC-SUB-{subjects[0]}"])

	def test_track_always_emits_trk_key(self):
		"""Memora Track → TRK-{name} regardless of key_type."""
		tracks = frappe.get_all("Memora Track", limit=1, pluck="name")
		if not tracks:
			self.skipTest("No Memora Track found")

		name = self._create_grant(
			[
				{
					"doctype": "Memora Grant Component",
					"target_doctype": "Memora Track",
					"target_name": tracks[0],
					"key_type": "full",
				}
			]
		)
		keys = get_grant_keys(name)
		self.assertEqual(keys, [f"TRK-{tracks[0]}"])

	def test_missing_key_type_defaults_to_full(self):
		"""No key_type on component → defaults to SUB-{name}."""
		subjects = frappe.get_all("Memora Subject", limit=1, pluck="name")
		if not subjects:
			self.skipTest("No Memora Subject found")

		name = self._create_grant(
			[
				{
					"doctype": "Memora Grant Component",
					"target_doctype": "Memora Subject",
					"target_name": subjects[0],
				}
			]
		)
		keys = get_grant_keys(name)
		self.assertEqual(keys, [f"SUB-{subjects[0]}"])

	def test_practice_track_raises_validation_error(self):
		"""key_type=practice + Memora Track → validation error."""
		tracks = frappe.get_all("Memora Track", limit=1, pluck="name")
		if not tracks:
			self.skipTest("No Memora Track found")

		with self.assertRaises(frappe.ValidationError):
			self._create_grant(
				[
					{
						"doctype": "Memora Grant Component",
						"target_doctype": "Memora Track",
						"target_name": tracks[0],
						"key_type": "practice",
					}
				]
			)

	def test_mixed_components(self):
		"""Grant with both full subject and practice subject emits correct keys."""
		subjects = frappe.get_all("Memora Subject", limit=2, pluck="name")
		if len(subjects) < 2:
			self.skipTest("Need at least 2 Memora Subjects")

		name = self._create_grant(
			[
				{
					"doctype": "Memora Grant Component",
					"target_doctype": "Memora Subject",
					"target_name": subjects[0],
					"key_type": "full",
				},
				{
					"doctype": "Memora Grant Component",
					"target_doctype": "Memora Subject",
					"target_name": subjects[1],
					"key_type": "practice",
				},
			]
		)
		keys = get_grant_keys(name)
		self.assertEqual(keys, [f"SUB-{subjects[0]}", f"PRAC-SUB-{subjects[1]}"])
