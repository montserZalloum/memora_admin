"""Daily dimension reconciliation task for analytics lakehouse (T023).

Safety-net scheduled task that performs a full refresh of all 6 dimension
Parquet files.  Catches missed doc_event triggers and ensures the analytics
server always has a consistent view of dimension data.

Registered in ``hooks.py`` under ``scheduler_events["cron"]`` at 04:15 daily.
"""

import frappe


def reconcile_dimensions():
    """Daily full refresh of all 6 dimension Parquet files."""
    from memora_admin.memora_admin.services.dimension_refresh import refresh_all_dimensions

    try:
        results = refresh_all_dimensions()
        frappe.logger().info(f"Dimension reconciliation complete: {results}")
    except Exception:
        frappe.log_error(title="Dimension reconciliation failed")
