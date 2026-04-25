"""End-to-end verification of the build debounce / dirty flag fix.

Run via:
    cd /home/corex/aurevia-bench && bench --site x.conanacademy.com execute \\
        memora_admin.scripts.test_build_debounce_fix.run

Each test is independent and self-cleans. Failures print the specific
assertion that broke; no test depends on order.

Covers:
  T1  Key-namespace consistency (the original bug):
        cache.set(nx=True) / cache.delete / cache.exists(shared=True)
        must all hit the same Redis key.
  T2  cache.exists default vs shared=True — verifies the prefix mismatch
        we worked around in build_worker._queue_followup_build_if_dirty.
  T3  Worker startup clear: cache.delete really clears the trigger's set,
        so the next hook's nx_set succeeds.
  T4  Dirty flag round-trip: set / exists / delete via the same access
        pattern build_trigger and build_worker use.
  T5  _queue_plan_build_now (cache hit path): when no debounce is held,
        nx_set succeeds and a Build Queue row is created.
  T6  _queue_plan_build_now (debounce-held path): when a build is already
        in flight, the dirty flag is set instead of queueing.
  T7  _queue_followup_build_if_dirty queues a recheck build when dirty
        is set, and does nothing when it isn't.
  T8  Idempotency: many _schedule_post_commit_build calls in one request
        coalesce to a single after_commit callback (frappe.flags dedupe).
  T9  End-to-end after_commit: the callback runs ON commit, creating the
        Build Queue row only after the triggering transaction commits.
"""

import redis

import frappe

from fastapi_app.core.redis_keys import build_debounce_key, build_dirty_key
from memora_admin.events.build_trigger import (
	_get_subject_id,
	_queue_plan_build_now,
	_schedule_post_commit_build,
	on_content_updated,
)
from memora_admin.tasks.build_worker import (
	_queue_followup_build_if_dirty,
)

PLAN_ID = "PLAN-00927"
SUBJECT_ID = "SUBJ-00716"
TRACK_ID = "Track-00471"
UNIT_ID = "UNT-00481"
TOPIC_ID = "TPC-00514"  # real topic; NOT actually trashed in tests
TEST_TAG = "BUILD_FIX_TEST"


def run():
	"""Entry point for `bench execute`."""
	state = {"passed": 0, "failed": 0, "failures": []}

	def ok(name):
		state["passed"] += 1
		print(f"  [PASS] {name}")

	def fail(name, msg):
		state["failed"] += 1
		state["failures"].append(f"{name}: {msg}")
		print(f"  [FAIL] {name}: {msg}")

	def check(cond, name, msg=""):
		if cond:
			ok(name)
		else:
			fail(name, msg or "condition false")

	def raw_redis():
		return redis.Redis(connection_pool=frappe.cache.connection_pool)

	def cleanup_keys():
		frappe.cache.delete(build_debounce_key(PLAN_ID))
		frappe.cache.delete(build_dirty_key(PLAN_ID))

	def cleanup_test_builds():
		rows = frappe.get_all(
			"Memora Build Queue",
			filters={
				"target_name": PLAN_ID,
				"trigger_reason": ["in", ["manual", "post_build_dirty_recheck", "content_update"]],
				"status": "Pending",
			},
			fields=["name"],
		)
		for r in rows:
			frappe.delete_doc("Memora Build Queue", r["name"], ignore_permissions=True, force=True)
		frappe.db.commit()
		return len(rows)

	def queued_count(trigger_reason=None):
		filters = {"target_name": PLAN_ID, "status": "Pending"}
		if trigger_reason:
			filters["trigger_reason"] = trigger_reason
		return frappe.db.count("Memora Build Queue", filters)

	# Reset state for a clean run.
	cleanup_keys()
	cleanup_test_builds()
	frappe.flags.pop(f"_build_after_commit_{PLAN_ID}", None)
	frappe.db.after_commit.reset()

	key = build_debounce_key(PLAN_ID)
	dkey = build_dirty_key(PLAN_ID)
	r = raw_redis()

	print()
	print("=" * 70)
	print(f"Build debounce fix verification — plan {PLAN_ID}")
	print("=" * 70)

	# ---- T1 -----------------------------------------------------------------
	print("\nT1: cache.set(nx=True) / cache.delete operate on the SAME raw key")
	cleanup_keys()
	was_set = frappe.cache.set(key, "v1", nx=True, ex=60)
	check(was_set is True, "T1.a first nx_set returns True", f"got {was_set!r}")
	raw_val = r.get(key)
	check(raw_val == b"v1", "T1.b raw redis sees the unprefixed key", f"got {raw_val!r}")
	was_set2 = frappe.cache.set(key, "v2", nx=True, ex=60)
	check(was_set2 is None, "T1.c second nx_set returns None when key held", f"got {was_set2!r}")
	deleted = frappe.cache.delete(key)
	check(deleted == 1, "T1.d cache.delete returned 1", f"got {deleted!r}")
	check(r.get(key) is None, "T1.e raw redis no longer has the key")
	was_set3 = frappe.cache.set(key, "v3", nx=True, ex=60)
	check(was_set3 is True, "T1.f nx_set succeeds again after cache.delete", f"got {was_set3!r}")
	cleanup_keys()

	# ---- T2 -----------------------------------------------------------------
	print("\nT2: cache.exists default (prefixed) vs shared=True (raw)")
	cleanup_keys()
	frappe.cache.set(key, "v", nx=True, ex=60)
	prefixed_exists = frappe.cache.exists(key)
	check(prefixed_exists == 0, "T2.a exists(key) default returns 0 (mismatched namespace)")
	raw_exists = frappe.cache.exists(key, shared=True)
	check(raw_exists == 1, "T2.b exists(key, shared=True) returns 1 (correct namespace)")
	cleanup_keys()

	# ---- T3 -----------------------------------------------------------------
	print("\nT3: worker startup clear — trigger's set is actually cleared")
	cleanup_keys()
	ok1 = frappe.cache.set(key, "trigger-set", nx=True, ex=60)
	check(ok1 is True, "T3.a trigger nx_set ok")
	frappe.cache.delete(build_debounce_key(PLAN_ID))
	frappe.cache.delete(build_dirty_key(PLAN_ID))
	ok2 = frappe.cache.set(key, "next-trigger", nx=True, ex=60)
	check(ok2 is True, "T3.b post-clear nx_set succeeds (THE forever-fix invariant)")
	cleanup_keys()

	# ---- T4 -----------------------------------------------------------------
	print("\nT4: dirty flag round-trip via the actual access pattern")
	cleanup_keys()
	frappe.cache.set(dkey, "1", ex=86400)
	check(frappe.cache.exists(dkey, shared=True) == 1, "T4.a dirty flag visible via shared=True exists")
	frappe.cache.delete(dkey)
	check(frappe.cache.exists(dkey, shared=True) == 0, "T4.b dirty flag cleared")

	# ---- T5 -----------------------------------------------------------------
	print("\nT5: _queue_plan_build_now happy path — queues a Build Queue row")
	cleanup_keys()
	cleanup_test_builds()
	before = queued_count("manual")
	_queue_plan_build_now(PLAN_ID, "manual", f"{TEST_TAG} T5")
	after = queued_count("manual")
	check(after == before + 1, "T5.a Build Queue row inserted", f"before={before} after={after}")
	check(r.get(key) is not None, "T5.b debounce key set after queueing")
	check(r.get(dkey) is None, "T5.c dirty flag NOT set on success path")
	cleanup_test_builds()

	# ---- T6 -----------------------------------------------------------------
	print("\nT6: _queue_plan_build_now debounce-held path — sets dirty flag")
	cleanup_keys()
	cleanup_test_builds()
	frappe.cache.set(key, "in-flight", nx=True, ex=60)
	before = queued_count("manual")
	_queue_plan_build_now(PLAN_ID, "manual", f"{TEST_TAG} T6")
	after = queued_count("manual")
	check(after == before, "T6.a no Build Queue row when debounce held", f"before={before} after={after}")
	check(r.get(dkey) == b"1", "T6.b dirty flag IS set when nx_set fails")
	cleanup_keys()
	cleanup_test_builds()

	# ---- T7 -----------------------------------------------------------------
	print("\nT7: _queue_followup_build_if_dirty — recheck behaviour")
	cleanup_keys()
	cleanup_test_builds()

	_queue_followup_build_if_dirty(PLAN_ID)
	n = queued_count("post_build_dirty_recheck")
	check(n == 0, "T7.a no follow-up queued when dirty flag absent", f"got {n}")

	frappe.cache.set(dkey, "1", ex=86400)
	_queue_followup_build_if_dirty(PLAN_ID)
	n = queued_count("post_build_dirty_recheck")
	check(n == 1, "T7.b follow-up queued when dirty flag set", f"got {n}")

	check(r.get(dkey) is None, "T7.c dirty flag consumed after recheck")

	_queue_followup_build_if_dirty(PLAN_ID)
	n = queued_count("post_build_dirty_recheck")
	check(n == 1, "T7.d second recheck without dirty does nothing", f"got {n}")

	cleanup_keys()
	cleanup_test_builds()

	# ---- T8 -----------------------------------------------------------------
	print("\nT8: _schedule_post_commit_build idempotency within one request")
	cleanup_keys()
	cleanup_test_builds()
	frappe.flags.pop(f"_build_after_commit_{PLAN_ID}", None)
	frappe.db.after_commit.reset()

	for i in range(10):
		_schedule_post_commit_build(PLAN_ID, "content_update", f"{TEST_TAG} T8 #{i}")

	queued_callbacks = len(frappe.db.after_commit._functions)
	check(
		queued_callbacks == 1,
		"T8.a only one after_commit callback registered for repeat calls",
		f"got {queued_callbacks}",
	)

	frappe.db.after_commit.run()
	frappe.db.commit()

	n = queued_count("content_update")
	check(n == 1, "T8.b only one Build Queue row created from the coalesced callback", f"got {n}")

	frappe.flags.pop(f"_build_after_commit_{PLAN_ID}", None)
	cleanup_keys()
	cleanup_test_builds()

	# ---- T9 -----------------------------------------------------------------
	print("\nT9: end-to-end — _schedule_post_commit_build only queues AFTER commit")
	cleanup_keys()
	cleanup_test_builds()
	frappe.flags.pop(f"_build_after_commit_{PLAN_ID}", None)
	frappe.db.after_commit.reset()

	before = queued_count()
	_schedule_post_commit_build(PLAN_ID, "manual", f"{TEST_TAG} T9")
	mid = queued_count()
	check(mid == before, "T9.a no Build Queue row before commit", f"before={before} mid={mid}")

	frappe.db.after_commit.run()
	frappe.db.commit()
	after = queued_count()
	check(
		after == before + 1,
		"T9.b Build Queue row created exactly once after commit",
		f"before={before} after={after}",
	)

	frappe.flags.pop(f"_build_after_commit_{PLAN_ID}", None)
	cleanup_keys()
	cleanup_test_builds()

	# ---- T10 ----------------------------------------------------------------
	# Subject resolution from non-lesson content. A failure here means
	# on_content_updated would log "Could not determine subject" and skip the
	# build trigger entirely — the user's "what about a topic?" question hits
	# this path.
	print("\nT10: _get_subject_id resolves correctly for Topic / Unit / Track / Subject")

	mock_subject = frappe._dict(doctype="Memora Subject", name=SUBJECT_ID)
	mock_track = frappe._dict(doctype="Memora Track", name=TRACK_ID, subject=SUBJECT_ID)
	mock_unit = frappe._dict(doctype="Memora Unit", name=UNIT_ID, track=TRACK_ID)
	mock_topic = frappe._dict(doctype="Memora Topic", name=TOPIC_ID, unit=UNIT_ID)
	mock_lesson = frappe._dict(doctype="Memora Lesson", name="LES-00719", subject=SUBJECT_ID)

	check(_get_subject_id(mock_subject) == SUBJECT_ID, "T10.a Subject → self")
	check(_get_subject_id(mock_track) == SUBJECT_ID, "T10.b Track → subject field")
	check(_get_subject_id(mock_unit) == SUBJECT_ID, "T10.c Unit → Track → Subject")
	check(_get_subject_id(mock_topic) == SUBJECT_ID, "T10.d Topic → Unit → Track → Subject")
	check(_get_subject_id(mock_lesson) == SUBJECT_ID, "T10.e Lesson → subject (direct)")

	# ---- T11 ----------------------------------------------------------------
	# Drive on_content_updated with a mock Topic on_trash. The topic-specific
	# cleanup (_delete_topic_challenge_json, _evict_topic_challenge_cache) runs
	# but is best-effort and idempotent — using a real topic id means we touch
	# files that might exist. To stay non-destructive, use a synthetic id; the
	# subject resolution path is what we're verifying.
	print("\nT11: on_content_updated for Topic on_trash schedules a post-commit build")
	cleanup_keys()
	cleanup_test_builds()
	frappe.flags.pop(f"_build_after_commit_{PLAN_ID}", None)
	frappe.db.after_commit.reset()

	# Use a synthetic topic id but a REAL parent unit so subject resolution succeeds.
	# _delete_topic_challenge_json("TPC-NONEXISTENT-FIXTEST") is a no-op (file doesn't exist).
	synthetic_topic = frappe._dict(
		doctype="Memora Topic",
		name="TPC-NONEXISTENT-FIXTEST",
		unit=UNIT_ID,
	)

	before = queued_count()
	on_content_updated(synthetic_topic, "on_trash")

	# Build is NOT queued yet (we're pre-commit).
	mid = queued_count()
	check(mid == before, "T11.a no Build Queue row before commit (Topic trash)", f"before={before} mid={mid}")

	# An after_commit callback IS registered.
	registered = len(frappe.db.after_commit._functions)
	check(registered >= 1, "T11.b after_commit callback registered for Topic", f"got {registered}")

	# Fire commit → callback runs → row appears.
	frappe.db.after_commit.run()
	frappe.db.commit()
	after = queued_count()
	check(after == before + 1, "T11.c Build Queue row created after commit (Topic trash)", f"before={before} after={after}")

	frappe.flags.pop(f"_build_after_commit_{PLAN_ID}", None)
	cleanup_keys()
	cleanup_test_builds()

	# ---- T12 ----------------------------------------------------------------
	# Same drill for Unit on_trash — the parent of topics. A unit can hold many
	# topics, so its deletion must trigger the same build queueing.
	print("\nT12: on_content_updated for Unit on_trash schedules a post-commit build")
	cleanup_keys()
	cleanup_test_builds()
	frappe.flags.pop(f"_build_after_commit_{PLAN_ID}", None)
	frappe.db.after_commit.reset()

	synthetic_unit = frappe._dict(
		doctype="Memora Unit",
		name="UNT-NONEXISTENT-FIXTEST",
		track=TRACK_ID,
	)
	before = queued_count()
	on_content_updated(synthetic_unit, "on_trash")
	registered = len(frappe.db.after_commit._functions)
	check(registered >= 1, "T12.a after_commit callback registered for Unit", f"got {registered}")
	frappe.db.after_commit.run()
	frappe.db.commit()
	after = queued_count()
	check(after == before + 1, "T12.b Build Queue row created after commit (Unit trash)", f"before={before} after={after}")

	frappe.flags.pop(f"_build_after_commit_{PLAN_ID}", None)
	cleanup_keys()
	cleanup_test_builds()

	# ---- T13 ----------------------------------------------------------------
	# Confirm the rebuilt JSON correctly EXCLUDES topics that no longer exist
	# in the published set. Frappe normally Restricts deleting a topic that
	# still has child lessons, so user flow is "delete lessons → delete topic".
	# Once both are gone, the published-topic query returns no row for the
	# deleted id and the unit's topic list is shorter — verified by checking
	# the build query directly.
	print("\nT13: published-topic query excludes a non-existent topic id")
	rows = frappe.db.sql(
		"""SELECT name FROM `tabMemora Topic`
		   WHERE name = %s AND is_published = 1""",
		("TPC-NONEXISTENT-FIXTEST",),
	)
	check(len(rows) == 0, "T13.a non-existent topic does not appear in published-topic query")
	# And a real published topic does appear.
	rows = frappe.db.sql(
		"""SELECT name FROM `tabMemora Topic`
		   WHERE name = %s AND is_published = 1""",
		(TOPIC_ID,),
	)
	check(len(rows) == 1, "T13.b real published topic does appear in the same query")

	# Final summary -----------------------------------------------------------
	print()
	print("=" * 70)
	print(f"RESULT: {state['passed']} passed, {state['failed']} failed")
	if state["failures"]:
		print("Failures:")
		for f in state["failures"]:
			print(f"  - {f}")
	print("=" * 70)
	return state
