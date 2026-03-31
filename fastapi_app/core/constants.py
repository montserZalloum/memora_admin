"""Redis key constants for memora services."""

from fastapi_app.core.redis_keys import (
	dirty_progress_key,
	dirty_wallets_key,
	interaction_buffer_key,
)

# Dirty set keys for background sync
# Frappe sync tasks process these sets to persist to MariaDB
DIRTY_PROGRESS_KEY = dirty_progress_key()
DIRTY_WALLETS_KEY = dirty_wallets_key()

# Interaction buffer key
INTERACTION_BUFFER_KEY = interaction_buffer_key()

# Game session TTL (1 hour in seconds)
# Per CONTEXT.md: Sessions auto-expire after 1 hour
GAME_SESSION_TTL = 3600

# FSRS stability threshold for mature memory classification (days)
MASTERY_MATURE_THRESHOLD = 21.0

# Cache TTL for memory mastery data (seconds, matches review overview TTL)
MASTERY_CACHE_TTL = 300
