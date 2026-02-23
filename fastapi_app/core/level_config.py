"""Dynamic level configuration with admin-configurable XP curve and titles.

Provides:
- LevelConfig frozen dataclass with hardcoded fallback defaults
- get_threshold(): Pure O(1) XP threshold computation
- calculate_level(): Pure O(1) inverse quadratic level calculation
- get_level_config(): Async Redis reader with cache-miss resilience
"""

import json
import math
from dataclasses import dataclass, field

import structlog

from fastapi_app.core.redis_keys import level_config_key

logger = structlog.get_logger()

DEFAULT_TITLES: dict[int, str] = {
	1: "Beginner",
	2: "Learner",
	3: "Explorer",
	4: "Scholar",
	5: "Achiever",
	6: "Expert",
	7: "Master",
	8: "Champion",
	9: "Legend",
	10: "Grandmaster",
	11: "Sage",
	12: "Titan",
	13: "Mythic",
	14: "Immortal",
	15: "Transcendent",
}


@dataclass(frozen=True)
class LevelConfig:
	a: int = 50
	b: int = 50
	max_level: int = 15
	titles: dict[int, str] = field(default_factory=lambda: dict(DEFAULT_TITLES))


DEFAULT_LEVEL_CONFIG = LevelConfig()


def get_threshold(level: int, a: int, b: int) -> int:
	"""Compute XP threshold for a given level. Pure, O(1).

	Formula: round(a * (level-1)^2 + b * (level-1))
	"""
	n = level - 1
	return round(a * n * n + b * n)


def calculate_level(total_xp: int, config: LevelConfig) -> tuple[int, str, int, int]:
	"""Calculate player level from total XP using config parameters.

	Uses O(1) inverse quadratic formula instead of list iteration.

	Returns (level, title, xp_in_level, xp_to_next_level).
	"""
	# Clamp negative XP to 0
	xp = max(0, total_xp)

	a = config.a
	b = config.b

	if xp == 0:
		level = 1
	else:
		# Inverse quadratic: level = floor((-b + sqrt(b^2 + 4*a*xp)) / (2*a)) + 1
		discriminant = b * b + 4 * a * xp
		raw_level = (-b + math.sqrt(discriminant)) / (2 * a) + 1
		level = int(raw_level)  # floor
		# Clamp to [1, max_level]
		level = max(1, min(level, config.max_level))

	title = config.titles.get(level, f"Level {level}")
	threshold_current = get_threshold(level, a, b)
	xp_in_level = xp - threshold_current

	if level < config.max_level:
		threshold_next = get_threshold(level + 1, a, b)
		xp_to_next = threshold_next - xp
	else:
		xp_to_next = 0

	return level, title, xp_in_level, xp_to_next


async def get_level_config(redis_client) -> LevelConfig:
	"""Load level config from Redis, fallback to defaults on miss/error."""
	try:
		cached = await redis_client.get(level_config_key())
		if cached is None:
			return DEFAULT_LEVEL_CONFIG

		data = json.loads(cached)
		titles = {int(k): v for k, v in data.get("titles", {}).items()}
		return LevelConfig(
			a=int(data["a"]),
			b=int(data["b"]),
			max_level=int(data["max_level"]),
			titles=titles if titles else dict(DEFAULT_TITLES),
		)
	except Exception as e:
		logger.warning("level_config_parse_error", error=str(e))
		return DEFAULT_LEVEL_CONFIG
