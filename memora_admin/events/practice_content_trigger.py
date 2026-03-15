"""Event handlers for triggering practice content regeneration on Review Item changes.

Registered as doc_events on Memora Review Item in hooks.py.

Uses Redis SET NX EX debounce (10 s window per subject) to batch rapid edits
before calling ``regenerate_for_subject``.  The actual regeneration runs inline
inside the Frappe web worker (same transaction context), which is acceptable
because a full-subject regeneration takes ~1-3 s for 10 K questions.

See research.md R8 for the debounce pattern rationale.
"""

from __future__ import annotations

import logging
import time

import frappe

from fastapi_app.core.redis_keys import practice_content_debounce_key

logger = logging.getLogger(__name__)

# Debounce window: 10 seconds — batches rapid edits to the same subject.
_DEBOUNCE_SECONDS = 10


# =============================================================================
# Doc Event Handlers (registered in hooks.py)
# =============================================================================


def on_review_item_changed(doc, method):
	"""Handle Review Item on_update and after_insert.

	Triggers a debounced practice content regeneration for the item's subject.
	"""
	subject_id = doc.subject
	if not subject_id:
		return

	_debounced_regenerate(subject_id, doc.name, method)


def on_review_item_deleted(doc, method):
	"""Handle Review Item on_trash.

	Triggers a debounced practice content regeneration for the item's subject.
	The item is still in the DB at this point (on_trash fires before deletion),
	but will be absent by the time the deferred regeneration runs.  Since we
	debounce by 10 s, the deletion will have committed before the next
	regeneration window opens.
	"""
	subject_id = doc.subject
	if not subject_id:
		return

	_debounced_regenerate(subject_id, doc.name, method)


# =============================================================================
# Debounced Regeneration
# =============================================================================


def _debounced_regenerate(subject_id: str, item_id: str, method: str) -> None:
	"""Queue a practice content regeneration with debounce.

	Uses Redis SET NX EX pattern — identical to ``build_trigger.py``:
	  - If the debounce key does NOT exist → set it (NX) with 10 s TTL (EX),
	    then enqueue the regeneration via ``frappe.enqueue``.
	  - If the key already exists → another regeneration is already pending;
	    skip silently.

	The regeneration is enqueued as a background job so it does not block the
	current save transaction.
	"""
	from memora_admin.utils.redis_connection import get_memora_redis

	try:
		r = get_memora_redis()
	except Exception as e:
		frappe.log_error(
			title="Practice Content Trigger: Redis Unavailable",
			message=f"item={item_id} method={method}: {e}",
		)
		return

	debounce_key = practice_content_debounce_key(subject_id)
	timestamp = str(int(time.time()))

	was_set = r.set(debounce_key, timestamp, nx=True, ex=_DEBOUNCE_SECONDS)

	if not was_set:
		# Regeneration already pending for this subject — skip
		return

	try:
		frappe.enqueue(
			"memora_admin.memora_admin.services.build.practice_content.regenerate_for_subject",
			subject_id=subject_id,
			queue="short",
			enqueue_after_commit=True,
		)
		logger.info(
			"Practice content regeneration enqueued for subject %s (triggered by %s %s)",
			subject_id,
			method,
			item_id,
		)
	except Exception as e:
		# Clean up debounce key so the next event can retry
		r.delete(debounce_key)
		frappe.log_error(
			title="Practice Content Trigger: Enqueue Failed",
			message=f"subject={subject_id} item={item_id}: {e}",
		)
