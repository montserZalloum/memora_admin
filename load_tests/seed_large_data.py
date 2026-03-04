#!/usr/bin/env python3
"""Seed Redis with 5 large subjects (50,000 lessons each) for load testing.

Run from repo root:
    cd load_tests && python3 seed_large_data.py

Cleanup:
    cd load_tests && python3 seed_large_data.py --cleanup

Connection: Redis at redis://127.0.0.1:13001 (dedicated Memora instance).
"""

import argparse
import asyncio
import json
import random
import sys
import time

import redis.asyncio as aioredis

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL = "redis://127.0.0.1:13001"

SUBJECTS = [
	"LOAD-MATH-01",
	"LOAD-MATH-02",
	"LOAD-SCI-01",
	"LOAD-SCI-02",
	"LOAD-ARAB-01",
]

# Test players created by setup_players.py
PLAYER_IDS = [
	"PLAYER-00324",
	"PLAYER-00325",
	"PLAYER-00326",
	"PLAYER-00327",
	"PLAYER-00328",
]

# Structure: 10 tracks x 10 units x 10 topics x 50 lessons = 50,000
TRACKS_PER_SUBJECT = 10
UNITS_PER_TRACK = 10
TOPICS_PER_UNIT = 10
LESSONS_PER_TOPIC = 50
TOTAL_LESSONS = TRACKS_PER_SUBJECT * UNITS_PER_TRACK * TOPICS_PER_UNIT * LESSONS_PER_TOPIC  # 50,000

COMPLETION_RATE = 0.4  # 40% of lessons completed
BITMAP_VERSION = 1


# ---------------------------------------------------------------------------
# Hierarchy builder
# ---------------------------------------------------------------------------


def build_hierarchy(subject_id: str) -> dict:
	"""Build SubjectHierarchy JSON matching fastapi_app/models/progress.py schema."""
	tracks = []
	lesson_idx = 0

	for t in range(1, TRACKS_PER_SUBJECT + 1):
		track_id = f"TRK-{subject_id}-T{t:02d}"
		units = []

		for u in range(1, UNITS_PER_TRACK + 1):
			unit_id = f"UNT-{subject_id}-T{t:02d}-U{u:02d}"
			topics = []

			for p in range(1, TOPICS_PER_UNIT + 1):
				topic_id = f"TOP-{subject_id}-T{t:02d}-U{u:02d}-P{p:02d}"
				lessons = []

				for _l in range(LESSONS_PER_TOPIC):
					lesson_idx += 1
					lessons.append(
						{
							"lesson_id": f"LES-{subject_id}-{lesson_idx:04d}",
							"bit_index": lesson_idx - 1,
							"xp": 10,
							"max_hearts": 5,
						}
					)

				topics.append(
					{
						"topic_id": topic_id,
						"is_linear": True,
						"is_free": False,
						"lessons": lessons,
					}
				)

			units.append(
				{
					"unit_id": unit_id,
					"is_linear": True,
					"is_free": False,
					"topics": topics,
				}
			)

		tracks.append(
			{
				"track_id": track_id,
				"is_linear": True,
				"is_sold_separately": False,
				"units": units,
			}
		)

	return {
		"subject_id": subject_id,
		"version": BITMAP_VERSION,
		"bit_range": TOTAL_LESSONS,
		"excluded_bits": [],
		"is_linear": True,
		"free_units": [],
		"free_topics": [],
		"content_hash": "",
		"tracks": tracks,
	}


# ---------------------------------------------------------------------------
# Bitmap builder
# ---------------------------------------------------------------------------


def build_bitmap(bit_range: int, completion_rate: float) -> bytes:
	"""Build a bitmap with `completion_rate` random bits set (MSB-first, Redis convention)."""
	byte_count = (bit_range + 7) // 8
	completed = set(random.sample(range(bit_range), int(bit_range * completion_rate)))

	bitmap = bytearray(byte_count)
	for bit in completed:
		byte_idx = bit // 8
		bit_offset = bit % 8
		bitmap[byte_idx] |= 0x80 >> bit_offset  # MSB first (Redis SETBIT convention)

	return bytes(bitmap)


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


async def seed():
	"""Seed hierarchies, bitmaps, and access grants."""
	# String client for JSON keys
	str_redis = aioredis.from_url(REDIS_URL, decode_responses=True)
	# Raw client for binary bitmap writes
	raw_redis = aioredis.from_url(REDIS_URL, decode_responses=False)

	try:
		await str_redis.ping()
		print(f"Connected to Redis at {REDIS_URL}\n")

		# --- 1. Hierarchy JSON ---
		print("=== Seeding Hierarchies ===")
		t0 = time.perf_counter()
		for subject_id in SUBJECTS:
			hierarchy = build_hierarchy(subject_id)
			hierarchy_json = json.dumps(hierarchy)
			key = f"memora:hierarchy:{subject_id}"
			await str_redis.set(key, hierarchy_json, ex=7200)  # 2h TTL
			size_kb = len(hierarchy_json) / 1024
			print(f"  {key} — {size_kb:.1f} KB ({TOTAL_LESSONS} lessons)")
		print(f"  Done in {time.perf_counter() - t0:.2f}s\n")

		# --- 2. Progress Bitmaps ---
		print("=== Seeding Progress Bitmaps ===")
		t0 = time.perf_counter()
		bitmap_count = 0
		for player_id in PLAYER_IDS:
			for subject_id in SUBJECTS:
				bitmap = build_bitmap(TOTAL_LESSONS, COMPLETION_RATE)
				key = f"memora:progress:{player_id}:{subject_id}:v{BITMAP_VERSION}"
				await raw_redis.setrange(key.encode(), 0, bitmap)
				await raw_redis.expire(key.encode(), 172800)  # 48h (PROGRESS_KEY_TTL)
				bitmap_count += 1
		print(f"  {bitmap_count} bitmaps ({len(PLAYER_IDS)} players x {len(SUBJECTS)} subjects)")
		print(f"  Each: {(TOTAL_LESSONS + 7) // 8} bytes, ~{int(TOTAL_LESSONS * COMPLETION_RATE)} bits set")
		print(f"  Done in {time.perf_counter() - t0:.2f}s\n")

		# --- 3. Access Grants ---
		print("=== Seeding Access Grants ===")
		t0 = time.perf_counter()
		for player_id in PLAYER_IDS:
			key = f"memora:access:{player_id}"
			access_keys = [f"SUB-{s}" for s in SUBJECTS]
			await str_redis.sadd(key, *access_keys)
			# Don't set TTL — access grants are persistent
		print(f"  {len(PLAYER_IDS)} players x {len(SUBJECTS)} subjects")
		print(f"  Done in {time.perf_counter() - t0:.2f}s\n")

		# --- 4. Verification ---
		print("=== Verification ===")
		first_player = PLAYER_IDS[0]
		first_subject = SUBJECTS[0]

		# Hierarchy check
		h_key = f"memora:hierarchy:{first_subject}"
		h_size = await str_redis.strlen(h_key)
		print(f"  {h_key} — exists, {h_size} bytes")

		# Bitmap check
		p_key = f"memora:progress:{first_player}:{first_subject}:v{BITMAP_VERSION}"
		p_size = await raw_redis.strlen(p_key.encode())
		p_bits = await raw_redis.bitcount(p_key.encode())
		print(f"  {p_key} — exists, {p_size} bytes, BITCOUNT={p_bits}")

		# Access check
		a_key = f"memora:access:{first_player}"
		a_card = await str_redis.scard(a_key)
		a_members = await str_redis.smembers(a_key)
		load_members = [m for m in a_members if "LOAD-" in m]
		print(f"  {a_key} — SCARD={a_card} (LOAD-* grants: {len(load_members)})")

		# Stats check (should be empty)
		s_key = f"memora:stats:{first_player}:{first_subject}:v{BITMAP_VERSION}"
		s_exists = await str_redis.exists(s_key)
		print(f"  {s_key} — {'exists (unexpected!)' if s_exists else 'empty (cold-start ready)'}")

		print(
			f"\nSeeded {len(SUBJECTS)} subjects x {TOTAL_LESSONS:,} lessons = {len(SUBJECTS) * TOTAL_LESSONS:,} total lessons"
		)
		print(
			f"Seeded bitmaps for {len(PLAYER_IDS)} players x {len(SUBJECTS)} subjects ({COMPLETION_RATE:.0%} completion)"
		)
		print(f"Seeded access grants for {len(PLAYER_IDS)} players ({len(SUBJECTS)} LOAD-* subjects each)")

	finally:
		await str_redis.aclose()
		await raw_redis.aclose()


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


async def cleanup():
	"""Remove all LOAD-* keys from Redis."""
	str_redis = aioredis.from_url(REDIS_URL, decode_responses=True)
	raw_redis = aioredis.from_url(REDIS_URL, decode_responses=False)

	try:
		await str_redis.ping()
		print(f"Connected to Redis at {REDIS_URL}\n")

		keys_to_delete = []

		# Hierarchy keys
		async for key in str_redis.scan_iter("memora:hierarchy:LOAD-*"):
			keys_to_delete.append(key)

		# Progress bitmaps
		async for key in str_redis.scan_iter("memora:progress:*:LOAD-*"):
			keys_to_delete.append(key)

		# Stats caches
		async for key in str_redis.scan_iter("memora:stats:*:LOAD-*"):
			keys_to_delete.append(key)

		# Remove LOAD-* access grants from player sets
		for player_id in PLAYER_IDS:
			a_key = f"memora:access:{player_id}"
			load_keys = [f"SUB-{s}" for s in SUBJECTS]
			removed = await str_redis.srem(a_key, *load_keys)
			if removed:
				print(f"  Removed {removed} LOAD-* grants from {a_key}")

		if keys_to_delete:
			# Delete in batches of 100
			for i in range(0, len(keys_to_delete), 100):
				batch = keys_to_delete[i : i + 100]
				await str_redis.delete(*batch)
			print(f"  Deleted {len(keys_to_delete)} LOAD-* keys")
		else:
			print("  No LOAD-* keys found")

		print("\nCleanup complete.")

	finally:
		await str_redis.aclose()
		await raw_redis.aclose()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Seed/cleanup large load test data in Redis")
	parser.add_argument("--cleanup", action="store_true", help="Remove all LOAD-* keys")
	args = parser.parse_args()

	if args.cleanup:
		asyncio.run(cleanup())
	else:
		asyncio.run(seed())
