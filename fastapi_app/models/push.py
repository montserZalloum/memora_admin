"""Web Push notification Pydantic models."""

from pydantic import BaseModel, Field


class PushKeys(BaseModel):
	"""Browser push subscription encryption keys."""

	p256dh: str = Field(max_length=256)
	auth: str = Field(max_length=128)


class PushSubscription(BaseModel):
	"""W3C PushSubscription object from browser pushManager.subscribe()."""

	endpoint: str = Field(max_length=2048, pattern=r"^https://")
	keys: PushKeys


class PushSubscribeRequest(BaseModel):
	"""Request body for POST /push/subscribe."""

	subscription: PushSubscription


class VapidKeyResponse(BaseModel):
	"""Response for GET /push/vapid-key."""

	public_key: str


class PushStatusResponse(BaseModel):
	"""Generic status response for push operations."""

	status: str
