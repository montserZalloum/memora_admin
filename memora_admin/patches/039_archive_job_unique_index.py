"""Create composite unique index on Memora Archive Job.

Frappe JSON schema only supports single-field unique constraints.
This patch adds a composite unique index on (source_doctype, archive_scope, schema_version)
to prevent duplicate archive jobs for the same source/scope/version combination.
"""

import frappe


def execute():
	# Check if index already exists
	existing = frappe.db.sql(
		"""
		SELECT 1 FROM information_schema.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Archive Job'
		AND INDEX_NAME = 'idx_archive_job_unique'
		LIMIT 1
		"""
	)
	if existing:
		return

	frappe.db.sql(
		"""
		CREATE UNIQUE INDEX `idx_archive_job_unique`
		ON `tabMemora Archive Job` (`source_doctype`(100), `archive_scope`(100), `schema_version`(50))
		"""
	)
