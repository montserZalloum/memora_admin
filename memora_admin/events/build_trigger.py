"""Build trigger handlers for Frappe doc_events.

Queue builds when content DocTypes are updated with 2-minute debounce.
Per plan: prevent build flooding via Redis SET NX EX pattern.
"""

import json
import time

import frappe

# Debounce configuration
DEBOUNCE_SECONDS = 120  # 2 minutes per plan
DEBOUNCE_KEY_PREFIX = "memora:build:pending:"


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

	# Trigger builds for all plans that contain this subject
	_queue_plan_builds_for_subject(subject_id, doc)


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

		debounce_key = f"{DEBOUNCE_KEY_PREFIX}plan:{plan_id}"

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
			from memora_admin.events.access_sync import get_fastapi_redis, rebuild_plan_free_subjects

			rebuild_plan_free_subjects(plan_id)
			r = get_fastapi_redis()
			r.publish("memora:cache:invalidate", json.dumps({
				"type": "plan_subjects",
				"plan_id": plan_id,
			}))
		except Exception as e:
			frappe.log_error(
				f"Failed to sync plan free subjects for {plan_id}: {e}",
				"Plan Subject Sync Error",
			)

	# Invalidate season_seq cache when plan's season assignment changes
	if doc.has_value_changed("season"):
		try:
			from memora_admin.events.access_sync import get_fastapi_redis

			r = get_fastapi_redis()
			r.delete(f"memora:plan_season_seq:{plan_id}")
			frappe.logger().info(f"plan_season_seq cache invalidated for {plan_id}")
		except Exception as e:
			frappe.log_error(
				f"Failed to invalidate plan_season_seq cache for {plan_id}: {e}",
				"Season Cache Invalidation Error",
			)

	cache = frappe.cache
	debounce_key = f"{DEBOUNCE_KEY_PREFIX}plan:{plan_id}"

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
	debounce_key = f"{DEBOUNCE_KEY_PREFIX}plan:{plan_id}"

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

	from memora_admin.events.access_sync import get_fastapi_redis

	try:
		r = get_fastapi_redis()

		# 1. Direct cache delete
		r.delete(f"memora:hierarchy:{subject_id}")

		# 2. Pubsub notification for FastAPI sidecar
		r.publish(
			"memora:cache:invalidate",
			json.dumps({
				"type": "hierarchy",
				"subject_id": subject_id,
				"timestamp": str(frappe.utils.now()),
			}),
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

	from memora_admin.events.access_sync import get_fastapi_redis

	try:
		r = get_fastapi_redis()

		# 1. Direct cache delete
		r.delete(f"memora:catalog:{plan_id}")

		# 2. Pubsub notification for FastAPI sidecar
		r.publish(
			"memora:cache:invalidate",
			json.dumps({
				"type": "catalog",
				"plan_id": plan_id,
				"timestamp": str(frappe.utils.now()),
			}),
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
	debounce_key = f"{DEBOUNCE_KEY_PREFIX}plan:{plan_id}"

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
