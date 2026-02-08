"""Purchase request endpoint."""

import redis.asyncio as redis
from fastapi import APIRouter, HTTPException, status

from fastapi_app.api.deps import CurrentUser, PurchaseServiceDep
from fastapi_app.models.purchase import PurchaseRequest, PurchaseResponse

router = APIRouter(prefix="/purchase", tags=["purchase"])


@router.post("/", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED)
async def submit_purchase(
	user: CurrentUser,
	purchase_service: PurchaseServiceDep,
	req: PurchaseRequest,
) -> PurchaseResponse:
	"""Submit a purchase request for a product grant.

	Creates a Subscription Transaction in Frappe with "Pending Approval" status
	and adds the product grant ID to the player's Redis pending set so the
	catalog hides it immediately.

	Requires authentication. Player must have an academic plan.

	Returns:
		201: Purchase request submitted successfully
		400: Player has no academic plan or validation error
		404: Product grant or player profile not found
		409: Duplicate pending purchase for this product
		503: Redis service unavailable
	"""
	if not user.plan:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Player has no academic plan",
		)

	try:
		return await purchase_service.submit_purchase(
			user_id=user.sub,
			plan_id=user.plan,
			req=req,
		)
	except redis.RedisError:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Service temporarily unavailable",
		)
