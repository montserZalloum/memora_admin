"""Admin access control endpoints."""

import structlog
from fastapi import APIRouter, HTTPException, Path, status

from fastapi_app.api.deps import AccessServiceDep, RequireAdmin
from fastapi_app.models.access import (
    GrantRequest,
    GrantResponse,
    RevokeRequest,
    RevokeResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/access", tags=["access"])


@router.post("/grants", response_model=GrantResponse)
async def create_grant(
    request: GrantRequest,
    user: RequireAdmin,
    access_service: AccessServiceDep,
) -> GrantResponse:
    """
    Grant player access to content.

    Per CONTEXT.md:
    - Grants are additive and permanent until revoked
    - Grant granularity: Subject-level or Track-level
    - Idempotent: re-granting same key is safe

    Requires admin role.
    """
    if not request.content_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EMPTY_KEYS", "message": "At least one content key required"},
        )

    granted = await access_service.grant_access(
        player_id=request.player_id,
        content_keys=request.content_keys,
    )

    logger.info(
        "access_granted",
        player_id=request.player_id,
        content_keys=request.content_keys,
        new_grants=granted,
        admin_user=user.sub,
    )

    return GrantResponse(
        granted=granted,
        message=f"Granted {granted} new access key(s)" if granted else "All keys already granted",
    )


@router.delete("/grants", response_model=RevokeResponse)
async def revoke_grant(
    request: RevokeRequest,
    user: RequireAdmin,
    access_service: AccessServiceDep,
) -> RevokeResponse:
    """
    Revoke player access to content.

    Requires admin role.
    """
    if not request.content_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EMPTY_KEYS", "message": "At least one content key required"},
        )

    revoked = await access_service.revoke_access(
        player_id=request.player_id,
        content_keys=request.content_keys,
    )

    logger.info(
        "access_revoked",
        player_id=request.player_id,
        content_keys=request.content_keys,
        revoked_grants=revoked,
        admin_user=user.sub,
    )

    return RevokeResponse(
        revoked=revoked,
        message=f"Revoked {revoked} access key(s)" if revoked else "No matching keys found",
    )


@router.get("/grants/{player_id}")
async def get_player_grants(
    user: RequireAdmin,
    access_service: AccessServiceDep,
    player_id: str = Path(pattern=r"^[a-zA-Z0-9._@-]+$"),
) -> dict:
    """
    Get all grants for a player.

    Requires admin role.
    """
    grants = await access_service.get_player_grants(player_id)

    return {
        "player_id": player_id,
        "grants": list(grants),
        "count": len(grants),
    }
