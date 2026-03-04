"""Access control models for Gate 1 and Gate 2 validation."""

from datetime import date

from pydantic import BaseModel


class SeasonMeta(BaseModel):
	"""Season metadata for Gate 1 validation.

	Cached in Redis hash, used for O(1) season status checks.
	"""

	season_id: str
	is_published: bool
	start_date: date
	end_date: date

	@property
	def is_expired(self) -> bool:
		"""Check if season has ended."""
		return date.today() > self.end_date

	@property
	def is_started(self) -> bool:
		"""Check if season has started."""
		return date.today() >= self.start_date

	@property
	def is_active(self) -> bool:
		"""Check if season is currently active.

		A season is active if:
		- It is published
		- It has started (start_date <= today)
		- It has not expired (today <= end_date)
		"""
		return self.is_published and self.is_started and not self.is_expired

	@classmethod
	def from_redis_hash(cls, season_id: str, data: dict) -> "SeasonMeta | None":
		"""Create SeasonMeta from Redis HGETALL response.

		Args:
		    season_id: The season identifier
		    data: Dict from Redis HGETALL (keys and values as str or bytes)

		Returns:
		    SeasonMeta instance or None if data is empty/invalid
		"""
		if not data:
			return None

		# Handle bytes keys/values from Redis
		def decode(v):
			return v.decode() if isinstance(v, bytes) else v

		try:
			return cls(
				season_id=season_id,
				is_published=decode(data.get(b"is_published", data.get("is_published", "0"))) == "1",
				start_date=date.fromisoformat(decode(data.get(b"start_date", data.get("start_date", "")))),
				end_date=date.fromisoformat(decode(data.get(b"end_date", data.get("end_date", "")))),
			)
		except (ValueError, KeyError):
			return None


class ContentAccessRequest(BaseModel):
	"""Request parameters for content access validation.

	Used to bundle content metadata needed for Double-Gate checks.
	Typically populated from content JSON or database lookup.
	"""

	season_id: str  # Season the content belongs to
	content_key: str  # Access key (e.g., "SUB-MATH", "TRK-MATH-01")
	is_free: bool = False  # If true, bypasses Gate 2


class AccessDeniedDetail(BaseModel):
	"""Structured error detail for access denial."""

	code: str  # Error code (SEASON_NOT_FOUND, SEASON_INACTIVE, etc.)
	message: str  # Human-readable message


# Webhook and Grant Models


class WebhookPayload(BaseModel):
	"""Provider-agnostic payment webhook payload.

	Per CONTEXT.md:
	- Provider-agnostic interface (specific provider TBD)
	- Idempotency via event_id tracking
	"""

	event_id: str  # Unique event ID for idempotency
	event_type: str  # e.g., "payment.completed"
	transaction_id: str  # Payment provider's transaction ID
	player_id: str  # Memora player ID (user_id)
	product_grant_id: str  # Memora Product Grant DocType name
	amount: float
	currency: str
	timestamp: str  # ISO format timestamp


class WebhookResponse(BaseModel):
	"""Response for webhook acknowledgment."""

	status: str  # "accepted", "already_processed", "error"
	message: str | None = None


class GrantRequest(BaseModel):
	"""Request body for admin grant endpoint."""

	player_id: str  # Player's user ID
	content_keys: list[str]  # e.g., ["SUB-MATH", "TRK-MATH-01"]


class GrantResponse(BaseModel):
	"""Response for grant operation."""

	granted: int  # Number of new grants added
	message: str


class RevokeRequest(BaseModel):
	"""Request body for admin revoke endpoint."""

	player_id: str
	content_keys: list[str]


class RevokeResponse(BaseModel):
	"""Response for revoke operation."""

	revoked: int  # Number of grants removed
	message: str
