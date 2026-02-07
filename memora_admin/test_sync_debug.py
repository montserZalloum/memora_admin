"""Debug script for testing sync - run via: bench --site x.conanacademy.com execute memora_admin.test_sync_debug.main"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def main():
    """Manually test the interaction buffer flush"""
    try:
        import frappe
        import redis

        logger.info("=" * 70)
        logger.info("MANUAL SYNC TEST")
        logger.info("=" * 70)

        # Get Redis connection
        r = redis.from_url(frappe.conf.redis_cache)

        INTERACTION_BUFFER_KEY = "memora:buffer:interactions"
        BATCH_SIZE = 1000

        # Get batch of items
        items = r.lrange(INTERACTION_BUFFER_KEY, 0, BATCH_SIZE - 1)
        logger.info(f"\nFound {len(items)} items in Redis buffer")

        if not items:
            logger.info("No interactions to flush")
            return

        count = len(items)
        inserted = 0
        errors = []

        for i, item_bytes in enumerate(items[:3]):  # Test only first 3
            try:
                # Parse JSON
                item_str = item_bytes.decode() if isinstance(item_bytes, bytes) else item_bytes
                item = json.loads(item_str)

                logger.info(f"\nProcessing item {i+1}:")
                logger.info(f"  Player: {item['player']}")
                logger.info(f"  Lesson: {item['lesson']}")
                logger.info(f"  Stage ID: {item.get('stage_id', 'N/A')}")

                # Check if player exists
                player_exists = frappe.db.exists("Memora Player Profile", item["player"])
                logger.info(f"  Player exists in DB: {bool(player_exists)}")

                # Check if lesson exists
                lesson_exists = frappe.db.exists("Memora Lesson", item["lesson"])
                logger.info(f"  Lesson exists in DB: {bool(lesson_exists)}")

                # Try to insert
                logger.info(f"  Creating doc...")
                doc = frappe.get_doc({
                    "doctype": "Memora Interaction Log",
                    "player": item["player"],
                    "lesson": item["lesson"],
                    "stage_id": str(item.get("stage_id", "")),
                    "event_type": item.get("event_type", "Completed"),
                    "time_spent": item.get("time_spent", 0),
                    "errors_count": item.get("errors_count", 0),
                    "timestamp": item.get("timestamp", datetime.now().isoformat()),
                    "client_metadata": json.dumps(item.get("metadata", {})),
                })

                logger.info(f"  Inserting...")
                result = doc.insert(ignore_permissions=True)
                logger.info(f"  ✓ SUCCESS: {result}")
                inserted += 1

            except Exception as e:
                logger.error(f"  ✗ FAILED: {e}", exc_info=True)
                errors.append(str(e))

        # Now try to commit
        logger.info(f"\nCommitting database changes...")
        try:
            frappe.db.commit()
            logger.info("✓ Commit successful")
        except Exception as e:
            logger.error(f"✗ Commit failed: {e}", exc_info=True)

        logger.info(f"\n" + "=" * 70)
        logger.info(f"RESULT: {inserted} inserted, {len(errors)} errors")
        logger.info("=" * 70)

        if errors:
            logger.error("Errors:")
            for err in errors:
                logger.error(f"  - {err}")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
