"""Application configuration using pydantic-settings."""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
	"""Application settings loaded from environment variables."""

	model_config = SettingsConfigDict(
		env_file=".env",
		env_file_encoding="utf-8",
		extra="ignore",
	)

	# Redis Configuration
	# CRITICAL: Must match Frappe's redis_cache in common_site_config.json
	# If not set in .env, app will fail to start (prevents silent port mismatches)
	redis_url: str  # REQUIRED in .env - no default

	# JWT Configuration
	jwt_secret: str  # REQUIRED in .env - no default
	jwt_algorithm: str = "HS256"

	# Paths
	bitmap_json_path: str  # REQUIRED in .env - no default

	# Environment
	environment: str = "development"
	api_version: str = "v1"

	# Logging
	slow_redis_threshold_ms: int = 50

	# Auth Token Configuration
	jwt_access_token_expire_minutes: int = 60
	jwt_refresh_token_expire_days: int = 30

	# Frappe Integration
	# REQUIRED in .env - no defaults (fail fast if not configured)
	frappe_url: str  # REQUIRED in .env - no default
	frappe_site: str  # REQUIRED in .env - no default
	frappe_api_key: str = ""
	frappe_api_secret: str = ""

	# Voucher Configuration
	# IMPORTANT: Must match voucher_hmac_secret in Frappe site_config.json
	voucher_hmac_secret: str = ""

	# Rate Limiting
	global_rate_limit: int = 100  # Max requests per IP per window
	global_rate_limit_window: int = 60  # Window duration in seconds
	reviews_rate_limit: int = 30  # Max review submits per player per window
	session_rate_limit: int = 10  # Max session start/end per player per window
	ws_max_connections_per_user: int = 5  # Max concurrent WebSocket connections

	# Live Challenge Rate Limits
	lc_join_rate_limit: int = 5
	lc_submit_rate_limit: int = 2

	# Challenge Hub Rate Limits
	ch_hierarchy_rate_limit: int = 10
	ch_attempt_rate_limit: int = 30
	ch_leaderboard_rate_limit: int = 10

	# Practice Arena Session Settings
	practice_session_size: int = 20
	practice_session_ttl: int = 3600

	# Practice Arena — Map file location
	practice_maps_dir: str = ""  # Path to practice/maps/ directory

	# Scaling: Redis Connection Pool
	redis_max_connections: int = 20  # Pool size per uvicorn worker

	# Scaling: WebSocket Broadcast
	ws_broadcast_concurrency: int = 0  # 0=sequential, >0=parallel with semaphore

	# Scaling: Rate Limiter Fail Behavior
	rate_limit_fail_open: bool = True  # True=pass on Redis failure, False=503

	# Scaling: Upstream Frappe API Client
	frappe_timeout: float = 30.0  # HTTP timeout in seconds
	frappe_max_connections: int = 100  # Connection pool size
	frappe_max_keepalive: int = 20  # Keepalive connection pool size

	@field_validator("redis_max_connections")
	@classmethod
	def redis_max_connections_ge_1(cls, v: int) -> int:
		if v < 1:
			raise ValueError("redis_max_connections must be >= 1")
		return v

	@field_validator("ws_broadcast_concurrency")
	@classmethod
	def ws_broadcast_concurrency_ge_0(cls, v: int) -> int:
		if v < 0:
			raise ValueError("ws_broadcast_concurrency must be >= 0")
		return v

	@field_validator("frappe_timeout")
	@classmethod
	def frappe_timeout_gt_0(cls, v: float) -> float:
		if v <= 0:
			raise ValueError("frappe_timeout must be > 0")
		return v

	@field_validator("frappe_max_connections")
	@classmethod
	def frappe_max_connections_ge_1(cls, v: int) -> int:
		if v < 1:
			raise ValueError("frappe_max_connections must be >= 1")
		return v

	@model_validator(mode="after")
	def keepalive_le_max_connections(self) -> "Settings":
		if self.frappe_max_keepalive < 0:
			raise ValueError("frappe_max_keepalive must be >= 0")
		if self.frappe_max_keepalive > self.frappe_max_connections:
			raise ValueError(
				f"frappe_max_keepalive ({self.frappe_max_keepalive}) "
				f"must be <= frappe_max_connections ({self.frappe_max_connections})"
			)
		return self


@lru_cache
def get_settings() -> Settings:
	"""Get cached settings instance."""
	return Settings()
