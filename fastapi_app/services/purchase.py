"""Purchase request service with Redis pending set and Frappe API delegation."""

import redis.asyncio as redis
import structlog
from fastapi import HTTPException, status

from fastapi_app.core.redis_keys import pending_key as _pending_key_fn
from fastapi_app.models.purchase import PurchaseRequest, PurchaseResponse
from fastapi_app.services.frappe_client import FrappeAPIError, FrappeClient

logger = structlog.get_logger(__name__)


class PurchaseService:
	"""Handle purchase request submission.

	Flow:
	1. Check Redis pending set for fast duplicate detection
	2. Call Frappe whitelisted API to create Subscription Transaction
	3. Write product grant ID to Redis pending set (catalog hides it)

	Write order: Frappe first, Redis second (per RESEARCH.md pitfall 2).
	"""

	def __init__(
		self,
		redis_client: redis.Redis,
		frappe_client: FrappeClient,
	):
		self.redis = redis_client
		self.frappe = frappe_client

	async def submit_purchase(
		self,
		user_id: str,
		plan_id: str,
		req: PurchaseRequest,
	) -> PurchaseResponse:
		"""Submit a purchase request for a product grant.

		Args:
			user_id: JWT sub claim (user ID, used as Redis key)
			plan_id: Player's academic plan document name
			req: Purchase request with product_grant_id and payment details

		Returns:
			PurchaseResponse with success message

		Raises:
			HTTPException 409: Duplicate pending purchase
			HTTPException 400: Validation error (product not in plan, unpublished, etc.)
			HTTPException 404: Product grant or player profile not found
			HTTPException 502: Frappe API failure
		"""
		pending_key = _pending_key_fn(user_id)

		# 1. Fast duplicate check via Redis pending set
		is_pending = await self.redis.sismember(pending_key, req.product_grant_id)
		if is_pending:
			logger.info(
				"purchase_duplicate_pending",
				user_id=user_id,
				product_grant_id=req.product_grant_id,
			)
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="Purchase request already pending for this product",
			)

		# 2. Call Frappe API to create transaction (validates grant, checks DB duplicates)
		try:
			result = await self.frappe.call(
				"memora_admin.api.purchase.create_purchase_request",
				{
					"user_id": user_id,
					"product_grant_id": req.product_grant_id,
					"payment_method": req.payment_method,
					"payment_proof_url": req.payment_proof_url,
					"plan_id": plan_id,
				},
			)
			logger.info(
				"purchase_frappe_created",
				user_id=user_id,
				product_grant_id=req.product_grant_id,
				transaction=result,
			)
		except FrappeAPIError as e:
			logger.error(
				"purchase_frappe_error",
				user_id=user_id,
				product_grant_id=req.product_grant_id,
				status_code=e.status_code,
				message=e.message,
			)
			# Map Frappe error codes to HTTP responses
			if e.status_code == 417 and "DuplicateEntryError" in e.message:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="Purchase request already pending for this product",
				)
			elif e.status_code == 404:
				raise HTTPException(
					status_code=status.HTTP_404_NOT_FOUND,
					detail=e.message,
				)
			elif e.status_code == 417:
				# ValidationError from Frappe
				raise HTTPException(
					status_code=status.HTTP_400_BAD_REQUEST,
					detail=e.message,
				)
			else:
				raise HTTPException(
					status_code=status.HTTP_502_BAD_GATEWAY,
					detail="Failed to process purchase request",
				)

		# 3. Write to Redis pending set AFTER Frappe succeeds
		# This ensures catalog hides the product immediately
		await self.redis.sadd(pending_key, req.product_grant_id)
		logger.info(
			"purchase_pending_set_updated",
			user_id=user_id,
			product_grant_id=req.product_grant_id,
		)

		return PurchaseResponse()
