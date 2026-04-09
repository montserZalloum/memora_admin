"""
Exam JSON build pipeline for Official Exams.

Triggered by Frappe doc_events on Memora Official Exam.
Builds per-exam JSON and per-subject index JSON to CDN storage.
"""

from __future__ import annotations

import json
import logging

import frappe

logger = logging.getLogger(__name__)


def on_exam_updated(doc, method):
	"""Rebuild exam JSON and subject index on save."""
	_build_exam_json(doc)
	_build_subject_index(doc.subject, doc.academic_plan)
	_purge_exam_cdn_cache(doc)


def on_exam_deleted(doc, method):
	"""Remove exam JSON and rebuild subject index on delete."""
	_delete_exam_json(doc)
	_build_subject_index(doc.subject, doc.academic_plan)
	_purge_exam_cdn_cache(doc)


def _build_exam_json(doc):
	"""Serialize exam + questions to JSON and write to CDN storage.

	Only writes if exam is published. Deletes JSON if unpublished.
	"""
	from memora_admin.memora_admin.services.build.storage import get_storage_backend

	storage = get_storage_backend()
	filename = f"exams/{doc.academic_plan}/{doc.subject}/{doc.name}.json"

	if not doc.is_published:
		storage.delete(filename)
		logger.info(f"Deleted unpublished exam JSON: {filename}")
		return

	questions = []
	for q in doc.questions:
		questions.append(
			{
				"idx": q.idx,
				"question_text": q.question_text,
				"choice_1": q.choice_1,
				"choice_2": q.choice_2,
				"choice_3": q.choice_3 or "",
				"choice_4": q.choice_4 or "",
				"correct_choice": q.correct_choice,
			}
		)

	exam_data = {
		"exam_id": doc.name,
		"exam_title": doc.exam_title,
		"subject_id": doc.subject,
		"academic_plan": doc.academic_plan,
		"question_count": len(questions),
		"questions": questions,
	}

	content = json.dumps(exam_data, ensure_ascii=False).encode("utf-8")
	storage.upload(filename, content)
	logger.info(f"Built exam JSON: {filename} ({len(questions)} questions)")


def _build_subject_index(subject_id, plan_id):
	"""Build _index.json listing all published exams for a (plan, subject) pair."""
	from memora_admin.memora_admin.services.build.storage import get_storage_backend

	storage = get_storage_backend()

	published_exams = frappe.get_all(
		"Memora Official Exam",
		filters={"subject": subject_id, "academic_plan": plan_id, "is_published": 1},
		fields=["name", "exam_title", "sort_order"],
		order_by="sort_order asc, creation asc",
	)

	# Get all question counts in a single query
	count_map = {}
	if published_exams:
		exam_names = [e.name for e in published_exams]
		placeholders = ", ".join(["%s"] * len(exam_names))
		q_counts = frappe.db.sql(
			f"""
			SELECT parent, COUNT(*) AS cnt
			FROM `tabMemora Official Exam Question`
			WHERE parent IN ({placeholders})
			GROUP BY parent
			""",
			exam_names,
			as_dict=True,
		)
		count_map = {row["parent"]: row["cnt"] for row in q_counts}

	exams_list = []
	for exam in published_exams:
		exams_list.append(
			{
				"exam_id": exam.name,
				"exam_title": exam.exam_title,
				"question_count": count_map.get(exam.name, 0),
			}
		)

	index_data = {
		"subject_id": subject_id,
		"plan_id": plan_id,
		"exams": exams_list,
	}

	filename = f"exams/{plan_id}/{subject_id}/_index.json"
	content = json.dumps(index_data, ensure_ascii=False).encode("utf-8")
	storage.upload(filename, content)
	logger.info(f"Built subject exam index: {filename} ({len(exams_list)} exams)")


def _delete_exam_json(doc):
	"""Delete exam JSON file from CDN storage."""
	from memora_admin.memora_admin.services.build.storage import get_storage_backend

	storage = get_storage_backend()
	filename = f"exams/{doc.academic_plan}/{doc.subject}/{doc.name}.json"
	storage.delete(filename)
	logger.info(f"Deleted exam JSON: {filename}")


def _purge_exam_cdn_cache(doc):
	"""Purge CDN cache for exam files (best-effort)."""
	try:
		from memora_admin.memora_admin.services.cdn.utils import get_purge_service

		purge_service = get_purge_service()
		if purge_service is None:
			return

		filenames = [
			f"exams/{doc.academic_plan}/{doc.subject}/{doc.name}.json",
			f"exams/{doc.academic_plan}/{doc.subject}/_index.json",
		]

		success = purge_service.purge_files(filenames)
		if success:
			logger.info(f"CDN cache purged for exam {doc.name}")
		else:
			logger.warning(f"CDN cache purge partially failed for exam {doc.name}")
	except Exception as e:
		logger.error(f"CDN purge error for exam {doc.name}: {e}")
