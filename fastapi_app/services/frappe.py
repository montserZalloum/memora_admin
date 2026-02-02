"""Frappe authentication service."""

import httpx

from fastapi_app.models.auth import FrappeUser


class FrappeAuthService:
    """Authenticate against Frappe server via REST API.

    Uses Frappe's auth logic (respects hooks, validations) to verify credentials.
    """

    def __init__(self, frappe_url: str, timeout: float = 10.0):
        """
        Initialize Frappe auth service.

        Args:
            frappe_url: Base URL of Frappe server (e.g., 'http://localhost:8000')
            timeout: HTTP request timeout in seconds
        """
        self.frappe_url = frappe_url.rstrip("/")
        self.timeout = timeout

    async def verify_credentials(self, email: str, password: str) -> FrappeUser | None:
        """
        Verify user credentials via Frappe login API.

        Args:
            email: User email address
            password: User password

        Returns:
            FrappeUser on successful authentication, None on failure.
            Returns None on any failure (generic response - doesn't reveal
            whether email exists or password was wrong).
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Step 1: Login to Frappe
                login_response = await client.post(
                    f"{self.frappe_url}/api/method/login",
                    json={"usr": email, "pwd": password},
                )

                if login_response.status_code != 200:
                    return None

                # Extract cookies for subsequent requests
                cookies = login_response.cookies

                try:
                    # Step 2: Get logged in user
                    user_response = await client.get(
                        f"{self.frappe_url}/api/method/frappe.auth.get_logged_user",
                        cookies=cookies,
                    )

                    if user_response.status_code != 200:
                        return None

                    user_data = user_response.json()
                    user_email = user_data.get("message")

                    if not user_email:
                        return None

                    # Step 3: Get user profile details
                    profile_response = await client.get(
                        f"{self.frappe_url}/api/resource/User/{user_email}",
                        cookies=cookies,
                    )

                    if profile_response.status_code == 200:
                        profile_data = profile_response.json().get("data", {})
                        return FrappeUser(
                            user_id=profile_data.get("name", user_email),
                            email=profile_data.get("email", user_email),
                            full_name=profile_data.get("full_name", ""),
                            user_type=profile_data.get("user_type", "Website User"),
                            time_zone=profile_data.get("time_zone"),
                        )

                    # Profile fetch failed, return minimal user data
                    return FrappeUser(
                        user_id=user_email,
                        email=user_email,
                        full_name="",
                        user_type="Website User",
                        time_zone=None,
                    )

                finally:
                    # Step 4: Always attempt logout to clean up session
                    try:
                        await client.get(
                            f"{self.frappe_url}/api/method/logout",
                            cookies=cookies,
                        )
                    except httpx.RequestError:
                        # Ignore logout failures - best effort cleanup
                        pass

        except httpx.RequestError:
            # Network error, timeout, etc. - return None (generic failure)
            return None
