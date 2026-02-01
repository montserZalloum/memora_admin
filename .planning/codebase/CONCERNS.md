# Codebase Concerns

**Analysis Date:** 2026-02-01

## Tech Debt

**Stub Implementation Across All DocTypes:**
- Issue: 54 Python files contain only pass statements with no implementation logic
- Files: All doctype files in `memora_admin/memora_admin/doctype/*/doctype_name.py` (e.g., `memora_player_subscription.py`, `memora_memory_state.py`, `memora_structure_progress.py`)
- Impact: No business logic, validation, or computed fields. Default Frappe behavior only - all custom behavior will need to be implemented
- Fix approach: Incrementally add validation methods, hooks, and computed field logic to each doctype as per CHANGES_SUMMARY.txt requirements

**Empty Test Suites:**
- Issue: 25 test files contain only pass statements with no actual test coverage
- Files: All doctype test files in `memora_admin/memora_admin/doctype/*/test_doctype_name.py`
- Impact: Zero test coverage; integration and business logic changes are unverified
- Fix approach: Implement test cases for validation rules, permission checks, and cross-doctype relationships

**Commented-Out JavaScript Client Logic:**
- Issue: All 25+ doctype JavaScript files contain only commented code
- Files: All `memora_admin/memora_admin/doctype/*/doctype_name.js`
- Impact: No frontend validation, form refresh logic, or field dependencies work on the client
- Fix approach: Implement frappe.ui.form.on handlers for dynamic field behavior, validation, and dependent calculations

**Missing Hooks Implementation:**
- Issue: `hooks.py` has all major hooks commented out (doc_events, scheduler_events, before_request, etc.)
- Files: `memora_admin/hooks.py` (lines 140-246)
- Impact: Cannot implement cross-cutting concerns like automatic field calculations, scheduled jobs, or request-level logging
- Fix approach: Activate and implement specific hooks as features are developed (e.g., Memory State FSRS updates, Build Queue processing)

## Known Bugs

**ESLint Configuration Too Permissive:**
- Symptoms: Many style rules disabled; unused variables and console statements allowed
- Files: `.eslintrc` (lines 11-25: "no-unused-vars": "off", "no-console": ["warn"])
- Trigger: Running eslint on JavaScript files will not catch unused variables or stray console.log statements
- Workaround: Manual code review or enable rules before production
- Fix approach: Enable "no-unused-vars": "error" and "no-console": "error" once all commented code is removed

**Permission Model Undefined:**
- Issue: All doctypes have System Manager-only permissions hardcoded
- Files: All `memora_admin/memora_admin/doctype/*/doctype_name.json` (permissions section)
- Impact: No role-based access control; Content Managers, Players, and custom roles cannot be granted permissions
- Fix approach: Define permission roles in hooks (Admin, Content Manager, Player, System Manager) and assign appropriate permissions per doctype

**No Input Validation or Constraints:**
- Issue: Many Link fields reference doctypes without validation that target exists
- Files: All doctype JSON files (e.g., `memora_memory_state.json` references "season", "subject", "player" without link_doctype validation)
- Impact: Orphaned records can reference non-existent parents; referential integrity depends solely on database
- Fix approach: Add validate methods to check foreign key existence before save; implement cascade delete rules

**Field Dependencies Not Computed:**
- Issue: Derived fields like "is_active", "completion_percentage", "next_review" are read-only but never calculated
- Files: `memora_player_subscription.json`, `memora_structure_progress.json`, `memora_memory_state.json`
- Impact: These fields will always be NULL or stale; critical gamification/learning logic breaks
- Fix approach: Implement on_update hooks or scheduled jobs to recalculate when dependencies change

## Security Considerations

**No Authentication Layer for API Endpoints:**
- Risk: Frappe doctypes expose REST endpoints by default with minimal auth checks
- Files: All doctype definitions leverage Frappe's REST API
- Current mitigation: Frappe's built-in permission checks (role-based)
- Recommendations:
  - Implement custom permission query conditions for sensitive doctypes (Wallet, Subscription Transaction, Player Profile)
  - Add rate limiting on batch endpoints (Build Queue, Sync Log)
  - Validate that players can only access their own records

**No Encryption for Sensitive Fields:**
- Risk: Fields like "access_key", "payment_method", "erpnext_invoice" stored as plain text
- Files: `memora_player_subscription.json`, `memora_subscription_transaction.json`
- Current mitigation: Database-level access control only
- Recommendations:
  - Implement field-level encryption for access_key (using Frappe's encrypt_password pattern)
  - Hash payment tokens before storage
  - Implement audit logging for financial records

**Missing Rate Limiting and Quota Enforcement:**
- Risk: Interaction logs and memory state updates have no throttling; DoS via spam requests
- Files: `memora_interaction_log.json`, `memora_memory_state.json`
- Current mitigation: None
- Recommendations:
  - Implement rate limiting in before_insert hooks (max X interactions per player per minute)
  - Add quota enforcement for memory state updates per learning session
  - Implement IP-based rate limiting at Frappe level

**No Audit Logging for Financial Transactions:**
- Risk: Subscription changes and payments lack revision tracking
- Files: `memora_subscription_transaction.json`, `memora_player_wallet.json`
- Current mitigation: Frappe's standard document versioning
- Recommendations:
  - Add explicit audit log doctype for financial state changes
  - Implement immutable transaction records (no edit after creation)
  - Add approval workflows for subscription status changes

## Performance Bottlenecks

**No Database Indexes for High-Volume Queries:**
- Problem: Querying interaction logs, memory states by player_id has no index
- Files: Doctype definitions for `memora_interaction_log`, `memora_memory_state`, `memora_structure_progress`
- Cause: Field definitions lack index=1 flag
- Improvement path:
  - Add indexes on frequently-queried fields: player_id, season_id, status, created/modified
  - Add compound indexes for common filters: (player_id, season_id), (player_id, created)
  - Update JSON to add "index": 1 in field definitions for `memora_interaction_log.py`, `memora_memory_state.json`, `memora_player_subscription.json`

**Bitset Operations Serialized as JSON:**
- Problem: "passed_lessons_bitset" is read_only integer but calculated synchronously; no batch optimization
- Files: `memora_structure_progress.json`
- Cause: Architecture delegates bitset calculation to Python, one record at a time
- Improvement path:
  - Precompute bitsets in background jobs (Build Queue processor)
  - Cache bitsets in Redis with dirty flags
  - Implement bulk update endpoint for batch progress updates

**FSRS Algorithm Not Implemented:**
- Problem: "stability", "difficulty", "next_review" fields defined but no calculation logic
- Files: `memora_memory_state.json`, `memora_settings.json` (fsrs_weights field)
- Cause: Algorithm stub missing; relies on external implementation
- Improvement path:
  - Implement FSRS algorithm using frappe_extension or standalone Python module
  - Cache FSRS state in Redis during active learning sessions
  - Batch update next_review in scheduled job (Settings.batch_interval_minutes)

**Interaction Log Append-Only Pattern Unsustainable:**
- Problem: Every lesson interaction creates a row; unbounded growth for active players
- Files: `memora_interaction_log.json`
- Cause: Analytics-heavy design without archival strategy
- Improvement path:
  - Implement time-based partitioning (yearly tables)
  - Add aggregation job to compute Analytics Aggregate and purge old logs (Settings.log_retention_days)
  - Implement batch archive to cold storage (S3) before deletion

## Fragile Areas

**Cross-DocType Relationships Without Integrity Checks:**
- Files: All doctype definitions with Link fields (e.g., `memora_memory_state.json` references season, subject, player, lesson)
- Why fragile: No ON DELETE CASCADE behavior defined; orphaned records possible if parent deleted
- Safe modification:
  - Add ignore_links_on_delete hook before deleting content
  - Implement cascade delete in Python (on_delete hook) or database constraints
  - Add referential integrity checks in validation
- Test coverage: Missing tests for cascade scenarios

**Gamification Calculations Spread Across Multiple DocTypes:**
- Files: `memora_player_profile.json` (total_xp, current_streak), `memora_player_wallet.json`, `memora_achievement.json`, `memora_subscription_transaction.json`
- Why fragile: XP calculation depends on Interaction Log + Achievement unlock rules + Season rewards; no single source of truth
- Safe modification:
  - Centralize XP calculation in a Gamification Service (new module)
  - Use hooks to trigger recalculation on relevant events
  - Add comprehensive test suite for edge cases (replay, max_hearts bonuses, season resets)
- Test coverage: No tests for gamification logic

**FSRS State Consistency Across Multi-Device Setup:**
- Files: `memora_memory_state.json`, `memora_player_device.json`, `memora_sync_log.json`
- Why fragile: Device A updates memory state; Device B syncs stale state; dirty flags unreliable
- Safe modification:
  - Implement optimistic locking (version field on Memory State)
  - Use Sync Log transactions to batch device syncs
  - Add conflict resolution strategy for simultaneous updates
- Test coverage: No tests for concurrent device scenarios

**Computed Field Dependencies Chain:**
- Files: `memora_player_profile.json` (completion_percentage depends on Subject + Structure Progress), `memora_settings.json` (configurable XP values)
- Why fragile: Changing settings doesn't invalidate cached computed fields
- Safe modification:
  - Add dirty_flag invalidation when Settings change (on_update hook)
  - Implement cache key versioning based on Settings.json_version
  - Add validation that all dependent fields recalculate
- Test coverage: No tests for settings propagation

## Scaling Limits

**Single Build Queue Processor Bottleneck:**
- Current capacity: Sequential processing; 1 content generation per build cycle
- Limit: If Build Queue grows beyond ~1000 pending items, content delivery lags
- Scaling path:
  - Implement worker pool (Celery or RQ) with configurable concurrency
  - Add priority queue (premium content first)
  - Batch content generation jobs

**In-Memory Redis Cache for Content JSON:**
- Current capacity: Depends on Redis max memory; typical configs 1-5GB
- Limit: Content grows beyond cache; fallback to DB queries become hot path
- Scaling path:
  - Implement tiered caching (hot=Redis, warm=CDN, cold=S3)
  - Add eviction policies and TTL management
  - Implement cache warming jobs for seasonal content

**No Horizontal Scaling for Interaction Logging:**
- Current capacity: Single table grows unbounded; row count = total interactions ever
- Limit: Query performance degrades after ~10M rows without partitioning
- Scaling path:
  - Implement time-based table partitioning (yearly or monthly)
  - Add log aggregation pipeline
  - Archive to columnar database (DuckDB/ClickHouse) for analytics

## Dependencies at Risk

**Frappe Framework Version Pinning:**
- Risk: `pyproject.toml` pins "frappe~=15.0.0" (compatible); breaking changes in 16.x not managed
- Impact: Cannot adopt newer Frappe features; security patches in 16+ not available
- Migration plan:
  - Plan upgrade path to Frappe 16/17
  - Test all doctypes and hooks against target version
  - Use Frappe's migration guides for breaking changes

**FSRS Algorithm Library Not Specified:**
- Risk: `CHANGES_SUMMARY.txt` lists FSRS integration but no package specified; implementation is stub
- Impact: Learning analytics core feature blocked; no package maintenance/security updates
- Migration plan:
  - Evaluate fsrs-py or equivalent Python package
  - If not suitable, implement custom FSRS (6+ weeks dev)
  - Document algorithm version and parameters

**CDN Provider Integration Undefined:**
- Risk: `CHANGES_SUMMARY.txt` mentions "AWS S3 / Cloudflare R2" but no implementation
- Impact: Content delivery stuck at prototype stage; no fallback strategy tested
- Migration plan:
  - Implement S3 adapter with signed URL generation
  - Add local filesystem fallback (offline-first mode)
  - Test failover scenarios before production

**ERPNext Integration Incomplete:**
- Risk: References to "erpnext_invoice", "related_grant", "Item integration" undefined
- Impact: Monetization features (Subscription Transaction → Invoice) non-functional
- Migration plan:
  - Define ERPNext API contracts (create invoice, get user, link document)
  - Implement webhook for invoice sync
  - Add error handling for ERPNext unavailability

## Missing Critical Features

**No Batch Sync Endpoint for Mobile Apps:**
- Problem: Mobile clients cannot efficiently fetch multiple resources (lessons, progress, memory state, achievements)
- Blocks: Offline-first architecture; mobile app development
- Implementation path:
  - Add GraphQL endpoint or batch REST endpoint
  - Return filtered resources per player and season
  - Implement delta sync (only changed records)

**No Build Queue Job Processor:**
- Problem: Build Queue doctype defined but no worker to process; content generation stalled
- Blocks: Content management workflow; new season launches impossible
- Implementation path:
  - Implement scheduled job (Frappe scheduler or Celery)
  - Process queue items: generate JSON, upload to CDN, update content_hash
  - Add retry logic and error logging

**No Device-Level Rate Limiting:**
- Problem: No max_devices enforcement; players can login on unlimited devices
- Blocks: Account security; session management unclear
- Implementation path:
  - Add on_insert validation in Memora Player Device
  - Enforce Settings.max_devices_per_player (default 5)
  - Implement device deactivation when limit exceeded

**No Player Wallet Recalculation Trigger:**
- Problem: Wallet (coins, premium_tokens) defined but no method to increment on achievement/purchase
- Blocks: Gamification reward system; monetization payment confirmation
- Implementation path:
  - Add after_submit hook to Subscription Transaction
  - Trigger wallet credit + achievement unlock
  - Implement transaction rollback if wallet update fails

**No Achievement Unlock Workflow:**
- Problem: Achievement doctype has unlock_condition_json but no evaluation logic
- Blocks: Gamification progression; leaderboards broken
- Implementation path:
  - Parse unlock_condition_json (e.g., "total_xp >= 1000")
  - Implement evaluation in after_update hooks for qualifying doctypes
  - Trigger achievement creation on unlock

## Test Coverage Gaps

**No API Integration Tests:**
- What's not tested: REST endpoints for all 28 doctypes
- Files: All doctype test files (`**/test_*.py`)
- Risk: Endpoint behavior undefined; permission checks may not work
- Priority: High
- Approach:
  - Add test class for each doctype with setup/teardown
  - Test CRUD operations with various roles
  - Test permission enforcement (System Manager vs Player vs Content Manager)

**No Spaced Repetition Algorithm Tests:**
- What's not tested: FSRS weight application, next_review calculation, memory stability updates
- Files: No test file (algorithm not implemented)
- Risk: Core learning feature untested; potential for feedback loops or softlocks
- Priority: High
- Approach:
  - Implement algorithm test with known test cases (fsrs-py provides examples)
  - Test stability/difficulty adjustments for pass/fail scenarios
  - Test next_review date generation

**No Multi-Device Sync Tests:**
- What's not tested: Device detection, sync conflict resolution, dirty flag propagation
- Files: No test file
- Risk: Data corruption in multi-device scenarios; sync logs unreliable
- Priority: High
- Approach:
  - Simulate multiple device logins
  - Update memory state on different devices concurrently
  - Verify sync_log conflict markers and resolution

**No Gamification Edge Case Tests:**
- What's not tested: XP bonuses, streak resets, achievement unlock conditions, replay penalties
- Files: No test file
- Risk: Players earn unexpected XP; achievements unlock incorrectly
- Priority: Medium
- Approach:
  - Test XP calculation with different max_hearts values
  - Test streak reset on missed days
  - Test achievement unlock thresholds

**No Content Generation Pipeline Tests:**
- What's not tested: Build Queue processing, JSON generation, hash calculation, CDN upload
- Files: No test file
- Risk: Corrupted content shipped; cache invalidation fails
- Priority: Medium
- Approach:
  - Mock CDN provider
  - Test queue item → JSON output
  - Test hash consistency across rebuilds

**No Permission Query Condition Tests:**
- What's not tested: Custom permission_query_conditions for sensitive doctypes
- Files: No test file (permission logic not implemented)
- Risk: Players can access other players' data; admins cannot filter properly
- Priority: High
- Approach:
  - Test that players see only own records in list views
  - Test that content managers can filter by season/subject
  - Test that system managers have unrestricted access

---

*Concerns audit: 2026-02-01*
