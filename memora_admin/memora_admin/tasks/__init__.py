"""
Scheduled tasks for Memora Admin.

Tasks:
- build_worker: Process pending content builds (every 2 minutes)
- sync: Persist Redis game state to MariaDB (every 1 minute)
  - sync_dirty_progress: Flush dirty progress bitmaps to Structure Progress
  - sync_dirty_wallets: Flush dirty wallet data to Player Profile
  - flush_interaction_buffer: Flush interaction buffer to Interaction Log
"""
