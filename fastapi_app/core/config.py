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
    redis_url: str = "redis://localhost:6379/0"
    redis_key_prefix: str = "memora:"

    # JWT Configuration
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # Paths
    bitmap_json_path: str

    # Environment
    environment: str = "development"
    api_version: str = "v1"

    # Logging
    slow_redis_threshold_ms: int = 50

    # Auth Token Configuration
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 30

    # Frappe Integration
    frappe_url: str = "http://localhost:8000"
    frappe_site: str = "x.conanacademy.com"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
