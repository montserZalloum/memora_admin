"""
Build worker scheduled task for processing pending builds.

Processes Memora Build Queue items:
1. Picks pending builds (oldest first)
2. Generates subject JSON via generator
3. Uploads to CDN via publisher
4. Notifies FastAPI for cache invalidation via Redis pub/sub
5. Sends Frappe realtime notifications for success/failure

Scheduled to run every 2 minutes via Frappe scheduler.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import frappe

logger = logging.getLogger(__name__)

# Redis key prefix for retry tracking
RETRY_KEY_PREFIX = "memora:build:retry:"
MAX_RETRIES = 3


def process_pending_builds():
	"""
	Process all pending builds in the queue.

	Entry point for Frappe scheduler (runs every 2 minutes).
	Processes builds oldest-first to maintain order.
	"""
	# Get all pending builds, ordered by creation (oldest first)
	pending_builds = frappe.get_all(
		"Memora Build Queue",
		filters={"status": "Pending"},
		fields=["name", "target_name", "triggered_by"],
		order_by="creation asc",
	)

	if not pending_builds:
		logger.debug("No pending builds to process")
		return

	logger.info(f"Processing {len(pending_builds)} pending builds")

	for build in pending_builds:
		try:
			_process_single_build(build)
		except Exception as e:
			logger.error(f"Error processing build {build.name}: {e}")
			_mark_build_failed(build.name, str(e))


def _process_single_build(build: dict):
	"""
	Process a single build queue item.

	Workflow:
	1. Mark as Processing
	2. Generate JSON files
	3. Upload to CDN
	4. On success: mark Completed, notify cache invalidation
	5. On failure: requeue with retry logic
	"""
	build_name = build["name"]
	target_name = build["target_name"]

	# Load build document
	build_doc = frappe.get_doc("Memora Build Queue", build_name)

	# Mark as Processing
	build_doc.status = "Processing"
	build_doc.started_at = datetime.now(timezone.utc)
	build_doc.save(ignore_permissions=True)
	frappe.db.commit()

	logger.info(f"Processing build {build_name} for subject {target_name}")

	try:
		# Import generator and publisher
		from memora_admin.memora_admin.services.build.generator import generate_subject_json
		from memora_admin.memora_admin.services.build.publisher import publish_to_cdn

		# Generate JSON files
		files = generate_subject_json(target_name)

		if not files:
			logger.warning(f"No files generated for subject {target_name}")
			build_doc.status = "Failed"
			build_doc.error_message = "No files generated - subject may not exist or have no content"
			_finalize_build(build_doc)
			_clear_retry_count(build_name)
			_send_notification(target_name, success=False, error="No files generated")
			return

		# Upload to CDN
		upload_success = publish_to_cdn(files, max_retries=3)

		if upload_success:
			# Success path
			build_doc.status = "Completed"
			build_doc.files_generated = len(files)
			build_doc.error_message = None

			# Notify cache invalidation via Redis pub/sub
			_notify_cache_invalidation(target_name)

			# Send success notification
			_send_notification(target_name, success=True)

			# Clear retry count
			_clear_retry_count(build_name)

			logger.info(f"Build {build_name} completed successfully, {len(files)} files published")
		else:
			# Upload failed - attempt requeue
			_requeue_build(build_doc)

	except Exception as e:
		logger.error(f"Build {build_name} failed with exception: {e}")
		build_doc.status = "Failed"
		build_doc.error_message = str(e)
		_clear_retry_count(build_name)
		_send_notification(target_name, success=False, error=str(e))

	finally:
		_finalize_build(build_doc)


def _finalize_build(build_doc):
	"""Set completion timestamp, calculate duration, and save."""
	build_doc.completed_at = datetime.now(timezone.utc)

	# Calculate duration if started_at is set
	if build_doc.started_at:
		started = build_doc.started_at
		completed = build_doc.completed_at

		# Handle timezone-naive datetimes from Frappe
		if started.tzinfo is None:
			started = started.replace(tzinfo=timezone.utc)
		if completed.tzinfo is None:
			completed = completed.replace(tzinfo=timezone.utc)

		duration = (completed - started).total_seconds()
		build_doc.duration_sec = int(duration)

	build_doc.save(ignore_permissions=True)
	frappe.db.commit()


def _notify_cache_invalidation(subject_id: str):
	"""
	Publish cache invalidation message to Redis pub/sub.

	FastAPI listens on this channel to invalidate hierarchy cache.
	"""
	channel = "memora:cache:invalidate"
	message = json.dumps({
		"type": "hierarchy",
		"subject_id": subject_id,
		"timestamp": datetime.now(timezone.utc).isoformat(),
	})

	try:
		frappe.cache.publish(channel, message)
		logger.info(f"Published cache invalidation for subject {subject_id}")
	except Exception as e:
		# Log but don't fail the build
		logger.error(f"Failed to publish cache invalidation: {e}")


def _send_notification(subject_id: str, success: bool, error: str | None = None):
	"""
	Send Frappe realtime notification for build completion.

	Shows in Frappe bell notifications for admins.
	"""
	if success:
		message = {
			"type": "success",
			"title": "Build Complete",
			"message": f"Subject {subject_id} build completed successfully",
			"subject_id": subject_id,
		}
	else:
		message = {
			"type": "error",
			"title": "Build Failed",
			"message": f"Subject {subject_id} build failed: {error or 'Unknown error'}",
			"subject_id": subject_id,
		}

	try:
		frappe.publish_realtime(
			event="build_complete",
			message=message,
			after_commit=True,
		)
		logger.debug(f"Sent build notification for {subject_id}: {'success' if success else 'failure'}")
	except Exception as e:
		logger.error(f"Failed to send realtime notification: {e}")


def _mark_build_failed(build_name: str, error: str):
	"""
	Mark build as failed and clean up retry tracking.

	Called when build processing throws an exception.
	"""
	try:
		build_doc = frappe.get_doc("Memora Build Queue", build_name)
		build_doc.status = "Failed"
		build_doc.error_message = error
		build_doc.completed_at = datetime.now(timezone.utc)
		build_doc.save(ignore_permissions=True)
		frappe.db.commit()

		# Clean up retry key
		_clear_retry_count(build_name)

		# Get target name for notification
		target_name = build_doc.target_name
		_send_notification(target_name, success=False, error=error)

	except Exception as e:
		logger.error(f"Failed to mark build {build_name} as failed: {e}")


def _requeue_build(build_doc):
	"""
	Requeue build for retry with Redis-based retry tracking.

	Uses INCR for atomic retry count increment.
	If max retries exceeded, marks as failed.
	"""
	build_name = build_doc.name
	retry_key = RETRY_KEY_PREFIX + build_name

	# Increment retry count atomically
	frappe.cache.incr(retry_key)

	# Get current count
	retry_count = frappe.cache.get(retry_key)
	if retry_count is not None:
		# Handle bytes response from Redis
		if isinstance(retry_count, bytes):
			retry_count = int(retry_count.decode())
		else:
			retry_count = int(retry_count)
	else:
		retry_count = 1

	if retry_count < MAX_RETRIES:
		# Reset to Pending for retry
		build_doc.status = "Pending"
		build_doc.error_message = f"Upload failed, retry {retry_count}/{MAX_RETRIES}"
		logger.info(f"Requeued build {build_name} for retry ({retry_count}/{MAX_RETRIES})")
	else:
		# Max retries exceeded
		build_doc.status = "Failed"
		build_doc.error_message = f"Max retries ({MAX_RETRIES}) exceeded"
		_clear_retry_count(build_name)
		_send_notification(build_doc.target_name, success=False, error="Max retries exceeded")
		logger.error(f"Build {build_name} failed after {MAX_RETRIES} retries")


def _clear_retry_count(build_name: str):
	"""
	Clear retry tracking key from Redis.

	Called on:
	- Successful build completion
	- Final failure (max retries)
	- Manual failure marking
	"""
	retry_key = RETRY_KEY_PREFIX + build_name
	try:
		frappe.cache.delete(retry_key)
	except Exception as e:
		logger.debug(f"Failed to clear retry key {retry_key}: {e}")
