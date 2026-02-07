"""Frappe API client for external whitelisted method calls."""

import httpx
import structlog
from fastapi_app.core.config import Settings

logger = structlog.get_logger()


class FrappeAPIError(Exception):
    """Raised when Frappe API returns an error."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Frappe API error ({status_code}): {message}")


class FrappeClient:
    """Async client for Frappe whitelisted methods."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            # Build authorization header
            auth_token = f"{self.settings.frappe_api_key}:{self.settings.frappe_api_secret}"
            self._client = httpx.AsyncClient(
                base_url=self.settings.frappe_url,
                headers={
                    "Authorization": f"token {auth_token}",
                    "Content-Type": "application/json",
                    "Host": self.settings.frappe_site,
                },
                timeout=30.0,
            )
        return self._client

    async def _call_method(self, method: str, **kwargs) -> dict:
        """
        Call a Frappe whitelisted method (internal).

        Args:
            method: Full method path (e.g., memora_admin.api.products.get_grant_keys)
            **kwargs: Method arguments

        Returns:
            The 'message' field from Frappe response

        Raises:
            FrappeAPIError: On non-200 response
        """
        client = await self._get_client()
        url = f"/api/method/{method}"

        logger.info("frappe_api_call", method=method, args=kwargs)

        response = await client.post(url, json=kwargs)

        if response.status_code != 200:
            error_msg = response.text
            try:
                error_data = response.json()
                error_msg = error_data.get("exc", error_msg)
            except Exception:
                pass
            logger.error(
                "frappe_api_error",
                method=method,
                status=response.status_code,
                error=error_msg,
            )
            raise FrappeAPIError(response.status_code, error_msg)

        data = response.json()
        return data.get("message", data)

    async def call(self, method: str, params: dict | None = None) -> dict | list | None:
        """
        Call a Frappe whitelisted method (public generic interface).

        This allows calling any Frappe whitelisted method by name.
        For common operations, prefer using specific methods like
        get_grant_keys() or create_subscription() for type safety.

        Args:
            method: Full method path (e.g., memora_admin.api.hierarchy.get_subject_hierarchy)
            params: Method parameters as a dictionary

        Returns:
            The 'message' field from Frappe response (dict, list, or None)

        Raises:
            FrappeAPIError: On non-200 response
        """
        params = params or {}
        return await self._call_method(method, **params)

    async def get_grant_keys(self, product_grant_id: str) -> list[str]:
        """
        Get grant keys from Memora Product Grant.

        Args:
            product_grant_id: Name of Product Grant document

        Returns:
            List of access keys (e.g., ["SUB-MATH", "TRK-MATH-01"])
        """
        result = await self._call_method(
            "memora_admin.api.products.get_grant_keys",
            product_grant_id=product_grant_id,
        )
        return result if isinstance(result, list) else []

    async def create_subscription(
        self,
        player_id: str,
        access_key: str,
        expires_at: str,
        transaction_id: str | None = None,
    ) -> dict:
        """
        Create Memora Player Subscription via Frappe API.

        This triggers doc_events hook which syncs to Redis.

        Args:
            player_id: Player profile name
            access_key: Access key to grant
            expires_at: Expiration date (ISO format)
            transaction_id: Optional transaction reference

        Returns:
            Dict with subscription name and created status
        """
        return await self._call_method(
            "memora_admin.api.subscriptions.create_subscription",
            player_id=player_id,
            access_key=access_key,
            expires_at=expires_at,
            transaction_id=transaction_id,
        )

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
