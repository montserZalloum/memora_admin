"""Structured JSON logger for the archive executor."""

import json
import logging
import os
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
	"""Formats log records as single-line JSON objects."""

	def format(self, record: logging.LogRecord) -> str:
		entry = {
			"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
			"level": record.levelname.lower(),
			"event": record.getMessage(),
		}
		if hasattr(record, "extra_fields"):
			entry.update(record.extra_fields)
		return json.dumps(entry, default=str)


class StructuredLogger:
	"""Logger that writes structured JSON lines to a log file."""

	def __init__(self, log_path: str):
		self._logger = logging.getLogger("archive_executor")
		self._logger.setLevel(logging.DEBUG)
		self._logger.handlers.clear()

		os.makedirs(log_path, exist_ok=True)
		file_path = os.path.join(log_path, "archive.log")
		handler = logging.FileHandler(file_path, encoding="utf-8")
		handler.setFormatter(JSONFormatter())
		self._logger.addHandler(handler)

	def _log(self, level: int, event: str, **kwargs):
		record = self._logger.makeRecord(
			name="archive_executor",
			level=level,
			fn="",
			lno=0,
			msg=event,
			args=(),
			exc_info=None,
		)
		record.extra_fields = kwargs
		self._logger.handle(record)

	def info(self, event: str, **kwargs):
		self._log(logging.INFO, event, **kwargs)

	def warning(self, event: str, **kwargs):
		self._log(logging.WARNING, event, **kwargs)

	def error(self, event: str, **kwargs):
		self._log(logging.ERROR, event, **kwargs)

	def debug(self, event: str, **kwargs):
		self._log(logging.DEBUG, event, **kwargs)
