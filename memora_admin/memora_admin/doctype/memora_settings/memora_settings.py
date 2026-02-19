# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MemoraSettings(Document):
	pass


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
