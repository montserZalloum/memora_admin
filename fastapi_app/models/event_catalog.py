"""Event catalog response models."""

from pydantic import BaseModel, Field


class CatalogEvent(BaseModel):
	"""A purchasable paid event in the catalog."""

	event_id: str
	event_name: str
	description: str | None = None
	scheduled_start: str  # ISO datetime
	price: float
	currency: str


class EventCatalogResponse(BaseModel):
	"""Event catalog endpoint response."""

	events: list[CatalogEvent] = Field(default_factory=list)
