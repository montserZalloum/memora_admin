"""Frappe API for gamification settings."""

import frappe


@frappe.whitelist(allow_guest=False)
def get_gamification_settings() -> dict:
	"""Get gamification settings from Memora Settings singleton.

	Returns dict with:
	- base_lesson_xp: Default XP for lesson completion
	- replay_xp: Fixed XP for replay
	- max_streak_multiplier_percent: Streak bonus cap

	Callable via: frappe.call("memora_admin.api.settings.get_gamification_settings")
	"""
	settings = frappe.get_single("Memora Settings")

	return {
		"base_lesson_xp": settings.base_lesson_xp or 100,
		"replay_xp": settings.replay_xp or 25,
		"max_streak_multiplier_percent": settings.max_streak_multiplier_percent or 50,
		"max_devices_per_player": settings.max_devices_per_player or 3,
		"default_max_hearts": settings.default_max_hearts or 5,
		"xp_per_heart": settings.xp_per_heart or 0,
	}
