"""Frappe authentication service."""

import httpx

from fastapi_app.models.auth import FrappeUser


def is_email(identifier: str) -> bool:
    """Check if identifier is email format.

    Per CONTEXT.md: Simple detection - email has @, mobile doesn't.
    """
    return "@" in identifier


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

    async def lookup_user_by_mobile(self, mobile: str) -> str | None:
        """Find Frappe User by mobile_no field.

        Per CONTEXT.md: Query Frappe User doctype by mobile_no field.
        Returns user email if found, None otherwise.
        Exact match required (no normalization).
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Query User doctype with mobile_no filter
                # Using Frappe REST API list filters
                response = await client.get(
                    f"{self.frappe_url}/api/resource/User",
                    params={
                        "filters": f'[["mobile_no", "=", "{mobile}"]]',
                        "fields": '["email"]',
                        "limit_page_length": 1,
                    },
                )

                if response.status_code != 200:
                    return None

                data = response.json().get("data", [])
                if data and len(data) > 0:
                    return data[0].get("email")

                return None

        except httpx.RequestError:
            return None

    async def get_player_profile(self, user_id: str) -> dict | None:
        """Get player profile including plan, display_name, avatar, gender.

        Returns dict with profile fields or None if not found.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.frappe_url}/api/resource/Memora Player Profile/{user_id}",
                    params={
                        "fields": '["plan", "display_name", "avatar", "gender"]',
                    },
                )

                if response.status_code != 200:
                    return None

                data = response.json().get("data", {})
                return {
                    "plan": data.get("plan"),
                    "display_name": data.get("display_name"),
                    "avatar": data.get("avatar"),
                    "gender": data.get("gender"),
                }

        except httpx.RequestError:
            return None
