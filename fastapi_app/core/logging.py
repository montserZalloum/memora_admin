"""Structured logging configuration using structlog."""

import logging
import sys
from logging.handlers import QueueHandler, QueueListener
from queue import SimpleQueue

import structlog


def configure_logging(environment: str) -> None:
	"""Configure structured logging based on environment.

	Uses QueueHandler + QueueListener so all log writes happen in a background
	daemon thread instead of blocking the asyncio event loop.

	Args:
	    environment: The environment name (development, production, etc.)
	"""
	# Shared processors for all environments
	shared_processors: list[structlog.types.Processor] = [
		structlog.stdlib.add_log_level,
		structlog.processors.TimeStamper(fmt="iso"),
		structlog.contextvars.merge_contextvars,
		structlog.processors.StackInfoRenderer(),
		structlog.processors.UnicodeDecoder(),
	]

	# Environment-specific renderer
	if environment == "production":
		renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
	else:
		renderer = structlog.dev.ConsoleRenderer(colors=True)

	# Configure structlog
	structlog.configure(
		processors=[
			*shared_processors,
			structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
		],
		logger_factory=structlog.stdlib.LoggerFactory(),
		wrapper_class=structlog.stdlib.BoundLogger,
		cache_logger_on_first_use=True,
	)

	# Configure standard library logging to use structlog
	formatter = structlog.stdlib.ProcessorFormatter(
		foreign_pre_chain=shared_processors,
		processors=[
			structlog.stdlib.ProcessorFormatter.remove_processors_meta,
			renderer,
		],
	)

	# Actual I/O handler (runs in background thread via QueueListener)
	stream_handler = logging.StreamHandler(sys.stdout)
	stream_handler.setFormatter(formatter)

	# Non-blocking queue: QueueHandler.emit() is O(1) in-memory put — no I/O.
	# QueueListener drains the queue in a background daemon thread so synchronous
	# log writes never block the asyncio event loop.
	log_queue: SimpleQueue = SimpleQueue()
	queue_handler = QueueHandler(log_queue)
	listener = QueueListener(log_queue, stream_handler, respect_handler_level=True)
	listener.start()

	root_logger = logging.getLogger()
	root_logger.handlers.clear()
	root_logger.addHandler(queue_handler)
	root_logger.setLevel(logging.INFO)

	# Set uvicorn loggers to use our handler
	for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
		logger = logging.getLogger(logger_name)
		logger.handlers.clear()
		logger.addHandler(queue_handler)
		logger.setLevel(logging.INFO)
