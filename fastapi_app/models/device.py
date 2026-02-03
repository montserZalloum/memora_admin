"""Device management Pydantic models."""

from pydantic import BaseModel


class DeviceInfo(BaseModel):
	"""Device information stored in Redis.

	Per CONTEXT.md:
	- device_id: Client-generated UUID stored locally by the app
	- fingerprint: Stable UA components for recognition after reinstall
	- push_token: Optional FCM/APNs token for notifications
	"""

	device_id: str
	device_name: str
	platform: str  # iOS, Android, Web
	user_agent: str | None = None
	last_login: str | None = None  # ISO timestamp
	fingerprint: str | None = None
	push_token: str | None = None


class DeviceRegistrationResult(BaseModel):
	"""Result of device registration attempt.

	Per CONTEXT.md:
	- status: "new", "existing", "fingerprint_match", "limit_exceeded"
	- On limit_exceeded: current_count and max_count are populated
	"""

	success: bool
	device_id: str
	device_name: str
	status: str  # "new", "existing", "fingerprint_match", "limit_exceeded"
	current_count: int | None = None
	max_count: int | None = None


class DeviceRegistrationRequest(BaseModel):
	"""Request body for device info during login.

	Per CONTEXT.md:
	- device_id: Client-generated UUID
	- platform: Optional hint (iOS, Android, Web) to override UA detection
	"""

	device_id: str
	platform: str | None = None
