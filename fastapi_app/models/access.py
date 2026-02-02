"""Access control models for Gate 1 season validation."""

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
