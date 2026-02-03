# Research Summary: v1.3 Leaderboard Profiles & Admin Device Management

**Project:** Memora Admin
**Domain:** Gamified educational platform extensions (leaderboard profile enrichment + admin device UI)
**Researched:** 2026-02-03
**Confidence:** HIGH

## Executive Summary

v1.3 extends the existing Memora Admin dual architecture (FastAPI sidecar + Frappe v15) with two focused enhancements: profile display names in leaderboards and admin device management UI. Research confirms that **no new dependencies are required** — all features are implementable using the validated stack from v1.0-v1.2.

The leaderboard currently returns `player_id` as a placeholder for `display_name`. Users expect human-readable names and avatars, not email addresses. The solution follows established patterns: a new ProfileService with Redis hash caching (matching existing DeviceService, WalletService patterns), batch profile fetch via pipeline (single network round-trip for 10-100 entries), and lazy-load cache population on leaderboard requests. Profile data already exists in the Memora Player Profile DocType (display_name, avatar fields). The critical implementation requirement is **batch lookups from day 1** to avoid N+1 query performance death.

Admin device management already has foundational infrastructure: DeviceService with get_devices()/remove_device() methods, Memora Player Device child table in Frappe, and device_sync.py hook for removal sync. The gap is **admin visibility**: devices registered in Redis aren't synced to the Frappe child table, leaving admins with an empty device list. The solution requires Redis-to-MariaDB sync (on form load or scheduled task) and enhanced Frappe form scripts with device removal confirmation dialogs. Security consideration: current stateless JWT design means removed devices continue working until token expiry — a blocklist pattern is recommended for immediate session invalidation.

## Key Findings

### Recommended Stack

**Zero new dependencies required.** v1.3 uses the validated stack from v1.0-v1.2.

All capabilities are provided by existing dependencies:
- **redis-py 5.0+** (existing): Pipeline for batch profile fetch, hash operations for profile cache
- **httpx via FrappeClient** (existing): Batch profile lookup from Frappe for cache misses
- **Frappe v15 JavaScript** (existing): Dialog API for device removal confirmation, form scripts for admin UI
- **Existing doc_events hooks** (existing): Profile cache invalidation, device removal sync pattern established in v1.0-v1.2

The ProfileService follows the same pattern as HierarchyService, DeviceService, and WalletService — Redis hash with TTL, pub/sub invalidation, and lazy-load fallback to Frappe.

**Critical version note:** redis-py 5.0+ is already in use and supports async pipelines natively (aioredis is abandoned). The batch profile lookup pattern is officially documented and proven in production Redis deployments.

### Expected Features

**Profile Display Names in Leaderboards:**

**Must have (table stakes):**
- Display human-readable names instead of player_id placeholders (LOW complexity, HIGH value)
- Consistent naming across all leaderboard types (daily/weekly/alltime) via single ProfileService
- Graceful fallback for missing profiles (new users or cache gaps) to "Player XXXX" or truncated player_id
- Avatar indicator support (DocType has "avatar 1", "avatar 2" Select field)
- Self-identification marker (is_me flag) — already built in existing endpoint

**Should have (competitive):**
- Profile cache in Redis for sub-2ms lookup vs ~50ms Frappe query (MEDIUM complexity, essential at scale)
- Batch profile lookup via pipeline for single round-trip (LOW complexity, prevents N+1 queries)
- Cache warm-up on login so active users always have cached profiles (LOW complexity)

**Defer (v2+):**
- Real-time profile updates (cache invalidation complexity; 1-hour staleness acceptable)
- User-settable display names (requires moderation, profanity filter; admin-set sufficient for v1.3)
- Anonymous leaderboard option (privacy feature adds complexity; use display_name anonymization if needed later)

**Admin Device Management:**

**Must have (table stakes):**
- View all devices for a player before removal (DeviceService.get_devices() exists)
- Remove specific device via child table deletion (DeviceService.remove_device() exists)
- Device identification info visible (device_name, platform, last_login) — all fields exist in Memora Player Device DocType
- Confirmation dialog before removal to prevent accidents (standard Frappe Dialog API)
- Sync devices from Redis to Frappe so admin view reflects current state (MEDIUM complexity, critical for usability)

**Should have (competitive):**
- Session invalidation on removal for immediate access revocation (MEDIUM complexity, security enhancement)
- Device count badge in form header for quick visibility (LOW complexity)
- Last login sorting for removal decisions (LOW complexity)

**Defer (v2+):**
- User-facing device management (security review needed, support burden for user accidents)
- Push notification on device removal (push infrastructure not built yet)

### Architecture Approach

v1.3 follows the **established dual architecture** from v1.0-v1.2: FastAPI sidecar handles high-performance game API (<20ms responses), Frappe handles admin UI and content management, Redis is the hot data cache, MariaDB is the cold data store.

**Major components:**

1. **ProfileService (NEW)** — Redis hash cache for player profiles (display_name, avatar) with TTL (1 hour) and pub/sub invalidation. Batch fetch method using pipeline (single network round-trip for N player_ids). Lazy-load fallback to Frappe API for cache misses. Follows HierarchyService pattern exactly.

2. **profile_sync.py hook (NEW)** — Frappe doc_events hook on Memora Player Profile update. When display_name or avatar changes, publish invalidation message to `memora:invalidation` channel (existing pub/sub infrastructure). Reuses established invalidation pattern from v1.0.

3. **Enhanced Device Display (MODIFY)** — Update memora_player_profile.js with device grid actions: "Remove Device" button using frappe.ui.Dialog, confirmation before deletion, sync from Redis on form load. Leverages existing Frappe form script API and custom button patterns already in codebase.

4. **Device Sync Task (NEW or MODIFY)** — Either scheduled sync (add to existing sync.py 1-minute cycle) or on-demand sync (API call from form load script). Fetches Redis devices via DeviceService, updates authorized_devices child table. Matches existing dirty-flag pattern from progress/wallet sync.

**Integration pattern:** Endpoint-level orchestration (not service-level coupling). Leaderboard endpoint injects ProfileServiceDep, fetches raw entries from LeaderboardService, then batch-enriches with profiles. ProfileService doesn't know about leaderboards, LeaderboardService doesn't know about profiles. Loose coupling maintains separation of concerns.

**Redis key design:**
- `memora:profile:{user_id}` — Hash with fields: display_name, avatar, cached_at
- No `memora:player_names` global hash — per-user hashes scale better and enable per-user TTL

**Performance targets maintained:**
- Access check: <2ms (unchanged)
- Progress fetch: <20ms (unchanged)
- **Leaderboard fetch: <25ms** (was <20ms raw; +5ms acceptable for profile enrichment with batch pipeline)

### Critical Pitfalls

1. **N+1 Query Problem in Profile Enrichment** — Fetching display names individually per leaderboard entry kills performance. With top 100 leaderboard and 2ms per Redis call = 200ms overhead, violating <25ms target. **Avoid:** Use redis-py pipeline for batch fetch in ProfileService from day 1. Test with limit=100, not limit=10. Single network round-trip regardless of batch size.

2. **Profile Cache Staleness Without Invalidation** — Player updates display_name in Frappe, leaderboard shows old name until 1-hour TTL expires. User frustration: "I changed my name but it still shows old." **Avoid:** Add profile_sync.py hook that publishes to `memora:invalidation` channel when display_name or avatar changes. Wire pub/sub handler in pubsub.py to call ProfileService.invalidate(player_id). Test: change name in Frappe, verify leaderboard updates within seconds.

3. **Redis-MariaDB Device State Divergence** — Admin removes device from Frappe Desk, but Redis device registry isn't updated. Or vice versa: device registered in Redis but not synced to child table. **Avoid:** Defensive reconciliation in admin view (fetch from both, remove orphaned Redis devices) or scheduled task backup. Test: remove device via SQL (bypassing hook), verify admin view triggers cleanup.

4. **Session Invalidation Gap After Device Removal** — Admin removes device, device_sync.py deletes from Redis and invalidates session, but user's active JWT continues working for 24 hours (stateless design). Security gap. **Avoid:** Add token blocklist pattern for admin-revoked tokens. When device removed, add family_id to blocklist (24-hour TTL), check blocklist in get_current_user dependency. Alternative: reduce access token lifetime to 15 minutes (refresh still 7 days).

5. **Frappe Child Table Permission Issues** — Admin user gets permission error accessing authorized_devices child table, or worse, unauthorized API access to child table. Current Memora Player Device schema has empty permissions array. **Avoid:** Add explicit permissions to child table JSON (System Manager: read + write) or ensure all access goes through parent document with permission checks. Test as non-admin to verify direct child table access is blocked.

## Implications for Roadmap

Based on research, v1.3 should be structured as **two independent phases** (profile display names and admin device management can be developed in parallel, no dependencies between them).

### Phase 14: Profile Display Names

**Rationale:** Profile enrichment is user-facing, high-impact (every leaderboard request), and must have batch lookup architecture correct from day 1. Cannot ship with N+1 queries and fix later — performance degrades as user base grows. ProfileService establishes the caching pattern that may be reused for other player metadata in future milestones.

**Delivers:**
- Human-readable display names and avatars in all leaderboard responses
- Sub-5ms profile enrichment overhead for top 100 leaderboard
- Cache invalidation on profile updates for fresh data

**Addresses features:**
- Display name instead of player_id (FEATURES.md table stakes)
- Batch profile lookup (FEATURES.md differentiator)
- Profile cache in Redis (FEATURES.md differentiator)

**Avoids pitfalls:**
- N+1 Query Problem (PITFALLS.md #1) via pipeline batch fetch
- Profile Cache Staleness (PITFALLS.md #2) via pub/sub invalidation hook

**Implementation plan:**
- Plan 14-01: ProfileService + Cache Infrastructure (service class, Pydantic models, Redis key design, batch fetch with pipeline, cache TTL)
- Plan 14-02: Frappe Integration (profile_sync.py hook, pub/sub handler registration, Frappe API endpoint for batch lookup)
- Plan 14-03: Leaderboard Enrichment (inject ProfileServiceDep into endpoint, modify LeaderboardEntry construction, test with limit=100)

### Phase 15: Admin Device Management

**Rationale:** Admin device visibility completes the device registration feature from v1.0. Currently, admins have no way to see which devices are registered (authorized_devices child table is empty because Redis isn't synced). Device removal hook exists but is invisible to admin workflow. This phase bridges the Redis (source of truth) and Frappe (admin view) gap.

**Delivers:**
- Admin can view all registered devices in player profile
- Admin can remove devices with confirmation dialog
- Device list synced from Redis to Frappe child table
- (Optional) Immediate session invalidation on removal

**Addresses features:**
- View all devices for a player (FEATURES.md table stakes)
- Remove specific device (FEATURES.md table stakes)
- Device sync from Redis to Frappe (FEATURES.md table stakes)
- Session invalidation on removal (FEATURES.md should-have)

**Avoids pitfalls:**
- Redis-MariaDB Device State Divergence (PITFALLS.md #3) via reconciliation in admin view
- Session Invalidation Gap (PITFALLS.md #4) via token blocklist (if implemented)
- Frappe Child Table Permission Issues (PITFALLS.md #5) via explicit permissions or parent-only access

**Implementation plan:**
- Plan 15-01: Device Sync Task (scheduled sync from Redis to MariaDB authorized_devices, or on-demand API endpoint, test reconciliation)
- Plan 15-02: Enhanced Frappe UI (memora_player_profile.js with device removal dialog, refresh button, device count badge)
- Plan 15-03: Session Invalidation (optional, add blocklist check in get_current_user for admin-revoked tokens)

### Phase Ordering Rationale

- **Phases 14 and 15 are independent** — no code dependencies, can run in parallel. ProfileService doesn't interact with DeviceService or Frappe admin UI.
- **Phase 14 first if sequential** — leaderboard profile enrichment is user-facing and higher priority than admin tooling. Establishes Redis caching pattern that admin features can reference.
- **Phase 15 second if sequential** — admin device management is internal tooling, lower urgency, but builds on existing device_sync.py patterns.
- **Both phases low risk** — no new dependencies, small surface area, established patterns from v1.0-v1.2.

### Research Flags

**Phases with standard patterns (skip research-phase):**
- **Phase 14 (Profile Display Names):** Well-documented Redis pipeline pattern, existing HierarchyService caching pattern to follow, Frappe hooks established in v1.0. No unknowns.
- **Phase 15 (Admin Device Management):** Existing DeviceService methods, Frappe form script API documented, child table permissions straightforward. No unknowns.

**No phases need /gsd:research-phase** — all patterns are either established in codebase (v1.0-v1.2 precedents) or officially documented (Redis, Frappe). This milestone is **incremental enhancement** using validated stack, not exploration of new domain.

**Validation during planning:**
- **Performance testing setup** — Plan 14 needs load testing with limit=100 to validate <25ms target
- **Permission testing** — Plan 15 needs non-admin user test to verify child table access control
- **Cache invalidation flow** — Plan 14 needs end-to-end test of profile update → pub/sub → cache clear

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | No new dependencies. redis-py 5.0+ pipeline pattern officially documented. Frappe v15 Dialog API in active use in codebase. All infrastructure exists. |
| Features | HIGH | Existing codebase has placeholders (display_name in LeaderboardEntry) and infrastructure (Memora Player Profile DocType, authorized_devices child table). Features are straightforward enhancements to existing systems. |
| Architecture | HIGH | ProfileService follows established HierarchyService pattern exactly (Redis hash, TTL, pub/sub invalidation, lazy load). Device sync follows existing sync.py dirty-flag pattern. No novel architecture required. |
| Pitfalls | HIGH | N+1 query problem is well-documented in Redis best practices. Cache invalidation patterns proven in v1.0 HierarchyService. Device sync edge cases identified from existing device_sync.py implementation. Session invalidation gap documented in OWASP session management guidance. |

**Overall confidence:** HIGH

### Gaps to Address

**Performance validation gap:** While batch pipeline pattern is documented, actual latency with 100 profile lookups in Memora's specific Redis setup needs validation. Plan 14 should include load testing with realistic data (1K players, top 100 leaderboard) before declaring success.

**Session invalidation decision:** Research identified the JWT blocklist pattern as a solution for immediate device revocation, but implementation complexity and performance impact need evaluation during Plan 15-03. Alternative approach (reduce access token TTL to 15 minutes) is simpler but affects all users, not just removed devices. Recommend: prototype blocklist in Plan 15-03, benchmark performance, decide based on overhead (<1ms acceptable).

**Profile cache TTL tuning:** Research recommends 1-hour TTL (matching HierarchyService), but actual staleness tolerance depends on how often users update display names. If profile updates are daily (common in gaming platforms), 1-hour is fine. If weekly, could extend to 24 hours for less cache churn. Recommend: start with 1 hour, monitor cache hit rate and invalidation frequency, tune if needed.

**Child table permission model:** Frappe child table permissions are confusing (empty permissions array may mean "inherit from parent" or "no direct access"). Research recommends explicit permissions for clarity, but existing codebase may rely on parent-only access. Recommend: test both approaches in Plan 15 (explicit permissions + parent-only access), choose based on which works correctly with current Frappe setup.

## Sources

### Primary (HIGH confidence)
- **Existing codebase analysis** — Reviewed fastapi_app/services/leaderboard.py, device.py, hierarchy.py; memora_admin/events/device_sync.py; memora_player_profile.json, memora_player_device.json; leaderboard.py endpoint with display_name placeholder. All infrastructure exists, patterns established.
- **redis-py 5.0+ documentation** — [Pipelines and Transactions](https://redis.io/docs/latest/develop/clients/redis-py/transpipe/), [Async Examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html), [Pipelining Guide](https://redis.io/docs/latest/develop/using-commands/pipelining/). Batch fetch pattern officially documented.
- **Frappe v15 API documentation** — [Form Scripts API](https://docs.frappe.io/framework/v15/user/en/api/form), [Dialog API](https://docs.frappe.io/framework/v15/user/en/api/dialog), [Adding Custom Button](https://docs.frappe.io/framework/user/en/guides/app-development/adding-custom-button-to-form). All UI components exist.
- **Frappe child table patterns** — [Child DocType Documentation](https://docs.frappe.io/framework/user/en/basics/doctypes/child-doctype), [Permission Issue #34925](https://github.com/frappe/erpnext/issues/34925), [No Permissions on Child Tables #16008](https://github.com/frappe/erpnext/issues/16008). Permissions model clarified.

### Secondary (MEDIUM confidence)
- **Redis leaderboard patterns** — [Redis Leaderboards Official](https://redis.io/solutions/leaderboards/), [System Design - Leaderboard](https://systemdesign.one/leaderboard-system-design/), [Python Redis Bulk Get](https://www.dragonflydb.io/code-examples/python-redis-bulk-get). Profile metadata caching patterns.
- **Cache invalidation best practices** — [Database Caching Strategies (AWS)](https://docs.aws.amazon.com/whitepapers/latest/database-caching-strategies-using-redis/caching-patterns.html), [Top 10 Caching Mistakes](https://moldstud.com/articles/p-top-10-common-caching-mistakes-to-avoid-for-enhanced-performance), [Caching Deep Dive](https://gajabagi.medium.com/caching-part-1-a-deep-dive-into-sync-race-conditions-and-the-timeline-fallacy-41cb10bbffe8). Event-driven invalidation recommended.
- **Session management security** — [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [Broken Session Management Vulnerability](https://knowledge-base.secureflag.com/vulnerabilities/broken_authentication/broken_session_management_vulnerability.html), [WorkOS Sessions API - Revocation](https://workos.com/blog/workos-sessions-api-session-revocation-sign-out-everywhere). Blocklist pattern for server-side revocation.
- **Device management UX** — [Netflix Device Management](https://help.netflix.com/en/node/128180), [Heroic Labs - Usernames and Leaderboards](https://forum.heroiclabs.com/t/usernames-and-leaderboards/535/2). Multi-device limit patterns in production platforms.

### Tertiary (LOW confidence)
- **GDPR privacy considerations** — [Privacy by Design & Default (GDPR) 2025](https://secureprivacy.ai/blog/privacy-by-design-gdpr-2025), [GDPR Compliance with Public User Profiles](https://meta.discourse.org/t/gdpr-compliance-when-using-public-user-profiles/95059). Display name privacy flags for minors (defer to future milestone, but noted for awareness).

---
*Research completed: 2026-02-03*
*Ready for roadmap: yes*
