"""Daily archive task for Memora Task Run Log.

Phase 0: Retry Failed batches — re-links each Failed batch to a non-Failed archive job
         (creating one if needed), resets to Pending, and increments retry_count.
         Batches at MAX_RETRY_COUNT are skipped with a frappe.log_error() alert.
Phase 1: Sync batch statuses — transitions Pending/Exported batches to Synced
         when the linked archive job is Completed, and reconciles to Purged when
         the generic archive executor has already deleted the source rows.
Phase 2: Create new archive jobs — calls scheduler.create_pending_jobs() for
         task_run_log archive type and creates a linked batch for each new job.

Scheduled via hooks.py: "0 2 * * *" (daily at 02:00)
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta

import frappe
from frappe.utils import now_datetime

from archive_executor.config import Config
from archive_executor.db import get_connection
from archive_executor.schemas import load_archive_type
from archive_executor.scheduler import (
    _build_job_meta,
    _insert_pending_job,
    _next_job_name,
    create_pending_jobs,
)
from memora_admin.tasks.task_utils import log_task_run

logger = logging.getLogger(__name__)

TASK_NAME = "archive_task_log"
RUNTIME_CAP_SECONDS = 300
TERMINAL_STATUSES = ("Success", "Failed", "Partial")
RETENTION_DAYS = 90
MAX_RETRY_COUNT = 3


def _normalize_file_checksum(value: str | None) -> str:
    """Store checksums in the batch tracker as a bare SHA-256 hex digest."""
    if not value:
        return ""

    checksum = str(value).strip()
    if checksum.lower().startswith("sha256:"):
        checksum = checksum.split(":", 1)[1]

    if len(checksum) <= 64:
        return checksum

    match = re.search(r"[0-9a-fA-F]{64}", checksum)
    if match:
        return match.group(0)

    return checksum[:64]


def archive_task_log(triggered_by: str = "Scheduler") -> None:
    """Daily archive task for Memora Task Run Log.

    Phase 0 — Retry Failed batches:
      For each Failed batch with retry_count >= MAX_RETRY_COUNT:
        - Log alert and skip
      For each Failed batch with retry_count < MAX_RETRY_COUNT:
        - Ensure a non-Failed archive job exists; create one if needed
        - Reset batch to Pending, increment retry_count, clear last_error

    Phase 1 — Sync batch statuses:
      For each Pending/Exported/Synced batch with a linked archive job:
        - If archive job is Completed -> transition batch to Synced
        - If archive job is Purged -> transition batch to Purged

    Phase 2 — Create new archive jobs:
      Calls scheduler.create_pending_jobs(config, "task_run_log", retention_days)
      For each new archive job created, creates a linked Memora Task Log Archive Batch

    Respects RUNTIME_CAP_SECONDS = 300. Logs via log_task_run().
    """
    start_time = time.monotonic()
    started_at = now_datetime()
    jobs_created = 0
    batches_created = 0
    synced_count = 0
    failed_count = 0
    retried_count = 0

    try:
        # Phase 0: Retry Failed batches
        retried_count = _retry_failed_batches(start_time)
        logger.info(f"{TASK_NAME}: retried {retried_count} failed batch(es)")

        if time.monotonic() - start_time >= RUNTIME_CAP_SECONDS:
            logger.warning(f"{TASK_NAME}: runtime cap reached after retry phase")
            log_task_run(
                task_name=TASK_NAME,
                status="Partial",
                processed=retried_count,
                failed=0,
                error_message="Runtime cap reached after retry phase",
                triggered_by=triggered_by,
                started_at=started_at,
            )
            return

        # Phase 1: Sync existing batch statuses
        synced_count, failed_count = _sync_batch_statuses()
        logger.info(f"{TASK_NAME}: synced {synced_count} batch(es), {failed_count} failed")

        if time.monotonic() - start_time >= RUNTIME_CAP_SECONDS:
            logger.warning(f"{TASK_NAME}: runtime cap reached after sync phase")
            log_task_run(
                task_name=TASK_NAME,
                status="Partial",
                processed=synced_count + retried_count,
                failed=failed_count,
                error_message="Runtime cap reached after sync phase",
                triggered_by=triggered_by,
                started_at=started_at,
            )
            return

        # Phase 2: Create new archive jobs for eligible date windows
        config = Config.from_env()
        new_job_names = create_pending_jobs(config, "task_run_log", RETENTION_DAYS)
        jobs_created = len(new_job_names)
        logger.info(f"{TASK_NAME}: {jobs_created} new archive job(s) created")

        # Commit to start a fresh snapshot so frappe.get_doc can see the newly
        # inserted jobs (created via a separate raw connection — REPEATABLE READ
        # would otherwise hide them from the current transaction).
        frappe.db.commit()

        for job_name in new_job_names:
            if time.monotonic() - start_time >= RUNTIME_CAP_SECONDS:
                logger.warning(f"{TASK_NAME}: runtime cap reached, deferring remaining batches")
                break

            try:
                job = frappe.get_doc("Memora Archive Job", job_name)
                job_meta = json.loads(job.job_meta or "{}")
                _create_batch_for_job(job_name, job.source_doctype, job_meta)
                batches_created += 1
            except Exception:
                frappe.log_error(title=f"{TASK_NAME}: failed to create batch for job {job_name}")

        log_task_run(
            task_name=TASK_NAME,
            status="Success",
            processed=synced_count + batches_created + retried_count,
            failed=failed_count,
            failed_details=[{
                "jobs_created": jobs_created,
                "batches_created": batches_created,
                "synced_count": synced_count,
                "failed_count": failed_count,
                "retried_count": retried_count,
            }],
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


def _create_batch_for_job(job_name: str, source_doctype: str, job_meta: dict) -> str:
    """Create a Memora Task Log Archive Batch linked to an archive job.

    Extracts date_from, date_to, cutoff_date from job_meta.query_filter.
    Returns the new batch name.
    """
    query_filter = job_meta.get("query_filter", {})
    date_from = query_filter.get("date_from", "")
    date_to = query_filter.get("date_to", "")
    cutoff_date = query_filter.get(
        "cutoff_date",
        str(date.today() - timedelta(days=RETENTION_DAYS)),
    )

    doc = frappe.get_doc({
        "doctype": "Memora Task Log Archive Batch",
        "source_doctype": source_doctype,
        "date_from": date_from,
        "date_to": date_to,
        "cutoff_date": cutoff_date,
        "status": "Pending",
        "archive_job_id": job_name,
        "retry_count": 0,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    logger.info(
        f"{TASK_NAME}: created batch {doc.name} for job {job_name} ({date_from} -> {date_to})"
    )
    return doc.name


def _retry_failed_batches(start_time: float) -> int:
    """Retry Failed batches with retry_count < MAX_RETRY_COUNT.

    For each batch at or over MAX_RETRY_COUNT: sets last_error and logs an alert (T013).
    For retryable batches: ensures a non-Failed archive job exists (creating one if
    needed), resets status to Pending, increments retry_count, and clears last_error (T012).

    Returns count of batches successfully queued for retry.
    """
    failed_batches = frappe.get_all(
        "Memora Task Log Archive Batch",
        filters={"status": "Failed"},
        fields=["name", "archive_job_id", "source_doctype", "date_from", "date_to", "retry_count"],
    )

    retried = 0
    for batch in failed_batches:
        if time.monotonic() - start_time >= RUNTIME_CAP_SECONDS:
            break

        if batch.retry_count >= MAX_RETRY_COUNT:
            # T013: max retry reached — alert and skip
            msg = "Max retry count reached — manual intervention required"
            frappe.db.set_value(
                "Memora Task Log Archive Batch",
                batch.name,
                {"last_error": msg},
            )
            frappe.db.commit()
            frappe.log_error(title=f"{TASK_NAME}: batch {batch.name} exceeded max retries")
            logger.warning(
                f"{TASK_NAME}: batch {batch.name} at max retries ({MAX_RETRY_COUNT}), skipping"
            )
            continue

        # T012: attempt retry
        try:
            job_name = _get_or_create_archive_job(batch.source_doctype, str(batch.date_from))
            frappe.db.set_value(
                "Memora Task Log Archive Batch",
                batch.name,
                {
                    "archive_job_id": job_name,
                    "retry_count": batch.retry_count + 1,
                    "last_error": "",
                    "status": "Pending",
                },
            )
            frappe.db.commit()
            retried += 1
            logger.info(
                f"{TASK_NAME}: retried batch {batch.name} (attempt {batch.retry_count + 1})"
                f" -> job {job_name}"
            )
        except Exception as e:
            frappe.db.set_value(
                "Memora Task Log Archive Batch",
                batch.name,
                {"last_error": str(e)},
            )
            frappe.db.commit()
            logger.error(f"{TASK_NAME}: retry failed for batch {batch.name}: {e}")

    return retried


def _get_or_create_archive_job(source_doctype: str, archive_scope: str) -> str:
    """Return an existing non-Failed archive job name, or create a new Pending one.

    Checks for a non-Failed archive job for the given (source_doctype, archive_scope)
    before creating a new one, making retries idempotent.
    """
    existing = frappe.db.get_value(
        "Memora Archive Job",
        {
            "source_doctype": source_doctype,
            "archive_scope": archive_scope,
            "schema_version": "v1",
            "status": ["not in", ["Failed"]],
        },
        "name",
    )
    if existing:
        return existing

    # No active job — create a new Pending one
    config = Config.from_env()
    conn = get_connection(config)
    try:
        archive_schema = load_archive_type(config.schema_registry_path, "task_run_log", "v1")
        next_date = (date.fromisoformat(archive_scope) + timedelta(days=1)).isoformat()
        job_meta = _build_job_meta(archive_schema, archive_scope, next_date)
        job_name = _next_job_name(conn)
        _insert_pending_job(
            conn=conn,
            name=job_name,
            source_doctype=source_doctype,
            archive_type="task_run_log",
            archive_scope=archive_scope,
            schema_version="v1",
            job_meta=job_meta,
        )
        return job_name
    finally:
        conn.close()


def _sync_batch_statuses() -> tuple[int, int]:
    """Scan active batches and transition based on linked archive job status.

    - Pending + job Exported/Transferred/Ingested → Exported (with file metadata)
    - Pending/Exported + job Completed → Synced (with file metadata)
    - Pending/Exported/Synced + job Purged → Purged (with file metadata)
    - On exception: persist last_error to the batch record

    Returns (synced_count, failed_count).
    """
    batches = frappe.get_all(
        "Memora Task Log Archive Batch",
        filters={"status": ["in", ["Pending", "Exported", "Synced"]], "archive_job_id": ["is", "set"]},
        fields=["name", "archive_job_id", "status"],
    )

    synced_count = 0
    failed_count = 0

    for batch in batches:
        try:
            job = frappe.db.get_value(
                "Memora Archive Job",
                batch.archive_job_id,
                ["status", "file_path", "file_checksum", "row_count"],
                as_dict=True,
            )
            if not job or not job.get("status"):
                continue

            job_status = job["status"]
            job_file_path = job.get("file_path") or ""
            job_file_checksum = _normalize_file_checksum(job.get("file_checksum"))
            job_row_count = job.get("row_count") or 0

            if batch.status == "Pending" and job_status in ("Exported", "Transferred", "Ingested"):
                frappe.db.set_value(
                    "Memora Task Log Archive Batch",
                    batch.name,
                    {
                        "status": "Exported",
                        "exported_at": now_datetime(),
                        "file_path": job_file_path,
                        "file_checksum": job_file_checksum,
                        "row_count": job_row_count,
                        "last_error": "",
                    },
                )
                frappe.db.commit()
                logger.info(
                    f"{TASK_NAME}: batch {batch.name} -> Exported "
                    f"(job {batch.archive_job_id} {job_status})"
                )

            elif job_status == "Completed" and batch.status in ("Pending", "Exported"):
                transition_time = now_datetime()
                update = {
                    "status": "Synced",
                    "synced_at": transition_time,
                    "file_path": job_file_path,
                    "file_checksum": job_file_checksum,
                    "row_count": job_row_count,
                    "last_error": "",
                }
                if batch.status == "Pending":
                    update["exported_at"] = transition_time
                frappe.db.set_value(
                    "Memora Task Log Archive Batch",
                    batch.name,
                    update,
                )
                frappe.db.commit()
                synced_count += 1
                logger.info(
                    f"{TASK_NAME}: batch {batch.name} -> Synced "
                    f"(job {batch.archive_job_id} Completed)"
                )

            elif job_status == "Purged":
                transition_time = now_datetime()
                update = {
                    "status": "Purged",
                    "purged_at": transition_time,
                    "file_path": job_file_path,
                    "file_checksum": job_file_checksum,
                    "row_count": job_row_count,
                    "last_error": "",
                }
                if batch.status == "Pending":
                    update["exported_at"] = transition_time
                    update["synced_at"] = transition_time
                elif batch.status == "Exported":
                    update["synced_at"] = transition_time
                frappe.db.set_value(
                    "Memora Task Log Archive Batch",
                    batch.name,
                    update,
                )
                frappe.db.commit()
                synced_count += 1
                logger.info(
                    f"{TASK_NAME}: batch {batch.name} -> Purged "
                    f"(job {batch.archive_job_id} Purged)"
                )

        except Exception as e:
            failed_count += 1
            try:
                frappe.db.set_value(
                    "Memora Task Log Archive Batch",
                    batch.name,
                    {"last_error": str(e)},
                )
                frappe.db.commit()
            except Exception:
                pass
            logger.error(f"{TASK_NAME}: failed to sync batch {batch.name}: {e}")

    return synced_count, failed_count
