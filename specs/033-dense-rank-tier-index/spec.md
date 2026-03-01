# Feature Specification: Exact Dense Rank at Scale (Tier Index)

**Feature Branch**: `033-dense-rank-tier-index`
**Created**: 2026-03-01
**Status**: Draft
**Input**: User description: "Replace read-time tier-walking Lua with indexed tier lookups using a maintained tier ZSET + tier counts HASH per leaderboard, achieving O(log T) dense rank reads with constant-work writes and no API behavior changes."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Dense Rank Read Performance at Scale (Priority: P1)

A player at the bottom of a 100k-player daily leaderboard requests their rank. The system computes their exact dense rank in O(log T) time (where T is the number of distinct XP tiers) instead of iterating through all tiers above them. The response time remains under 20ms regardless of the player's position on the board.

**Why this priority**: This is the core problem being solved. Bottom-ranked players currently trigger thousands of Redis operations via tier-walking Lua, blocking the Redis event loop and causing latency spikes for all users.

**Independent Test**: Can be tested by populating a leaderboard with 100k members across many distinct XP tiers, querying the rank of the lowest-ranked player, and verifying the response is correct and fast.

**Acceptance Scenarios**:

1. **Given** a daily leaderboard with 100k players and 5000 distinct XP tiers, **When** the bottom-ranked player requests their rank, **Then** the dense rank is returned correctly in under 20ms.
2. **Given** multiple players tied at XP=150 and others at XP=200, **When** any player at XP=150 requests their rank, **Then** they all receive the same dense rank (ties share rank).
3. **Given** a player at the top tier (highest XP), **When** they request their rank, **Then** rank=1 and xp_to_next=null.
4. **Given** a player not present on a leaderboard, **When** they request their rank, **Then** the response matches current behavior exactly (rank=None for plan-scoped, rank=total+1 for global).

---

### User Story 2 - Atomic Tier Maintenance on XP Award (Priority: P1)

When a player earns XP (completing a session), the system atomically updates both the leaderboard score and the tier metadata in a single operation per leaderboard variant. If the player moves to a new XP tier, the old tier count is decremented (and removed if empty) and the new tier count is incremented. No stale tiers accumulate.

**Why this priority**: Without correct tier maintenance, ranks become materially wrong. Stale empty tiers inflate rank numbers for every player below them. This is equally critical as the read path.

**Independent Test**: Can be tested by awarding XP to players, checking that tier counts match actual player distributions, and verifying that abandoned tiers (0 players) are immediately removed.

**Acceptance Scenarios**:

1. **Given** a player at XP=100 (tier "100" has 3 players), **When** they earn 50 XP, **Then** tier "100" count drops to 2, tier "150" count increments to N+1, and the leaderboard score is 150.
2. **Given** a player is the last member of tier "200", **When** they earn 10 XP, **Then** tier "200" is completely removed from both the tier index and counts, and tier "210" is created/incremented.
3. **Given** a brand-new player (not on the board), **When** they earn their first 5 XP, **Then** the player appears in the leaderboard, tier "5" is created in the tier index, and tier "5" count is incremented.
4. **Given** the system awards XP across up to 8 leaderboard variants simultaneously, **When** a single session completes, **Then** all 8 variants have their tier metadata correctly maintained.

---

### User Story 3 - Race-Free Backfill of Existing Leaderboards (Priority: P2)

An operator runs a backfill command to populate tier metadata for all existing leaderboard keys. The backfill acquires a per-leaderboard lock to prevent races with live XP writes, builds the metadata from the current state, atomically installs it, and releases the lock. Live traffic experiences at most brief delays (not errors) during backfill.

**Why this priority**: Without backfill, the new read path has no metadata to work with. However, a read-path fallback (legacy approach) makes this safe to run post-deploy rather than blocking launch.

**Independent Test**: Can be tested by populating leaderboards, running backfill while a concurrent writer awards XP, and verifying that the final metadata exactly matches the leaderboard state with no lost updates.

**Acceptance Scenarios**:

1. **Given** 50 active leaderboard keys, **When** the backfill command runs, **Then** each gets correct tier index and tier counts matching its current member distribution.
2. **Given** a live XP write arrives for a leaderboard currently being backfilled, **When** the write encounters the lock, **Then** it retries briefly and succeeds (no error returned to the player).
3. **Given** backfill completes for a leaderboard, **When** subsequent rank reads occur, **Then** they use the indexed path (no fallback to legacy approach).

---

### User Story 4 - Graceful Fallback During Rollout (Priority: P2)

During the transition period, if tier metadata is missing for a leaderboard (not yet backfilled), rank reads fall back to the existing approach. This fallback is logged for monitoring. Once metadata exists and is verified consistent, the fallback is removed.

**Why this priority**: Enables zero-downtime deployment. Code can be deployed before backfill runs without breaking any user-facing behavior.

**Independent Test**: Can be tested by querying rank on a leaderboard with no metadata keys, verifying the legacy path produces correct results, and checking that a fallback metric is emitted.

**Acceptance Scenarios**:

1. **Given** a leaderboard with no tier metadata keys, **When** a player requests their rank, **Then** the system uses the legacy approach and returns the correct rank.
2. **Given** a leaderboard with tier metadata keys, **When** a player requests their rank, **Then** the system uses the indexed path.
3. **Given** fallback is triggered, **When** observability is checked, **Then** a fallback usage counter is incremented.

---

### User Story 5 - Metadata Cleanup Prevents Orphan Accumulation (Priority: P3)

The existing cleanup job that deletes expired leaderboard keys is extended to also delete the corresponding tier metadata keys under the separate prefix. Metadata keys follow the same date-based retention policy as their parent leaderboard keys.

**Why this priority**: Without cleanup, orphaned metadata keys accumulate indefinitely, consuming memory. Important but not urgent since TTLs provide a safety net.

**Independent Test**: Can be tested by creating leaderboard + metadata keys with old dates, running cleanup, and verifying all three key types (leaderboard, tier index, tier counts) are deleted together.

**Acceptance Scenarios**:

1. **Given** a daily leaderboard key older than 30 days with corresponding metadata keys, **When** cleanup runs, **Then** all three keys (leaderboard, tier index, tier counts) are deleted.
2. **Given** a weekly leaderboard key within retention period, **When** cleanup runs, **Then** the leaderboard and its metadata keys are preserved.
3. **Given** orphaned metadata keys (leaderboard key already expired via TTL), **When** cleanup runs, **Then** the orphaned metadata keys are also deleted.

---

### Edge Cases

- **Zero XP award**: System skips leaderboard updates entirely (existing guard for xp_amount <= 0); no tier metadata changes occur.
- **Negative XP (if ever introduced)**: Tier maintenance must handle score decreasing; old_tier > new_tier is valid.
- **Concurrent XP awards to same player**: Two simultaneous awards for the same player on the same key — atomicity ensures each sees correct old score.
- **Player removed from leaderboard (plan change)**: When a player is removed, no tier metadata update occurs. The tier count becomes stale by 1 per removal. This is acceptable because plan changes are rare and the count error is bounded.
- **Data store restart after backfill**: Metadata keys may be lost. Read path falls back to legacy approach. Next XP write recreates metadata for that player's tier only. A re-backfill restores full metadata.
- **Archive jobs scanning leaderboard keys**: Must NOT encounter metadata keys. Metadata uses a separate prefix so union/merge operations in archive jobs never touch non-compatible key types.
- **Midnight boundary during backfill**: Backfill processes keys that exist at scan time. Keys created after scan start (new day's leaderboard) will have metadata maintained by the new write path.
- **Integer overflow of tier counts**: With 100k players max per leaderboard, counts stay well within integer limits.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST maintain a tier index for each active leaderboard, tracking only XP tiers with at least one player (member=tier string, score=tier integer).
- **FR-002**: System MUST maintain a tier counts structure for each active leaderboard, tracking the exact number of players at each XP tier.
- **FR-003**: System MUST atomically update leaderboard score and tier metadata in a single operation per leaderboard variant when awarding XP.
- **FR-004**: System MUST remove a tier from both the tier index and tier counts when the last player leaves that tier.
- **FR-005**: System MUST compute dense rank by counting distinct tiers above the player's XP (using xp+1 as lower bound, since XP is integer) plus 1.
- **FR-006**: System MUST compute xp_to_next by finding the lowest tier above the player's XP and return null when the player is at the highest tier (preserving current nullability behavior).
- **FR-007**: System MUST use a separate key prefix (`memora:lbmeta:*`) for tier metadata, distinct from the leaderboard prefix (`memora:lb:*`), to prevent archive/cleanup jobs from encountering incompatible key types.
- **FR-008**: System MUST apply the same TTL to tier metadata keys as their corresponding leaderboard keys, using the same expiration logic (not inside the atomic write operation).
- **FR-009**: System MUST fall back to the legacy tier-walking approach when tier metadata keys are missing for a leaderboard (rollout safety).
- **FR-010**: System MUST provide a backfill command that populates tier metadata for all existing leaderboard keys using a per-key lock to prevent races with live writes.
- **FR-011**: System MUST extend the cleanup job to delete tier metadata keys using the same date-based retention policy as their corresponding leaderboard keys.
- **FR-012**: System MUST NOT modify the daily XP summary updates — they remain outside the atomic write operation and independent of tier logic.
- **FR-013**: System MUST preserve all existing response shapes, nullability semantics, and edge-case behaviors (unranked players, ties, top-tier xp_to_next=null, neighbor windows).
- **FR-014**: System MUST NOT introduce loops or unbounded iteration in the write-path operation.
- **FR-015**: System MUST log tier cardinality, write operation latency, backfill progress, and fallback usage counts for operational monitoring.
- **FR-016**: System MUST mirror the same scope identifiers (date, subject, plan, plan+subject) from leaderboard keys into the corresponding metadata key names via centralized key builder functions.

### Key Entities

- **Leaderboard (existing)**: A ranked collection keyed by scope (daily/weekly, global/subject/plan/plan+subject), with player_id as member and integer XP as score.
- **Tier Index**: A sorted collection per leaderboard tracking active XP tiers. Member is the tier value as string (e.g., "193"), score is the same tier as integer (193). Only tiers with at least one player exist.
- **Tier Counts**: A key-value mapping per leaderboard tracking player count per tier. Key is the tier value as string, value is the integer count of players currently at that tier.
- **Backfill Lock**: A short-lived lock acquired per leaderboard during backfill to prevent concurrent write races. Released immediately after metadata installation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Dense rank read for the lowest-ranked player on a 100k-player leaderboard completes in under 20ms (down from potentially hundreds of ms with tier-walking).
- **SC-002**: Dense rank read latency is consistent regardless of player position on the board (no worst-case cliff for bottom players).
- **SC-003**: XP award write path completes all leaderboard updates (including tier maintenance) within the existing performance budget, with no user-perceptible latency increase.
- **SC-004**: After backfill, the sum of all tier counts for a leaderboard equals the total member count of that leaderboard (integrity invariant holds for 100% of boards).
- **SC-005**: Zero stale tiers accumulate during normal operation — every tier in the index has at least one player at that XP value.
- **SC-006**: All existing leaderboard features (top list, dense rank, neighbor window, xp_to_next, tie handling, unranked behavior) produce identical results before and after the change.
- **SC-007**: Cleanup job prevents orphaned metadata keys from accumulating — after cleanup runs, no metadata keys exist without a corresponding active or recently-active leaderboard key.
- **SC-008**: Backfill of all active leaderboards completes without data loss — no XP awards are dropped or miscounted during the migration.

## Assumptions

- XP values are always integers. The tier normalization uses floor() as a safety measure, but fractional XP is not expected.
- A single XP award updates at most 8 leaderboard variants (global daily/weekly, subject daily/weekly, plan daily/weekly, plan+subject daily/weekly). This is the current maximum.
- The write amplification from ~16 ops to ~56 ops per XP award (8 atomic operations x ~7 internal ops each) is within system capacity for 100k concurrent users.
- Archive jobs use merge/union operations on `memora:lb:*` scans. The prefix separation (`memora:lbmeta:*`) guarantees these jobs never encounter incompatible key types.
- Player removal (during plan changes) does not update tier metadata. The resulting count error (bounded at 1 per removal) is acceptable given the rarity of plan changes and periodic integrity checks.
- The per-key lock approach is preferred over maintenance windows for backfill, as it allows zero-downtime migration.
- The daily XP summary is independent of leaderboard tier logic and must not be coupled to it.

## Constraints

- No new database schema changes or admin-panel entities.
- No changes to API response shapes, field nullability, or endpoint contracts.
- No approximation — dense ranks must be exact.
- Metadata key prefix must be separate from leaderboard keys to protect archive/union operations.
- TTL must not be set inside atomic operations — only via the existing expiration logic in the application layer.
- The deployment must support a phased rollout: fallback first, then backfill, then new write path, then fallback removal.
