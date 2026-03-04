"""Content report endpoint for players to report bugs, errors, and suggestions."""

import structlog
from fastapi import APIRouter, HTTPException, status

from fastapi_app.api.deps import CurrentUser, RedisClient
from fastapi_app.core.redis_keys import report_cooldown_key
from fastapi_app.models.report import ContentReportRequest, ContentReportResponse
from fastapi_app.services.frappe_client import FrappeAPIError

logger = structlog.get_logger()

router = APIRouter(prefix="/reports", tags=["reports"])

MAX_SCREENSHOT_BASE64_SIZE = 7 * 1024 * 1024  # 7MB base64 ≈ 5MB image
COOLDOWN_SECONDS = 60


@router.post("", response_model=ContentReportResponse, status_code=status.HTTP_201_CREATED)
async def submit_content_report(
	body: ContentReportRequest,
	user: CurrentUser,
	redis_client: RedisClient,
) -> ContentReportResponse:
	"""Submit a content report (bug, error, suggestion).

	Rate-limited to 1 report per 60 seconds per player.
	"""
	player_id = user.sub

	# Validate screenshot
	if body.screenshot_base64 and not body.screenshot_filename:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail={
				"code": "MISSING_FILENAME",
				"message": "screenshot_filename is required when screenshot_base64 is provided",
			},
		)

	if body.screenshot_base64 and len(body.screenshot_base64) > MAX_SCREENSHOT_BASE64_SIZE:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail={"code": "SCREENSHOT_TOO_LARGE", "message": "Screenshot must be under 5MB"},
		)

	# Redis cooldown check (degrade gracefully on Redis failure)
	cooldown_key = report_cooldown_key(player_id)
	try:
		was_set = await redis_client.set(cooldown_key, "1", nx=True, ex=COOLDOWN_SECONDS)
		if not was_set:
			raise HTTPException(
				status_code=status.HTTP_429_TOO_MANY_REQUESTS,
				detail={"code": "RATE_LIMITED", "message": "Please wait before submitting another report"},
			)
	except HTTPException:
		raise
	except Exception as e:
		logger.warning("report_cooldown_redis_error", error=str(e), player=player_id)
		# Degrade gracefully — allow the report through

	# Call Frappe to create the report
	from fastapi_app.api.deps import get_frappe_client

	frappe_client = await get_frappe_client()

	params = {
		"player": player_id,
		"report_type": body.report_type,
		"description": body.description,
	}
	if body.subject:
		params["subject"] = body.subject
	if body.lesson:
		params["lesson"] = body.lesson
	if body.screenshot_base64:
		params["screenshot_base64"] = body.screenshot_base64
		params["screenshot_filename"] = body.screenshot_filename

	try:
		result = await frappe_client.call(
			"memora_admin.api.reports.create_content_report",
			params=params,
		)
	except FrappeAPIError as e:
		logger.error("report_creation_failed", player=player_id, error=e.message)
		# Delete cooldown key so player can retry
		try:
			await redis_client.delete(cooldown_key)
		except Exception:
			pass
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail={
				"code": "REPORT_CREATION_FAILED",
				"message": "Failed to create report. Please try again.",
			},
		)

	logger.info("content_report_submitted", player=player_id, report=result.get("name"))

	return ContentReportResponse(
		name=result.get("name", ""),
		message="Report submitted successfully",
	)
