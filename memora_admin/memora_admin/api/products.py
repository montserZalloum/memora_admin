"""Frappe API for product-related operations.

Canonical implementation lives at memora_admin.api.products.
This re-export keeps existing ``from memora_admin.memora_admin.api.products import …``
imports working without maintaining two copies.
"""

from memora_admin.api.products import get_grant_keys  # noqa: F401
