"""Health check endpoints."""

import redis.asyncio as redis
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from fastapi_app.api.deps import RedisClient
from fastapi_app.core.config import get_settings
from fastapi_app.core.redis_keys import (
	dirty_progress_key,
	dirty_wallets_key,
	interaction_buffer_key,
)
from fastapi_app.models.health import RedisHealthReport

logger = structlog.get_logger()

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict:
	"""Liveness check - fast, no dependencies."""
	settings = get_settings()
	return {"status": "alive", "api_version": settings.api_version}


@router.get("/ready")
async def readiness(redis_client: RedisClient) -> JSONResponse:
	"""Readiness check - verifies Redis connection."""
	settings = get_settings()
	dependencies: dict[str, str] = {}
	overall_status = "ready"

	try:
		await redis_client.ping()
		dependencies["redis"] = "ok"
	except redis.ConnectionError:
		dependencies["redis"] = "unreachable"
		overall_status = "not_ready"

	status_code = 200 if overall_status == "ready" else 503
	return JSONResponse(
		status_code=status_code,
		content={
			"status": overall_status,
			"api_version": settings.api_version,
			"dependencies": dependencies,
		},
		headers={"Cache-Control": "no-store"},
	)


@router.get("/redis")
async def redis_health(redis_client: RedisClient) -> JSONResponse:
	"""Redis health and metrics — no authentication required.

	Returns RedisHealthReport with memory, buffer, dirty set, and connection metrics.
	Status: healthy / degraded / unhealthy based on thresholds.
	"""
	try:
		mem_info = await redis_client.info("memory")
		client_info = await redis_client.info("clients")
		persist_info = await redis_client.info("persistence")
		server_info = await redis_client.info("server")

		used_memory = mem_info.get("used_memory", 0)
		maxmemory = mem_info.get("maxmemory", 0)

		used_mb = round(used_memory / (1024 * 1024), 2)
		max_mb = round(maxmemory / (1024 * 1024), 2) if maxmemory else 0
		memory_pct = round((used_memory / maxmemory) * 100, 1) if maxmemory else 0

		buffer_len = await redis_client.llen(interaction_buffer_key())
		dirty_wallets = await redis_client.scard(dirty_wallets_key())
		dirty_progress = await redis_client.scard(dirty_progress_key())
		connected = client_info.get("connected_clients", 0)
		aof = bool(persist_info.get("aof_enabled", 0))
		uptime = server_info.get("uptime_in_seconds", 0)
		total_keys = await redis_client.dbsize()

		# Determine status
		if memory_pct > 95 or buffer_len > 50000:
			health_status = "unhealthy"
		elif (
			memory_pct > 80
			or maxmemory == 0
			or (10000 <= buffer_len <= 50000)
			or dirty_wallets > 1000
			or dirty_progress > 1000
		):
			health_status = "degraded"
		else:
			health_status = "healthy"

		report = RedisHealthReport(
			status=health_status,
			used_memory_mb=used_mb,
			max_memory_mb=max_mb,
			memory_usage_percent=memory_pct,
			interaction_buffer_length=buffer_len,
			dirty_wallets_count=dirty_wallets,
			dirty_progress_count=dirty_progress,
			connected_clients=connected,
			aof_enabled=aof,
			uptime_seconds=uptime,
			total_keys=total_keys,
		)

		status_code = 200 if health_status != "unhealthy" else 503
		return JSONResponse(status_code=status_code, content=report.model_dump())

	except Exception as e:
		logger.error("redis_health_check_failed", error=str(e), exc_info=True)
		report = RedisHealthReport(
			status="unhealthy",
			used_memory_mb=0,
			max_memory_mb=0,
			memory_usage_percent=0,
			interaction_buffer_length=0,
			dirty_wallets_count=0,
			dirty_progress_count=0,
			connected_clients=0,
			aof_enabled=False,
			uptime_seconds=0,
			total_keys=0,
		)
		return JSONResponse(status_code=503, content=report.model_dump())
