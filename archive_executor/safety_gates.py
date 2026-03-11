"""Pre-cleanup safety gates for season partition drops.

All five gates must pass before DROP PARTITION is permitted.
Any single gate failure blocks cleanup entirely.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import get_connection

_SEASON_PARTITION_RE = re.compile(r"^p_season_\d+$")


@dataclass
class GateCheck:
	gate_name: str
	passed: bool
	message: str
	details: dict = field(default_factory=dict)


@dataclass
class GateResult:
	passed: bool
	gates: list[GateCheck]
	blockers: list[str]
	season_name: str
	season_seq: int
	checked_at: str


def _check_archive_validation(config: Config, season_seq: int) -> GateCheck:
	"""Gate 1: A Completed or Purged archive job must exist for this season."""
	archive_scope = f"season_{season_seq}"
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT name, status, row_count, file_checksum "
				"FROM `tabMemora Archive Job` "
				"WHERE source_doctype = 'Memora Memory State' "
				"  AND archive_scope = %s "
				"  AND schema_version = 'v1' "
				"  AND status IN ('Completed', 'Purged') "
				"LIMIT 1",
				(archive_scope,),
			)
			row = cursor.fetchone()
	finally:
		conn.close()

	if row:
		return GateCheck(
			gate_name="archive_validation",
			passed=True,
			message=f"Validated archive found: {row['name']} (status={row['status']})",
			details={"job_name": row["name"], "status": row["status"], "row_count": row["row_count"]},
		)

	return GateCheck(
		gate_name="archive_validation",
		passed=False,
		message=f"No validated archive found for {archive_scope}. Archive must complete before cleanup.",
		details={"archive_scope": archive_scope},
	)


def _check_player_linkage(config: Config, season_name: str) -> GateCheck:
	"""Gate 2: No player profiles linked to this season."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT COUNT(*) AS cnt FROM `tabMemora Player Profile` WHERE season = %s",
				(season_name,),
			)
			cnt = cursor.fetchone()["cnt"]
	finally:
		conn.close()

	if cnt == 0:
		return GateCheck(
			gate_name="player_linkage",
			passed=True,
			message="No active player profiles linked to this season.",
			details={"season_name": season_name, "linked_players": 0},
		)

	return GateCheck(
		gate_name="player_linkage",
		passed=False,
		message=f"{cnt} active player profiles still linked to season {season_name}. Reassign players before cleanup.",
		details={"season_name": season_name, "linked_players": cnt},
	)


def _check_plan_linkage(config: Config, season_name: str) -> GateCheck:
	"""Gate 3: No published academic plans linked to this season."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT COUNT(*) AS cnt FROM `tabMemora Academic Plan` "
				"WHERE season = %s AND is_published = 1",
				(season_name,),
			)
			cnt = cursor.fetchone()["cnt"]
	finally:
		conn.close()

	if cnt == 0:
		return GateCheck(
			gate_name="plan_linkage",
			passed=True,
			message="No published academic plans linked to this season.",
			details={"season_name": season_name, "linked_plans": 0},
		)

	return GateCheck(
		gate_name="plan_linkage",
		passed=False,
		message=f"{cnt} published academic plans still linked to season {season_name}. Unpublish or reassign plans before cleanup.",
		details={"season_name": season_name, "linked_plans": cnt},
	)


def _check_partition_exists(config: Config, season_seq: int) -> GateCheck:
	"""Gate 4: Target partition exists and matches expected naming pattern."""
	partition_name = f"p_season_{season_seq}"

	if not _SEASON_PARTITION_RE.match(partition_name):
		return GateCheck(
			gate_name="partition_exists",
			passed=False,
			message=f"Partition name {partition_name} does not match expected pattern p_season_N.",
			details={"partition_name": partition_name},
		)

	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT PARTITION_NAME "
				"FROM INFORMATION_SCHEMA.PARTITIONS "
				"WHERE TABLE_SCHEMA = DATABASE() "
				"  AND TABLE_NAME = 'tabMemora Memory State' "
				"  AND PARTITION_NAME = %s",
				(partition_name,),
			)
			row = cursor.fetchone()
	finally:
		conn.close()

	if row:
		return GateCheck(
			gate_name="partition_exists",
			passed=True,
			message=f"Partition {partition_name} found on tabMemora Memory State.",
			details={"partition_name": partition_name},
		)

	return GateCheck(
		gate_name="partition_exists",
		passed=False,
		message=f"Partition {partition_name} not found on tabMemora Memory State. Cannot DROP non-existent partition.",
		details={"partition_name": partition_name},
	)


def _check_grace_period(config: Config, season_seq: int) -> GateCheck:
	"""Gate 0: Archive job must have been Completed for at least purge_grace_days.

	Gives operators a verification window before the irreversible DROP PARTITION.
	Configurable via PURGE_GRACE_DAYS (default 7).
	"""
	grace_days = config.purge_grace_days
	archive_scope = f"season_{season_seq}"
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(
				"SELECT name, status, completed_at "
				"FROM `tabMemora Archive Job` "
				"WHERE source_doctype = 'Memora Memory State' "
				"  AND archive_scope = %s "
				"  AND schema_version = 'v1' "
				"  AND status IN ('Completed', 'Purged') "
				"ORDER BY completed_at ASC "
				"LIMIT 1",
				(archive_scope,),
			)
			row = cursor.fetchone()
	finally:
		conn.close()

	if not row:
		return GateCheck(
			gate_name="grace_period",
			passed=False,
			message=f"No completed archive found for {archive_scope}. Cannot evaluate grace period.",
			details={"archive_scope": archive_scope, "grace_days": grace_days},
		)

	completed_at = row["completed_at"]
	if completed_at is None:
		return GateCheck(
			gate_name="grace_period",
			passed=False,
			message=f"Archive {row['name']} has no completed_at timestamp. Cannot evaluate grace period.",
			details={"job_name": row["name"], "grace_days": grace_days},
		)

	if isinstance(completed_at, str):
		completed_at = datetime.fromisoformat(completed_at)

	# Make timezone-aware if naive
	if completed_at.tzinfo is None:
		completed_at = completed_at.replace(tzinfo=timezone.utc)

	cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
	days_since = (datetime.now(timezone.utc) - completed_at).days

	if completed_at <= cutoff:
		return GateCheck(
			gate_name="grace_period",
			passed=True,
			message=f"Grace period satisfied: archive completed {days_since} days ago (requires {grace_days}).",
			details={
				"job_name": row["name"],
				"completed_at": completed_at.isoformat(),
				"days_since_completed": days_since,
				"grace_days": grace_days,
			},
		)

	remaining = grace_days - days_since
	return GateCheck(
		gate_name="grace_period",
		passed=False,
		message=f"Grace period not met: archive completed {days_since} days ago, requires {grace_days} days. {remaining} days remaining.",
		details={
			"job_name": row["name"],
			"completed_at": completed_at.isoformat(),
			"days_since_completed": days_since,
			"grace_days": grace_days,
			"days_remaining": remaining,
		},
	)


def check_all_gates(config: Config, season_name: str, season_seq: int) -> GateResult:
	"""Run all five safety gates and return aggregate result.

	All gates are always executed (no short-circuit) so operators see all blockers at once.
	"""
	gates = [
		_check_grace_period(config, season_seq),
		_check_archive_validation(config, season_seq),
		_check_player_linkage(config, season_name),
		_check_plan_linkage(config, season_name),
		_check_partition_exists(config, season_seq),
	]

	blockers = [g.message for g in gates if not g.passed]

	return GateResult(
		passed=len(blockers) == 0,
		gates=gates,
		blockers=blockers,
		season_name=season_name,
		season_seq=season_seq,
		checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
	)
