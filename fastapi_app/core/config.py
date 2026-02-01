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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
