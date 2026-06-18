"""Build trigger handlers for Frappe doc_events.

Queue builds when content DocTypes are updated with 2-minute debounce.
Per plan: prevent build flooding via Redis SET NX EX pattern.
"""

import json
import time

import frappe

from fastapi_app.core.redis_keys import (
	build_debounce_key,
	build_dirty_key,
	cache_invalidation_channel,
	catalog_key,
	ch_question_lookup_key,
	hierarchy_key,
	plan_free_subjects_key,
	plan_manifest_key,
	plan_season_seq_key,
)

# Debounce configuration
DEBOUNCE_SECONDS = 120  # 2 minutes per plan
DIRTY_FLAG_TTL_SECONDS = 86400  # 24 hours — must outlive worst-case worker stall


# =============================================================================
# Post-Commit Build Queue Helper
# =============================================================================
#
# Why post-commit?
# ----------------
# Frappe doc-event hooks (on_update, on_trash, ...) run INSIDE the doc's
# transaction, BEFORE the row is committed. If we queued the build directly
# from the hook body, two race windows opened:
#
#   1. The build worker could pick up the queued build and read the DB BEFORE
#      our hook's commit landed — producing stale output.
#   2. If our nx_set on the debounce key failed (because another build was
#      already in-flight) and our commit then landed AFTER that worker's DB
#      read, our change was silently dropped.
#
# Deferring the nx_set + Build Queue insert to `frappe.db.after_commit`
# guarantees: (a) by the time the worker's next DB read happens, our row is
# committed, and (b) if nx_set fails the dirty flag we set is observed by the
# worker's post-build re-check. No content event can be lost.
#
# Why the dirty flag in addition to nx_set?
# -----------------------------------------
# nx_set succeeds in the common case (worker has cleared the debounce key by
# the time our after_commit fires). When it fails — meaning a concurrent build
# is already queued — we set `build_dirty_key(plan_id)`. The worker clears
# this flag at build start and re-checks it after publishing; if it's set, the
# worker queues a follow-up build to capture whatever change tripped it.
# =============================================================================


def _schedule_post_commit_build(plan_id: str, trigger_reason: str, doc_info: str | None = None) -> None:
	"""Register an after-commit callback that queues a build for the plan.

	The callback runs once the triggering transaction commits, so:
	- The doc edit is visible to subsequent DB reads (incl. the build worker's).
	- nx_set on the debounce key reflects the real "is a build in flight" state.

	If nx_set fails (another build is already queued), we set the plan's dirty
	flag instead of dropping the event. The worker re-checks this flag after
	finishing its build and queues a follow-up rebuild if needed.

	Idempotent per plan_id within a single request via `frappe.flags`.
	"""
	flag_key = f"_build_after_commit_{plan_id}"
	if frappe.flags.get(flag_key):
		return  # already scheduled in this request — coalesce
	frappe.flags[flag_key] = True

	def _queue_now():
		_queue_plan_build_now(plan_id, trigger_reason, doc_info)

	try:
		frappe.db.after_commit.add(_queue_now)
	except Exception as e:
		frappe.log_error(
			f"Failed to register after_commit build callback for {plan_id}: {e}",
			"Build Trigger Error",
		)


def _queue_plan_build_now(plan_id: str, trigger_reason: str, doc_info: str | None = None) -> None:
	"""Run the nx_set debounce + Build Queue insert. Call only after commit."""
	cache = frappe.cache
	debounce_key = build_debounce_key(plan_id)
	timestamp = str(int(time.time()))

	was_set = cache.set(debounce_key, timestamp, nx=True, ex=DEBOUNCE_SECONDS)

	if not was_set:
		# A build is already queued/processing for this plan. Mark the plan
		# dirty so the worker re-checks at the end of its current build and
		# queues a follow-up — guarantees no event is silently dropped.
		try:
			cache.set(build_dirty_key(plan_id), "1", ex=DIRTY_FLAG_TTL_SECONDS)
			frappe.logger().debug(
				f"Build already pending for plan {plan_id}; dirty flag set"
				+ (f" (triggered by {doc_info})" if doc_info else "")
			)
		except Exception as e:
			frappe.log_error(
				f"Failed to set dirty flag for plan {plan_id}: {e}",
				"Build Trigger Error",
			)
		return

	try:
		build_queue = frappe.get_doc(
			{
				"doctype": "Memora Build Queue",
				"target_type": "Memora Academic Plan",
				"target_name": plan_id,
				"trigger_reason": trigger_reason,
				"triggered_by": frappe.session.user,
				"status": "Pending",
			}
		)
		build_queue.insert(ignore_permissions=True)
		frappe.db.commit()  # ensure the queue record is durable for the worker
		frappe.logger().info(
			f"Build queued: {build_queue.name} for plan {plan_id}"
			+ (f" (triggered by {doc_info})" if doc_info else "")
		)
	except Exception as e:
		# Use raw `delete` (not delete_value) so the namespace matches the raw
		# `cache.set(..., nx=True)` above — `delete_value` applies a db_name
		# prefix that `set(nx=True)` does not, causing it to silently no-op.
		try:
			cache.delete(debounce_key)
		except Exception:
			pass
		frappe.log_error(
			f"Failed to queue build for plan {plan_id}: {e}",
			"Build Trigger Error",
		)


# =============================================================================
# Content Update Handler
# =============================================================================


def on_content_updated(doc, method):
	"""
	Handle content DocType updates: invalidate hierarchy cache and queue plan builds.

	1. Immediately invalidates hierarchy cache (direct DEL + pubsub)
	2. Queues plan builds for all plans containing the subject (with debounce)
	3. On lesson trash: also deletes the shared lesson JSON file from storage + CDN
	4. On topic trash: also deletes the challenge question JSON file from storage + CDN

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

	# Schedule a second invalidation AFTER commit.  The immediate DEL above runs
	# before the transaction commits, so a concurrent FastAPI request can refill
	# the cache with pre-commit (stale) data.  Re-invalidating post-commit
	# closes this race window.  Idempotent — DEL on a missing key is a no-op.
	_schedule_post_commit_hierarchy_invalidation(subject_id)

	# When a lesson is deleted, its shared JSON file (lessons/{lesson_id}.json) is orphaned
	# since no plan will reference it anymore. Delete it directly from storage + CDN.
	if doc.doctype == "Memora Lesson" and method == "on_trash":
		_delete_lesson_json(doc.name)

	# When a topic is deleted, its challenge question JSON (challenges/{topic_id}.json)
	# is orphaned. Individual lesson trashes trigger Review Item cleanup which can
	# rebuild/delete the file, but direct topic trash skips that path.
	if doc.doctype == "Memora Topic" and method == "on_trash":
		_delete_topic_challenge_json(doc.name)
		_evict_topic_challenge_cache(doc.name)

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

	Queue insertion is deferred to after-commit so the worker's next DB read
	always sees the triggering edit. See _schedule_post_commit_build().
	"""
	plan_subjects = frappe.get_all(
		"Memora Plan Subject",
		filters={"subject": subject_id},
		fields=["parent"],
	)

	if not plan_subjects:
		return

	doc_info = f"{doc.doctype} {doc.name} in subject {subject_id}"
	for ps in plan_subjects:
		plan_id = ps["parent"]
		if not plan_id:
			continue
		_schedule_post_commit_build(plan_id, "content_update", doc_info)


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

	# Skip queueing a build when this save originated from the build pipeline
	# itself. plan_generator persists Plan Subject meta_data via plan_doc.save();
	# without this guard that save re-triggers the build every cycle — an
	# infinite one-build-per-minute loop.
	if doc.flags.get("ignore_build_trigger"):
		return

	_schedule_post_commit_build(plan_id, "plan_update", f"plan {plan_id}")


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
		_schedule_post_commit_hierarchy_invalidation(subject_id)

	_invalidate_catalog_cache(plan_id)

	_schedule_post_commit_build(
		plan_id, "plan_subject_change", f"plan_subject change on {plan_id}"
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


def _schedule_post_commit_hierarchy_invalidation(subject_id: str):
	"""Re-invalidate hierarchy cache after the current transaction commits.

	Doc-event hooks fire before commit, so a concurrent request can refill
	the cache with stale pre-commit data.  This schedules a lightweight
	background job (via ``enqueue_after_commit``) that re-DELs the key once
	the data is visible to all transactions.

	Deduplicates per subject per request via ``frappe.flags``.
	"""
	flag_key = f"_hierarchy_post_commit_{subject_id}"
	if frappe.flags.get(flag_key):
		return  # Already scheduled for this subject in this request
	frappe.flags[flag_key] = True

	try:
		frappe.enqueue(
			"memora_admin.events.build_trigger._invalidate_hierarchy_cache",
			subject_id=subject_id,
			queue="short",
			enqueue_after_commit=True,
		)
	except Exception as e:
		frappe.log_error(
			f"Failed to schedule post-commit hierarchy invalidation for {subject_id}: {e}",
			"Post-Commit Invalidation Error",
		)


def _delete_lesson_json(lesson_id: str):
	"""Delete orphaned lesson JSON from storage + CDN when lesson is trashed."""
	_delete_and_purge(f"lessons/{lesson_id}.json", "lesson")


def _delete_topic_challenge_json(topic_id: str):
	"""Delete orphaned challenge JSON from storage + CDN when topic is trashed."""
	_delete_and_purge(f"challenges/{topic_id}.json", "challenge")


def _delete_and_purge(file_key: str, label: str):
	"""Delete a file from storage and purge from CDN edge cache.

	Best-effort: errors are logged but never fail the caller.
	"""
	try:
		from memora_admin.memora_admin.services.build.storage import get_storage_backend

		storage = get_storage_backend()
		deleted = storage.delete(file_key)

		if deleted:
			frappe.logger().info(f"Deleted orphaned {label} JSON: {file_key}")
		else:
			frappe.logger().debug(f"{label.capitalize()} JSON not found (already clean): {file_key}")
	except Exception as e:
		frappe.log_error(
			f"Failed to delete {label} JSON {file_key}: {e}",
			f"{label.capitalize()} JSON Cleanup Error",
		)

	try:
		from memora_admin.memora_admin.services.cdn.utils import get_purge_service

		purge_service = get_purge_service()
		if purge_service is not None:
			purge_service.purge_files([file_key])
			frappe.logger().info(f"CDN cache purged for deleted {label}: {file_key}")
	except Exception as e:
		frappe.log_error(
			f"Failed to purge CDN for {label} {file_key}: {e}",
			f"{label.capitalize()} CDN Purge Error",
		)


def _evict_topic_challenge_cache(topic_id: str):
	"""Evict the question lookup cache so in-flight grading can't use stale data.

	The key has a 300s TTL and would self-expire, but explicit eviction closes
	the window where a student could submit a challenge against a deleted topic.

	Best-effort: errors are logged but never fail the deletion.
	"""
	try:
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()
		r.delete(ch_question_lookup_key(topic_id))
	except Exception as e:
		frappe.log_error(
			f"Failed to evict challenge cache for topic {topic_id}: {e}",
			"Challenge Cache Eviction Error",
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

	_schedule_post_commit_build(
		plan_id, "plan_overrider_change", f"Plan Overrider {doc.name}"
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


# =============================================================================
# Challenge Hub: Question File Rebuild Trigger
# =============================================================================

# Debounce window: 10 seconds — batches rapid edits to the same topic.
_CHALLENGE_DEBOUNCE_SECONDS = 10


def on_review_item_changed(doc, method):
	"""Rebuild challenge question file when a Review Item changes (debounced).

	Registered as doc_event on Memora Review Item (after_insert, on_update, on_trash).
	Uses Redis SET NX EX debounce + frappe.enqueue to batch rapid edits
	during bulk imports into a single background rebuild per topic.
	"""
	if not doc.topic:
		return

	_debounced_challenge_rebuild(doc.topic, doc.name, method)


def _debounced_challenge_rebuild(topic_id: str, item_id: str, method: str) -> None:
	"""Queue a challenge question file rebuild with debounce.

	Uses Redis SET NX EX pattern — identical to practice_content_trigger.py.
	"""
	from memora_admin.utils.redis_connection import get_memora_redis

	try:
		r = get_memora_redis()
	except Exception as e:
		frappe.log_error(
			title="Challenge Question Trigger: Redis Unavailable",
			message=f"item={item_id} method={method}: {e}",
		)
		return

	from fastapi_app.core.redis_keys import challenge_question_debounce_key

	debounce_key = challenge_question_debounce_key(topic_id)
	was_set = r.set(debounce_key, str(int(time.time())), nx=True, ex=_CHALLENGE_DEBOUNCE_SECONDS)

	if not was_set:
		return

	try:
		frappe.enqueue(
			"memora_admin.memora_admin.services.build.challenge_questions.rebuild_topic_question_file",
			topic_id=topic_id,
			queue="short",
			enqueue_after_commit=True,
		)
	except Exception as e:
		r.delete(debounce_key)
		frappe.log_error(
			title="Challenge Question Trigger: Enqueue Failed",
			message=f"topic={topic_id} item={item_id}: {e}",
		)
