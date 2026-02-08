"""Catalog cache invalidation handlers for Frappe doc_events.

When Product Grant documents change in Frappe, clear the corresponding
plan's catalog cache in Redis and notify the FastAPI sidecar via pubsub.

Per 21-CONTEXT.md: catalog cache has NO TTL, invalidation is event-driven only.
"""

import json

import frappe

from memora_admin.events.access_sync import get_fastapi_redis


def on_product_grant_changed(doc, method):
	"""Invalidate catalog cache when a Product Grant is created/updated/deleted.

	Two-pronged invalidation:
	1. Direct r.delete() for immediate cache clear (even if FastAPI pubsub has delay)
	2. Pubsub publish so FastAPI sidecar's in-process CatalogService also invalidates

	Args:
		doc: Memora Product Grant document
		method: Frappe hook method name (after_insert, on_update, on_trash)
	"""
	plan_id = doc.plan
	if not plan_id:
		frappe.logger().warning("Product Grant %s has no plan, skipping catalog invalidation", doc.name)
		return

	r = get_fastapi_redis()

	# 1. Direct cache delete (immediate effect)
	r.delete(f"memora:catalog:{plan_id}")

	# 2. Pubsub notification for FastAPI sidecar
	r.publish(
		"memora:cache:invalidate",
		json.dumps({
			"type": "catalog",
			"plan_id": plan_id,
			"timestamp": str(frappe.utils.now()),
		}),
	)

	frappe.logger().info(f"Catalog cache invalidated for plan {plan_id}")
