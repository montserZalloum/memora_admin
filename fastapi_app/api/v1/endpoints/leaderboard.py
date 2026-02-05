"""Leaderboard endpoints for XP rankings.

Per CONTEXT.md (Phase 10):
- GET /leaderboard/{type} - Top N players
- GET /leaderboard/{type}/me - User's rank with neighbors
"""

from typing import Literal

import structlog
from fastapi import APIRouter, Query

from fastapi_app.api.deps import CurrentUser, LeaderboardServiceDep, ProfileServiceDep
from fastapi_app.models.leaderboard import (
    LeaderboardEntry,
    LeaderboardResponse,
    MyRankResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

LeaderboardTypeParam = Literal["daily", "weekly", "alltime"]


@router.get("/{lb_type}", response_model=LeaderboardResponse)
async def get_leaderboard(
    lb_type: LeaderboardTypeParam,
    user: CurrentUser,
    leaderboard_service: LeaderboardServiceDep,
    profile_service: ProfileServiceDep,
    limit: int = Query(10, ge=1, le=100, description="Number of entries to return"),
    subject_id: str | None = Query(None, description="Optional subject filter"),
) -> LeaderboardResponse:
    """
    Get top N players from a leaderboard.

    Per CONTEXT.md:
    - Three types: daily, weekly, alltime
    - Optional subject filtering for class-specific competitions
    - Dense ranking: tied players share same rank number

    Args:
        lb_type: Leaderboard type (daily, weekly, alltime)
        user: Current authenticated user
        leaderboard_service: Service for leaderboard operations
        profile_service: Service for profile lookups
        limit: Maximum entries to return (1-100, default 10)
        subject_id: Optional subject for filtered leaderboards

    Returns:
        LeaderboardResponse with entries and total_players
    """
    # Fetch top players from service
    raw_entries = await leaderboard_service.get_top(lb_type, limit, subject_id)

    # Get total players count (ZCARD)
    key = leaderboard_service._get_key(lb_type, subject_id)
    total_players = await leaderboard_service.redis.zcard(key)

    # Batch fetch profiles for all entries (single round-trip)
    player_ids = [entry["player_id"] for entry in raw_entries]
    profiles = await profile_service.get_profiles_batch(player_ids)

    # Build LeaderboardEntry list with profile data
    entries = [
        LeaderboardEntry(
            rank=entry["rank"],
            player_id=entry["player_id"],
            display_name=profiles[entry["player_id"]].display_name,
            xp=entry["xp"],
            avatar=profiles[entry["player_id"]].avatar,
            is_me=entry["player_id"] == user.sub,
        )
        for entry in raw_entries
    ]

    logger.info(
        "leaderboard_fetched",
        user_id=user.sub,
        lb_type=lb_type,
        subject_id=subject_id,
        limit=limit,
        returned=len(entries),
        total_players=total_players,
    )

    return LeaderboardResponse(
        leaderboard_type=lb_type,
        subject_id=subject_id,
        entries=entries,
        total_players=total_players,
    )


@router.get("/{lb_type}/me", response_model=MyRankResponse)
async def get_my_rank(
    lb_type: LeaderboardTypeParam,
    user: CurrentUser,
    leaderboard_service: LeaderboardServiceDep,
    profile_service: ProfileServiceDep,
    subject_id: str | None = Query(None, description="Optional subject filter"),
) -> MyRankResponse:
    """
    Get user's rank with surrounding neighbors.

    Per CONTEXT.md:
    - Separate endpoint from main leaderboard
    - Include +/-2 neighbors for context around user's position
    - Include distance to next tier (XP needed to pass player above)
    - Unranked users (0 XP) treated as tied for last place

    Args:
        lb_type: Leaderboard type (daily, weekly, alltime)
        user: Current authenticated user
        leaderboard_service: Service for leaderboard operations
        profile_service: Service for profile lookups
        subject_id: Optional subject for filtered leaderboards

    Returns:
        MyRankResponse with rank, xp, xp_to_next, neighbors
    """
    # Get user's rank with neighbors from service
    result = await leaderboard_service.get_my_rank(
        player_id=user.sub,
        lb_type=lb_type,
        subject_id=subject_id,
        neighbor_count=2,  # Per CONTEXT.md: +/-2 neighbors
    )

    # Batch fetch profiles for neighbors (single round-trip)
    player_ids = [n["player_id"] for n in result["neighbors"]]
    profiles = await profile_service.get_profiles_batch(player_ids)

    # Build neighbor entries with profile data
    neighbors = [
        LeaderboardEntry(
            rank=n["rank"],
            player_id=n["player_id"],
            display_name=profiles[n["player_id"]].display_name,
            xp=n["xp"],
            avatar=profiles[n["player_id"]].avatar,
            is_me=n.get("is_me", False),
        )
        for n in result["neighbors"]
    ]

    logger.info(
        "my_rank_fetched",
        user_id=user.sub,
        lb_type=lb_type,
        subject_id=subject_id,
        rank=result["rank"],
        xp=result["xp"],
        xp_to_next=result["xp_to_next"],
        neighbor_count=len(neighbors),
    )

    return MyRankResponse(
        rank=result["rank"],
        xp=result["xp"],
        xp_to_next=result["xp_to_next"],
        neighbors=neighbors,
        total_players=result["total_players"],
    )
