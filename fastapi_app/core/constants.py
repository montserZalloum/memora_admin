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

# FSRS stability threshold for mature memory classification (days)
MASTERY_MATURE_THRESHOLD = 21.0

# Cache TTL for memory mastery data (seconds, matches review overview TTL)
MASTERY_CACHE_TTL = 300
