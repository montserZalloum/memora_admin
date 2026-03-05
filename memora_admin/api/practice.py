"""Frappe API for Practice Arena hierarchy metadata."""

import time

import frappe

_INDEX_EXISTS_CACHE: dict[tuple[str, str, str], bool] = {}


def _string_list(values: list[str] | None) -> list[str]:
	"""Normalize optional list input to a compact list of non-empty strings."""
	return [value for value in (values or []) if isinstance(value, str) and value]


def _placeholders(values: list[str]) -> str:
	"""Return a %s placeholder list sized to the provided values."""
	return ", ".join(["%s"] * len(values))


def _build_review_item_scope(
	subject_id: str,
	accessible_lessons: list[str],
	topic_ids: list[str] | None = None,
	served_item_ids: list[str] | None = None,
) -> tuple[str, list[str]]:
	"""Build the shared WHERE clause used by practice item lookups."""
	lesson_ids = _string_list(accessible_lessons)
	if not lesson_ids:
		return "", []

	clauses = [
		"ri.subject = %s",
		f"ri.lesson IN ({_placeholders(lesson_ids)})",
	]
	params: list[str] = [subject_id, *lesson_ids]

	selected_topics = _string_list(topic_ids)
	if selected_topics:
		clauses.append(f"ri.topic IN ({_placeholders(selected_topics)})")
		params.extend(selected_topics)

	served_ids = _string_list(served_item_ids)
	if served_ids:
		clauses.append(f"ri.item_id NOT IN ({_placeholders(served_ids)})")
		params.extend(served_ids)

	return " AND ".join(clauses), params


def _rank_topics_by_availability(topic_counts: dict[str, int], limit: int | None = None) -> list[str]:
	"""Return topic_ids ordered the same way the FastAPI quota logic prefers them."""
	ranked_topics = [
		topic_id
		for topic_id, count in sorted(topic_counts.items(), key=lambda item: (-item[1], item[0]))
		if count > 0
	]
	if limit is None or limit <= 0:
		return ranked_topics
	return ranked_topics[:limit]


def _table_has_index(table_name: str, index_name: str) -> bool:
	"""Check whether the current site has the requested index, with process-local caching."""
	site = getattr(frappe.local, "site", "") or ""
	cache_key = (site, table_name, index_name)
	if cache_key in _INDEX_EXISTS_CACHE:
		return _INDEX_EXISTS_CACHE[cache_key]

	result = frappe.db.sql(
		"""
		SELECT 1
		FROM INFORMATION_SCHEMA.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
		  AND TABLE_NAME = %s
		  AND INDEX_NAME = %s
		LIMIT 1
		""",
		(table_name, index_name),
	)
	exists = bool(result)
	_INDEX_EXISTS_CACHE[cache_key] = exists
	return exists


def _review_item_table_ref(alias: str = "ri") -> str:
	"""Return the Review Item table reference with a safe FORCE INDEX hint when available."""
	table_ref = f"`tabMemora Review Item` {alias}"
	if _table_has_index("tabMemora Review Item", "idx_practice_scope"):
		return f"{table_ref} FORCE INDEX (`idx_practice_scope`)"
	return table_ref


def _count_practice_scope(
	subject_id: str,
	accessible_lessons: list[str],
	selected_topics: list[str] | None = None,
	player_id: str | None = None,
	session_started_at: str | None = None,
) -> tuple[dict[str, int], int]:
	"""Count in-scope items per topic and optionally the items already seen this session."""
	where_clause, params = _build_review_item_scope(subject_id, accessible_lessons, selected_topics)
	if not where_clause:
		return {}, 0

	session_count_sql = "0 AS session_seen_cnt"
	session_join_sql = ""
	query_params: list[str] = list(params)

	if player_id and session_started_at:
		session_count_sql = """
			SUM(
				CASE
					WHEN session_pl.item_id IS NULL THEN 0
					ELSE 1
				END
			) AS session_seen_cnt
		"""
		session_join_sql = """
		LEFT JOIN `tabMemora Practice Log` session_pl
			ON session_pl.item_id = ri.item_id
		   AND session_pl.player_id = %s
		   AND session_pl.last_seen_at >= %s
		"""
		query_params = [player_id, session_started_at, *params]

	rows = frappe.db.sql(
		f"""
		SELECT ri.topic, COUNT(*) as cnt, {session_count_sql}
		FROM {_review_item_table_ref("ri")}
		{session_join_sql}
		WHERE {where_clause}
		GROUP BY ri.topic
		""",
		tuple(query_params),
		as_dict=True,
	)

	topic_counts = {row.topic: int(row.cnt or 0) for row in rows}
	session_served_count = sum(int((row.get("session_seen_cnt") or 0)) for row in rows)
	return topic_counts, session_served_count


@frappe.whitelist(allow_guest=False)
def get_practice_hierarchy_meta(subject_id: str) -> dict | None:
	"""Get titles and Review Item counts for a subject's practice hierarchy.

	Returns a flat lookup structure (NOT nested) for fast cache-and-merge
	with the existing SubjectHierarchy structure in FastAPI.

	Returns:
	    {
	        "subject_title": "الرياضيات",
	        "tracks": {"TRK-00001": {"title": "الجبر"}},
	        "units": {"UNI-00001": {"title": "المعادلات", "track": "TRK-00001"}},
	        "topics": {"TOP-00001": {"title": "المعادلات الخطية", "unit": "UNI-00001"}},
	        "item_counts": {"TOP-00001": 45, "TOP-00002": 35},
	    }
	"""
	if not frappe.db.exists("Memora Subject", subject_id):
		return None

	subject_title = frappe.db.get_value("Memora Subject", subject_id, "subject_title") or subject_id

	tracks = frappe.get_all(
		"Memora Track",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "track_title"],
		order_by="idx asc",
	)

	units = frappe.get_all(
		"Memora Unit",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "unit_title", "track"],
		order_by="idx asc",
	)

	topics = frappe.get_all(
		"Memora Topic",
		filters={"subject": subject_id, "is_published": 1},
		fields=["name", "topic_title", "unit"],
		order_by="idx asc",
	)

	# Review Item counts grouped by topic
	item_counts_raw = frappe.db.sql(
		"""
		SELECT topic, COUNT(*) as cnt
		FROM `tabMemora Review Item`
		WHERE subject = %s
		GROUP BY topic
		""",
		subject_id,
		as_dict=True,
	)

	return {
		"subject_title": subject_title,
		"tracks": {t.name: {"title": t.track_title or t.name} for t in tracks},
		"units": {u.name: {"title": u.unit_title or u.name, "track": u.track} for u in units},
		"topics": {t.name: {"title": t.topic_title or t.name, "unit": t.unit} for t in topics},
		"item_counts": {r.topic: r.cnt for r in item_counts_raw},
	}


@frappe.whitelist(allow_guest=False)
def count_practice_items_per_topic(
	subject_id: str,
	accessible_lessons: list[str],
	selected_topics: list[str] | None = None,
) -> dict[str, int]:
	"""Count available practice items per topic for the selected lesson scope."""
	topic_counts, _session_served_count = _count_practice_scope(
		subject_id=subject_id,
		accessible_lessons=accessible_lessons,
		selected_topics=selected_topics,
	)
	return topic_counts


@frappe.whitelist(allow_guest=False)
def count_practice_items_seen_in_session(
	player_id: str,
	subject_id: str,
	accessible_lessons: list[str],
	selected_topics: list[str] | None = None,
	session_started_at: str | None = None,
) -> int:
	"""Count in-scope Review Items already submitted in the active practice session."""
	if not session_started_at:
		return 0

	_topic_counts, session_served_count = _count_practice_scope(
		subject_id=subject_id,
		accessible_lessons=accessible_lessons,
		selected_topics=selected_topics,
		player_id=player_id,
		session_started_at=session_started_at,
	)
	return session_served_count


@frappe.whitelist(allow_guest=False)
def prepare_practice_batch(
	player_id: str,
	subject_id: str,
	accessible_lessons: list[str],
	selected_topics: list[str] | None = None,
	served_item_ids: list[str] | None = None,
	per_topic_limit: int = 20,
	max_topics: int | None = None,
	session_started_at: str | None = None,
) -> dict:
	"""Return topic counts plus batched candidate rows in one RPC."""
	topic_counts, session_served_count = _count_practice_scope(
		subject_id=subject_id,
		accessible_lessons=accessible_lessons,
		selected_topics=selected_topics,
		player_id=player_id,
		session_started_at=session_started_at,
	)
	if not topic_counts or per_topic_limit <= 0:
		return {
			"topic_counts": topic_counts,
			"candidate_rows": [],
			"session_served_count": session_served_count,
		}

	candidate_topic_ids = _rank_topics_by_availability(topic_counts, max_topics)
	candidate_rows = select_practice_candidates(
		player_id=player_id,
		subject_id=subject_id,
		accessible_lessons=accessible_lessons,
		topic_ids=candidate_topic_ids,
		served_item_ids=served_item_ids,
		per_topic_limit=per_topic_limit,
		session_started_at=session_started_at,
	)
	return {
		"topic_counts": topic_counts,
		"candidate_rows": candidate_rows,
		"session_served_count": session_served_count,
	}


@frappe.whitelist(allow_guest=False)
def select_practice_candidates(
	player_id: str,
	subject_id: str,
	accessible_lessons: list[str],
	topic_ids: list[str],
	served_item_ids: list[str] | None = None,
	per_topic_limit: int = 20,
	session_started_at: str | None = None,
) -> list[dict]:
	"""Fetch the top N candidate rows per topic in one query."""
	selected_topics = _string_list(topic_ids)
	if not selected_topics or per_topic_limit <= 0:
		return []

	where_clause, params = _build_review_item_scope(
		subject_id,
		accessible_lessons,
		selected_topics,
		served_item_ids,
	)
	if not where_clause:
		return []

	exclude_current_session_clause = ""
	extra_params: list[str] = []
	if session_started_at:
		exclude_current_session_clause = " AND (pl.last_seen_at IS NULL OR pl.last_seen_at < %s)"
		extra_params.append(session_started_at)

	priority_case = """
		CASE
			WHEN pl.item_id IS NULL THEN 0
			ELSE 1
		END
	"""
	sort_seen_expr = "COALESCE(pl.last_seen_at, '1970-01-01')"

	rows = frappe.db.sql(
		f"""
		SELECT candidates.item_id, candidates.question_text, candidates.choice_1, candidates.choice_2,
			   candidates.choice_3, candidates.choice_4, candidates.correct_choice, candidates.content_json,
			   candidates.stage_type, candidates.topic, candidates.priority, candidates.sort_seen
		FROM (
			SELECT ri.item_id, ri.question_text, ri.choice_1, ri.choice_2,
				   ri.choice_3, ri.choice_4, ri.correct_choice, ri.content_json,
				   ri.stage_type, ri.topic,
				   {priority_case} AS priority,
				   {sort_seen_expr} AS sort_seen,
				   ROW_NUMBER() OVER (
					   PARTITION BY ri.topic
					   ORDER BY {priority_case} ASC, {sort_seen_expr} ASC, ri.item_id ASC
				   ) AS topic_rank
			FROM {_review_item_table_ref("ri")}
			LEFT JOIN `tabMemora Practice Log` pl
				ON pl.item_id = ri.item_id AND pl.player_id = %s
			WHERE {where_clause}{exclude_current_session_clause}
		) candidates
		WHERE candidates.topic_rank <= %s
		ORDER BY candidates.topic, candidates.topic_rank
		""",
		tuple([player_id, *params, *extra_params, per_topic_limit]),
		as_dict=True,
	)
	return rows


@frappe.whitelist(allow_guest=False)
def select_practice_questions_for_topic(
	player_id: str,
	subject_id: str,
	accessible_lessons: list[str],
	topic_id: str,
	served_item_ids: list[str] | None = None,
	limit: int = 20,
) -> list[dict]:
	"""Fetch ranked question rows for one topic."""
	if not topic_id or limit <= 0:
		return []

	where_clause, params = _build_review_item_scope(
		subject_id,
		accessible_lessons,
		[topic_id],
		served_item_ids,
	)
	if not where_clause:
		return []

	rows = frappe.db.sql(
		"""
		SELECT ri.item_id, ri.question_text, ri.choice_1, ri.choice_2,
			   ri.choice_3, ri.choice_4, ri.correct_choice, ri.content_json,
			   ri.stage_type, ri.topic,
			   CASE
				   WHEN pl.item_id IS NULL THEN 0
				   ELSE 1
			   END AS priority,
			   COALESCE(pl.last_seen_at, '1970-01-01') AS sort_seen
		FROM """
		+ _review_item_table_ref("ri")
		+ """
		LEFT JOIN `tabMemora Practice Log` pl
			ON pl.item_id = ri.item_id AND pl.player_id = %s
		WHERE """
		+ where_clause
		+ """
		ORDER BY priority ASC, sort_seen ASC
		LIMIT %s
		""",
		tuple([player_id, *params, limit]),
		as_dict=True,
	)
	return rows


@frappe.whitelist(allow_guest=False)
def get_existing_practice_item_ids(item_ids: list[str]) -> list[str]:
	"""Return the subset of item_ids that still exist in the Review Item table."""
	requested_ids = _string_list(item_ids)
	if not requested_ids:
		return []

	rows = frappe.db.sql(
		f"SELECT item_id FROM `tabMemora Review Item` WHERE item_id IN ({_placeholders(requested_ids)})",
		tuple(requested_ids),
		as_dict=True,
	)
	return [row.item_id for row in rows]


@frappe.whitelist(allow_guest=False)
def upsert_practice_results(player_id: str, results: list[dict], seen_at: str) -> list[str]:
	"""Persist one submitted batch into Memora Practice Log and return accepted item_ids."""
	started = time.perf_counter()
	requested_ids = _string_list([result.get("item_id") for result in (results or [])])
	if not requested_ids:
		return []

	values_parts = []
	params = []
	accepted_ids: list[str] = []
	for result in results or []:
		item_id = result.get("item_id")
		if not item_id:
			continue

		is_correct = bool(result.get("is_correct"))
		result_str = "Correct" if is_correct else "Incorrect"
		correct_int = 1 if is_correct else 0

		values_parts.append("(%s, %s, %s, %s, %s, 1, %s)")
		params.extend([player_id, item_id, seen_at, seen_at, result_str, correct_int])
		accepted_ids.append(item_id)

	if not values_parts:
		return accepted_ids

	upsert_started = time.perf_counter()
	frappe.db.sql(
		f"""
		INSERT INTO `tabMemora Practice Log`
			(player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
		VALUES {", ".join(values_parts)}
		ON DUPLICATE KEY UPDATE
			last_seen_at = VALUES(last_seen_at),
			last_result = VALUES(last_result),
			attempt_count = attempt_count + 1,
			correct_count = correct_count + VALUES(correct_count)
		""",
		tuple(params),
	)
	upsert_ms = round((time.perf_counter() - upsert_started) * 1000, 2)
	commit_started = time.perf_counter()
	frappe.db.commit()
	commit_ms = round((time.perf_counter() - commit_started) * 1000, 2)
	frappe.logger("memora_admin.practice").info(
		"practice_upsert_timed player_id=%s requested_count=%s accepted_count=%s validate_ms=%.2f upsert_ms=%.2f commit_ms=%.2f total_ms=%.2f",
		player_id,
		len(requested_ids),
		len(accepted_ids),
		0.0,
		upsert_ms,
		commit_ms,
		round((time.perf_counter() - started) * 1000, 2),
	)
	return accepted_ids
