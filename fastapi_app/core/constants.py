"""Redis key constants for memora services."""

# Dirty set keys for background sync
# Frappe sync tasks process these sets to persist to MariaDB
DIRTY_PROGRESS_KEY = "memora:dirty:progress"
DIRTY_WALLETS_KEY = "memora:dirty:wallets"

# Interaction buffer key
INTERACTION_BUFFER_KEY = "memora:buffer:interactions"

# Game session TTL (1 hour in seconds)
# Per CONTEXT.md: Sessions auto-expire after 1 hour
GAME_SESSION_TTL = 3600

# --- Level System ---
# XP thresholds: Level N requires LEVEL_THRESHOLDS[N-1] total XP
# Progression curve: early levels are quick, later levels require more
LEVEL_THRESHOLDS = [
	0,  # Level 1: 0 XP
	100,  # Level 2: 100 XP
	300,  # Level 3: 300 XP
	600,  # Level 4: 600 XP
	1000,  # Level 5: 1000 XP
	1500,  # Level 6: 1500 XP
	2100,  # Level 7: 2100 XP
	2800,  # Level 8: 2800 XP
	3600,  # Level 9: 3600 XP
	4500,  # Level 10: 4500 XP
	5500,  # Level 11: 5500 XP
	6700,  # Level 12: 6700 XP
	8000,  # Level 13: 8000 XP
	9500,  # Level 14: 9500 XP
	11000,  # Level 15: 11000 XP
]

LEVEL_TITLES = [
	"Beginner",  # Level 1
	"Learner",  # Level 2
	"Explorer",  # Level 3
	"Scholar",  # Level 4
	"Achiever",  # Level 5
	"Expert",  # Level 6
	"Master",  # Level 7
	"Champion",  # Level 8
	"Legend",  # Level 9
	"Grandmaster",  # Level 10
	"Sage",  # Level 11
	"Titan",  # Level 12
	"Mythic",  # Level 13
	"Immortal",  # Level 14
	"Transcendent",  # Level 15
]

# FSRS stability threshold for mature memory classification (days)
MASTERY_MATURE_THRESHOLD = 21.0

# Cache TTL for memory mastery data (seconds, matches review overview TTL)
MASTERY_CACHE_TTL = 300


def calculate_level(total_xp: int) -> tuple[int, str, int, int]:
	"""Calculate player level from total XP.

	Returns (level, title, xp_in_level, xp_to_next_level).
	- level: 1-based level number
	- title: Human-readable level title
	- xp_in_level: XP earned within the current level
	- xp_to_next_level: XP remaining to reach next level (0 if max)
	"""
	for i in range(len(LEVEL_THRESHOLDS) - 1, -1, -1):
		if total_xp >= LEVEL_THRESHOLDS[i]:
			level = i + 1
			title = LEVEL_TITLES[min(i, len(LEVEL_TITLES) - 1)]
			xp_in_level = total_xp - LEVEL_THRESHOLDS[i]
			if i + 1 < len(LEVEL_THRESHOLDS):
				xp_to_next = LEVEL_THRESHOLDS[i + 1] - total_xp
			else:
				xp_to_next = 0  # Max level
			return level, title, xp_in_level, xp_to_next
	# Fallback: should never reach here since LEVEL_THRESHOLDS[0] == 0
	return 1, LEVEL_TITLES[0], total_xp, LEVEL_THRESHOLDS[1] - total_xp
