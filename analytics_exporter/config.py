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
	# SSH / remote transfer (optional — leave blank for export-only mode)
	ssh_host: str = ""
	ssh_user: str = ""
	ssh_key_path: str = ""
	ssh_port: int = 22
	ssh_timeout: int = 300
	remote_analytics_path: str = "/data/analytics"

	def has_ssh_config(self) -> bool:
		"""Return True if SSH host and user are configured."""
		return bool(self.ssh_host and self.ssh_user)

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
			ssh_host=os.environ.get("ANALYTICS_SSH_HOST", ""),
			ssh_user=os.environ.get("ANALYTICS_SSH_USER", ""),
			ssh_key_path=os.environ.get("ANALYTICS_SSH_KEY_PATH", ""),
			ssh_port=int(os.environ.get("ANALYTICS_SSH_PORT", "22")),
			ssh_timeout=int(os.environ.get("ANALYTICS_SSH_TIMEOUT", "300")),
			remote_analytics_path=os.environ.get("ANALYTICS_REMOTE_PATH", "/data/analytics"),
		)
