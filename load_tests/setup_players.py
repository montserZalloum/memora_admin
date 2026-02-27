#!/usr/bin/env python3
"""Create test players and subscriptions for load testing.

Run from bench root: python3 apps/memora_admin/load_tests/setup_players.py
"""

import sys
import os

# Add bench apps to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import frappe

SITE = "x.conanacademy.com"
SEASON = "SEAS-00635"
PLAN = "PLAN-00572"
GRADE = "GRD-00025"
MAJOR = "MJR-00026"
PASSWORD = "LoadTest2026!"
AVATAR = "pre"

MOBILES = [f"078000000{i}" for i in range(1, 6)]

SUBJECTS = [
	"SUBJ-00704",
	"SUBJ-00705",
	"SUBJ-00706",
	"SUBJ-00707",
	"SUBJ-00708",
]


def main():
	frappe.init(site=SITE, sites_path="/home/corex/aurevia-bench/sites")
	frappe.connect()
	frappe.set_user("Administrator")

	player_ids = []

	for mobile in MOBILES:
		player_id = _create_or_get_player(mobile)
		player_ids.append(player_id)
		_ensure_subscriptions(player_id)

	frappe.db.commit()

	# Hydrate Redis access cache
	_hydrate_redis(player_ids)

	print("\n=== Setup Complete ===")
	for mobile, pid in zip(MOBILES, player_ids):
		print(f"  {mobile} -> {pid}")
	print(f"  Subjects: {SUBJECTS}")
	print(f"  Password: {PASSWORD}")

	frappe.destroy()


def _create_or_get_player(mobile: str) -> str:
	"""Create a player or return existing one."""
	from frappe.utils.password import update_password

	existing = frappe.db.get_value("Memora Player Profile", {"mobile": mobile}, "name")
	if existing:
		print(f"  Player {mobile} already exists: {existing}")
		update_password(existing, PASSWORD, doctype="Memora Player Profile", fieldname="password")
		return existing

	doc = frappe.get_doc(
		{
			"doctype": "Memora Player Profile",
			"mobile": mobile,
			"password": PASSWORD,
			"plan": PLAN,
			"grade": GRADE,
			"major": MAJOR,
			"season": SEASON,
			"display_name": f"LoadTest {mobile[-1]}",
			"avatar": AVATAR,
		}
	)
	doc.insert(ignore_permissions=True)
	print(f"  Created player {mobile}: {doc.name}")

	# Initialize Redis wallet
	try:
		from fastapi_app.core.redis_keys import WALLET_KEY_TTL, wallet_key
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()
		wk = wallet_key(doc.name)
		r.hset(wk, mapping={"xp": 0, "streak": 0})
		r.expire(wk, WALLET_KEY_TTL)
	except Exception as e:
		print(f"    Warning: Redis wallet init failed: {e}")

	return doc.name


def _ensure_subscriptions(player_id: str):
	"""Create subscriptions for all subjects if not already present."""
	for subject_id in SUBJECTS:
		access_key = f"SUB-{subject_id}"
		existing = frappe.db.get_value(
			"Memora Player Subscription",
			{"player": player_id, "access_key": access_key},
			"name",
		)
		if existing:
			frappe.db.set_value("Memora Player Subscription", existing, "is_active", 1)
			continue

		doc = frappe.get_doc(
			{
				"doctype": "Memora Player Subscription",
				"player": player_id,
				"access_key": access_key,
				"expires_at": "2027-01-01",
				"is_active": 1,
			}
		)
		doc.insert(ignore_permissions=True)
		print(f"    Subscription: {access_key} for {player_id}")


def _hydrate_redis(player_ids: list[str]):
	"""Push access keys into Redis for immediate FastAPI access."""
	try:
		from fastapi_app.core.redis_keys import ACCESS_KEY_TTL, access_key as access_key_fn
		from memora_admin.utils.redis_connection import get_memora_redis

		r = get_memora_redis()
		for player_id in player_ids:
			redis_key = access_key_fn(player_id)
			r.delete(redis_key)
			keys = [f"SUB-{s}" for s in SUBJECTS]
			r.sadd(redis_key, *keys)
			r.expire(redis_key, ACCESS_KEY_TTL)
			members = r.smembers(redis_key)
			print(f"  Redis {redis_key}: {len(members)} keys")
	except Exception as e:
		print(f"  Warning: Redis hydration failed: {e}")


if __name__ == "__main__":
	main()
