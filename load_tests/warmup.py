"""Pre-authenticate test players and save tokens to file.

Run once before Locust:
    cd apps/memora_admin && python3 -m load_tests.warmup

Saves tokens.json in load_tests/ directory. Locust reads it via AuthMixin
to skip login calls entirely — every virtual user starts instantly.
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx

from load_tests import config

TOKENS_PATH = Path(__file__).parent / "tokens.json"


async def warmup():
	tokens = []
	async with httpx.AsyncClient(base_url=config.HOST, timeout=30) as client:
		for creds in config.TEST_PLAYERS:
			device_id = f"warmup-{uuid.uuid4().hex[:8]}"
			fake_ip = f"10.0.0.{len(tokens) + 1}"
			resp = await client.post(
				"/api/v1/auth/player/login",
				json={"mobile": creds["mobile"], "password": creds["password"]},
				headers={"X-Device-ID": device_id, "X-Forwarded-For": fake_ip},
			)
			if resp.status_code == 200:
				data = resp.json()
				tokens.append(
					{
						"token": data["access_token"],
						"player_id": data.get("player_id", ""),
						"device_id": device_id,
					}
				)
				print(f"  {creds['mobile']} OK")
			else:
				print(f"  {creds['mobile']} FAILED ({resp.status_code}): {resp.text[:100]}")

	if not tokens:
		print("\nNo tokens obtained. Check that the server is running and credentials are correct.")
		sys.exit(1)

	with open(TOKENS_PATH, "w") as f:
		json.dump(tokens, f, indent=2)

	print(f"\nSaved {len(tokens)} tokens to {TOKENS_PATH}")


if __name__ == "__main__":
	asyncio.run(warmup())
