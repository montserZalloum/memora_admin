"""
Tests for access grant management endpoints.

Tests verify admin-only CRUD endpoints:
- POST /api/v1/access/grants - Grant access to content keys
- DELETE /api/v1/access/grants - Revoke access
- GET /api/v1/access/grants/{player_id} - List grants

Reference: contracts/endpoint-test-contracts.md §6
"""

import json

import pytest

from fastapi_app.core.redis_keys import access_key


@pytest.mark.asyncio
class TestAccessGrants:
	"""Access grant CRUD operations."""

	async def test_admin_grant_access(self, admin_client, redis_client):
		"""Admin can grant access to content keys."""
		client, token, email, family_id = admin_client
		player_id = "PLAYER-TEST-GRANT-001"

		try:
			resp = await client.post(
				"/api/v1/access/grants",
				json={"player_id": player_id, "content_keys": ["SUB-MATH", "SUB-SCIENCE"]},
				headers={"Authorization": f"Bearer {token}"},
			)

			assert resp.status_code == 200
			data = resp.json()
			assert data["granted"] >= 1  # At least one new grant
			assert "message" in data
		finally:
			# Cleanup
			await redis_client.delete(access_key(player_id))

	async def test_grant_idempotent(self, admin_client, redis_client):
		"""Granting same key twice returns granted=0 on second call."""
		client, token, email, family_id = admin_client
		player_id = "PLAYER-TEST-IDEMPOTENT-001"
		content_key = "SUB-ENGLISH"

		try:
			# First grant
			resp1 = await client.post(
				"/api/v1/access/grants",
				json={"player_id": player_id, "content_keys": [content_key]},
				headers={"Authorization": f"Bearer {token}"},
			)
			assert resp1.status_code == 200
			assert resp1.json()["granted"] >= 1

			# Second grant (same key)
			resp2 = await client.post(
				"/api/v1/access/grants",
				json={"player_id": player_id, "content_keys": [content_key]},
				headers={"Authorization": f"Bearer {token}"},
			)
			assert resp2.status_code == 200
			assert resp2.json()["granted"] == 0  # No new grants
		finally:
			await redis_client.delete(access_key(player_id))

	async def test_grant_empty_keys(self, admin_client):
		"""Admin grant with empty content_keys returns 400."""
		client, token, email, family_id = admin_client

		resp = await client.post(
			"/api/v1/access/grants",
			json={"player_id": "PLAYER-001", "content_keys": []},
			headers={"Authorization": f"Bearer {token}"},
		)

		assert resp.status_code == 400
		data = resp.json()
		assert "code" in data.get("detail", {})

	async def test_admin_revoke_access(self, admin_client, redis_client):
		"""Admin can revoke access from content keys."""
		client, token, email, family_id = admin_client
		player_id = "PLAYER-TEST-REVOKE-001"
		content_key = "SUB-HISTORY"

		try:
			# Grant first
			resp_grant = await client.post(
				"/api/v1/access/grants",
				json={"player_id": player_id, "content_keys": [content_key]},
				headers={"Authorization": f"Bearer {token}"},
			)
			assert resp_grant.status_code == 200

			# Then revoke
			resp_revoke = await client.request(
				"DELETE",
				"/api/v1/access/grants",
				json={"player_id": player_id, "content_keys": [content_key]},
				headers={"Authorization": f"Bearer {token}"},
			)

			assert resp_revoke.status_code == 200
			data = resp_revoke.json()
			assert data["revoked"] >= 1
			assert "message" in data
		finally:
			await redis_client.delete(access_key(player_id))

	async def test_admin_list_grants(self, admin_client, redis_client):
		"""Admin can list all grants for a player."""
		client, token, email, family_id = admin_client
		player_id = "PLAYER-TEST-LIST-001"
		content_keys = ["SUB-MATH", "SUB-ENGLISH"]

		try:
			# Grant keys
			resp_grant = await client.post(
				"/api/v1/access/grants",
				json={"player_id": player_id, "content_keys": content_keys},
				headers={"Authorization": f"Bearer {token}"},
			)
			assert resp_grant.status_code == 200

			# List grants
			resp_list = await client.get(
				f"/api/v1/access/grants/{player_id}",
				headers={"Authorization": f"Bearer {token}"},
			)

			assert resp_list.status_code == 200
			data = resp_list.json()
			assert data["player_id"] == player_id
			assert "grants" in data
			assert isinstance(data["grants"], list)
			assert "count" in data
		finally:
			await redis_client.delete(access_key(player_id))

	async def test_non_admin_grant_forbidden(self, authed_client, redis_client):
		"""Non-admin player cannot grant access."""
		client, token, player_id, family_id = authed_client

		resp = await client.post(
			"/api/v1/access/grants",
			json={"player_id": "OTHER-PLAYER", "content_keys": ["SUB-MATH"]},
			headers={"Authorization": f"Bearer {token}"},
		)

		assert resp.status_code == 403

	async def test_non_admin_revoke_forbidden(self, authed_client):
		"""Non-admin player cannot revoke access."""
		client, token, player_id, family_id = authed_client

		resp = await client.request(
			"DELETE",
			"/api/v1/access/grants",
			json={"player_id": "OTHER-PLAYER", "content_keys": ["SUB-MATH"]},
			headers={"Authorization": f"Bearer {token}"},
		)

		assert resp.status_code == 403

	async def test_non_admin_list_forbidden(self, authed_client):
		"""Non-admin player cannot list grants for other players."""
		client, token, player_id, family_id = authed_client

		resp = await client.get(
			f"/api/v1/access/grants/OTHER-PLAYER",
			headers={"Authorization": f"Bearer {token}"},
		)

		assert resp.status_code == 403
