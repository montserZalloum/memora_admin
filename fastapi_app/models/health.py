"""Pydantic models for Redis health monitoring."""

from typing import Literal

from pydantic import BaseModel


class RedisHealthReport(BaseModel):
	"""Redis health and metrics report.

	Returned by GET /api/v1/health/redis.
	Status thresholds:
	- healthy: memory <80%, buffer <10000, dirty sets <1000
	- degraded: memory 80-95% OR buffer 10000-50000 OR dirty sets >1000
	- unhealthy: memory >95% OR buffer >50000 OR Redis unreachable
	"""

	status: Literal["healthy", "degraded", "unhealthy"]
	used_memory_mb: float
	max_memory_mb: float
	memory_usage_percent: float
	interaction_buffer_length: int
	dirty_wallets_count: int
	dirty_progress_count: int
	connected_clients: int
	aof_enabled: bool
	uptime_seconds: int
	total_keys: int
