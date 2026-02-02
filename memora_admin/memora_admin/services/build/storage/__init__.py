"""
Storage backend abstraction for CDN uploads.

Exports:
- StorageBackend: Abstract base class
- LocalStorageBackend: Local filesystem implementation
- get_storage_backend: Factory function to get configured backend
"""

import frappe

from memora_admin.memora_admin.services.build.storage.base import StorageBackend
from memora_admin.memora_admin.services.build.storage.local import LocalStorageBackend

__all__ = ["StorageBackend", "LocalStorageBackend", "get_storage_backend"]


def get_storage_backend() -> StorageBackend:
	"""
	Get the configured storage backend.

	Currently returns LocalStorageBackend for development.
	R2StorageBackend can be added later based on site config.

	Returns:
	    Configured StorageBackend instance
	"""
	# Get Frappe site's public/files/cdn directory
	base_path = frappe.get_site_path("public", "files", "cdn")

	return LocalStorageBackend(base_path=base_path, base_url="/files/cdn")
