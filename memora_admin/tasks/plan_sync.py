"""Periodic sync task to repair and maintain plan subjects in Redis.

This task runs every 6 hours to ensure all plan subjects are synced to Redis.
Used as a safety net in case event handlers are missed or data gets out of sync.

Scheduled in hooks.py: "0 */6 * * *" (every 6 hours)
"""

import frappe

from memora_admin.events.access_sync import rebuild_plan_free_subjects


def sync_all_plan_subjects_to_redis():
	"""Rebuild Redis cache for all plan subjects (periodic maintenance task).

	Runs every 6 hours as a safety net to repair any missing data.
	"""
	try:
		# Get all academic plans
		plans = frappe.get_all("Memora Academic Plan", pluck="name")

		if not plans:
			frappe.logger().info("No plans found for Redis sync")
			return

		frappe.logger().info(f"Syncing {len(plans)} plans to Redis")

		synced_count = 0
		for plan_id in plans:
			try:
				rebuild_plan_free_subjects(plan_id)
				synced_count += 1
			except Exception as e:
				frappe.logger().error(f"Error syncing plan {plan_id} to Redis: {e}", exc_info=True)

		frappe.logger().info(f"Successfully synced {synced_count}/{len(plans)} plans to Redis")

	except Exception as e:
		frappe.logger().error(f"Error in plan Redis sync task: {e}", exc_info=True)
