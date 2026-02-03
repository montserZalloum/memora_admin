"""Build trigger handlers for Frappe doc_events.

Queue builds when content DocTypes are updated with 2-minute debounce.
Per plan: prevent build flooding via Redis SET NX EX pattern.
"""

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
	Queue a build when content DocType is updated.

	Uses Redis SET NX EX pattern for debounce:
	- If key doesn't exist: set key with TTL, queue build
	- If key exists: skip (build already pending)

	Handles: Memora Subject, Track, Unit, Topic, Lesson
	"""
	subject_id = _get_subject_id(doc)

	if not subject_id:
		frappe.log_error(
			f"Could not determine subject for {doc.doctype} {doc.name}",
			"Build Trigger Error",
		)
		return

	cache = frappe.cache
	debounce_key = f"{DEBOUNCE_KEY_PREFIX}{subject_id}"

	# Redis SET NX EX pattern for debounce
	# Returns True if key was set (no existing key), None/False if key existed
	timestamp = str(int(time.time()))
	was_set = cache.set(debounce_key, timestamp, nx=True, ex=DEBOUNCE_SECONDS)

	if not was_set:
		frappe.logger().debug(f"Build already pending for subject {subject_id}")
		return

	# Create Build Queue entry
	try:
		build_queue = frappe.get_doc(
			{
				"doctype": "Memora Build Queue",
				"target_type": "Memora Subject",
				"target_name": subject_id,
				"trigger_reason": "content_update",
				"triggered_by": frappe.session.user,
				"status": "Pending",
			}
		)
		build_queue.insert(ignore_permissions=True)

		frappe.logger().info(
			f"Build queued: {build_queue.name} for subject {subject_id} "
			f"(triggered by {doc.doctype} {doc.name})"
		)
	except Exception as e:
		# Clear debounce key if queue entry failed so retry is possible
		cache.delete_value(debounce_key)
		frappe.log_error(
			f"Failed to queue build for subject {subject_id}: {e}",
			"Build Trigger Error",
		)


# =============================================================================
# Helper Functions
# =============================================================================


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
	"""
	plan_id = doc.name

	cache = frappe.cache
	debounce_key = f"{DEBOUNCE_KEY_PREFIX}plan:{plan_id}"

	# Redis SET NX EX pattern for debounce
	timestamp = str(int(time.time()))
	was_set = cache.set(debounce_key, timestamp, nx=True, ex=DEBOUNCE_SECONDS)

	if not was_set:
		frappe.logger().debug(f"Build already pending for plan {plan_id}")
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

		frappe.logger().info(
			f"Build queued: {build_queue.name} for plan {plan_id} "
			f"(triggered by {doc.doctype} {doc.name})"
		)
	except Exception as e:
		cache.delete_value(debounce_key)
		frappe.log_error(
			f"Failed to queue build for plan {plan_id}: {e}",
			"Build Trigger Error",
		)


def on_plan_subject_changed(doc, method):
	"""
	Queue a build when Plan Subject is added/modified/removed.

	Triggers rebuild of the parent plan.
	"""
	plan_id = doc.parent

	if not plan_id:
		frappe.log_error(
			f"Plan Subject {doc.name} has no parent plan",
			"Build Trigger Error",
		)
		return

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

		frappe.logger().info(
			f"Build queued: {build_queue.name} for plan {plan_id} "
			f"(triggered by Plan Subject change)"
		)
	except Exception as e:
		cache.delete_value(debounce_key)
		frappe.log_error(
			f"Failed to queue build for plan {plan_id}: {e}",
			"Build Trigger Error",
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

		frappe.logger().info(
			f"Build queued: {build_queue.name} for plan {plan_id} "
			f"(triggered by Plan Overrider {doc.name})"
		)
	except Exception as e:
		cache.delete_value(debounce_key)
		frappe.log_error(
			f"Failed to queue build for plan {plan_id}: {e}",
			"Build Trigger Error",
		)
