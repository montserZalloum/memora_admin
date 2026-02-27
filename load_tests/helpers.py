"""Shared authentication mixin and request helpers for Locust load tests."""

import random
from uuid import uuid4

from load_tests import config


class AuthMixin:
	"""Mixin providing on_start() authentication for all Locust user classes.

	Authenticates via POST /api/v1/auth/player/login with a random player
	from config.TEST_PLAYERS. Stores self.token, self.device_id, and self.fake_ip.

	Each virtual user gets a unique fake IP via X-Forwarded-For to simulate
	real-world traffic where each player has a distinct IP address. This prevents
	the global rate limiter from throttling all virtual users as a single source.
	"""

	def on_start(self):
		player = random.choice(config.TEST_PLAYERS)
		self.device_id = f"locust-{uuid4().hex[:12]}"
		self.fake_ip = f"10.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}"
		self.token = None

		with self.client.post(
			"/api/v1/auth/player/login",
			json={"mobile": player["mobile"], "password": player["password"]},
			headers={"X-Device-ID": self.device_id, "X-Forwarded-For": self.fake_ip},
			catch_response=True,
		) as resp:
			if resp.status_code == 200:
				data = resp.json()
				self.token = data["access_token"]
				resp.success()
			elif resp.status_code == 429:
				resp.success()  # Rate limit is expected under load (FR-007)
			else:
				resp.failure(f"Login failed: {resp.status_code}")


def api_get(user, path, name=None, **kwargs):
	"""GET request with Bearer auth, 429/401 tolerance, and aggregated naming.

	Args:
		user: Locust HttpUser instance (must have .token, .client, .fake_ip attributes).
		path: API path (e.g., "/api/v1/profile").
		name: Aggregated request name for stats grouping (FR-006).
		**kwargs: Extra kwargs passed to client.get().

	Returns:
		Response object on success, None on 429/401/error or missing token.
	"""
	if not user.token:
		return None

	headers = kwargs.pop("headers", {})
	headers["Authorization"] = f"Bearer {user.token}"
	headers["X-Forwarded-For"] = user.fake_ip
	headers["X-Device-ID"] = user.device_id

	with user.client.get(
		path,
		name=name or path,
		headers=headers,
		catch_response=True,
		**kwargs,
	) as resp:
		if resp.status_code == 429:
			resp.success()  # Expected under load (FR-007)
			return None
		elif resp.status_code == 401:
			resp.success()  # Session expired under load (FR-008)
			user.token = None
			return None
		elif resp.status_code >= 400:
			resp.failure(f"{resp.status_code}: {resp.text[:200]}")
			return None
		resp.success()
		return resp


def api_post(user, path, json=None, name=None, **kwargs):
	"""POST request with Bearer auth, 429/401 tolerance, and aggregated naming.

	Args:
		user: Locust HttpUser instance (must have .token, .client, .fake_ip attributes).
		path: API path (e.g., "/api/v1/sessions/start").
		json: Request body dict.
		name: Aggregated request name for stats grouping (FR-006).
		**kwargs: Extra kwargs passed to client.post().

	Returns:
		Response object on success, None on 429/401/error or missing token.
	"""
	if not user.token:
		return None

	headers = kwargs.pop("headers", {})
	headers["Authorization"] = f"Bearer {user.token}"
	headers["X-Forwarded-For"] = user.fake_ip
	headers["X-Device-ID"] = user.device_id

	with user.client.post(
		path,
		json=json,
		name=name or path,
		headers=headers,
		catch_response=True,
		**kwargs,
	) as resp:
		if resp.status_code == 429:
			resp.success()  # Expected under load (FR-007)
			return None
		elif resp.status_code == 401:
			resp.success()  # Session expired under load (FR-008)
			user.token = None
			return None
		elif resp.status_code == 409:
			resp.success()  # Conflict (e.g., active session exists) — expected
			return None
		elif resp.status_code >= 400:
			resp.failure(f"{resp.status_code}: {resp.text[:200]}")
			return None
		resp.success()
		return resp
