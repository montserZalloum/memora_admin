"""
Local filesystem storage backend for development.

Uses atomic temp-then-rename pattern for crash safety.
Files are stored in Frappe's public/files/cdn directory.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from memora_admin.memora_admin.services.build.storage.base import StorageBackend

logger = logging.getLogger(__name__)


class LocalStorageBackend(StorageBackend):
	"""
	Local filesystem storage backend.

	Implements atomic writes using tempfile + os.replace for crash safety.
	"""

	def __init__(self, base_path: str, base_url: str = "/files/cdn"):
		"""
		Initialize local storage backend.

		Args:
		    base_path: Absolute path to storage directory
		    base_url: URL prefix for generated public URLs
		"""
		self.base_path = Path(base_path)
		self.base_url = base_url.rstrip("/")

		# Create base directory if not exists
		self.base_path.mkdir(parents=True, exist_ok=True)

	def upload(self, key: str, content: bytes, content_type: str = "application/json") -> str:
		"""
		Upload content to local filesystem with atomic write.

		Uses temp file + os.replace for crash safety.

		Args:
		    key: Relative path within storage (e.g., "track_001.json")
		    content: Bytes content to write
		    content_type: MIME type (unused for local storage)

		Returns:
		    Public URL of the uploaded file
		"""
		target_path = self.base_path / key

		# Create parent directories if needed with proper permissions
		self._ensure_directory(target_path.parent)

		# Atomic write: temp file -> fsync -> rename
		fd = None
		temp_path = None

		try:
			# Create temp file in same directory (required for atomic rename)
			fd, temp_path = tempfile.mkstemp(dir=target_path.parent, prefix=".tmp_")

			# Write content
			os.write(fd, content)

			# Flush to disk
			os.fsync(fd)
			os.close(fd)
			fd = None

			# Atomic rename (os.replace is atomic on POSIX systems)
			os.replace(temp_path, target_path)

			# Set permissions to be world-readable and group-writable for web server access
			os.chmod(target_path, 0o664)

			logger.debug(f"Uploaded {key} to {target_path}")
			return f"{self.base_url}/{key}"

		except Exception as e:
			# Cleanup temp file on error
			if fd is not None:
				try:
					os.close(fd)
				except OSError:
					pass

			if temp_path and os.path.exists(temp_path):
				try:
					os.unlink(temp_path)
				except OSError:
					pass

			logger.error(f"Failed to upload {key}: {e}")
			raise

	def delete(self, key: str) -> bool:
		"""
		Delete a file from storage.

		Args:
		    key: Relative path within storage

		Returns:
		    True if deleted, False if not found or error
		"""
		target_path = self.base_path / key

		try:
			if target_path.exists():
				target_path.unlink()
				logger.debug(f"Deleted {key}")
				return True
			return False
		except OSError as e:
			logger.error(f"Failed to delete {key}: {e}")
			return False

	def exists(self, key: str) -> bool:
		"""
		Check if a file exists in storage.

		Args:
		    key: Relative path within storage

		Returns:
		    True if file exists
		"""
		return (self.base_path / key).exists()

	def read(self, key: str) -> bytes | None:
		"""
		Read content from storage.

		Args:
		    key: Relative path within storage

		Returns:
		    File content as bytes, or None if not found
		"""
		target_path = self.base_path / key

		try:
			if target_path.exists():
				return target_path.read_bytes()
			return None
		except OSError as e:
			logger.error(f"Failed to read {key}: {e}")
			return None

	def list_directory(self, prefix: str) -> list[str]:
		"""
		List all file keys under a prefix.

		Args:
		    prefix: Directory prefix to list (e.g., "plans/PLAN-001/")

		Returns:
		    List of file keys relative to base_path
		"""
		target_path = self.base_path / prefix
		if not target_path.exists() or not target_path.is_dir():
			return []

		result = []
		for file_path in target_path.rglob("*"):
			if file_path.is_file():
				# Return key relative to base_path
				result.append(str(file_path.relative_to(self.base_path)))
		return result

	def delete_directory(self, key: str) -> bool:
		"""
		Delete a directory and all its contents from storage.

		Args:
		    key: Relative path within storage (directory)

		Returns:
		    True if deleted, False if not found or error
		"""
		target_path = self.base_path / key

		try:
			if target_path.exists() and target_path.is_dir():
				import shutil

				shutil.rmtree(target_path)
				logger.debug(f"Deleted directory {key}")
				return True
			return False
		except OSError as e:
			logger.error(f"Failed to delete directory {key}: {e}")
			return False

	def _ensure_directory(self, path: Path) -> None:
		"""
		Create directory with proper permissions for web server access.

		Creates parent directories as needed and sets permissions to 0o2775
		(setgid + rwxrwxr-x) so new files/dirs inherit the www-data group
		and allow group write access for the web server.

		Args:
		    path: Directory path to create
		"""
		if path.exists():
			return

		# Create directory with parents
		path.mkdir(parents=True, exist_ok=True)

		# Fix permissions on all created directories (walk up to base_path)
		current = path
		while current != self.base_path and current.exists():
			try:
				# Set to setgid + rwxrwxr-x (group writable + inherit group for web server)
				os.chmod(current, 0o2775)
			except OSError as e:
				logger.warning(f"Failed to set permissions on {current}: {e}")
			current = current.parent
