"""
Abstract storage backend interface for CDN uploads.

Provides a clean abstraction layer that can be swapped between:
- LocalStorageBackend: Local filesystem for development
- R2StorageBackend: Cloudflare R2 for production (future)
"""

from abc import ABC, abstractmethod


class StorageBackend(ABC):
	"""Abstract base class for storage backends."""

	@abstractmethod
	def upload(self, key: str, content: bytes, content_type: str = "application/json") -> str:
		"""
		Upload content to storage.

		Args:
		    key: The file path/key within the storage (e.g., "subject/track_001.json")
		    content: The bytes content to upload
		    content_type: MIME type of the content

		Returns:
		    Public URL of the uploaded file
		"""
		pass

	@abstractmethod
	def delete(self, key: str) -> bool:
		"""
		Delete a file from storage.

		Args:
		    key: The file path/key to delete

		Returns:
		    True if deleted successfully, False otherwise
		"""
		pass

	@abstractmethod
	def exists(self, key: str) -> bool:
		"""
		Check if a file exists in storage.

		Args:
		    key: The file path/key to check

		Returns:
		    True if file exists, False otherwise
		"""
		pass

	@abstractmethod
	def read(self, key: str) -> bytes | None:
		"""
		Read content from storage.

		Args:
		    key: The file path/key to read

		Returns:
		    File content as bytes, or None if not found
		"""
		pass

	@abstractmethod
	def delete_directory(self, key: str) -> bool:
		"""
		Delete a directory and all its contents from storage.

		Args:
		    key: The directory path/key to delete

		Returns:
		    True if deleted successfully, False otherwise
		"""
		pass
