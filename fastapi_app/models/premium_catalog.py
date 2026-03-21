"""Premium catalog response models."""

from pydantic import BaseModel


class PremiumCatalogResponse(BaseModel):
	"""Premium catalog endpoint response.

	If ``available`` is False, the remaining fields indicate why:
	- no plan → player has no plan
	- has_premium → already has usable premium
	- has_pending_purchase → purchase in progress
	- price not configured → plan doesn't offer premium
	"""

	available: bool = False
	plan_id: str | None = None
	plan_name: str | None = None
	price: float | None = None
	currency: str | None = None
	has_premium: bool = False
	has_pending_purchase: bool = False


class PremiumVoucherCatalogResponse(BaseModel):
	"""Premium voucher catalog endpoint response.

	Tells the frontend whether there are redeemable plan_premium voucher
	cards available for the player's plan.
	"""

	available: bool = False
	plan_id: str | None = None
	plan_name: str | None = None
	face_value: str | None = None
