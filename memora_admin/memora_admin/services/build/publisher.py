"""
CDN publisher with retry logic and atomic swap pattern.

Publishes generated JSON files to CDN storage using:
- Temp location upload first (staging)
- Atomic swap to final location
- Retry with exponential backoff on failures
- Cleanup on success or failure
"""

from __future__ import annotations

import logging
import time
from typing import Any

import frappe

from memora_admin.memora_admin.services.build.storage import get_storage_backend

logger = logging.getLogger(__name__)


def publish_to_cdn(files: list[dict], max_retries: int = 3) -> bool:
	"""
	Publish generated files to CDN with retry and atomic swap.

	Args:
	    files: List of file dicts from generator:
	           [{"filename": "track_001.json", "content": "...", "subject_id": "..."}]
	    max_retries: Number of upload attempts per file (default: 3)

	Returns:
	    True if all files published successfully, False otherwise
	"""
	storage = get_storage_backend()

	# Create temp prefix for staging uploads
	temp_prefix = f"_temp_{int(time.time() * 1000)}/"

	# Flatten file structure (generator returns flat list, but handles nested children)
	flat_files = _flatten_files(files)

	if not flat_files:
		logger.warning("No files to publish")
		return True

	# Track uploaded temp keys for cleanup
	uploaded_temp_keys: list[str] = []
	# Track (temp_key, final_key) pairs for swap
	key_pairs: list[tuple[str, str]] = []

	# Phase 1: Upload to temp location
	for file_dict in flat_files:
		filename = file_dict["filename"]
		content = file_dict["content"]

		# Build keys
		temp_key = f"{temp_prefix}{filename}"
		final_key = filename

		# Encode content to bytes
		content_bytes = content.encode("utf-8") if isinstance(content, str) else content

		# Attempt upload with retry
		success = False
		last_error = None

		for attempt in range(max_retries):
			try:
				storage.upload(temp_key, content_bytes)
				uploaded_temp_keys.append(temp_key)
				key_pairs.append((temp_key, final_key))
				success = True
				logger.debug(f"Uploaded {filename} to temp location (attempt {attempt + 1})")
				break
			except Exception as e:
				last_error = e
				if attempt < max_retries - 1:
					# Exponential backoff: 2^attempt seconds
					sleep_time = 2**attempt
					logger.warning(
						f"Upload failed for {filename} (attempt {attempt + 1}/{max_retries}), "
						f"retrying in {sleep_time}s: {e}"
					)
					time.sleep(sleep_time)
				else:
					logger.error(f"Upload failed for {filename} after {max_retries} attempts: {e}")

		if not success:
			# Cleanup uploaded temp files
			_cleanup_temp_files(storage, uploaded_temp_keys)

			# Log error
			frappe.log_error(
				title=f"CDN Upload Failed: {filename}",
				message=f"Failed to upload after {max_retries} attempts.\nLast error: {last_error}",
			)
			return False

	# Phase 2: Atomic swap (copy from temp to final location)
	for temp_key, final_key in key_pairs:
		try:
			# Read from temp location
			content = storage.read(temp_key)
			if content is None:
				logger.error(f"Temp file missing during swap: {temp_key}")
				_cleanup_temp_files(storage, uploaded_temp_keys)
				return False

			# Write to final location (atomic via LocalStorageBackend)
			storage.upload(final_key, content)
			logger.debug(f"Swapped {temp_key} -> {final_key}")

		except Exception as e:
			logger.error(f"Atomic swap failed for {final_key}: {e}")
			frappe.log_error(
				title=f"CDN Swap Failed: {final_key}",
				message=str(e),
			)
			_cleanup_temp_files(storage, uploaded_temp_keys)
			return False

	# Phase 3: Cleanup temp files (best effort)
	_cleanup_temp_files(storage, uploaded_temp_keys)

	logger.info(f"Published {len(flat_files)} files to CDN")
	return True


def _flatten_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""
	Flatten nested file structure from generator.

	Recursively collects all files, including any nested "children" arrays.

	Args:
	    files: List of file dicts, potentially with nested "children" key

	Returns:
	    Flat list of {filename, content} dicts
	"""
	result: list[dict[str, Any]] = []

	for file_dict in files:
		# Skip if missing required fields
		if "filename" not in file_dict or "content" not in file_dict:
			continue

		# Add this file (copy only filename and content)
		result.append({
			"filename": file_dict["filename"],
			"content": file_dict["content"],
		})

		# Recursively flatten children if present
		if "children" in file_dict and isinstance(file_dict["children"], list):
			child_files = _flatten_files(file_dict["children"])
			result.extend(child_files)

	return result


def _cleanup_temp_files(storage: Any, temp_keys: list[str]) -> None:
	"""
	Clean up temporary files (best effort).

	Args:
	    storage: StorageBackend instance
	    temp_keys: List of temp file keys to delete
	"""
	for key in temp_keys:
		try:
			storage.delete(key)
		except Exception as e:
			# Best effort - don't fail if cleanup fails
			logger.debug(f"Failed to cleanup temp file {key}: {e}")
