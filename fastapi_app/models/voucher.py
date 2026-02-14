"""Voucher preview and redemption request/response models."""

from pydantic import BaseModel, Field


class VoucherPreviewRequest(BaseModel):
	"""Preview request: student submits a PIN to see available grants."""

	pin: str = Field(..., min_length=6, max_length=20, description="Voucher card PIN")


class VoucherRedeemRequest(BaseModel):
	"""Redeem request: student submits PIN + chosen grant to redeem."""

	pin: str = Field(..., min_length=6, max_length=20, description="Voucher card PIN")
	grant_id: str = Field(..., description="Product Grant ID to redeem")


class VoucherGrant(BaseModel):
	"""A single available grant from a voucher card."""

	grant_id: str
	name: str


class VoucherPreviewResponse(BaseModel):
	"""Preview response: face value and available (not-yet-owned) grants."""

	face_value: str
	grants: list[VoucherGrant]


class VoucherRedeemResponse(BaseModel):
	"""Redeem success response."""

	status: str = "success"
	transaction_id: str


class VoucherErrorResponse(BaseModel):
	"""Error response with machine-readable code and optional retry countdown."""

	error: str
	retry_after: int | None = None  # Only present for RATE_LIMITED
