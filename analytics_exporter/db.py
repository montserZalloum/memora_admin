"""Database connection and helpers for the analytics exporter."""

import re
from contextlib import contextmanager

import pymysql
import pymysql.cursors

from .config import Config

# Allowlist pattern for SQL identifiers (table names, column names).
# Only allows alphanumeric, underscores, and spaces (Frappe uses spaces in table names).
_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_ ]+$")


def validate_identifier(name: str) -> str:
	"""Validate a SQL identifier against an allowlist pattern.

	Raises ValueError if the name contains characters outside [a-zA-Z0-9_ ].
	Returns the validated name for convenience.
	"""
	if not name or not _SAFE_IDENTIFIER_RE.match(name):
		raise ValueError(f"Invalid SQL identifier: {name!r}")
	return name


def get_connection(config: Config) -> pymysql.Connection:
	"""Create a new pymysql connection with READ COMMITTED isolation."""
	conn = pymysql.connect(
		host=config.db_host,
		port=config.db_port,
		user=config.db_user,
		password=config.db_password,
		database=config.db_name,
		charset="utf8mb4",
		cursorclass=pymysql.cursors.DictCursor,
	)
	with conn.cursor() as cursor:
		cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
	return conn


@contextmanager
def streaming_cursor(config: Config):
	"""Context manager yielding a server-side streaming cursor (SSDictCursor).

	Issues READ COMMITTED isolation on the connection before streaming.
	"""
	conn = get_connection(config)
	cursor = None
	try:
		cursor = conn.cursor(pymysql.cursors.SSDictCursor)
		yield cursor
	finally:
		if cursor is not None:
			cursor.close()
		conn.close()
