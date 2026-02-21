"""Frappe API for build-related operations."""

import frappe


@frappe.whitelist(allow_guest=False)
def queue_manual_build(subject_id: str) -> dict:
	"""
	Queue manual plan builds for all plans containing a subject.

	Invalidates hierarchy cache immediately, then queues plan builds
	(bypassing debounce).

	Args:
		subject_id: Memora Subject name

	Returns:
		dict with success status and build_ids list
	"""
	# Validate subject exists
	if not frappe.db.exists("Memora Subject", subject_id):
		frappe.throw(f"Subject {subject_id} not found")

	# Invalidate hierarchy cache immediately
	from memora_admin.events.build_trigger import _invalidate_hierarchy_cache

	_invalidate_hierarchy_cache(subject_id)

	# Find all plans containing this subject
	plan_subjects = frappe.get_all(
		"Memora Plan Subject",
		filters={"subject": subject_id},
		fields=["parent"],
	)

	if not plan_subjects:
		return {"success": True, "build_ids": [], "message": "No plans contain this subject"}

	build_ids = []
	for ps in plan_subjects:
		plan_id = ps["parent"]
		if not plan_id:
			continue

		build_queue = frappe.get_doc(
			{
				"doctype": "Memora Build Queue",
				"target_type": "Memora Academic Plan",
				"target_name": plan_id,
				"trigger_reason": "manual",
				"triggered_by": frappe.session.user,
				"status": "Pending",
			}
		)
		build_queue.insert(ignore_permissions=True)
		build_ids.append(build_queue.name)

		frappe.logger().info(
			f"Manual build queued: {build_queue.name} for plan {plan_id} "
			f"(subject {subject_id}) by {frappe.session.user}"
		)

	return {"success": True, "build_ids": build_ids}
