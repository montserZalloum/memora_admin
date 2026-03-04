"""Frappe API for build-related operations."""

import frappe


@frappe.whitelist(allow_guest=False)
def queue_manual_build(subject_id: str) -> dict:
	"""
	Queue a manual build for a subject.

	Manual builds bypass debounce (no Redis key check).

	Args:
		subject_id: Memora Subject name

	Returns:
		dict with success status and build_id
	"""
	# Validate subject exists
	if not frappe.db.exists("Memora Subject", subject_id):
		frappe.throw(f"Subject {subject_id} not found")

	# Create Build Queue entry (bypassing debounce)
	build_queue = frappe.get_doc(
		{
			"doctype": "Memora Build Queue",
			"target_type": "Memora Subject",
			"target_name": subject_id,
			"trigger_reason": "manual",
			"triggered_by": frappe.session.user,
			"status": "Pending",
		}
	)
	build_queue.insert(ignore_permissions=True)

	frappe.logger().info(
		f"Manual build queued: {build_queue.name} for subject {subject_id} " f"by {frappe.session.user}"
	)

	return {"success": True, "build_id": build_queue.name}
