"""Level Settings sync: Frappe → Redis on save.

Pushes level config (curve params + titles) to Redis for FastAPI consumption.
Follows the two-pronged pattern: direct SET + pubsub publish.
"""

import json
from datetime import datetime

import frappe

from fastapi_app.core.redis_keys import cache_invalidation_channel, level_config_key
from memora_admin.utils.redis_connection import get_memora_redis


def on_level_settings_updated(doc, method):
	"""Push level config to Redis when Memora Level Settings is saved."""
	try:
		payload = json.dumps(
			{
				"a": doc.quadratic_coefficient,
				"b": doc.linear_coefficient,
				"max_level": doc.max_level,
				"titles": {row.level_number: row.title_en for row in doc.level_titles},
			}
		)

		r = get_memora_redis()
		r.set(level_config_key(), payload, ex=3600)
		r.publish(
			cache_invalidation_channel(),
			json.dumps(
				{
					"type": "level_config",
					"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
				}
			),
		)

		frappe.logger().info(
			f"Level config synced to Redis: a={doc.quadratic_coefficient}, b={doc.linear_coefficient}, max={doc.max_level}"
		)
	except Exception as e:
		frappe.logger().error(f"Failed to sync level config to Redis: {e}")
