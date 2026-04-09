"""Product catalog response models."""

from pydantic import BaseModel, Field


class CatalogSubject(BaseModel):
	"""Subject within a product bundle."""

	subject_id: str
	alias_title: str | None = None
	notes: str | None = None
	key_type: str | None = None


class CatalogTrack(BaseModel):
	"""Separately-sold track within a product bundle."""

	track_id: str
	track_title: str
	subject_id: str
	description: str | None = None
	image: str | None = None
	key_type: str | None = None


class CatalogProduct(BaseModel):
	"""A purchasable product in the catalog."""

	product_grant_id: str = Field(..., description="DocType name e.g. GRNT-00239")
	bundle_name: str = Field(..., description="Title from Memora Product Grant")
	price: float = Field(..., description="Raw price_list_rate number")
	subjects: list[CatalogSubject] = Field(default_factory=list)
	tracks: list[CatalogTrack] = Field(default_factory=list)


class CatalogResponse(BaseModel):
	"""Catalog endpoint response."""

	products: list[CatalogProduct] = Field(default_factory=list)
