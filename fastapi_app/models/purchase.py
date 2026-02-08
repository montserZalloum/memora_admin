"""Purchase request and response models."""

from pydantic import BaseModel, Field


class PurchaseRequest(BaseModel):
	"""Purchase request from player."""

	product_grant_id: str = Field(..., description="Product Grant ID e.g. GRNT-00239")
	payment_method: str = Field(default="Manual-Admin", description="Payment method")
	payment_proof_url: str | None = Field(None, description="URL of uploaded payment proof image")


class PurchaseResponse(BaseModel):
	"""Purchase submission response."""

	message: str = Field(default="Purchase request submitted successfully")
