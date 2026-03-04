"""Tests for wallet endpoints."""

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

from fastapi_app.tests.conftest import cleanup_player_keys, seed_wallet

# Mark all tests as async
pytestmark = pytest.mark.asyncio


class TestWalletRetrieval:
	"""Tests for wallet endpoints (US5)."""

	async def test_get_own_wallet(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should retrieve their own wallet with correct XP and streak.

		Seed wallet hash via seed_wallet(redis, player_id, xp=150, streak=3)
		→ GET /api/v1/wallet
		→ 200 OK
		→ Response has xp=150, streak=3
		"""
		client, token, player_id, family_id = authed_client

		# Seed wallet in Redis
		await seed_wallet(redis_client, player_id, xp=150, streak=3)

		# Get own wallet
		response = await client.get("/api/v1/wallet")

		assert response.status_code == 200
		data = response.json()
		assert data["xp"] == 150
		assert data["streak"] == 3

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)

	async def test_empty_wallet_defaults(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Player should get default wallet (xp=0, streak=0) when no wallet data exists.

		No wallet seeded
		→ GET /api/v1/wallet
		→ 200 OK
		→ Response has xp=0, streak=0 (defaults)
		"""
		client, token, player_id, family_id = authed_client

		# Do NOT seed wallet - test default hydration
		response = await client.get("/api/v1/wallet")

		assert response.status_code == 200
		data = response.json()
		assert data["xp"] == 0
		assert data["streak"] == 0

		# Cleanup (no wallet created, but cleanup is safe)
		await cleanup_player_keys(redis_client, player_id)

	async def test_admin_get_player_wallet(
		self,
		admin_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Admin should retrieve any player's wallet data.

		Seed wallet for target player
		→ Admin GET /api/v1/wallet/{player_id}
		→ 200 OK
		→ Response has wallet data
		"""
		admin_http_client, admin_token, admin_email, admin_family_id = admin_client

		# Create a target player ID different from admin
		target_player_id = "PLAYER-TARGET-001"

		# Seed wallet for target player
		await seed_wallet(redis_client, target_player_id, xp=250, streak=5)

		# Admin retrieves target player's wallet
		response = await admin_http_client.get(f"/api/v1/wallet/{target_player_id}")

		assert response.status_code == 200
		data = response.json()
		assert data["xp"] == 250
		assert data["streak"] == 5

		# Cleanup
		await cleanup_player_keys(redis_client, target_player_id)

	async def test_non_admin_forbidden(
		self,
		authed_client: tuple[AsyncClient, str, str, str],
		redis_client: redis.Redis,
	) -> None:
		"""
		Regular player should not be able to retrieve another player's wallet.

		Player client GET /api/v1/wallet/{other_player_id}
		→ 403 Forbidden
		"""
		client, token, player_id, family_id = authed_client

		# Create a target player ID different from current player
		target_player_id = "PLAYER-TARGET-002"

		# Seed wallet for target player
		await seed_wallet(redis_client, target_player_id, xp=100, streak=2)

		# Regular player tries to access another player's wallet
		response = await client.get(f"/api/v1/wallet/{target_player_id}")

		assert response.status_code == 403

		# Cleanup
		await cleanup_player_keys(redis_client, player_id)
		await cleanup_player_keys(redis_client, target_player_id)
