# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""
Memora Memory State Document class.

WARNING: This DocType uses a non-standard schema (RANGE-partitioned, BIGINT PK,
BINARY(16) item_id). The Frappe ORM MUST NOT be used for data operations.
All queries must use frappe.db.sql() with season_seq in WHERE and UUID_TO_BIN/BIN_TO_UUID
for item_id. See setup.py for full schema reference.

The Document class below blocks ORM save/insert/delete operations to prevent
accidental misuse. Use the raw SQL helpers in fsrs_processor.py instead:
  _lookup_memory_state(), _update_memory_state(), _insert_memory_state()
"""

import frappe
from frappe.model.document import Document


class MemoraMemoryState(Document):
	def before_save(self):
		"""Block Frappe ORM save operations on this partitioned table.

		Memora Memory State has a non-standard schema (BIGINT PK, BINARY item_id,
		composite PK for partitioning) that is incompatible with Frappe ORM.
		All writes must use raw SQL via frappe.db.sql().
		"""
		frappe.throw(
			"Memora Memory State is a RANGE-partitioned table with non-standard schema. "
			"Frappe ORM save/insert is forbidden. Use raw SQL via frappe.db.sql() instead. "
			"See fsrs_processor.py for helper functions.",
			title="Partitioned Table Protection",
		)

	def before_insert(self):
		"""Block Frappe ORM insert operations on this partitioned table."""
		frappe.throw(
			"Memora Memory State is a RANGE-partitioned table with non-standard schema. "
			"Frappe ORM insert is forbidden. Use _insert_memory_state() from fsrs_processor.py.",
			title="Partitioned Table Protection",
		)

	def on_trash(self):
		"""Block Frappe ORM delete operations on this partitioned table.

		frappe.delete_doc() generates DELETE without season_seq in WHERE,
		which would scan all partitions on a 10B-row table.
		Use raw SQL with season_seq in WHERE for any deletion.
		"""
		frappe.throw(
			"Memora Memory State is a RANGE-partitioned table with non-standard schema. "
			"Frappe ORM delete is forbidden. DELETE queries must include season_seq in WHERE "
			"for partition pruning. Use frappe.db.sql() with explicit season_seq.",
			title="Partitioned Table Protection",
		)
