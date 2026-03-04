"""
Cloudflare CDN cache purge service.

Wraps Cloudflare v4 API for cache invalidation:
- purge_files: per-URL purge (batched at 30 URLs per request, 1 retry on error)
- purge_all: full zone purge
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

_CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
_BATCH_SIZE = 30
_RETRY_DELAY = 2  # seconds between retry attempts


class CloudflarePurgeService:
	def __init__(self, zone_id: str, api_token: str, cdn_base_url: str) -> None:
		self._zone_id = zone_id
		self._api_token = api_token
		self._cdn_base_url = cdn_base_url.rstrip("/")
		self._url = f"{_CLOUDFLARE_API_BASE}/zones/{zone_id}/purge_cache"
		self._headers = {
			"Authorization": f"Bearer {api_token}",
			"Content-Type": "application/json",
		}

	def purge_files(self, filenames: list[str]) -> bool:
		"""
		Purge specific files from Cloudflare edge cache.

		Args:
			filenames: Relative file paths from the build pipeline
			           (e.g., ["_subjects.json", "plans/PLAN-00001/manifest.json"])

		Returns:
			True if all batches succeed, False if any batch fails.
			Never raises exceptions.
		"""
		if not filenames:
			return True

		# Construct full CDN URLs from relative filenames
		urls = [f"{self._cdn_base_url}/files/cdn/{fn.lstrip('/')}" for fn in filenames]

		all_success = True
		for i in range(0, len(urls), _BATCH_SIZE):
			batch = urls[i : i + _BATCH_SIZE]
			if not self._make_request({"files": batch}):
				all_success = False

		return all_success

	def purge_all(self) -> bool:
		"""
		Purge the entire Cloudflare zone cache.

		Returns:
			True if successful, False if failed.
			Never raises exceptions.
		"""
		return self._make_request({"purge_everything": True})

	def _make_request(self, payload: dict) -> bool:
		"""
		Make a single Cloudflare API request with one retry on transient failures.

		No retry on 4xx (configuration error — won't self-heal).
		Logs all failures to Frappe Error Log for visibility.

		Returns:
			True on success, False on failure.
		"""
		for attempt in range(2):
			try:
				resp = requests.post(self._url, headers=self._headers, json=payload, timeout=10)

				if 400 <= resp.status_code < 500:
					# Client error — no retry, log and return False immediately
					try:
						errors = resp.json().get("errors", [])
					except Exception:
						errors = []
					logger.error(
						f"Cloudflare purge failed (4xx, no retry): status={resp.status_code} errors={errors}"
					)
					_log_error(
						"CDN Purge Failed (4xx)",
						f"status={resp.status_code} errors={errors} payload={payload}",
					)
					return False

				if resp.status_code == 200:
					try:
						data = resp.json()
					except Exception:
						data = {}
					if data.get("success"):
						logger.info(f"Cloudflare purge succeeded (attempt {attempt + 1})")
						return True
					# 200 but success=false — treat as retryable
					errors = data.get("errors", [])
					logger.warning(
						f"Cloudflare purge returned success=false (attempt {attempt + 1}): {errors}"
					)
				else:
					# 5xx or unexpected status — log and retry
					logger.warning(
						f"Cloudflare purge failed: status={resp.status_code} (attempt {attempt + 1})"
					)

			except requests.RequestException as e:
				logger.warning(f"Cloudflare purge request error (attempt {attempt + 1}): {e}")

			# Wait before retrying (only before second attempt)
			if attempt == 0:
				time.sleep(_RETRY_DELAY)

		# All attempts exhausted — log to Frappe Error Log
		_log_error("CDN Purge Failed", f"All retry attempts exhausted. payload={payload}")
		return False


def _log_error(title: str, message: str) -> None:
	"""Log to Frappe Error Log, silently ignore if Frappe is unavailable."""
	try:
		import frappe

		frappe.log_error(title=title, message=message)
	except Exception:
		logger.error(f"{title}: {message}")
