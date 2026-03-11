"""Daily purge task for Memora Task Run Log.

Scans Synced batches and deletes their source rows from production
in bounded sub-batches of 10,000, each committed independently,
with a 5-second lock timeout and a 300-second runtime cap.

No row within the 90-day retention window is ever deleted.

Scheduled via hooks.py: "30 3 * * *" (daily at 03:30)
"""

from __future__ import annotations

import logging
import time

import frappe
from frappe.utils import now_datetime
from pymysql import OperationalError

from memora_admin.tasks.task_utils import log_task_run

logger = logging.getLogger(__name__)

TASK_NAME = "purge_task_log"
RUNTIME_CAP_SECONDS = 300
TERMINAL_STATUSES = ("Success", "Failed", "Partial")
RETENTION_DAYS = 90
SUB_BATCH_SIZE = 10_000
SOURCE_TABLE = "tabMemora Task Run Log"


def purge_task_log(triggered_by: str = "Scheduler") -> None:
    """Daily purge task for Memora Task Run Log.

    Queries Synced batches and deletes source rows in sub-batches of 10,000.
    Marks each batch Purged only after zero rows remain in its window.
    Enforces RUNTIME_CAP_SECONDS = 300. Logs via log_task_run().
    """
    start_time = time.monotonic()
    started_at = now_datetime()
    batches_purged = 0
    batches_failed = 0

    try:
        batches = frappe.get_all(
            "Memora Task Log Archive Batch",
            filters={"status": "Synced"},
            fields=["name", "date_from", "date_to", "source_doctype"],
        )

        logger.info(f"{TASK_NAME}: {len(batches)} Synced batch(es) to purge")

        for batch in batches:
            try:
                total_deleted = 0
                cap_hit = False

                while True:
                    if time.monotonic() - start_time >= RUNTIME_CAP_SECONDS:
                        logger.warning(
                            f"{TASK_NAME}: runtime cap reached during batch {batch.name}, deferring"
                        )
                        cap_hit = True
                        break

                    deleted = _purge_sub_batch(
                        frappe.db,
                        SOURCE_TABLE,
                        str(batch.date_from),
                        str(batch.date_to),
                        RETENTION_DAYS,
                        TERMINAL_STATUSES,
                    )
                    total_deleted += deleted

                    if deleted == 0:
                        break

                if not cap_hit:
                    frappe.db.set_value(
                        "Memora Task Log Archive Batch",
                        batch.name,
                        {
                            "status": "Purged",
                            "purged_at": now_datetime(),
                        },
                    )
                    frappe.db.commit()
                    batches_purged += 1
                    logger.info(
                        f"{TASK_NAME}: batch {batch.name} -> Purged ({total_deleted} rows deleted)"
                    )

            except OperationalError as e:
                frappe.db.set_value(
                    "Memora Task Log Archive Batch",
                    batch.name,
                    {"last_error": str(e)},
                )
                frappe.db.commit()
                batches_failed += 1
                logger.error(f"{TASK_NAME}: batch {batch.name} lock timeout: {e}")

            except Exception as e:
                batches_failed += 1
                try:
                    frappe.db.set_value(
                        "Memora Task Log Archive Batch",
                        batch.name,
                        {"last_error": str(e)},
                    )
                    frappe.db.commit()
                except Exception:
                    pass
                logger.error(f"{TASK_NAME}: batch {batch.name} failed: {e}")
                frappe.log_error(title=f"{TASK_NAME}: failed to purge batch {batch.name}")

        log_task_run(
            task_name=TASK_NAME,
            status="Success" if batches_failed == 0 else "Partial",
            processed=batches_purged,
            failed=batches_failed,
            triggered_by=triggered_by,
            started_at=started_at,
        )

    except Exception as e:
        logger.error(f"{TASK_NAME} failed: {e}")
        log_task_run(
            task_name=TASK_NAME,
            status="Failed",
            error_message=str(e),
            triggered_by=triggered_by,
            started_at=started_at,
        )
        raise


def _purge_sub_batch(
    conn,
    source_table: str,
    date_from: str,
    date_to: str,
    retention_days: int,
    terminal_statuses: tuple[str, ...],
) -> int:
    """Execute one sub-batch of up to 10,000 row deletions.

    Steps:
    1. Opens a fresh session context with innodb_lock_wait_timeout = 5
    2. SELECTs up to 10,000 eligible names in the date window, outside retention
    3. If no rows, returns 0
    4. DELETEs those rows by name
    5. Commits immediately
    6. Returns the count of deleted rows

    On OperationalError (lock timeout): rolls back and re-raises.
    """
    try:
        conn.sql("SET SESSION innodb_lock_wait_timeout = 5")

        placeholders = ", ".join(["%s"] * len(terminal_statuses))
        select_sql = (
            f"SELECT name FROM `{source_table}` "
            f"WHERE status IN ({placeholders}) "
            f"AND completed_at >= %s AND completed_at < %s "
            f"AND completed_at < DATE_SUB(NOW(), INTERVAL %s DAY) "
            f"ORDER BY completed_at "
            f"LIMIT {SUB_BATCH_SIZE}"
        )
        params = list(terminal_statuses) + [date_from, date_to, retention_days]
        rows = conn.sql(select_sql, params)

        if not rows:
            return 0

        names = [r[0] for r in rows]
        in_placeholders = ", ".join(["%s"] * len(names))
        conn.sql(
            f"DELETE FROM `{source_table}` WHERE name IN ({in_placeholders})",
            names,
        )
        conn.commit()

        return len(names)

    except OperationalError:
        conn.rollback()
        raise
