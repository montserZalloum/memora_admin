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

	# SSH / Transfer
	ssh_host: str
	ssh_user: str
	ssh_key_path: str
	ssh_port: int
	ssh_timeout: int
	remote_archive_path: str
	remote_live_path: str

	# Analytics server
	analytics_cmd_path: str
	duckdb_path: str

	# Live Sync
	live_output_path: str
	live_lock_file: str

	# Incremental Sync (Memory State)
	sync_state_path: str
	sync_output_path: str
	sync_overlap_seconds: int
	sync_remote_path: str

	# Purge safety
	purge_grace_days: int

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
			# SSH / Transfer
			ssh_host=os.environ.get("ANALYTICS_SSH_HOST", ""),
			ssh_user=os.environ.get("ANALYTICS_SSH_USER", ""),
			ssh_key_path=os.environ.get("ANALYTICS_SSH_KEY_PATH", ""),
			ssh_port=int(os.environ.get("ANALYTICS_SSH_PORT", "22")),
			ssh_timeout=int(os.environ.get("ANALYTICS_SSH_TIMEOUT", "300")),
			remote_archive_path=os.environ.get("REMOTE_ARCHIVE_PATH", ""),
			remote_live_path=os.environ.get("REMOTE_LIVE_PATH", ""),
			# Analytics server
			analytics_cmd_path=os.environ.get("ANALYTICS_CMD_PATH", "/opt/analytics/memora-analytics"),
			duckdb_path=os.environ.get("REMOTE_DUCKDB_PATH", ""),
			# Live Sync
			live_output_path=os.environ.get("LIVE_OUTPUT_PATH", "/data/memora/live/"),
			live_lock_file=os.environ.get("LIVE_LOCK_FILE", "/var/run/memora-live-sync.lock"),
			# Incremental Sync (Memory State)
			sync_state_path=os.environ.get("SYNC_STATE_PATH", "/data/memora/sync_state/"),
			sync_output_path=os.environ.get("SYNC_OUTPUT_PATH", "/data/memora/sync_output/"),
			sync_overlap_seconds=int(os.environ.get("SYNC_OVERLAP_SECONDS", "300")),
			sync_remote_path=os.environ.get("SYNC_REMOTE_PATH", ""),
			# Purge safety
			purge_grace_days=int(os.environ.get("PURGE_GRACE_DAYS", "7")),
		)

	def has_ssh_config(self) -> bool:
		"""Returns True if SSH host/user/key are all configured."""
		return bool(self.ssh_host and self.ssh_user and self.ssh_key_path)
