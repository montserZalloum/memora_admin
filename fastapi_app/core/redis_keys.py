"""Centralized Redis key builders for all memora: keys.

EVERY Redis key used in the project must be defined here.
When adding a new Redis key, create a builder function here first.
Do NOT use inline f-strings for memora: keys anywhere in the codebase.

This file is the single source of truth for:
- Key naming conventions
- Key documentation (what data lives there, what type, TTL)
- Preventing key format mismatches between producers and consumers

Usage:
    from fastapi_app.core.redis_keys import access_key, wallet_key
    data = await redis.hgetall(wallet_key("PLAYER-00001"))
"""

# =============================================================================
# TTL Constants (seconds)
# =============================================================================
# TTL policy for keys that self-heal via ensure_hydrated().
# Keys with TTL are evictable under memory pressure (volatile-ttl policy).
# Protected keys (dirty sets, buffer) MUST NEVER have TTL.
#
# NOTE: Lua scripts cannot import Python constants, so they use literal values.
# Cross-references below document which Lua scripts duplicate each constant.
# If you change a TTL value, update BOTH the constant here AND the Lua literal.

ANNOUNCEMENTS_CACHE_TTL = 300
"""5 minutes. Applied to announcements:active STRING key.

Short TTL handles date-based expiry naturally without scheduled cleanup.
Admin actions (create/edit/delete) trigger immediate invalidation via
two-pronged pattern (DEL + pubsub), so TTL is only a safety net.
"""

WALLET_KEY_TTL = 172800
"""48 hours. Applied to wallet:{player} hashes.

Lua duplicates:
- wallet.py STREAK_UPDATE_SCRIPT uses literal 172800
"""

PROGRESS_KEY_TTL = 172800
"""48 hours. Applied to progress:{user}:{subject}:v{version} bitmaps.

Lua duplicates:
- game_session.py SESSION_COMPLETE_SCRIPT uses literal 172800
"""

ACCESS_KEY_TTL = 86400
"""24 hours. Applied to access:{player} sets."""

PLAN_FREE_SUBJECTS_TTL = 43200
"""12 hours. Applied to plan:{plan}:free_subjects sets."""

# =============================================================================
# Access & Permissions
# =============================================================================


def access_key(player_id: str) -> str:
	"""Player's access grants set.

	Type: SET of content keys (e.g., "SUB-MATH", "TRK-MATH-01")
	Producers: access_sync.py (Frappe hook), AccessService.ensure_hydrated()
	Consumers: AccessService.check_access(), CatalogService.get_player_catalog()
	TTL: None (persistent, explicit SREM to revoke)
	"""
	return f"memora:access:{player_id}"


def plan_free_subjects_key(plan_id: str) -> str:
	"""Subjects marked as non-premium (is_premium=0) in a plan.

	Type: SET of subject IDs
	Producers: access_sync.py on_plan_subject_changed(), rebuild_plan_free_subjects()
	Consumers: AccessService.is_subject_free_in_plan()
	TTL: None (event-driven invalidation)
	"""
	return f"memora:plan:{plan_id}:free_subjects"


def subjects_with_free_content_key() -> str:
	"""Global set of subjects that have at least one free unit or topic.

	Type: SET of subject IDs
	Producers: access_sync.py (unit/topic is_free hooks), HierarchyService (auto-repair)
	Consumers: HierarchyService.get_subjects_with_free_content()
	TTL: None (event-driven invalidation)
	"""
	return "memora:subjects_with_free_content"


# =============================================================================
# Progress & Completion
# =============================================================================


def progress_key(user_id: str, subject_id: str, version: int = 1) -> str:
	"""Player's lesson completion bitmap for a subject.

	Type: STRING (bitmap — each bit = one lesson)
	Producers: ProgressService.complete_lesson(), GameSessionService.complete_session()
	Consumers: ProgressService.is_complete(), StatsService (recompute)
	TTL: None (synced to MariaDB via dirty set)
	"""
	return f"memora:progress:{user_id}:{subject_id}:v{version}"


def stats_key(user_id: str, subject_id: str, version: int = 1) -> str:
	"""Pre-computed progress statistics hash for a subject.

	Type: HASH (completed, total, {track_id}:completed, {track_id}:total, ...)
	Also stores _content_hash for staleness detection.
	Producers: StatsService.set_stats(), StatsService.increment_completion_stats()
	Consumers: StatsService.get_stats(), progress endpoint
	TTL: 1 hour (with jitter)
	"""
	return f"memora:stats:{user_id}:{subject_id}:v{version}"


def dirty_progress_key() -> str:
	"""Dirty set of progress keys pending sync to MariaDB.

	Type: SET of strings formatted as "{user_id}:{subject_id}:v{version}"
	Producers: ProgressService.complete_lesson(), GameSessionService (Lua script)
	Consumers: sync.py sync_dirty_progress()
	TTL: None (consumed by background task)
	"""
	return "memora:dirty:progress"


# =============================================================================
# Wallet & Economy
# =============================================================================


def wallet_key(player_id: str) -> str:
	"""Player's wallet hash (XP, streak, streak_date).

	Type: HASH (xp: int, streak: int, streak_date: YYYY-MM-DD)
	Producers: WalletService.award_xp(), WalletService.update_streak()
	Consumers: WalletService.get_wallet(), ProfilePageService.get_hero()
	TTL: None (synced to MariaDB via dirty set)
	"""
	return f"memora:wallet:{player_id}"


def dirty_wallets_key() -> str:
	"""Dirty set of player IDs pending wallet sync to MariaDB.

	Type: SET of player IDs
	Producers: WalletService.award_xp(), WalletService.update_streak()
	Consumers: sync.py sync_dirty_wallets()
	TTL: None (consumed by background task)
	"""
	return "memora:dirty:wallets"


def daily_xp_key(player_id: str) -> str:
	"""Per-player daily XP summary hash (MariaDB-backed).

	Type: HASH (date string -> XP earned that day)
	Producers: LeaderboardService.update_leaderboards()
	Consumers: ProfilePageService.get_weekly_activity() (Phase 3 fallback)
	TTL: 8 days (covers 7-day activity window + 1 buffer)
	"""
	return f"memora:daily_xp:{player_id}"


# =============================================================================
# Content Hierarchy & Metadata
# =============================================================================


def hierarchy_key(subject_id: str) -> str:
	"""Cached subject hierarchy JSON (units, topics, lessons, stages).

	Type: STRING (JSON-encoded SubjectHierarchy)
	Producers: HierarchyService.get_hierarchy() on cache miss
	Consumers: HierarchyService.get_hierarchy(), progress/session endpoints
	TTL: 1 hour
	"""
	return f"memora:hierarchy:{subject_id}"


def catalog_key(plan_id: str) -> str:
	"""Cached product catalog JSON for a plan.

	Type: STRING (JSON array of CatalogProduct)
	Producers: CatalogService.get_catalog() on cache miss
	Consumers: CatalogService.get_catalog(), CatalogService.get_player_catalog()
	TTL: None (infinite — event-driven invalidation only)
	"""
	return f"memora:catalog:{plan_id}"


def plan_manifest_key(plan_id: str) -> str:
	"""Cached plan manifest JSON for mobile app serving.

	Type: STRING (JSON-encoded PlanManifest)
	Producers: PlanService.get_manifest() on cache miss
	Consumers: PlanService.get_manifest()
	TTL: 1 hour
	"""
	return f"memora:plan:{plan_id}:manifest"


# =============================================================================
# Session & Game State
# =============================================================================


def session_key(user_id: str) -> str:
	"""Player's auth session data (family ID + plan).

	Type: STRING (JSON: {"fid": uuid, "plan": plan_id})
	Producers: SessionService.create_session()
	Consumers: SessionService.validate_session()
	TTL: 30 days (matches refresh token lifetime)
	"""
	return f"memora:session:{user_id}"


def game_session_key(user_id: str) -> str:
	"""Active game session hash.

	Type: HASH (session_id, lesson_id, subject_id, device_id, started_at)
	Producers: GameSessionService.start_session() (Lua script)
	Consumers: GameSessionService.get_active_session(), complete_session()
	TTL: 1 hour (auto-expire abandoned sessions)
	"""
	return f"memora:gamesession:{user_id}"


def game_session_completion_key(user_id: str, session_id: str) -> str:
	"""Recently completed lesson-session response cache.

	Type: STRING (JSON-encoded EndSessionResponse or "__PENDING__")
	Producers: GameSessionService.complete_session(), cache_end_response()
	Consumers: GameSessionService.get_end_response_state()
	TTL: Short-lived (used only to absorb duplicate end-session retries)
	"""
	return f"memora:gamesession:complete:{user_id}:{session_id}"


def devices_key(user_id: str) -> str:
	"""Player's registered devices hash.

	Type: HASH (device:{id}:name, device:{id}:ua, device:{id}:platform, ...)
	Producers: DeviceService.register_device() (Lua script)
	Consumers: DeviceService.get_devices(), DeviceService.validate_device()
	TTL: None (persistent until admin removal)
	"""
	return f"memora:devices:{user_id}"


# =============================================================================
# Practice Arena
# =============================================================================


def dirty_review_items_key() -> str:
	"""Dirty set of lesson IDs pending Review Item extraction.

	Type: SET of lesson names (e.g., "LES-00001")
	Producers: review_item_sync.on_lesson_save() (SADD)
	Consumers: sync.py sync_dirty_review_items() (SMEMBERS + SREM)
	TTL: None (protected — never evicted)
	Schedule: Every 2 minutes (*/2 * * * *)
	"""
	return "memora:dirty:review_items"


def practice_session_key(player_id: str) -> str:
	"""Active practice session for a player.

	Type: HASH (subject_id, filter, tracks, units, topics, batch_seq,
	           served_item_ids, accessible_lessons, created_at, submitted_{N})
	Producers: PracticeService.start_session()
	Consumers: PracticeService.continue_session(), submit_batch()
	TTL: practice_session_ttl (default 3600s)
	"""
	return f"memora:practice:{player_id}"


def practice_hierarchy_meta_key(subject_id: str) -> str:
	"""Cached practice hierarchy metadata (titles + Review Item counts).

	Type: STRING (JSON: {subject_title, tracks, units, topics, item_counts})
	Producers: PracticeService._load_hierarchy_meta() on cache miss
	Consumers: PracticeService.get_practice_hierarchy()
	TTL: 1 hour
	"""
	return f"memora:practice:hierarchy_meta:{subject_id}"


# =============================================================================
# Leaderboard
# =============================================================================

# Leaderboard prefix used by both FastAPI and Frappe tasks
LB_PREFIX = "memora:lb"

# Leaderboard metadata prefix (tier index + tier counts)
LBMETA_PREFIX = "memora:lbmeta"

# Plan-scoped leaderboard TTLs
PLAN_DAILY_KEY_TTL = 48 * 3600  # 48 hours
PLAN_WEEKLY_KEY_TTL = 8 * 86400  # 8 days

# Backfill lock TTL
LBMETA_LOCK_TTL = 30  # 30 seconds — auto-expire safety net


def lb_daily_key(date_str: str, subject_id: str | None = None) -> str:
	"""Daily leaderboard sorted set (resets at midnight Asia/Amman).

	Type: ZSET (player_id -> XP earned today)
	Producers: LeaderboardService.update_leaderboards()
	Consumers: LeaderboardService.get_top(), ProfilePageService.get_weekly_activity()
	TTL: 30 days
	"""
	base = f"{LB_PREFIX}:daily:{date_str}"
	return f"{base}:subject:{subject_id}" if subject_id else base


def lb_weekly_key(friday_date: str, subject_id: str | None = None) -> str:
	"""Weekly leaderboard sorted set (Islamic week: Fri-Thu).

	Type: ZSET (player_id -> XP earned this week)
	Producers: LeaderboardService.update_leaderboards()
	Consumers: LeaderboardService.get_top(), get_my_rank()
	TTL: 90 days
	"""
	base = f"{LB_PREFIX}:weekly:{friday_date}"
	return f"{base}:subject:{subject_id}" if subject_id else base


def lb_daily_plan_key(date_str: str, plan_id: str, subject_id: str | None = None) -> str:
	"""Daily plan-scoped leaderboard sorted set.

	Type: ZSET (player_id -> XP earned today within plan)
	Producers: LeaderboardService.update_leaderboards()
	Consumers: LeaderboardService.get_top(), get_my_rank()
	TTL: 48 hours (PLAN_DAILY_KEY_TTL)
	"""
	base = f"{LB_PREFIX}:daily:{date_str}:plan:{plan_id}"
	return f"{base}:subject:{subject_id}" if subject_id else base


def lb_weekly_plan_key(friday_date: str, plan_id: str, subject_id: str | None = None) -> str:
	"""Weekly plan-scoped leaderboard sorted set (Islamic week: Fri-Thu).

	Type: ZSET (player_id -> XP earned this week within plan)
	Producers: LeaderboardService.update_leaderboards()
	Consumers: LeaderboardService.get_top(), get_my_rank()
	TTL: 8 days (PLAN_WEEKLY_KEY_TTL)
	"""
	base = f"{LB_PREFIX}:weekly:{friday_date}:plan:{plan_id}"
	return f"{base}:subject:{subject_id}" if subject_id else base


def lb_archive_daily_key(date_str: str, subject_id: str | None = None) -> str:
	"""Archived daily leaderboard (copied by leaderboard_reset.py at 00:10 AM).

	Type: ZSET (snapshot of yesterday's daily leaderboard)
	Producers: leaderboard_reset.py archive_daily_leaderboard()
	Consumers: ProfilePageService.get_weekly_activity() (Phase 2 fallback)
	TTL: 30 days
	"""
	base = f"{LB_PREFIX}:archive:daily:{date_str}"
	return f"{base}:subject:{subject_id}" if subject_id else base


# =============================================================================
# Leaderboard Metadata (Tier Index)
# =============================================================================


def _lbmeta_scope_suffix(
	period: str,
	date_str: str,
	plan_id: str | None = None,
	subject_id: str | None = None,
) -> str:
	"""Build the scope segment shared by tieridx/tiercnt keys.

	Internal helper — use lbmeta_tieridx_key() or lbmeta_tiercnt_key() instead.
	"""
	parts = [LBMETA_PREFIX, period, date_str]
	if plan_id:
		parts.extend(["plan", plan_id])
	if subject_id:
		parts.extend(["subject", subject_id])
	return ":".join(parts)


def lbmeta_tieridx_key(
	period: str,
	date_str: str,
	plan_id: str | None = None,
	subject_id: str | None = None,
) -> str:
	"""Tier index ZSET for a leaderboard (member=XP tier string, score=XP tier value).

	Type: ZSET (e.g., member="193", score=193.0)
	Producers: _TIER_AWARE_ZINCRBY_LUA (atomic write), backfill_tier_metadata()
	Consumers: get_my_rank() indexed read path (ZCOUNT + ZRANGEBYSCORE)
	TTL: Same as parent leaderboard (set via EXPIRE after Lua eval)
	"""
	return f"{_lbmeta_scope_suffix(period, date_str, plan_id, subject_id)}:tieridx"


def lbmeta_tiercnt_key(
	period: str,
	date_str: str,
	plan_id: str | None = None,
	subject_id: str | None = None,
) -> str:
	"""Tier counts HASH for a leaderboard (field=XP tier string, value=player count).

	Type: HASH (e.g., field="193", value="5")
	Producers: _TIER_AWARE_ZINCRBY_LUA (atomic write), backfill_tier_metadata()
	Consumers: Internal (used by Lua script for tier lifecycle management)
	TTL: Same as parent leaderboard (set via EXPIRE after Lua eval)
	"""
	return f"{_lbmeta_scope_suffix(period, date_str, plan_id, subject_id)}:tiercnt"


def lbmeta_lock_key(lb_key_suffix: str) -> str:
	"""Per-leaderboard lock for backfill (SET NX EX 30 pattern).

	Type: STRING (value: "backfill:{timestamp}")
	Producers: backfill_tier_metadata()
	Consumers: backfill_tier_metadata()
	TTL: 30 seconds (LBMETA_LOCK_TTL — auto-expire safety net)
	"""
	return f"{LBMETA_PREFIX}:lock:{lb_key_suffix}"


def lbmeta_keys_from_lb_key(lb_key: str) -> tuple[str, str]:
	"""Derive tier metadata keys from an existing leaderboard key.

	Replaces the 'memora:lb:' prefix with 'memora:lbmeta:' and appends
	':tieridx' / ':tiercnt' suffixes.

	Args:
		lb_key: Full leaderboard key (e.g., 'memora:lb:daily:2026-03-01:subject:SUBJ-001')

	Returns:
		Tuple of (tieridx_key, tiercnt_key)

	Example:
		>>> lbmeta_keys_from_lb_key("memora:lb:daily:2026-03-01")
		('memora:lbmeta:daily:2026-03-01:tieridx', 'memora:lbmeta:daily:2026-03-01:tiercnt')
	"""
	suffix = lb_key.replace(f"{LB_PREFIX}:", "", 1)
	base = f"{LBMETA_PREFIX}:{suffix}"
	return (f"{base}:tieridx", f"{base}:tiercnt")


# =============================================================================
# Interaction Buffer (FSRS)
# =============================================================================


def interaction_buffer_key() -> str:
	"""JSON-encoded interaction events pending batch flush to MariaDB.

	Type: LIST of JSON strings
	Producers: GameSessionService.complete_session() (Lua RPUSH)
	Consumers: sync.py flush_interaction_buffer()
	TTL: None (consumed by background task)
	"""
	return "memora:buffer:interactions"


# =============================================================================
# Auth Rate Limiting
# =============================================================================


def ratelimit_ip_key(ip_address: str) -> str:
	"""Auth attempt counter per IP (login rate limiting).

	Type: STRING (counter)
	Producers: RateLimiter.check_rate_limit()
	Consumers: RateLimiter.check_rate_limit(), get_remaining()
	TTL: 60 seconds (window)
	"""
	return f"memora:ratelimit:ip:{ip_address}"


def ratelimit_account_key(account: str) -> str:
	"""Auth attempt counter per account/email (login rate limiting).

	Type: STRING (counter)
	Producers: RateLimiter.check_rate_limit()
	Consumers: RateLimiter.check_rate_limit(), get_remaining()
	TTL: 60 seconds (window)
	"""
	return f"memora:ratelimit:account:{account}"


# =============================================================================
# Global & Per-Player Rate Limiting
# =============================================================================


def global_ratelimit_key(ip_address: str) -> str:
	"""Global per-IP rate limit counter (middleware).

	Type: STRING (counter)
	Producers: GlobalRateLimitMiddleware
	Consumers: GlobalRateLimitMiddleware
	TTL: Configurable window (default 60s)
	"""
	return f"memora:global_rl:ip:{ip_address}"


def player_ratelimit_key(scope: str, player_id: str) -> str:
	"""Per-player rate limit counter for specific scopes.

	Scopes: "reviews", "session_start", "session_end"

	Type: STRING (counter)
	Producers: deps.py player_rate_limit()
	Consumers: deps.py player_rate_limit()
	TTL: Configurable window from settings
	"""
	return f"memora:rl:{scope}:{player_id}"


# =============================================================================
# Voucher / Redemption
# =============================================================================


def voucher_fail_player_key(player_id: str) -> str:
	"""Failed voucher redemption attempts per player.

	Type: STRING (counter)
	Producers: VoucherService.record_failure()
	Consumers: VoucherService.check_rate_limit()
	TTL: 1 hour
	"""
	return f"memora:voucher_fail:player:{player_id}"


def voucher_fail_ip_key(ip_address: str) -> str:
	"""Failed voucher redemption attempts per IP.

	Type: STRING (counter)
	Producers: VoucherService.record_failure()
	Consumers: VoucherService.check_rate_limit()
	TTL: 1 hour
	"""
	return f"memora:voucher_fail:ip:{ip_address}"


# =============================================================================
# Reviews (FSRS Spaced Repetition)
# =============================================================================


def reviews_overview_key(player_id: str) -> str:
	"""Cached review overview (due count per subject).

	Type: STRING (JSON array)
	Producers: ReviewService.get_overview() on cache miss
	Consumers: ReviewService.get_overview()
	TTL: 5 minutes
	"""
	return f"memora:reviews_overview:{player_id}"


# =============================================================================
# Profile & User Data
# =============================================================================


def profile_key(player_id: str) -> str:
	"""Cached player profile (display_name, avatar).

	Type: STRING (JSON PlayerProfile)
	Producers: ProfileService.set_profile(), profile_sync.py, profile_cache.py
	Consumers: ProfileService.get_profiles_batch()
	TTL: 1 hour
	"""
	return f"memora:profile:{player_id}"


def player_plan_key(player_id: str) -> str:
	"""Cached player's plan ID for profile page optimization.

	Type: STRING (plan_id)
	Producers: ProfilePageService._resolve_season_seq()
	Consumers: ProfilePageService._resolve_season_seq()
	TTL: 24 hours
	"""
	return f"memora:player_plan:{player_id}"


def plan_season_seq_key(plan_id: str) -> str:
	"""Cached plan's season sequence number.

	Type: STRING (integer as string)
	Producers: ProfilePageService._resolve_season_seq()
	Consumers: ProfilePageService._resolve_season_seq()
	TTL: 24 hours
	"""
	return f"memora:plan_season_seq:{plan_id}"


def items_learned_key(player_id: str, subject_id: str | None = None) -> str:
	"""Cached items learned count (Memory State records).

	Type: STRING (integer as string)
	Producers: ProfilePageService.get_stats() on cache miss
	Consumers: ProfilePageService.get_stats()
	TTL: 5 minutes (MASTERY_CACHE_TTL)
	"""
	return f"memora:items_learned:{player_id}:{subject_id or 'all'}"


def mastery_key(player_id: str, subject_id: str | None, season_seq: int) -> str:
	"""Memory mastery counters (mature/learning counts).

	Type: HASH (mature: int, learning: int)
	Producers: Frappe API get_memory_mastery() as side effect
	Consumers: ProfilePageService.get_mastery()
	TTL: 5 minutes (MASTERY_CACHE_TTL)
	"""
	subj = subject_id or "all"
	return f"memora:mastery:{player_id}:{subj}:s{season_seq}"


# =============================================================================
# Announcements
# =============================================================================


def announcements_active_key() -> str:
	"""All active announcements JSON cache.

	Type: STRING (JSON array of announcement dicts)
	Producers: AnnouncementService.get_active_announcements() on cache miss
	Consumers: AnnouncementService.get_for_player()
	TTL: 5 minutes (ANNOUNCEMENTS_CACHE_TTL)
	Invalidation: Frappe hook on Memora Announcement → DEL + pubsub
	"""
	return "memora:announcements:active"


# =============================================================================
# Configuration & Settings
# =============================================================================


def level_config_key() -> str:
	"""Dynamic level configuration (curve params + titles).

	Type: STRING (JSON: {a, b, max_level, titles})
	Producers: level_sync.py on_level_settings_updated()
	Consumers: level_config.py get_level_config()
	TTL: 1 hour
	"""
	return "memora:config:levels"


def gamification_settings_key() -> str:
	"""Cached gamification settings (XP rewards, etc.).

	Type: STRING (JSON GamificationSettings)
	Producers: settings_sync.on_settings_updated() (eager write on save),
	           SettingsService._hydrate_from_frappe() (cold start fallback)
	Consumers: SettingsService.get_gamification_settings()
	TTL: None (persistent — invalidated by Frappe hook on Memora Settings save)
	"""
	return "memora:settings:gamification"


# =============================================================================
# Pub/Sub Channels
# =============================================================================


def cache_invalidation_channel() -> str:
	"""Pub/sub channel for cache invalidation from Frappe to FastAPI.

	Payload: JSON {"type": "hierarchy|plan|profile|catalog|...", ...}
	Producers: build_trigger.py, catalog_sync.py, profile_sync.py, level_sync.py
	Consumers: pubsub.py start_pubsub_listener()
	"""
	return "memora:cache:invalidate"


def notify_channel(user_id: str) -> str:
	"""Per-user notification pub/sub channel for WebSocket forwarding.

	Payload: JSON event (e.g., subscription approved/rejected)
	Producers: memora_subscription_transaction.py
	Consumers: pubsub.py start_notification_listener(), notifications.py WebSocket
	"""
	return f"memora:notify:{user_id}"


# =============================================================================
# Purchase / Pending Transactions
# =============================================================================


def pending_key(player_id: str) -> str:
	"""Player's pending purchase set (hides products from catalog).

	Type: SET of product_grant_id strings
	Producers: PurchaseService.submit_purchase()
	Consumers: CatalogService.get_player_catalog(), subscription_transaction.py
	TTL: None (cleared when transaction is approved/rejected)
	"""
	return f"memora:pending:{player_id}"


# =============================================================================
# Season (Frappe-managed)
# =============================================================================


def season_key(season_id: str) -> str:
	"""Season metadata hash (Gate 1 validation).

	Type: HASH (is_published, start_date, end_date, season_seq)
	Producers: access_sync.py on_season_updated()
	Consumers: SeasonService (Gate 1 check)
	TTL: None (event-driven)
	"""
	return f"memora:season:{season_id}"


# =============================================================================
# Build System (Frappe-managed)
# =============================================================================


def build_debounce_key(plan_id: str) -> str:
	"""Build debounce key to prevent flooding (SET NX EX pattern).

	Type: STRING (timestamp)
	Producers: build_trigger.py on_content_updated(), on_plan_updated()
	Consumers: build_trigger.py (NX check)
	TTL: 120 seconds (DEBOUNCE_SECONDS)
	"""
	return f"memora:build:pending:plan:{plan_id}"


def build_retry_key(build_id: str) -> str:
	"""Build retry counter for failed builds.

	Type: STRING (counter)
	Producers: build_worker.py
	Consumers: build_worker.py
	TTL: Build-specific
	"""
	return f"memora:build:retry:{build_id}"


# =============================================================================
# FSRS Background Processor (Frappe-managed)
# =============================================================================


def fsrs_last_processed_key() -> str:
	"""Timestamp of last FSRS processing run.

	Type: STRING (ISO timestamp)
	Producers: fsrs_processor.py
	Consumers: fsrs_processor.py
	"""
	return "memora:fsrs:last_processed"


def fsrs_processed_key(player: str, item_id: str, creation: str) -> str:
	"""Idempotency key for FSRS interaction processing.

	Type: STRING (flag)
	Producers: fsrs_processor.py
	Consumers: fsrs_processor.py
	"""
	return f"memora:fsrs:processed:{player}:{item_id}:{creation}"


def fsrs_card_state_key(player: str, item_id: str) -> str:
	"""FSRS card state cache.

	Type: STRING (JSON card state)
	Producers: fsrs_processor.py
	Consumers: fsrs_processor.py
	"""
	return f"memora:fsrs:{player}:{item_id}"


# =============================================================================
# Task Deduplication (Frappe-managed)
# =============================================================================


def task_ran_key(hour_key: str) -> str:
	"""Dedup flag for hourly Frappe tasks (e.g., profile cache warmup).

	Type: STRING ("1")
	Producers: profile_cache.py
	Consumers: profile_cache.py
	TTL: 1 hour
	"""
	return f"memora:task_ran:{hour_key}"


# =============================================================================
# Subject Totals Cache (Sync tasks)
# =============================================================================


def subject_total_lessons_key(subject_id: str) -> str:
	"""Cached total lesson count for a subject (used by sync).

	Type: STRING (integer as string)
	Producers: sync.py _batch_get_subject_totals()
	Consumers: sync.py sync_dirty_progress()
	TTL: 1 hour
	"""
	return f"memora:subject:total_lessons:{subject_id}"


# =============================================================================
# Hydration
# =============================================================================


def hydration_lock_key(cache_key: str) -> str:
	"""Distributed lock during cache hydration (prevents thundering herd).

	Type: STRING with short TTL (SET NX EX pattern)
	Producers: hydration.py guarded_hydrate()
	Consumers: hydration.py guarded_hydrate()
	TTL: 30 seconds (lock_ttl parameter)
	"""
	return f"memora:hydrating:{cache_key}"


# =============================================================================
# Reports
# =============================================================================


# =============================================================================
# Plan Change
# =============================================================================

FREEZE_KEY_TTL = 30
"""30 seconds. Safety-net auto-expire for per-player freeze during plan change.

If the plan change operation crashes mid-way, the freeze key expires and
normal operations (sync jobs, session endpoints) resume automatically.
"""

PLAN_CHANGE_COOLDOWN_TTL = 86400
"""24 hours. Cooldown window between successive plan changes (FR-004)."""


def freeze_key(player_id: str) -> str:
	"""Per-player freeze during plan change.

	Type: STRING (value: Unix timestamp of freeze start)
	Producers: PlanChangeService.execute()
	Consumers: sync_dirty_wallets(), sync_dirty_progress(),
	           POST /sessions/start, POST /sessions/end
	TTL: 30s (FREEZE_KEY_TTL — safety net auto-expire)
	"""
	return f"memora:freeze:{player_id}"


def plan_change_ts_key(player_id: str) -> str:
	"""Cooldown timestamp for plan change rate limiting.

	Type: STRING (value: Unix timestamp of last plan change)
	Producers: PlanChangeService.execute() (after successful change)
	Consumers: PlanChangeService._check_cooldown()
	TTL: 24h (PLAN_CHANGE_COOLDOWN_TTL)
	"""
	return f"memora:plan_change_ts:{player_id}"


# --- SCAN patterns for per-player cache cleanup (plan change) ---


def player_progress_pattern(player_id: str) -> str:
	"""SCAN pattern matching all progress bitmaps for a player.

	Matches keys produced by progress_key(player_id, *, *).
	Consumers: PlanChangeService._pre_cleanup(), _post_cleanup()
	"""
	return f"memora:progress:{player_id}:*"


def player_stats_pattern(player_id: str) -> str:
	"""SCAN pattern matching all stats hashes for a player.

	Matches keys produced by stats_key(player_id, *, *).
	Consumers: PlanChangeService._post_cleanup()
	"""
	return f"memora:stats:{player_id}:*"


def player_items_learned_pattern(player_id: str) -> str:
	"""SCAN pattern matching all items-learned counts for a player.

	Matches keys produced by items_learned_key(player_id, *).
	Consumers: PlanChangeService._post_cleanup()
	"""
	return f"memora:items_learned:{player_id}:*"


def player_mastery_pattern(player_id: str) -> str:
	"""SCAN pattern matching all mastery hashes for a player.

	Matches keys produced by mastery_key(player_id, *, *).
	Consumers: PlanChangeService._post_cleanup()
	"""
	return f"memora:mastery:{player_id}:*"


def player_fsrs_pattern(player_id: str) -> str:
	"""SCAN pattern matching all FSRS card state keys for a player.

	Matches keys produced by fsrs_card_state_key(player_id, *).
	Consumers: PlanChangeService._post_cleanup()
	"""
	return f"memora:fsrs:{player_id}:*"


def player_fsrs_processed_pattern(player_id: str) -> str:
	"""SCAN pattern matching all FSRS processed keys for a player.

	Matches keys produced by fsrs_processed_key(player_id, *, *).
	Consumers: PlanChangeService._post_cleanup()
	"""
	return f"memora:fsrs:processed:{player_id}:*"


def report_cooldown_key(player_id: str) -> str:
	"""Cooldown for player content reports (1 per 60s).

	Type: STRING ("1")
	Producers: reports.py submit_content_report()
	Consumers: reports.py submit_content_report()
	TTL: 60 seconds
	"""
	return f"memora:report_cooldown:{player_id}"


# =============================================================================
# OTP & Registration
# =============================================================================


def registration_options_key() -> str:
	"""Cached registration options (grades, plans, seasons).

	Type: STRING (JSON: {grades, plans, seasons})
	Producers: auth.py _get_registration_options() on cache miss
	Consumers: auth.py get_registration_options(), register verify
	TTL: 5 minutes
	"""
	return "memora:registration_options"


def pending_reg_key(pending_id: str) -> str:
	"""Pending registration data awaiting OTP verification.

	Type: STRING (JSON with mobile, plan, grade, major, etc.)
	Producers: OTPService.create_pending_registration()
	Consumers: OTPService.verify_registration_otp(), resend_registration_otp()
	TTL: 5 minutes (OTP_TTL)
	"""
	return f"memora:pending_reg:{pending_id}"


def phone_reserved_key(mobile: str) -> str:
	"""Phone reservation flag during registration flow.

	Prevents duplicate registrations for the same phone while OTP is pending.

	Type: STRING ("1")
	Producers: auth endpoint (initiate registration)
	Consumers: auth endpoint (check phone availability)
	TTL: 10 minutes
	"""
	return f"memora:phone_reserved:{mobile}"


def ratelimit_otp_phone_key(mobile: str) -> str:
	"""OTP send rate limit counter per phone number.

	Type: STRING (counter)
	Producers: auth endpoint (send OTP)
	Consumers: auth endpoint (send OTP)
	TTL: 10 minutes
	"""
	return f"memora:ratelimit:otp:phone:{mobile}"


def ratelimit_otp_ip_key(ip: str) -> str:
	"""OTP send rate limit counter per IP address.

	Type: STRING (counter)
	Producers: OTPService._check_otp_rate_limit()
	Consumers: OTPService._check_otp_rate_limit()
	TTL: 10 minutes (RATE_LIMIT_WINDOW)
	"""
	return f"memora:ratelimit:otp:ip:{ip}"


def ratelimit_otp_cooldown_key(mobile: str) -> str:
	"""OTP resend cooldown flag per phone number.

	Prevents spamming OTP resend — 60s cooldown between requests.

	Type: STRING ("1")
	Producers: OTPService._set_cooldown()
	Consumers: OTPService._check_cooldown()
	TTL: 60 seconds (COOLDOWN_TTL)
	"""
	return f"memora:ratelimit:otp:cooldown:{mobile}"


def reset_otp_key(mobile: str) -> str:
	"""Password reset OTP data for phone verification.

	Type: STRING (JSON: {"otp": code, "attempts": int})
	Producers: OTPService.create_password_reset()
	Consumers: OTPService.verify_password_reset_otp()
	TTL: 5 minutes (OTP_TTL)
	"""
	return f"memora:reset_otp:{mobile}"


def reset_token_key(token: str) -> str:
	"""Password reset token mapping to mobile number.

	Type: STRING (mobile number)
	Producers: auth endpoint (after OTP verified)
	Consumers: auth endpoint (set new password)
	TTL: 10 minutes
	"""
	return f"memora:reset_token:{token}"


# =============================================================================
# Webhooks
# =============================================================================


def webhook_idempotency_key(event_id: str) -> str:
	"""Webhook event idempotency marker.

	Type: STRING ("processing" or "completed")
	Producers: webhooks.py payment_webhook()
	Consumers: webhooks.py payment_webhook(), process_payment_webhook()
	TTL: 24 hours
	"""
	return f"memora:webhook:{event_id}"


def webhook_retry_queue_key() -> str:
	"""Webhook retry queue for failed payment webhooks.

	Type: LIST of JSON strings
	Producers: webhooks.py process_payment_webhook() on failure
	Consumers: webhooks.py retry processing
	TTL: None (persistent until processed)
	"""
	return "memora:webhook:retry_queue"


# =============================================================================
# SCAN Patterns (for background tasks iterating over key sets)
# =============================================================================

# Pattern for SCAN match= parameter to iterate all game session keys
GAME_SESSION_SCAN_PATTERN = "memora:gamesession:*"

# Pattern for SCAN match= parameter to iterate all wallet keys
WALLET_SCAN_PATTERN = "memora:wallet:*"
