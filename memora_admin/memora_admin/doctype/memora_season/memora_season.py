# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


def _get_next_season_seq() -> int:
	"""Get next safe season_seq by checking both Season and Memory State tables."""
	max_seq = frappe.db.sql("""
		SELECT GREATEST(
			COALESCE((SELECT MAX(season_seq) FROM `tabMemora Season`), 0),
			COALESCE((SELECT MAX(season_seq) FROM `tabMemora Memory State`), 0)
		)
	""")[0][0]
	return int(max_seq) + 1


@frappe.whitelist()
def get_next_season_seq():
	return _get_next_season_seq()


class MemoraSeason(Document):
	def before_insert(self):
		# Always auto-assign if blank (field is read_only in UI)
		if not self.season_seq:
			self.season_seq = _get_next_season_seq()
		frappe.msgprint(f"Season Seq auto-assigned: {self.season_seq}", indicator="green", alert=True)

	def after_insert(self):
		self._ensure_memory_state_partition()

	def validate(self):
		if not self.is_new():
			old = self.get_doc_before_save()
			if old and old.season_seq and old.season_seq != self.season_seq:
				frappe.throw("Season Seq cannot be changed after creation.")

	def _ensure_memory_state_partition(self):
		"""REORGANIZE p_future to create a dedicated partition for this season's season_seq."""
		seq = int(self.season_seq)
		partition_name = f"p_season_{seq}"

		# Check if partition already exists (idempotent)
		exists = frappe.db.sql(
			"""
			SELECT 1 FROM INFORMATION_SCHEMA.PARTITIONS
			WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = 'tabMemora Memory State'
			AND PARTITION_NAME = %s
			LIMIT 1
		""",
			(partition_name,),
		)

		if exists:
			print(f"[after_insert] Partition {partition_name} already exists, skipping")
			return

		# Check that the table is actually partitioned (p_future must exist)
		has_future = frappe.db.sql("""
			SELECT 1 FROM INFORMATION_SCHEMA.PARTITIONS
			WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = 'tabMemora Memory State'
			AND PARTITION_NAME = 'p_future'
			LIMIT 1
		""")

		if not has_future:
			print(f"[after_insert] Table not partitioned yet, skipping partition creation for seq={seq}")
			return

		frappe.db.sql_ddl(f"""
			ALTER TABLE `tabMemora Memory State`
			REORGANIZE PARTITION p_future INTO (
				PARTITION {partition_name} VALUES LESS THAN ({seq + 1}),
				PARTITION p_future VALUES LESS THAN MAXVALUE
			)
		""")
		print(f"[after_insert] Created partition {partition_name} (VALUES LESS THAN {seq + 1})")
