# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MemoraChallengeReward(Document):
	pass


def rewards_to_dicts(rewards) -> list[dict]:
	"""Serialize reward child-table rows to plain dicts for Redis/JSON use."""
	return [
		{
			"rank": r.rank,
			"reward_type": r.reward_type,
			"xp_amount": r.xp_amount or 0,
			"prize_description": r.prize_description or "",
		}
		for r in (rewards or [])
	]
