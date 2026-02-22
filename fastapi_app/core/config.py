"""Application configuration using pydantic-settings."""

from functools import lru_cache

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
	redis_key_prefix: str = "memora:"

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


@lru_cache
def get_settings() -> Settings:
	"""Get cached settings instance."""
	return Settings()
