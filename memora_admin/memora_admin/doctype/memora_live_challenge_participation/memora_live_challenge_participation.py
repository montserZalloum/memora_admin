# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraLiveChallengeParticipation(Document):
	def before_insert(self):
		"""Application-level fast check (first line of defense)."""
		if frappe.db.exists(
			"Memora Live Challenge Participation",
			{"event": self.event, "player": self.player},
		):
			frappe.throw(f"Player {self.player} has already joined event {self.event}")

	pass


def on_doctype_update():
	"""Add DB-level unique index on (event, player) — true atomic enforcement.

	Called by bench migrate via run_module_method. Prevents TOCTOU race even under concurrency.
	"""
	if not frappe.db.sql(
		"""SELECT 1 FROM information_schema.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Live Challenge Participation'
		AND INDEX_NAME = 'unique_event_player'
		LIMIT 1"""
	):
		frappe.db.sql(
			"""ALTER TABLE `tabMemora Live Challenge Participation`
			ADD UNIQUE INDEX `unique_event_player` (`event`, `player`)"""
		)
