"""Build trigger handlers for Frappe doc_events.

Queue builds when content DocTypes are updated with 2-minute debounce.
Per plan: prevent build flooding via Redis SET NX EX pattern.
"""

import json
import time

import frappe

from fastapi_app.core.redis_keys import (
	build_debounce_key,
	cache_invalidation_channel,
	catalog_key,
	hierarchy_key,
	plan_free_subjects_key,
	plan_manifest_key,
	plan_season_seq_key,
)

# Debounce configuration
DEBOUNCE_SECONDS = 120  # 2 minutes per plan


# =============================================================================
# Content Update Handler
# =============================================================================


def on_content_updated(doc, method):
	"""
	Handle content DocType updates: invalidate hierarchy cache and queue plan builds.

	1. Immediately invalidates hierarchy cache (direct DEL + pubsub)
	2. Queues plan builds for all plans containing the subject (with debounce)
	3. On lesson trash: also deletes the shared lesson JSON file from storage + CDN

	Handles: Memora Subject, Track, Unit, Topic, Lesson
	"""
	subject_id = _get_subject_id(doc)

	if not subject_id:
		frappe.log_error(
			f"Could not determine subject for {doc.doctype} {doc.name}",
			"Build Trigger Error",
		)
		return

	# Invalidate hierarchy cache immediately (direct DEL + pubsub).
	# Previously this happened as a side effect of the subject build completing (~2 min delay).
	_invalidate_hierarchy_cache(subject_id)

	# When a lesson is deleted, its shared JSON file (lessons/{lesson_id}.json) is orphaned
	# since no plan will reference it anymore. Delete it directly from storage + CDN.
	if doc.doctype == "Memora Lesson" and method == "on_trash":
		_delete_lesson_json(doc.name)

	# GAP 2: When a subject is deleted directly, remove it from all plan free_subjects sets.
	# on_plan_subject_changed handles child row deletion but not direct Subject DocType deletion.
	if doc.doctype == "Memora Subject" and method == "on_trash":
		_remove_subject_from_plan_free_subjects(doc.name)

	# Trigger builds for all plans that contain this subject.
	# Must run BEFORE cascade-deleting Plan Subject rows so the query still finds the plans.
	_queue_plan_builds_for_subject(subject_id, doc)

	# After queuing builds, cascade-delete orphaned Plan Subject rows so future builds
	# and the 6-hour plan_sync task do not process a deleted subject.
	if doc.doctype == "Memora Subject" and method == "on_trash":
		_cascade_delete_plan_subjects(subject_id)


# =============================================================================
# Helper Functions
# =============================================================================


def _queue_plan_builds_for_subject(subject_id: str, doc):
	"""
	Queue builds for all plans that contain the given subject.

	When content changes, plans that reference the subject need rebuilding
	to update aggregated fields like is_free_preview in their manifest.
	"""
	# Find all plans that contain this subject
	plan_subjects = frappe.get_all(
		"Memora Plan Subject",
		filters={"subject": subject_id},
		fields=["parent"],
	)

	if not plan_subjects:
		return

	cache = frappe.cache
	timestamp = str(int(time.time()))

	for ps in plan_subjects:
		plan_id = ps["parent"]
		if not plan_id:
			continue

		debounce_key = build_debounce_key(plan_id)

		# Redis SET NX EX pattern for debounce
		was_set = cache.set(debounce_key, timestamp, nx=True, ex=DEBOUNCE_SECONDS)

		if not was_set:
			continue

		try:
			build_queue = frappe.get_doc(
				{
					"doctype": "Memora Build Queue",
					"target_type": "Memora Academic Plan",
					"target_name": plan_id,
					"trigger_reason": "content_update",
					"triggered_by": frappe.session.user,
					"status": "Pending",
				}
			)
			build_queue.insert(ignore_permissions=True)

			frappe.logger().info(
				f"Build queued: {build_queue.name} for plan {plan_id} "
				f"(triggered by {doc.doctype} {doc.name} in subject {subject_id})"
			)
		except Exception as e:
			cache.delete_value(debounce_key)
			frappe.log_error(
				f"Failed to queue build for plan {plan_id}: {e}",
				"Build Trigger Error",
			)


def _get_subject_id(doc) -> str | None:
	"""
	Extract subject ID from any content DocType.

	Hierarchy: Subject -> Track -> Unit -> Topic -> Lesson
	Lesson has direct link to Subject.
	"""
	doctype = doc.doctype

	if doctype == "Memora Subject":
		return doc.name

	if doctype == "Memora Track":
		return doc.subject

	if doctype == "Memora Unit":
		# Unit -> Track -> Subject
		if doc.track:
			track_subject = frappe.get_cached_value("Memora Track", doc.track, "subject")
			return track_subject
		return None

	if doctype == "Memora Topic":
		# Topic -> Unit -> Track -> Subject
		if doc.unit:
			unit_track = frappe.get_cached_value("Memora Unit", doc.unit, "track")
			if unit_track:
				track_subject = frappe.get_cached_value("Memora Track", unit_track, "subject")
				return track_subject
		return None

	if doctype == "Memora Lesson":
		# Lesson has direct link to Subject
		return doc.subject

	return None


# =============================================================================
# Plan Update Handlers
# =============================================================================


def on_plan_updated(doc, method):
	"""
	Queue a build when Academic Plan is updated.

	Uses same debounce pattern as content updates:
	- If key doesn't exist: set key with TTL, queue build
	- If key exists: skip (build already pending)

	Also invalidates:
	- plan_season_seq cache when the season field changes
	- catalog cache always (Plan Subject child rows like alias_title/notes are
	  saved via the parent plan, and Frappe does not reliably fire child on_update
	  events from editable_grid saves)
	"""
	plan_id = doc.name

	# Always invalidate catalog cache when plan is saved.
	# Plan Subject rows (alias_title, notes) are edited inline via the parent plan form,
	# and Frappe's editable_grid does not reliably fire child doc on_update hooks.
	_invalidate_catalog_cache(plan_id)

	# Only rebuild plan free subjects and notify clients if is_premium actually changed.
	# get_doc_before_save() gives us the previous child table state to compare.
	old_doc = doc.get_doc_before_save()
	if _has_is_premium_changed(old_doc, doc):
		try:
			from memora_admin.events.access_sync import rebuild_plan_free_subjects
			from memora_admin.utils.redis_connection import get_memora_redis

			rebuild_plan_free_subjects(plan_id)
			r = get_memora_redis()
			r.publish(
				cache_invalidation_channel(),
				json.dumps(
					{
						"type": "plan_subjects",
						"plan_id": plan_id,
					}
				),
			)
		except Exception as e:
			frappe.log_error(
				f"Failed to sync plan free subjects for {plan_id}: {e}",
				"Plan Subject Sync Error",
			)

	# Invalidate season_seq cache when plan's season assignment changes
	if doc.has_value_changed("season"):
		try:
			from memora_admin.utils.redis_connection import get_memora_redis

			r = get_memora_redis()
			r.delete(plan_season_seq_key(plan_id))
			frappe.logger().info(f"plan_season_seq cache invalidated for {plan_id}")
		except Exception as e:
			frappe.log_error(
				f"Failed to invalidate plan_season_seq cache for {plan_id}: {e}",
				"Season Cache Invalidation Error",
			)

	cache = frappe.cache
	debounce_key = build_debounce_key(plan_id)

	# Redis SET NX EX pattern for debounce
	timestamp = str(int(time.time()))
	was_set = cache.set(debounce_key, timestamp, nx=True, ex=DEBOUNCE_SECONDS)

	if not was_set:
		return

	# Create Build Queue entry
	try:
		build_queue = frappe.get_doc(
			{
				"doctype": "Memora Build Queue",
				"target_type": "Memora Academic Plan",
				"target_name": plan_id,
				"trigger_reason": "plan_update",
				"triggered_by": frappe.session.user,
				"status": "Pending",
			}
		)
		build_queue.insert(ignore_permissions=True)

	except Exception as e:
		cache.delete_value(debounce_key)
		frappe.log_error(
			f"Failed to queue build for plan {plan_id}: {e}",
			"Build Trigger Error",
		)


def on_plan_subject_changed(doc, method):
	"""
	Queue a build when Plan Subject is added/modified/removed.

	Triggers rebuild of the parent plan AND invalidates both:
	- Hierarchy cache for the affected subject (free_units/free_topics)
	- Catalog cache for the plan (alias_title, notes in products.subjects)

	Plan Subject changes affect both hierarchies (free content metadata) and
	catalog (alias_title, notes displayed in products).
	"""
	plan_id = doc.parent
	subject_id = doc.subject

	if not plan_id:
		return

	# Invalidate both caches immediately when Plan Subject changes.
	# Hierarchy cache: free_units/free_topics derived from Plan Subject meta_data
	# Catalog cache: alias_title/notes in product subjects come from Plan Subject
	if subject_id:
		_invalidate_hierarchy_cache(subject_id)

	_invalidate_catalog_cache(plan_id)

	# Reuse plan debounce logic
	cache = frappe.cache
	debounce_key = build_debounce_key(plan_id)

	timestamp = str(int(time.time()))
	was_set = cache.set(debounce_key, timestamp, nx=True, ex=DEBOUNCE_SECONDS)

	if not was_set:
		frappe.logger().debug(f"Build already pending for plan {plan_id}")
		return

	try:
		build_queue = frappe.get_doc(
			{
				"doctype": "Memora Build Queue",
				"target_type": "Memora Academic Plan",
				"target_name": plan_id,
				"trigger_reason": "plan_subject_change",
				"triggered_by": frappe.session.user,
				"status": "Pending",
			}
		)
		build_queue.insert(ignore_permissions=True)

	except Exception as e:
		cache.delete_value(debounce_key)
		frappe.log_error(
			f"Failed to queue build for plan {plan_id}: {e}",
			"Build Trigger Error",
		)


# =============================================================================
# Plan Deletion Handler
# =============================================================================


def on_plan_deleted(doc, method):
	"""
	Clean up all storage and Redis state when an Academic Plan is deleted.

	1. Cancels any pending/processing builds (prevents orphaned files post-deletion)
	2. Deletes plans/{plan_id}/ directory from storage + CDN
	3. Clears Redis keys: catalog, manifest, free_subjects, build debounce
	4. Publishes cache invalidation to FastAPI sidecar

	Best-effort: all operations are wrapped in try/except and logged on failure.
	"""
	plan_id = doc.name
	_cancel_pending_builds(plan_id)
	_delete_plan_directory(plan_id)
	_delete_plan_redis_keys(plan_id)


def _delete_plan_directory(plan_id: str):
	"""Delete the plans/{plan_id}/ directory from storage and purge from CDN.

	Lists files before deletion so we can issue targeted CDN purge requests.
	Best-effort: errors are logged but never fail the deletion.
	"""
	plan_prefix = f"plans/{plan_id}"
	file_keys: list[str] = []

	try:
		from memora_admin.memora_admin.services.build.storage import get_storage_backend

		storage = get_storage_backend()
		# Capture file list before deletion so CDN purge can reference them
		file_keys = storage.list_directory(plan_prefix)
		deleted = storage.delete_directory(plan_prefix)

		if deleted:
			frappe.logger().info(f"Deleted plan directory: {plan_prefix}/")
		else:
			frappe.logger().debug(f"Plan directory not found (already clean): {plan_prefix}/")
	except Exception as e:
		frappe.log_error(
			f"Failed to delete plan directory {plan_prefix}: {e}",
			"Plan Directory Cleanup Error",
		)

	if not file_keys:
		return

	try:
		from memora_admin.memora_admin.services.cdn.utils import get_purge_service

		purge_service = get_purge_service()
		if purge_service is not None:
			purge_service.purge_files(file_keys)
			frappe.logger().info(
				f"CDN cache purged for deleted plan: {plan_prefix}/ ({len(file_keys)} files)"
			)
	except Exception as e:
		frappe.log_error(
			f"Failed to purge CDN for plan {plan_prefix}: {e}",
			"Plan CDN Purge Error",
		)


def _delete_plan_redis_keys(plan_id: str):
	"""Delete all Redis keys for a deleted plan and publish cache invalidation.

	Best-effort: errors are logged but never fail the deletion.
	"""
	import json

	from memora_admin.utils.redis_connection import get_memora_redis

	try:
		r = get_memora_redis()

		r.delete(
			catalog_key(plan_id),
			plan_manifest_key(plan_id),
			plan_free_subjects_key(plan_id),
			build_debounce_key(plan_id),
		)

		r.publish(
			cache_invalidation_channel(),
			json.dumps({"type": "catalog", "plan_id": plan_id}),
		)

		frappe.logger().info(f"Redis keys cleaned up for deleted plan {plan_id}")
	except Exception as e:
		frappe.log_error(
			f"Failed to clean up Redis keys for deleted plan {plan_id}: {e}",
			"Plan Redis Cleanup Error",
		)


def _has_is_premium_changed(old_doc, doc) -> bool:
	"""Compare is_premium across old and new plan_subjects child rows."""
	if not old_doc:
		return True  # New doc — treat as changed

	old_map = {row.subject: row.is_premium for row in (old_doc.plan_subjects or [])}
	new_map = {row.subject: row.is_premium for row in (doc.plan_subjects or [])}

	return old_map != new_map


def _invalidate_hierarchy_cache(subject_id: str):
	"""Invalidate hierarchy cache for a subject via direct delete + pubsub.

	Two-pronged approach (same pattern as catalog_sync.py):
	1. Direct Redis DEL for immediate effect
	2. Pubsub publish so FastAPI sidecar's in-process HierarchyService also invalidates
	"""
	import json

	from memora_admin.utils.redis_connection import get_memora_redis

	try:
		r = get_memora_redis()

		# 1. Direct cache delete
		r.delete(hierarchy_key(subject_id))

		# 2. Pubsub notification for FastAPI sidecar
		r.publish(
			cache_invalidation_channel(),
			json.dumps(
				{
					"type": "hierarchy",
					"subject_id": subject_id,
					"timestamp": str(frappe.utils.now()),
				}
			),
		)

		frappe.logger().info(f"Hierarchy cache invalidated for subject {subject_id}")
	except Exception as e:
		frappe.log_error(
			f"Failed to invalidate hierarchy cache for {subject_id}: {e}",
			"Hierarchy Cache Invalidation Error",
		)


def _delete_lesson_json(lesson_id: str):
	"""Delete the shared lesson JSON file from storage and purge from CDN.

	Called when a lesson is trashed. The file lessons/{lesson_id}.json is shared
	across plans but becomes truly orphaned when the lesson is deleted.

	Best-effort: errors are logged but never fail the deletion.
	"""
	lesson_key = f"lessons/{lesson_id}.json"

	try:
		from memora_admin.memora_admin.services.build.storage import get_storage_backend

		storage = get_storage_backend()
		deleted = storage.delete(lesson_key)

		if deleted:
			frappe.logger().info(f"Deleted orphaned lesson JSON: {lesson_key}")
		else:
			frappe.logger().debug(f"Lesson JSON not found (already clean): {lesson_key}")
	except Exception as e:
		frappe.log_error(
			f"Failed to delete lesson JSON {lesson_key}: {e}",
			"Lesson JSON Cleanup Error",
		)

	# Also purge from CDN edge cache
	try:
		from memora_admin.memora_admin.services.cdn.utils import get_purge_service

		purge_service = get_purge_service()
		if purge_service is not None:
			purge_service.purge_files([lesson_key])
			frappe.logger().info(f"CDN cache purged for deleted lesson: {lesson_key}")
	except Exception as e:
		frappe.log_error(
			f"Failed to purge CDN for lesson {lesson_key}: {e}",
			"Lesson CDN Purge Error",
		)


def _invalidate_catalog_cache(plan_id: str):
	"""Invalidate catalog cache for a plan via direct delete + pubsub.

	Two-pronged approach (same pattern as catalog_sync.py):
	1. Direct Redis DEL for immediate effect
	2. Pubsub publish so FastAPI sidecar's in-process CatalogService also invalidates

	Called when Plan Subject changes (alias_title, notes, etc.) affect catalog products.
	"""
	import json

	from memora_admin.utils.redis_connection import get_memora_redis

	try:
		r = get_memora_redis()

		# 1. Direct cache delete
		r.delete(catalog_key(plan_id))

		# 2. Pubsub notification for FastAPI sidecar
		r.publish(
			cache_invalidation_channel(),
			json.dumps(
				{
					"type": "catalog",
					"plan_id": plan_id,
					"timestamp": str(frappe.utils.now()),
				}
			),
		)

		frappe.logger().info(f"Catalog cache invalidated for plan {plan_id}")
	except Exception as e:
		frappe.log_error(
			f"Failed to invalidate catalog cache for {plan_id}: {e}",
			"Catalog Cache Invalidation Error",
		)


def on_plan_overrider_changed(doc, method):
	"""
	Queue a build when Plan Overrider is created/modified/deleted.

	Triggers rebuild of the associated plan.
	"""
	plan_id = doc.plan

	if not plan_id:
		frappe.log_error(
			f"Plan Overrider {doc.name} has no plan reference",
			"Build Trigger Error",
		)
		return

	cache = frappe.cache
	debounce_key = build_debounce_key(plan_id)

	timestamp = str(int(time.time()))
	was_set = cache.set(debounce_key, timestamp, nx=True, ex=DEBOUNCE_SECONDS)

	if not was_set:
		frappe.logger().debug(f"Build already pending for plan {plan_id}")
		return

	try:
		build_queue = frappe.get_doc(
			{
				"doctype": "Memora Build Queue",
				"target_type": "Memora Academic Plan",
				"target_name": plan_id,
				"trigger_reason": "plan_overrider_change",
				"triggered_by": frappe.session.user,
				"status": "Pending",
			}
		)
		build_queue.insert(ignore_permissions=True)

	except Exception as e:
		cache.delete_value(debounce_key)
		frappe.log_error(
			f"Failed to queue build for plan {plan_id}: {e}",
			"Build Trigger Error",
		)


# =============================================================================
# GAP 2: Subject Deletion — Stale Plan free_subjects Sets
# =============================================================================


def _cascade_delete_plan_subjects(subject_id: str):
	"""Delete all Plan Subject child rows that reference a deleted Subject.

	Frappe does not cascade-delete child rows when a Link target is deleted.
	Orphaned rows cause two problems:
	  1. Future builds find them and regenerate empty JSON for the deleted subject.
	  2. The 6-hour plan_sync task resurrects memora:plan:{plan}:free_subjects Redis keys.

	Must be called AFTER _queue_plan_builds_for_subject() so builds are queued for the
	right plans before the rows are removed.

	Best-effort: errors are logged but never fail the deletion.
	"""
	try:
		count = frappe.db.count("Memora Plan Subject", {"subject": subject_id})
		if not count:
			return

		frappe.db.delete("Memora Plan Subject", {"subject": subject_id})
		frappe.logger().info(f"Cascade-deleted {count} Plan Subject row(s) for deleted subject {subject_id}")
	except Exception as e:
		frappe.log_error(
			f"Failed to cascade-delete Plan Subject rows for {subject_id}: {e}",
			"Subject Deletion Cleanup Error",
		)


def _cancel_pending_builds(plan_id: str):
	"""Cancel pending and processing builds for a deleted plan.

	Prevents the build worker from running a build for a plan that no longer exists,
	which would generate and upload empty JSON files creating new orphaned artifacts.

	Uses status='Failed' (no 'Cancelled' status in the DocType) with a clear
	error_message so admins know why the build was stopped.

	Best-effort: errors are logged but never fail the deletion.
	"""
	try:
		pending = frappe.get_all(
			"Memora Build Queue",
			filters={"target_name": plan_id, "status": ["in", ["Pending", "Processing"]]},
			fields=["name"],
		)

		if not pending:
			return

		for build in pending:
			frappe.db.set_value(
				"Memora Build Queue",
				build["name"],
				{
					"status": "Failed",
					"error_message": f"Plan {plan_id} was deleted — build cancelled",
				},
			)

		frappe.logger().info(f"Cancelled {len(pending)} pending build(s) for deleted plan {plan_id}")
	except Exception as e:
		frappe.log_error(
			f"Failed to cancel pending builds for plan {plan_id}: {e}",
			"Plan Deletion Cleanup Error",
		)


def _remove_subject_from_plan_free_subjects(subject_id: str):
	"""Remove a directly-deleted subject from all plan free_subjects Redis sets.

	When a Memora Subject is trashed, its ID may linger in
	memora:plan:{plan_id}:free_subjects sets for any plan that had it as free.
	The on_plan_subject_changed handler covers Plan Subject child row deletion
	but NOT direct Subject DocType deletion.

	Best-effort: errors are logged but never fail the deletion.
	"""
	from memora_admin.utils.redis_connection import get_memora_redis

	try:
		plan_subjects = frappe.get_all(
			"Memora Plan Subject",
			filters={"subject": subject_id, "is_premium": 0},
			fields=["parent"],
		)

		if not plan_subjects:
			return

		r = get_memora_redis()
		for ps in plan_subjects:
			plan_id = ps["parent"]
			if plan_id:
				r.srem(plan_free_subjects_key(plan_id), subject_id)
				frappe.logger().info(
					f"Removed deleted subject {subject_id} from plan {plan_id} free_subjects set"
				)
	except Exception as e:
		frappe.log_error(
			f"Failed to remove deleted subject {subject_id} from plan free_subjects: {e}",
			"Subject Deletion Cleanup Error",
		)
