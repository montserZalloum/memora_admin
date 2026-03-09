"""Archive executor configuration loaded from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
	db_host: str
	db_port: int
	db_user: str
	db_password: str
	db_name: str
	archive_output_path: str
	schema_registry_path: str
	log_path: str
	lock_file: str
	chunk_size: int
	stuck_timeout_hours: int

	@classmethod
	def from_env(cls) -> "Config":
		return cls(
			db_host=os.environ["DB_HOST"],
			db_port=int(os.environ.get("DB_PORT", "3306")),
			db_user=os.environ["DB_USER"],
			db_password=os.environ["DB_PASSWORD"],
			db_name=os.environ["DB_NAME"],
			archive_output_path=os.environ.get("ARCHIVE_OUTPUT_PATH", "/data/memora/archives/"),
			schema_registry_path=os.environ["SCHEMA_REGISTRY_PATH"],
			log_path=os.environ.get("LOG_PATH", "/var/log/memora-archive/"),
			lock_file=os.environ.get("LOCK_FILE", "/var/run/memora-archive.lock"),
			chunk_size=int(os.environ.get("CHUNK_SIZE", "50000")),
			stuck_timeout_hours=int(os.environ.get("STUCK_TIMEOUT_HOURS", "1")),
		)
