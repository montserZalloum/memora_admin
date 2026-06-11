# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

"""Helpers for honoring `get_query` filters inside custom link-search methods.

Doctypes registered under the `standard_queries` hook receive a `filters` dict
(already JSON-decoded by `frappe.desk.search.search_widget`) but must apply it
themselves. These helpers translate the common operator forms into SQL.
"""


def extract_in_list(filters, field):
	"""Return the list of allowed values for `field` from a link-search `filters` dict.

	Supports:
	- ``{field: ["in", [a, b]]}`` — Frappe operator form sent by a control's get_query
	- ``{field: [a, b]}`` — a bare list of values
	- ``{field: "a"}`` — a single scalar

	Returns ``None`` when the field is absent (no constraint), otherwise a list
	(possibly empty, which the caller should treat as "match nothing").
	"""
	if not isinstance(filters, dict):
		return None

	value = filters.get(field)
	if value is None:
		return None

	if isinstance(value, (list, tuple)):
		if len(value) == 2 and isinstance(value[0], str) and value[0].lower() == "in":
			inner = value[1]
			return list(inner) if isinstance(inner, (list, tuple)) else [inner]
		return list(value)

	return [value]
