"""Analytics exporter configuration loaded from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
	db_host: str
	db_port: int
	db_user: str
	db_password: str
	db_name: str
	analytics_output_path: str
	analytics_schema_path: str
	analytics_chunk_size: int
	analytics_log_path: str
	analytics_mode: str
	analytics_datasets: list
	analytics_interaction_from: str | None = None
	analytics_interaction_to: str | None = None

	@classmethod
	def from_env(cls) -> "Config":
		datasets_raw = os.environ.get("ANALYTICS_DATASETS", "")
		datasets = [d.strip() for d in datasets_raw.split(",") if d.strip()] if datasets_raw else []
		return cls(
			db_host=os.environ["DB_HOST"],
			db_port=int(os.environ.get("DB_PORT", "3306")),
			db_user=os.environ["DB_USER"],
			db_password=os.environ["DB_PASSWORD"],
			db_name=os.environ["DB_NAME"],
			analytics_output_path=os.environ.get("ANALYTICS_OUTPUT_PATH", "analytics_exports"),
			analytics_schema_path=os.environ.get("ANALYTICS_SCHEMA_PATH", "analytics_exporter/schemas"),
			analytics_chunk_size=int(os.environ.get("ANALYTICS_CHUNK_SIZE", "50000")),
			analytics_log_path=os.environ.get("ANALYTICS_LOG_PATH", "logs/analytics_exporter.log"),
			analytics_mode=os.environ.get("ANALYTICS_MODE", "auto"),
			analytics_datasets=datasets,
			analytics_interaction_from=os.environ.get("ANALYTICS_INTERACTION_FROM") or None,
			analytics_interaction_to=os.environ.get("ANALYTICS_INTERACTION_TO") or None,
		)
