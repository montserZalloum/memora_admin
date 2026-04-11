"""
Build worker scheduled task for processing pending plan builds.

Processes Memora Build Queue items:
1. Picks pending builds (oldest first)
2. Generates plan JSON via generate_plan_json
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
from frappe.utils import now_datetime

from fastapi_app.core.redis_keys import build_retry_key as _build_retry_key_fn
from fastapi_app.core.redis_keys import cache_invalidation_channel

logger = logging.getLogger(__name__)

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
	build_doc.started_at = now_datetime()
	build_doc.save(ignore_permissions=True)
	frappe.db.commit()

	# Clear the debounce key now that we are actively processing this build.
	# If the plan is saved again while the build runs, on_plan_updated will be
	# able to set a fresh debounce key and queue a new build — preventing the
	# post-build blind window where changes made after build completion but
	# before the 120s TTL expires were silently dropped.
	try:
		from fastapi_app.core.redis_keys import build_debounce_key

		frappe.cache.delete_value(build_debounce_key(target_name))
	except Exception:
		pass  # Non-fatal — worst case is a redundant queued build, never a missed one

	logger.info(f"Processing build {build_name} for {build_doc.target_type} {target_name}")

	try:
		# Import generator and publisher
		from memora_admin.memora_admin.services.build.plan_generator import generate_plan_json
		from memora_admin.memora_admin.services.build.publisher import publish_to_cdn

		target_type = build_doc.target_type

		if target_type != "Memora Academic Plan":
			logger.warning(f"Skipping unsupported build target_type={target_type} for {target_name}")
			build_doc.status = "Failed"
			build_doc.error_message = f"Unsupported target_type: {target_type}"
			_finalize_build(build_doc)
			return

		files = generate_plan_json(target_name)

		if not files:
			logger.warning(f"No files generated for plan {target_name}")
			build_doc.status = "Failed"
			build_doc.error_message = "No files generated - plan may not exist or have no content"
			_finalize_build(build_doc)
			_clear_retry_count(build_name)
			_send_notification(
				target_name, success=False, error="No files generated", target_type=target_type
			)
			return

		# Upload to CDN
		upload_success = publish_to_cdn(files, max_retries=3)

		if upload_success:
			# Success path
			build_doc.status = "Completed"
			build_doc.files_generated = len(files)
			build_doc.error_message = None

			# Notify cache invalidation via Redis pub/sub
			_notify_cache_invalidation(target_name, target_type)

			# Send success notification
			_send_notification(target_name, success=True, target_type=target_type)

			# Clear retry count
			_clear_retry_count(build_name)

			# Clean up orphaned plan-scoped files (best-effort, never fails build)
			orphaned_filenames = _cleanup_orphaned_files(target_name, files)

			# Purge CDN cache for published files + orphans (best-effort, never fails build)
			_purge_cdn_cache(files, extra_filenames=orphaned_filenames)

			logger.info(
				f"Build {build_name} completed successfully for plan {target_name}, {len(files)} files published"
			)
		else:
			# Upload failed - attempt requeue
			_requeue_build(build_doc)

	except Exception as e:
		logger.error(f"Build {build_name} failed with exception: {e}")
		build_doc.status = "Failed"
		build_doc.error_message = str(e)
		_clear_retry_count(build_name)
		_send_notification(target_name, success=False, error=str(e), target_type=build_doc.target_type)

	finally:
		_finalize_build(build_doc)


def _finalize_build(build_doc):
	"""Set completion timestamp, calculate duration, and save."""
	build_doc.completed_at = now_datetime()

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


def _notify_cache_invalidation(target_id: str, target_type: str = "Memora Academic Plan"):
	"""
	Invalidate Redis manifest cache and publish pub/sub notification.

	Two-step approach for reliability:
	1. Directly DELETE the manifest key from Memora Redis (port 13001) so the
	   stale entry is gone immediately, even if the FastAPI sidecar misses the
	   pub/sub message (e.g. during a restart).
	2. Publish the invalidation message on Memora Redis so the FastAPI sidecar
	   can also evict any in-process caches.

	NOTE: Must use get_memora_redis() (port 13001), NOT frappe.cache (port 13000).
	The FastAPI sidecar subscribes to Memora Redis — messages on Frappe Redis
	are never received by it.

	Args:
		target_id: The plan_id
		target_type: "Memora Academic Plan"
	"""
	from fastapi_app.core.redis_keys import plan_manifest_key
	from memora_admin.utils.redis_connection import get_memora_redis

	message = json.dumps(
		{
			"type": "plan",
			"plan_id": target_id,
			"timestamp": datetime.now(timezone.utc).isoformat(),
		}
	)

	try:
		r = get_memora_redis()
		# Step 1: direct delete so the stale key is gone regardless of pub/sub delivery
		r.delete(plan_manifest_key(target_id))
		# Step 2: pub/sub for FastAPI in-process cache eviction
		r.publish(cache_invalidation_channel(), message)
		logger.info(f"Cache invalidated for plan {target_id} (key deleted + pub/sub published)")
	except Exception as e:
		# Log but don't fail the build
		logger.error(f"Failed to invalidate cache for plan {target_id}: {e}")


def _send_notification(
	target_id: str, success: bool, error: str | None = None, target_type: str = "Memora Academic Plan"
):
	"""
	Send Frappe realtime notification for build completion.

	Shows in Frappe bell notifications for admins.

	Args:
		target_id: The plan_id
		success: Whether the build succeeded
		error: Error message if failed
		target_type: "Memora Academic Plan"
	"""
	entity_type = "Plan"

	if success:
		message = {
			"type": "success",
			"title": "Build Complete",
			"message": f"{entity_type} {target_id} build completed successfully",
			"target_id": target_id,
			"target_type": target_type,
		}
	else:
		message = {
			"type": "error",
			"title": "Build Failed",
			"message": f"{entity_type} {target_id} build failed: {error or 'Unknown error'}",
			"target_id": target_id,
			"target_type": target_type,
		}

	try:
		frappe.publish_realtime(
			event="build_complete",
			message=message,
			after_commit=True,
		)
		logger.debug(
			f"Sent build notification for {entity_type} {target_id}: {'success' if success else 'failure'}"
		)
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
		build_doc.completed_at = now_datetime()
		build_doc.save(ignore_permissions=True)
		frappe.db.commit()

		# Clean up retry key
		_clear_retry_count(build_name)

		# Get target info for notification
		target_name = build_doc.target_name
		target_type = build_doc.target_type or "Memora Academic Plan"
		_send_notification(target_name, success=False, error=error, target_type=target_type)

	except Exception as e:
		logger.error(f"Failed to mark build {build_name} as failed: {e}")


def _requeue_build(build_doc):
	"""
	Requeue build for retry with Redis-based retry tracking.

	Uses INCR for atomic retry count increment.
	If max retries exceeded, marks as failed.
	"""
	build_name = build_doc.name
	retry_key = _build_retry_key_fn(build_name)

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
		target_type = build_doc.target_type or "Memora Academic Plan"
		_send_notification(
			build_doc.target_name, success=False, error="Max retries exceeded", target_type=target_type
		)
		logger.error(f"Build {build_name} failed after {MAX_RETRIES} retries")


def _clear_retry_count(build_name: str):
	"""
	Clear retry tracking key from Redis.

	Called on:
	- Successful build completion
	- Final failure (max retries)
	- Manual failure marking
	"""
	retry_key = _build_retry_key_fn(build_name)
	try:
		frappe.cache.delete(retry_key)
	except Exception as e:
		logger.debug(f"Failed to clear retry key {retry_key}: {e}")


def _cleanup_orphaned_files(plan_id: str, files: list) -> list[str]:
	"""
	Remove plan-scoped files from storage that are no longer in the new build.

	Only cleans up files under plans/{plan_id}/. Shared lesson files (lessons/*.json)
	are NOT touched because they may be referenced by other plans.

	Best-effort: errors are logged but never fail the build.

	Args:
		plan_id: The plan ID (e.g., "PLAN-00001")
		files: Nested file list from the generator

	Returns:
		List of orphaned filenames that were deleted (for CDN purge)
	"""
	orphaned: list[str] = []
	plan_prefix = f"plans/{plan_id}/"

	try:
		from memora_admin.memora_admin.services.build.storage import get_storage_backend

		storage = get_storage_backend()

		# Collect all plan-scoped filenames from the new build
		def _collect_plan_filenames(file_list: list) -> set[str]:
			names: set[str] = set()
			for f in file_list:
				if isinstance(f, dict) and "filename" in f:
					fn = f["filename"]
					if fn.startswith(plan_prefix):
						names.add(fn)
					if "children" in f and isinstance(f["children"], list):
						names.update(_collect_plan_filenames(f["children"]))
			return names

		new_files = _collect_plan_filenames(files)

		# List all existing files under plans/{plan_id}/
		existing_files = set(storage.list_directory(plan_prefix))

		# Compute orphans
		orphans = existing_files - new_files

		if not orphans:
			return []

		for orphan_key in orphans:
			try:
				storage.delete(orphan_key)
				orphaned.append(orphan_key)
				logger.info(f"Deleted orphaned file: {orphan_key}")
			except Exception as e:
				logger.warning(f"Failed to delete orphaned file {orphan_key}: {e}")

		if orphaned:
			logger.info(f"Cleaned up {len(orphaned)} orphaned files for plan {plan_id}")

	except Exception as e:
		logger.error(f"Orphan cleanup error for plan {plan_id} (build unaffected): {e}")

	return orphaned


def _purge_cdn_cache(files: list, extra_filenames: list[str] | None = None) -> None:
	"""
	Purge published files from Cloudflare edge cache after a successful build.

	Best-effort: failures are logged but never raise or affect build outcome.

	Args:
		files: Nested file list from the generator (same structure passed to publish_to_cdn).
		extra_filenames: Additional filenames to purge (e.g., orphaned files).
	"""
	try:
		from memora_admin.memora_admin.services.cdn.utils import get_purge_service

		purge_service = get_purge_service()
		if purge_service is None:
			# CDN not configured — skip silently
			return

		# Flatten nested structure to extract all filenames
		def _collect_filenames(file_list: list) -> list[str]:
			names: list[str] = []
			for f in file_list:
				if isinstance(f, dict) and "filename" in f:
					names.append(f["filename"])
					if "children" in f and isinstance(f["children"], list):
						names.extend(_collect_filenames(f["children"]))
			return names

		filenames = _collect_filenames(files)

		# Add orphaned filenames for CDN purge
		if extra_filenames:
			filenames.extend(extra_filenames)

		if not filenames:
			logger.debug("No filenames to purge from CDN")
			return

		success = purge_service.purge_files(filenames)
		if success:
			logger.info(f"CDN cache purged for {len(filenames)} files")
		else:
			logger.warning(f"CDN cache purge partially failed for {len(filenames)} files (see Error Log)")

	except Exception as e:
		logger.error(f"CDN cache purge error (build unaffected): {e}")
