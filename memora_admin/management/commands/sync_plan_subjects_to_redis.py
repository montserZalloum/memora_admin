"""Management command to sync plan subjects to Redis.

This command rebuilds the Redis cache for all plan subjects.
Used for initial setup or repair if cache gets corrupted.

Usage:
    bench exec memora_admin.management.commands.sync_plan_subjects_to_redis.run
    OR
    python manage.py sync_plan_subjects_to_redis
"""

import frappe
from memora_admin.events.access_sync import rebuild_plan_free_subjects


def run():
    """Sync all plan subjects to Redis."""
    print("=== Syncing Plan Subjects to Redis ===\n")

    # Get all academic plans
    plans = frappe.get_all("Memora Academic Plan", pluck="name")

    if not plans:
        print("No plans found!")
        return

    print(f"Found {len(plans)} plans\n")

    # Rebuild each plan's free subjects in Redis
    for plan_id in plans:
        try:
            rebuild_plan_free_subjects(plan_id)
            print(f"✓ Synced plan: {plan_id}")
        except Exception as e:
            print(f"✗ Error syncing plan {plan_id}: {e}")

    print(f"\n=== Completed ===")
    print(f"All {len(plans)} plans synced to Redis")
    print("\nYou can now test the endpoints:")
    print('  curl -H "Authorization: Bearer <TOKEN>" \\')
    print('    http://127.0.0.1:8002/api/v1/progress/{subject}/tracks')
