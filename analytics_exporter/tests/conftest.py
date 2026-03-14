"""Pytest configuration and shared fixtures for analytics exporter tests.

Integration tests require a live MariaDB connection.
Set environment variables:

    DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=...

Run unit tests only (no DB):
    pytest -m unit analytics_exporter/tests/ -v

Run integration tests:
    DB_HOST=127.0.0.1 ... python3 -m pytest analytics_exporter/tests/ -v
"""

import os

import pymysql
import pymysql.cursors
import pytest

from analytics_exporter.config import Config


# ---------------------------------------------------------------------------
# Pytest markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
	config.addinivalue_line(
		"markers",
		"integration: mark test as requiring a live MariaDB database connection",
	)
	config.addinivalue_line(
		"markers",
		"unit: mark test as a pure unit test (no external dependencies required)",
	)


# ---------------------------------------------------------------------------
# DB config / connection fixtures
# ---------------------------------------------------------------------------

def _get_env(key: str, fallback_key: str | None = None, default: str = "") -> str:
	return os.environ.get(key) or (os.environ.get(fallback_key) if fallback_key else None) or default


@pytest.fixture(scope="session")
def analytics_db_config() -> Config:
	"""Config pointing at the integration test MariaDB database.

	Skips if no DB credentials are configured.
	"""
	db_host = _get_env("TEST_DB_HOST", "DB_HOST")
	if not db_host:
		pytest.skip("Integration tests require DB_HOST or TEST_DB_HOST")

	db_name = _get_env("TEST_DB_NAME", "DB_NAME")
	if not db_name:
		pytest.skip("Integration tests require DB_NAME or TEST_DB_NAME")

	schema_path = os.path.join(os.path.dirname(__file__), "..", "schemas")

	return Config(
		db_host=db_host,
		db_port=int(_get_env("TEST_DB_PORT", "DB_PORT", "3306")),
		db_user=_get_env("TEST_DB_USER", "DB_USER", "frappe"),
		db_password=_get_env("TEST_DB_PASSWORD", "DB_PASSWORD", ""),
		db_name=db_name,
		analytics_output_path="/tmp/memora_analytics_inttest/",
		analytics_schema_path=str(os.path.abspath(schema_path)),
		analytics_chunk_size=1000,
		analytics_log_path="/tmp/memora_analytics_inttest/analytics.log",
		analytics_mode="auto",
		analytics_datasets=[],
		analytics_interaction_from=None,
		analytics_interaction_to=None,
	)


@pytest.fixture(scope="session")
def db_conn(analytics_db_config: Config):
	"""Session-scoped pymysql connection with READ COMMITTED isolation."""
	conn = pymysql.connect(
		host=analytics_db_config.db_host,
		port=analytics_db_config.db_port,
		user=analytics_db_config.db_user,
		password=analytics_db_config.db_password,
		database=analytics_db_config.db_name,
		charset="utf8mb4",
		cursorclass=pymysql.cursors.DictCursor,
	)
	with conn.cursor() as cursor:
		cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
	yield conn
	conn.close()


# ---------------------------------------------------------------------------
# Table name constants
# ---------------------------------------------------------------------------

PRACTICE_LOG_TABLE  = "tabMemora Practice Log"
REVIEW_ITEM_TABLE   = "tabMemora Review Item"
SUBJECT_TABLE       = "tabMemora Subject"
TRACK_TABLE         = "tabMemora Track"
UNIT_TABLE          = "tabMemora Unit"
TOPIC_TABLE         = "tabMemora Topic"
LESSON_TABLE        = "tabMemora Lesson"
SEASON_TABLE        = "tabMemora Season"
GRADE_TABLE         = "tabMemora Grade"
MAJOR_TABLE         = "tabMemora Major"
ACADEMIC_PLAN_TABLE = "tabMemora Academic Plan"
GRADE_MAJOR_TABLE   = "tabMemora Grade Major"

# New tables added in feature 048
PLAYER_PROFILE_TABLE             = "tabMemora Player Profile"
INTERACTION_LOG_TABLE            = "tabMemora Interaction Log"
SUBSCRIPTION_TABLE               = "tabMemora Player Subscription"
SUBSCRIPTION_TRANSACTION_TABLE   = "tabMemora Subscription Transaction"
VOUCHER_CARD_TABLE               = "tabMemora Voucher Card"
VOUCHER_BATCH_TABLE              = "tabMemora Voucher Batch"
VOUCHER_ALLOCATION_TABLE         = "tabMemora Voucher Allocation"
CHALLENGE_ATTEMPT_TABLE          = "tabMemora Challenge Attempt"
CHALLENGE_ATTEMPT_DETAIL_TABLE   = "tabMemora Challenge Attempt Detail"
STRUCTURE_PROGRESS_TABLE         = "tabMemora Structure Progress"
PLAYER_WALLET_TABLE              = "tabMemora Player Wallet"
LESSON_STAGE_TABLE               = "tabMemora Lesson Stage"
LESSON_STAGE_SETTINGS_TABLE      = "tabMemora Lesson Stage Settings"
CONTENT_REPORT_TABLE             = "tabMemora Content Report"
LIVE_CHALLENGE_EVENT_TABLE       = "tabMemora Live Challenge Event"
LIVE_CHALLENGE_PARTICIPATION_TABLE = "tabMemora Live Challenge Participation"
ARCHIVE_JOB_TABLE                = "tabMemora Archive Job"
TASK_RUN_LOG_TABLE               = "tabMemora Task Run Log"
BUILD_QUEUE_TABLE                = "tabMemora Build Queue"
MEMORY_STATE_TABLE               = "tabMemora Memory State"

# Prefixes for test data isolation
PL_PLAYER_PREFIX = "TEST-PL"
RI_ITEM_PREFIX   = "TEST-RI"
HI_PREFIX        = "TEST-HI"
AC_PREFIX        = "TEST-AC"


# ---------------------------------------------------------------------------
# Practice Log helpers  (player_ids: TEST-PL-NNN, item_ids: TEST-RI-{prefix}-NNN)
# ---------------------------------------------------------------------------

def practice_log_rows(conn, prefix: str, count: int, batch_size: int = 500) -> int:
	"""Insert test practice log rows. Returns inserted count.

	player_id pattern: TEST-PL-NNN
	item_id pattern:   TEST-RI-{prefix}-NNNNNN
	last_seen_at: 2099-06-01 + offset (far future to avoid production data collision)
	"""
	from datetime import datetime, timedelta

	base_ts = datetime(2099, 6, 1, 0, 0, 0)
	rows = []
	for n in range(1, count + 1):
		ts = base_ts + timedelta(seconds=n * 60)
		attempt_count = (n % 9) + 1
		correct_count = n % attempt_count
		rows.append((
			f"{PL_PLAYER_PREFIX}-{(n % 5) + 1:03d}",
			f"{RI_ITEM_PREFIX}-{prefix}-{n:06d}",
			attempt_count,
			correct_count,
			ts.strftime("%Y-%m-%d %H:%M:%S"),
			ts.strftime("%Y-%m-%d %H:%M:%S"),
			"Correct" if n % 2 == 0 else "Incorrect",
		))

	sql = (
		"INSERT IGNORE INTO `tabMemora Practice Log` "
		"(`player_id`, `item_id`, `attempt_count`, `correct_count`, "
		" `first_seen_at`, `last_seen_at`, `last_result`) "
		"VALUES (%s, %s, %s, %s, %s, %s, %s)"
	)
	inserted = 0
	for i in range(0, len(rows), batch_size):
		batch = rows[i:i + batch_size]
		with conn.cursor() as cursor:
			cursor.executemany(sql, batch)
		conn.commit()
		inserted += len(batch)
	return inserted


def cleanup_practice_log_rows(conn, prefix: str) -> None:
	"""Delete test practice log rows matching TEST-RI-{prefix}-* item_id."""
	with conn.cursor() as cursor:
		cursor.execute(
			"DELETE FROM `tabMemora Practice Log` WHERE `item_id` LIKE %s",
			(f"{RI_ITEM_PREFIX}-{prefix}-%",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# Review Item helpers  (name/item_id: TEST-RI-{prefix}-NNNNNN)
# ---------------------------------------------------------------------------

def review_item_rows(
	conn,
	prefix: str,
	count: int,
	subject_id: str | None = None,
	track_id: str | None = None,
	unit_id: str | None = None,
	topic_id: str | None = None,
	lesson_id: str | None = None,
) -> int:
	"""Insert test review items with complete curriculum hierarchy FKs.

	If hierarchy IDs are not provided, falls back to TEST-HI-{prefix}-* defaults.
	Returns inserted count.
	"""
	rows = []
	for n in range(1, count + 1):
		item_name = f"{RI_ITEM_PREFIX}-{prefix}-{n:06d}"
		rows.append((
			item_name,                                           # name (Frappe PK)
			item_name,                                           # item_id
			lesson_id  or f"{HI_PREFIX}-LESSON-{prefix}-001",
			topic_id   or f"{HI_PREFIX}-TOPIC-{prefix}-001",
			unit_id    or f"{HI_PREFIX}-UNIT-{prefix}-001",
			track_id   or f"{HI_PREFIX}-TRACK-{prefix}-001",
			subject_id or f"{HI_PREFIX}-SUBJ-{prefix}-001",
			n,                                                   # idx
		))

	sql = (
		"INSERT IGNORE INTO `tabMemora Review Item` "
		"(`name`, `item_id`, `lesson`, `topic`, `unit`, `track`, `subject`, "
		" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
		"VALUES (%s, %s, %s, %s, %s, %s, %s, "
		"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, %s)"
	)
	with conn.cursor() as cursor:
		cursor.executemany(sql, rows)
	conn.commit()
	return len(rows)


def cleanup_review_item_rows(conn, prefix: str) -> None:
	"""Delete test review items whose name matches TEST-RI-{prefix}-*."""
	with conn.cursor() as cursor:
		cursor.execute(
			"DELETE FROM `tabMemora Review Item` WHERE `name` LIKE %s",
			(f"{RI_ITEM_PREFIX}-{prefix}-%",),
		)
	conn.commit()


# ---------------------------------------------------------------------------
# Hierarchy helpers  (names: TEST-HI-{prefix}-*)
# ---------------------------------------------------------------------------

def hierarchy_rows(conn, prefix: str) -> dict:
	"""Insert a complete one-row curriculum hierarchy (subject/track/unit/topic/lesson).

	Returns dict: {subject_id, track_id, unit_id, topic_id, lesson_id}.
	"""
	subj_id   = f"{HI_PREFIX}-SUBJ-{prefix}-001"
	track_id  = f"{HI_PREFIX}-TRACK-{prefix}-001"
	unit_id   = f"{HI_PREFIX}-UNIT-{prefix}-001"
	topic_id  = f"{HI_PREFIX}-TOPIC-{prefix}-001"
	lesson_id = f"{HI_PREFIX}-LESSON-{prefix}-001"

	with conn.cursor() as cursor:
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Subject` "
			"(`name`, `subject_title`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1)",
			(subj_id, f"Test Subject {prefix}"),
		)
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Track` "
			"(`name`, `track_title`, `subject`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, %s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1)",
			(track_id, f"Test Track {prefix}", subj_id),
		)
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Unit` "
			"(`name`, `unit_title`, `track`, `subject`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, %s, %s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1)",
			(unit_id, f"Test Unit {prefix}", track_id, subj_id),
		)
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Topic` "
			"(`name`, `topic_title`, `unit`, `track`, `subject`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1)",
			(topic_id, f"Test Topic {prefix}", unit_id, track_id, subj_id),
		)
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Lesson` "
			"(`name`, `lesson_title`, `topic`, `unit`, `track`, `subject`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, "
			" `is_published`, `is_reviewable`) "
			"VALUES (%s, %s, %s, %s, %s, %s, "
			"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1, 1, 1)",
			(lesson_id, f"Test Lesson {prefix}", topic_id, unit_id, track_id, subj_id),
		)
	conn.commit()

	return {
		"subject_id": subj_id,
		"track_id":   track_id,
		"unit_id":    unit_id,
		"topic_id":   topic_id,
		"lesson_id":  lesson_id,
	}


def cleanup_hierarchy_rows(conn, prefix: str) -> None:
	"""Delete test hierarchy rows in FK-safe order: lesson → topic → unit → track → subject."""
	patterns = [
		(LESSON_TABLE, f"{HI_PREFIX}-LESSON-{prefix}-%"),
		(TOPIC_TABLE,  f"{HI_PREFIX}-TOPIC-{prefix}-%"),
		(UNIT_TABLE,   f"{HI_PREFIX}-UNIT-{prefix}-%"),
		(TRACK_TABLE,  f"{HI_PREFIX}-TRACK-{prefix}-%"),
		(SUBJECT_TABLE,f"{HI_PREFIX}-SUBJ-{prefix}-%"),
	]
	with conn.cursor() as cursor:
		for table, pattern in patterns:
			cursor.execute(f"DELETE FROM `{table}` WHERE `name` LIKE %s", (pattern,))
	conn.commit()


# ---------------------------------------------------------------------------
# Academic Context helpers  (names: TEST-AC-{prefix}-*)
# ---------------------------------------------------------------------------

def academic_context_rows(conn, prefix: str, season_seq: int = 9990) -> dict:
	"""Insert one complete academic context set: season/grade/major/plan/grade_major.

	season_seq must be unique across concurrent tests — caller should pass a
	distinct value per prefix if running multiple contexts simultaneously.

	Returns dict: {season_id, grade_id, major_id, plan_id}.
	"""
	season_id = f"{AC_PREFIX}-SEAS-{prefix}-001"
	grade_id  = f"{AC_PREFIX}-GRADE-{prefix}-001"
	major_id  = f"{AC_PREFIX}-MAJOR-{prefix}-001"
	plan_id   = f"{AC_PREFIX}-PLAN-{prefix}-001"
	gm_name   = f"{AC_PREFIX}-GM-{prefix}-001"

	with conn.cursor() as cursor:
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Season` "
			"(`name`, `season_title`, `season_seq`, `start_date`, `end_date`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`, `is_published`) "
			"VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1, 1)",
			(season_id, f"Test Season {prefix}", season_seq, "2099-01-01", "2099-12-31"),
		)
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Grade` "
			"(`name`, `grade_title`, `sort_order`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, %s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1)",
			(grade_id, f"Test Grade {prefix}", 99),
		)
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Major` "
			"(`name`, `major_title`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1)",
			(major_id, f"Test Major {prefix}"),
		)
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Academic Plan` "
			"(`name`, `plan_name`, `season`, `grade`, `major`, `is_published`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`) "
			"VALUES (%s, %s, %s, %s, %s, 1, NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 1)",
			(plan_id, f"Test Plan {prefix}", season_id, grade_id, major_id),
		)
		cursor.execute(
			"INSERT IGNORE INTO `tabMemora Grade Major` "
			"(`name`, `parent`, `parenttype`, `parentfield`, `idx`, `major`, "
			" `creation`, `modified`, `modified_by`, `owner`, `docstatus`) "
			"VALUES (%s, %s, 'Memora Grade', 'majors', 1, %s, "
			"        NOW(), NOW(), 'test@test.com', 'test@test.com', 0)",
			(gm_name, grade_id, major_id),
		)
	conn.commit()

	return {
		"season_id": season_id,
		"grade_id":  grade_id,
		"major_id":  major_id,
		"plan_id":   plan_id,
	}


def cleanup_academic_context_rows(conn, prefix: str) -> None:
	"""Delete test academic context rows in FK-safe order."""
	with conn.cursor() as cursor:
		cursor.execute(
			"DELETE FROM `tabMemora Grade Major` WHERE `parent` LIKE %s",
			(f"{AC_PREFIX}-GRADE-{prefix}-%",),
		)
		cursor.execute(
			"DELETE FROM `tabMemora Academic Plan` WHERE `name` LIKE %s",
			(f"{AC_PREFIX}-PLAN-{prefix}-%",),
		)
		cursor.execute(
			"DELETE FROM `tabMemora Season` WHERE `name` LIKE %s",
			(f"{AC_PREFIX}-SEAS-{prefix}-%",),
		)
		cursor.execute(
			"DELETE FROM `tabMemora Grade` WHERE `name` LIKE %s",
			(f"{AC_PREFIX}-GRADE-{prefix}-%",),
		)
		cursor.execute(
			"DELETE FROM `tabMemora Major` WHERE `name` LIKE %s",
			(f"{AC_PREFIX}-MAJOR-{prefix}-%",),
		)
	conn.commit()
