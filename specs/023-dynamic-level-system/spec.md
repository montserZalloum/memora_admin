# Feature Specification: Dynamic Level System

**Feature Branch**: `023-dynamic-level-system`
**Created**: 2026-02-22
**Status**: Draft
**Input**: User description: "Replace hardcoded level thresholds and titles with admin-configurable settings backed by a formula-based computation and Redis caching"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Admin Edits Level Titles (Priority: P1)

An administrator opens the Memora Level Settings page, changes "Beginner" to "Newcomer" for Level 1, and saves. Within seconds, a player calling the profile API receives "Newcomer" as their level title -- no code deploy required.

**Why this priority**: This is the core value proposition. Decoupling content from code gives admins full control over the progression experience without developer intervention.

**Independent Test**: Can be fully tested by saving a title change and verifying the profile API returns the updated title within 5 seconds.

**Acceptance Scenarios**:

1. **Given** an admin has the Level Settings page open, **When** they change `title_en` for Level 1 from "Beginner" to "Newcomer" and save, **Then** the profile API returns "Newcomer" as the title for a Level 1 player within 5 seconds.
2. **Given** an admin adds a new Level 16 title row, **When** a player earns enough XP to reach Level 16, **Then** the profile API returns the newly configured title.
3. **Given** an admin saves Level Settings with an Arabic title "مبتدئ" for Level 1, **Then** the Arabic title is persisted and available for future language selection features.

---

### User Story 2 - Admin Adjusts XP Curve (Priority: P1)

An administrator changes the quadratic coefficient from 50 to 75 on the Level Settings page and saves. All players' levels are immediately recalculated against the new curve without any data migration -- XP values stay the same, only the level boundaries change.

**Why this priority**: XP curve tuning is essential for balancing game progression. Admins must be able to experiment with difficulty curves without developer involvement.

**Independent Test**: Can be fully tested by changing the curve coefficient, saving, and verifying that a player's level changes in the profile API response.

**Acceptance Scenarios**:

1. **Given** the curve is set to a=50, b=50 (default), **When** a player has 500 XP, **Then** they are Level 3 (threshold at 300 XP).
2. **Given** an admin changes the curve to a=75, b=50, **When** the same player with 500 XP calls the profile API, **Then** their level recalculates based on the new curve.
3. **Given** the admin changes `max_level` from 15 to 20, **When** a player with very high XP calls the profile API, **Then** their level can now go up to 20 instead of capping at 15.

---

### User Story 3 - Resilience on Cache Loss (Priority: P2)

The cache is flushed or the config key expires. The next profile API call still returns correct level information using hardcoded fallback defaults, with no errors or downtime.

**Why this priority**: The system must never break due to cache loss. Fallback behavior ensures 100% uptime of the level system.

**Independent Test**: Can be fully tested by deleting the cached config key and verifying the profile API still returns valid level data matching the default configuration.

**Acceptance Scenarios**:

1. **Given** the cached level config does not exist, **When** a player calls the profile API, **Then** the system uses fallback defaults (a=50, b=50, max=15, 15 titles) and returns correct level data.
2. **Given** the cached config expires, **When** the next profile API call is made, **Then** the fallback defaults are used seamlessly with no error.
3. **Given** fallback defaults are in use, **When** an admin saves Level Settings, **Then** the cache is repopulated and subsequent API calls use the saved config.

---

### User Story 4 - Default Config Matches Current Behavior (Priority: P1)

On first installation or when using fallback defaults, the system produces level thresholds and titles identical to the current hardcoded values. No player experiences any change in their level or title.

**Why this priority**: Backward compatibility is critical. The migration must be invisible to all existing players and the mobile client.

**Independent Test**: Can be fully tested by comparing the output of the new level calculation function against the current hardcoded function for XP values 0 through 12000.

**Acceptance Scenarios**:

1. **Given** default config (a=50, b=50), **When** thresholds are computed for levels 1-11, **Then** they exactly match [0, 100, 300, 600, 1000, 1500, 2100, 2800, 3600, 4500, 5500].
2. **Given** default config, **When** level is calculated for 500 XP, **Then** it returns (level=3, title="Explorer", xp_in_level=200, xp_to_next=100) -- identical to current behavior.
3. **Given** default config, **When** level is calculated for 11000 XP, **Then** it returns (level=15, title="Transcendent", xp_in_level=0, xp_to_next=0) -- max level with zero XP to next.

---

### Edge Cases

- What happens when `total_xp` is negative? System treats it as 0 (Level 1, "Beginner").
- What happens when `max_level` is set to 1? Every player is Level 1 regardless of XP; `xp_to_next` is 0.
- What happens when no title is configured for a player's level? System returns fallback title "Level N" (e.g., "Level 16").
- What happens when the quadratic coefficient is set to a very large value (e.g., 1000)? Levels become very hard to reach, but the formula still works correctly.
- What happens when the child table has gaps (e.g., Level 1, Level 3, but no Level 2)? Level 2 players get the "Level 2" fallback title. Contiguous level numbers are not required.
- What happens when a player exceeds max level XP? They stay at max level with `xp_to_next` = 0.
- What happens when the admin saves with duplicate level numbers? Validation rejects the save with an error message.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide an admin interface to configure level curve parameters (quadratic coefficient, linear coefficient, max level).
- **FR-002**: System MUST provide an admin interface to configure level titles with English names, optional Arabic names, and optional icons.
- **FR-003**: System MUST compute XP thresholds using the quadratic formula `threshold(level) = a * (level-1)^2 + b * (level-1)` instead of a static list.
- **FR-004**: System MUST compute level from XP in constant time O(1) using the inverse quadratic formula, not by iterating over thresholds.
- **FR-005**: System MUST cache the level configuration and serve it to the game API with sub-millisecond lookup.
- **FR-006**: System MUST push updated configuration to the cache immediately when an admin saves changes, using the same two-pronged invalidation pattern (direct write + pubsub notification) used by existing config sync mechanisms.
- **FR-007**: System MUST use hardcoded fallback defaults (a=50, b=50, max=15, 15 titles) when the cache is empty, ensuring zero downtime on cache loss.
- **FR-008**: System MUST validate inputs on save: quadratic coefficient >= 1, linear coefficient >= 0, max level >= 1, no duplicate level numbers, non-empty English titles.
- **FR-009**: System MUST return the same profile API response schema after the change -- no fields added, removed, or renamed. The mobile client must not break.
- **FR-010**: System MUST remove all hardcoded level threshold lists, title lists, and the old level calculation function from the game API codebase after migration.
- **FR-011**: System MUST pre-populate the initial configuration with 15 level titles matching the current hardcoded values ("Beginner" through "Transcendent").

### Key Entities

- **Level Settings**: Singleton configuration holding curve parameters (quadratic coefficient, linear coefficient, max level) and a collection of level title entries.
- **Level Title**: An entry in the level titles collection, containing a level number, English title, optional Arabic title, and optional icon.
- **Level Config (cached)**: A read-only snapshot of Level Settings stored in the cache, containing the curve parameters and a title lookup map keyed by level number.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Admins can change a level title and see the updated title reflected in the player-facing API within 5 seconds, without any code deployment.
- **SC-002**: Admins can adjust curve parameters and player levels recalculate immediately on the next API call.
- **SC-003**: Default configuration produces XP thresholds identical to the current hardcoded values for levels 1-11: [0, 100, 300, 600, 1000, 1500, 2100, 2800, 3600, 4500, 5500].
- **SC-004**: Level calculation completes in constant time O(1), independent of the number of levels configured.
- **SC-005**: Profile API response format is unchanged -- existing mobile clients continue working without modification.
- **SC-006**: All existing level calculation tests pass with identical assertions after migration.
- **SC-007**: No references to the old hardcoded level lists or function remain in the game API codebase.
- **SC-008**: Cache loss (flush, expiry, restart) does not cause errors -- the system gracefully falls back to defaults.

## Assumptions

- The quadratic formula `a*(level-1)^2 + b*(level-1)` with defaults a=50, b=50 reproduces the current thresholds for levels 1-11 exactly. Levels 12-15 in the current hardcoded list differ slightly from the formula (e.g., Level 12: current=6700, formula=6600) but the formula values will be the new canonical thresholds once this feature ships.
- Arabic title support (`title_ar`) is stored but not yet served to clients. Language selection logic is a future feature.
- The icon field on level titles is stored but not currently used by the mobile client.
- Only System Managers have access to the Level Settings admin page.
- The existing profile API response fields (`level`, `level_title`, `current_xp`, `xp_in_level`, `xp_for_next_level`, `xp_level_start`, `xp_level_end`) will continue to be computed the same way, with `xp_level_start` and `xp_level_end` derived from the formula rather than from a list lookup.
