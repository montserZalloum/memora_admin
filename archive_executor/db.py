"""Database connection and helpers for the archive executor."""

from contextlib import contextmanager

import pymysql
import pymysql.cursors

from .config import Config


def get_connection(config: Config) -> pymysql.Connection:
	"""Create a new pymysql connection."""
	return pymysql.connect(
		host=config.db_host,
		port=config.db_port,
		user=config.db_user,
		password=config.db_password,
		database=config.db_name,
		charset="utf8mb4",
		cursorclass=pymysql.cursors.DictCursor,
	)


@contextmanager
def streaming_cursor(config: Config):
	"""Context manager yielding a server-side cursor for streaming large result sets.

	Uses SSCursor (unbuffered) to avoid loading all rows into memory.
	"""
	conn = get_connection(config)
	try:
		cursor = conn.cursor(pymysql.cursors.SSDictCursor)
		yield cursor
	finally:
		cursor.close()
		conn.close()


def atomic_update(config: Config, sql: str, params: tuple = ()) -> int:
	"""Execute an UPDATE statement atomically. Returns affected row count."""
	conn = get_connection(config)
	try:
		with conn.cursor() as cursor:
			cursor.execute(sql, params)
		conn.commit()
		return cursor.rowcount
	finally:
		conn.close()
