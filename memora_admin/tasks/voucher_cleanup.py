"""Daily cleanup task for expired encrypted voucher export files.

Deletes encrypted export files older than 30 days to reduce security risk.
The encrypted file is only needed for physical printing; after 30 days
it is assumed the batch has been printed or the export is no longer needed.
"""

import frappe
from frappe.utils import add_days, now_datetime


def cleanup_expired_exports():
	"""Delete encrypted export files older than 30 days.

	Finds all Voucher Batches with an encrypted_file_url set, checks
	the associated File doc's creation date, and deletes files older
	than 30 days. Each batch is processed independently so individual
	failures don't stop the entire cleanup.
	"""
	cutoff = add_days(now_datetime(), -30)

	batches = frappe.get_all(
		"Memora Voucher Batch",
		filters={"encrypted_file_url": ["is", "set"]},
		fields=["name", "encrypted_file_url"],
	)

	deleted_count = 0
	for batch_data in batches:
		try:
			file_name = frappe.db.get_value(
				"File",
				{
					"file_url": batch_data.encrypted_file_url,
					"attached_to_doctype": "Memora Voucher Batch",
					"attached_to_name": batch_data.name,
				},
				"name",
			)

			if not file_name:
				# File doc missing but URL set -- clear the stale reference
				frappe.db.set_value("Memora Voucher Batch", batch_data.name, "encrypted_file_url", "")
				continue

			creation = frappe.db.get_value("File", file_name, "creation")
			if creation and creation < cutoff:
				frappe.delete_doc("File", file_name, ignore_permissions=True)
				frappe.db.set_value("Memora Voucher Batch", batch_data.name, "encrypted_file_url", "")
				deleted_count += 1
				frappe.logger().info(
					f"Deleted expired export for batch {batch_data.name} (created {creation})"
				)

		except Exception:
			frappe.log_error(title=f"Voucher cleanup failed for batch {batch_data.name}")

	if deleted_count:
		frappe.db.commit()
		frappe.logger().info(f"Voucher cleanup complete: {deleted_count} expired export(s) deleted")
