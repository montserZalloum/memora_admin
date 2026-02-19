"""
Factory function for the CDN purge service.

Reads Memora Settings singleton to determine CDN configuration
and returns an appropriate service instance (or None).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_purge_service():
	"""
	Return a configured CloudflarePurgeService, or None if CDN is not enabled/configured.

	Reads from Memora Settings singleton. Returns None with a warning log
	if CDN is enabled but required fields (zone_id, api_token, cdn_base_url) are missing.
	"""
	try:
		import frappe

		settings = frappe.get_single("Memora Settings")

		if not settings.cdn_enabled:
			return None

		if settings.storage_provider != "Cloudflare CDN":
			return None

		zone_id = settings.cloudflare_zone_id
		api_token = settings.get_password("access_key")
		cdn_base_url = settings.cdn_base_url

		if not zone_id or not api_token or not cdn_base_url:
			logger.warning(
				"CDN is enabled but Cloudflare configuration is incomplete "
				f"(zone_id={bool(zone_id)}, api_token={bool(api_token)}, cdn_base_url={bool(cdn_base_url)})"
			)
			return None

		from memora_admin.memora_admin.services.cdn.cloudflare import CloudflarePurgeService

		return CloudflarePurgeService(
			zone_id=zone_id,
			api_token=api_token,
			cdn_base_url=cdn_base_url,
		)

	except Exception as e:
		logger.error(f"Failed to initialize CDN purge service: {e}")
		return None
