"""Redis key constants for memora services."""

# Dirty set keys for background sync
# Frappe sync tasks process these sets to persist to MariaDB
DIRTY_PROGRESS_KEY = "memora:dirty:progress"
DIRTY_WALLETS_KEY = "memora:dirty:wallets"

# Interaction buffer key
INTERACTION_BUFFER_KEY = "memora:buffer:interactions"
