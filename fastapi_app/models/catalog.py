"""Product catalog response models."""

from pydantic import BaseModel, Field


class CatalogSubject(BaseModel):
	"""Subject within a product bundle."""

	subject_id: str
	alias_title: str | None = None
	notes: str | None = None


class CatalogProduct(BaseModel):
	"""A purchasable product in the catalog."""

	product_grant_id: str = Field(..., description="DocType name e.g. GRNT-00239")
	bundle_name: str = Field(..., description="Item name from ERPNext")
	price: float = Field(..., description="Raw price_list_rate number")
	subjects: list[CatalogSubject] = Field(default_factory=list)


class CatalogResponse(BaseModel):
	"""Catalog endpoint response."""

	products: list[CatalogProduct] = Field(default_factory=list)
