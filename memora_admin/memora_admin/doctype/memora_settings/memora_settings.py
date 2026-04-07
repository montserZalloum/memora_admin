# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraSettings(Document):
	pass


@frappe.whitelist()
def generate_vapid_keys():
	"""Generate VAPID key pair and save to Memora Settings.

	Uses py_vapid (bundled with pywebpush) to generate an ECDSA P-256 key pair.
	Keys are base64url-encoded per the Web Push standard.
	"""
	settings = frappe.get_single("Memora Settings")
	if settings.vapid_public_key:
		frappe.throw("VAPID keys already exist. Delete them manually before regenerating.")

	from memora_admin.utils.vapid import generate_vapid_keypair

	public_key_b64, private_key_b64 = generate_vapid_keypair()

	settings.vapid_public_key = public_key_b64
	settings.vapid_private_key = private_key_b64
	settings.save(ignore_permissions=True)
	frappe.db.commit()

	frappe.msgprint("VAPID keys generated successfully.", indicator="green", alert=True)


@frappe.whitelist()
def purge_all_cdn_cache():
	from memora_admin.memora_admin.services.cdn.utils import get_purge_service

	purge_service = get_purge_service()
	if purge_service is None:
		frappe.throw("CDN is not configured. Please enable CDN and fill in all Cloudflare settings.")

	success = purge_service.purge_all()
	if success:
		frappe.msgprint("CDN cache purged successfully.", indicator="green", alert=True)
	else:
		frappe.msgprint(
			"CDN cache purge failed. Check the error log for details.", indicator="red", alert=True
		)
