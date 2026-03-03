"""Frappe API for gamification settings."""

import frappe


@frappe.whitelist(allow_guest=False)
def get_gamification_settings() -> dict:
	"""Get gamification settings from Memora Settings singleton.

	Returns dict with:
	- base_lesson_xp: Default XP for lesson completion
	- replay_xp: Fixed XP for replay
	- max_streak_multiplier_percent: Streak bonus cap
	- max_devices_per_player, default_max_hearts, xp_per_heart, session_timeout_days

	Callable via: frappe.call("memora_admin.api.settings.get_gamification_settings")
	"""
	settings = frappe.get_single("Memora Settings")

	return {
		"base_lesson_xp": settings.base_lesson_xp if settings.base_lesson_xp is not None else 100,
		"replay_xp": settings.replay_xp if settings.replay_xp is not None else 25,
		"max_streak_multiplier_percent": settings.max_streak_multiplier_percent if settings.max_streak_multiplier_percent is not None else 50,
		"max_devices_per_player": settings.max_devices_per_player if settings.max_devices_per_player is not None else 3,
		"default_max_hearts": settings.default_max_hearts if settings.default_max_hearts is not None else 5,
		"xp_per_heart": settings.xp_per_heart if settings.xp_per_heart is not None else 0,
		"session_timeout_days": settings.session_timeout_days if settings.session_timeout_days is not None else 30,
	}
