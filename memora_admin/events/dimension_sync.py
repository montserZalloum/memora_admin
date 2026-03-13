"""Dimension refresh event handlers for analytics lakehouse (T022).

These doc_event handlers enqueue background dimension refresh jobs when
relevant DocTypes are created or updated.  Each handler uses
``frappe.enqueue()`` with ``deduplicate=True`` so that rapid-fire changes
(e.g. bulk admin edits) result in at most one queued refresh per entity.

Wired in ``hooks.py`` under ``doc_events``.
"""

import frappe

_REFRESH_FN = "memora_admin.memora_admin.services.dimension_refresh.refresh_dimension"


def on_player_changed(doc, method):
    """Trigger player + player_history dimension refresh after profile change."""
    frappe.enqueue(
        _REFRESH_FN,
        entity="player",
        queue="short",
        deduplicate=True,
    )
    frappe.enqueue(
        _REFRESH_FN,
        entity="player_history",
        queue="short",
        deduplicate=True,
    )


def on_plan_changed(doc, method):
    """Trigger plan dimension refresh after academic plan change."""
    frappe.enqueue(
        _REFRESH_FN,
        entity="plan",
        queue="short",
        deduplicate=True,
    )


def on_season_changed(doc, method):
    """Trigger season dimension refresh after season change."""
    frappe.enqueue(
        _REFRESH_FN,
        entity="season",
        queue="short",
        deduplicate=True,
    )


def on_review_item_changed(doc, method):
    """Trigger review_item dimension refresh after review item change."""
    frappe.enqueue(
        _REFRESH_FN,
        entity="review_item",
        queue="short",
        deduplicate=True,
    )


def on_lesson_changed(doc, method):
    """Trigger lesson dimension refresh after lesson change."""
    frappe.enqueue(
        _REFRESH_FN,
        entity="lesson",
        queue="short",
        deduplicate=True,
    )
