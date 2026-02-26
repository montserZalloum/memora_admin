# Feature Specification: Player Plan Change (Season Transition)

**Feature Branch**: `028-player-plan-change`
**Created**: 2026-02-26
**Status**: Draft
**Input**: User description: "Player Plan Change — enabling players to self-serve a plan change when their season expires or voluntarily, transitioning to a new active plan with a clean slate."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Expired Season Plan Change (Priority: P1)

A player whose season has ended is completely blocked from accessing any content. They need to independently transition to a new active plan so they can resume learning. After selecting a new plan, all their data is reset (clean slate) and they start fresh as if they were a brand-new user.

**Why this priority**: This is the primary use case. Without it, players are permanently frozen when a season ends — total loss of engagement and retention.

**Independent Test**: Can be fully tested by creating a player on an expired season, triggering the plan change flow, and verifying the player can access content on the new plan with all counters at zero.

**Acceptance Scenarios**:

1. **Given** a player on an expired season (end_date < today), **When** the player selects a new plan linked to an active season, **Then** the system changes their plan, resets all progress/XP/subscriptions to zero, and requires re-login.
2. **Given** a player on an expired season, **When** the player completes the plan change, **Then** a complete historical snapshot of their previous data is preserved before any deletions occur.
3. **Given** a player on an expired season, **When** the player logs back in after plan change, **Then** they see zero XP, zero streak, no completed lessons, no owned products, and are not on any leaderboard.
4. **Given** a player on an expired season, **When** the player has an active game session during plan change, **Then** the session is force-closed and the plan change proceeds without requiring the player to finish the lesson.

---

### User Story 2 - Voluntary Plan Change (Priority: P2)

A player on an active season decides to voluntarily change their plan (e.g., switching grade, major, or academic track). The same clean-slate process applies — all data is reset for the new plan.

**Why this priority**: Important for flexibility (students change tracks), but less urgent than the season-expired scenario since these players can still access content.

**Independent Test**: Can be tested by having a player on an active season trigger a plan change and verifying the same clean-slate behavior.

**Acceptance Scenarios**:

1. **Given** a player on an active season, **When** they request a plan change to a different plan, **Then** the system performs the same clean-slate transition as for expired seasons.
2. **Given** a player who changed their plan less than 24 hours ago, **When** they attempt another plan change, **Then** the system rejects the request with a clear message indicating when they can try again.
3. **Given** a player, **When** they select their current plan, **Then** the system rejects the request indicating they are already on that plan.

---

### User Story 3 - Browse Available Plans (Priority: P2)

A player needs to see which plans are available for selection before making a change. The system shows all plans linked to active seasons (published, end_date >= today), grouped by grade and major, excluding the player's current plan.

**Why this priority**: Required for the plan change flow to work — players must be able to see and select from eligible plans.

**Independent Test**: Can be tested by querying available plans and verifying the response includes only plans with active seasons, excludes the current plan, and is grouped by grade/major.

**Acceptance Scenarios**:

1. **Given** a player on any plan, **When** they request available plans, **Then** the system returns plans grouped by grade and major, filtered to active seasons only (is_published=1, end_date >= today), excluding the player's current plan.
2. **Given** no plans with active seasons exist, **When** the player requests available plans, **Then** the system returns an empty list with an appropriate message.
3. **Given** a player, **When** they browse available plans, **Then** each plan includes its grade, major, season name, and plan title.

---

### User Story 4 - Data Preservation for Analytics (Priority: P3)

Before any data is deleted during a plan change, a complete snapshot is saved as a historical record. This enables analytics (retention rates, XP at transition, migration patterns) and provides a recovery path if needed.

**Why this priority**: Critical for business intelligence and safety net, but does not directly affect the player experience.

**Independent Test**: Can be tested by performing a plan change and verifying the history record contains accurate snapshots of XP, streak, lessons, time, subscriptions, and progress.

**Acceptance Scenarios**:

1. **Given** a player with existing data (XP, subscriptions, progress), **When** a plan change occurs, **Then** a history record is created with accurate snapshots of all data before modification.
2. **Given** a plan change history record, **When** queried, **Then** it contains the previous plan/grade/major/season, new plan/grade/major/season, trigger reason, and timestamp.
3. **Given** a player, **When** examining their plan change history over time, **Then** the system can reconstruct their complete journey across seasons.

---

### Edge Cases

- **Concurrent plan change requests**: If two requests arrive simultaneously for the same player, only one succeeds; the second is rejected (same-plan check or lock wait).
- **In-flight gameplay during plan change**: If a player is mid-lesson when the plan change executes, any in-flight session completion writes must not corrupt the post-reset state. A short-lived freeze mechanism must prevent race conditions between session completion/sync jobs and the plan change operation.
- **Dirty sync race condition**: Background sync jobs that run every minute could overwrite zeroed values with stale cached data. The plan change must neutralize the player's entries in dirty sync sets before modifying any data.
- **Activity data rehydration**: After plan change, the profile activity view must not rehydrate old daily XP data from the database backup. The daily XP history stored in the database must be cleared during the plan change.
- **Season sequence cache staleness**: After plan change, review/mastery features rely on a cached season sequence value. This cache must be invalidated so the player sees data for their new season, not the old one.
- **Archived leaderboard data**: The player must be removed from archived/historical leaderboard snapshots (daily and weekly archives) to ensure a complete clean slate in the activity view.
- **Interaction buffer entries**: Interactions less than 1 minute old (pending flush) may be attributed to the wrong season post-change. This is an accepted edge case with no material impact.
- **Pending purchases**: Must be cleared so products are not hidden from the new plan's catalog.
- **No available plans**: If no plans are linked to active seasons, the player is informed with a clear message.
- **Same subjects on new plan**: Clean slate still applies — new season sequence provides automatic isolation for memory/review state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow a player to change their plan to any eligible plan, regardless of their current grade or major.
- **FR-002**: System MUST only present plans linked to seasons where `is_published = 1` AND `end_date >= today` (active seasons). The `start_date` is not checked.
- **FR-003**: System MUST exclude the player's current plan from the available plans list.
- **FR-004**: System MUST enforce a 24-hour cooldown between plan changes, checked via the timestamp of the most recent plan change history record.
- **FR-005**: System MUST preserve a complete historical snapshot before modifying or deleting any player data, including: total XP, streak, lessons completed, time spent, subscriptions (as JSON), and progress records (as JSON).
- **FR-006**: System MUST reset the player's wallet counters (XP, streak, lessons, time) to zero upon plan change.
- **FR-007**: System MUST delete all player subscription records upon plan change.
- **FR-008**: System MUST delete all player structure progress records upon plan change.
- **FR-009**: System MUST update the player's profile with the new plan, grade, major, and season.
- **FR-010**: System MUST reset the player's daily XP history (stored in the wallet record) to prevent stale activity data from rehydrating after plan change.
- **FR-011**: System MUST invalidate the player's authentication session, requiring re-login after plan change.
- **FR-012**: System MUST force-close any active game session the player has at the time of plan change.
- **FR-013**: System MUST remove the player from all leaderboard rankings (all-time, daily, weekly, and archived daily/weekly snapshots).
- **FR-014**: System MUST clear all player-scoped cached data (progress bitmaps, stats, wallet, access grants, profile, plan cache, daily XP, reviews overview, practice sessions, pending purchases, FSRS card state, FSRS idempotency keys, items learned, mastery breakdowns).
- **FR-015**: System MUST neutralize the player's entries in background sync tracking sets before modifying any database records, to prevent stale data from overwriting the clean slate.
- **FR-016**: System MUST implement a short-lived per-player freeze mechanism during the plan change operation to prevent in-flight gameplay completions and sync jobs from writing post-reset data.
- **FR-017**: System MUST invalidate the player's cached season sequence value so review/mastery features reference the correct new season.
- **FR-018**: System MUST auto-detect the trigger reason ("Season Expired" vs "Voluntary Change") based on the current season's end date, without relying on client input.
- **FR-019**: System MUST NOT modify historical audit records (interaction logs, voucher redemption logs, subscription transactions).
- **FR-024**: System MUST delete all Memory State records for the player's current season sequence upon plan change, and include a count snapshot in the history record. Memory states are player-facing learning data (mastery/items-learned), not audit records.
- **FR-020**: System MUST notify the player via real-time channel (if connected) that their plan has changed and re-login is required.
- **FR-021**: System MUST ensure the entire database operation is atomic — if any step fails, all changes are rolled back and no partial state remains.
- **FR-022**: System MUST treat cache cleanup failures as non-fatal — if cache operations fail after a successful database commit, the system relies on cache self-healing mechanisms (TTL expiry and hydration).
- **FR-023**: System MUST derive the new grade, major, and season from the selected plan rather than relying on client-provided values for these fields.

### Key Entities

- **Player Plan History**: An insert-only historical record capturing the complete state of a player's data before each plan change. Contains: player reference, previous and new plan/grade/major/season, wallet snapshots (XP, streak, lessons, time), subscriptions snapshot (JSON), progress snapshot (JSON), timestamp, and trigger reason ("Season Expired" or "Voluntary Change").
- **Player Profile**: The player's current plan assignment including plan, grade, major, and season references.
- **Player Wallet**: The player's accumulated metrics: total XP, current streak, total lessons completed, total time spent, and daily XP history.
- **Player Subscription**: Access grants for specific content — all deleted during plan change.
- **Structure Progress**: Per-subject completion tracking — all deleted during plan change.

### Business Rules

| Rule                    | Description                                                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------------------------------ |
| Rate Limit              | Maximum one plan change per 24-hour window per player                                                        |
| Clean Slate             | All player progress, XP, streak, subscriptions, and leaderboard positions reset to zero                      |
| Session Invalidation    | Player must re-login after plan change (auth session contains stale plan reference)                          |
| Active Season Filter    | Only plans linked to published seasons with end_date >= today are eligible (start_date is NOT checked)       |
| Trigger Auto-Detection  | Backend determines if change is "Season Expired" or "Voluntary Change" based on current season's end_date    |
| Concurrent Safety       | Only one plan change request can succeed for a player at a time                                              |
| Freeze During Change    | In-flight gameplay and sync jobs must not write data for the player during the transition                     |
| No Data Carryover       | Nothing transfers between plans — player starts completely fresh                                             |
| Memory State Reset      | All Memory State records for the player's current season are deleted (mastery/items-learned reset to zero)    |
| Audit Trail Untouched   | Interaction logs, voucher redemption logs, and subscription transactions are never modified                   |

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Players blocked by expired seasons can independently resume learning on a new plan within 2 minutes of initiating the change.
- **SC-002**: After a plan change, 100% of player-facing data (XP, streak, progress, leaderboards, catalog, review items, activity history) shows a clean slate — zero stale data from the previous plan.
- **SC-003**: Complete historical snapshot is preserved for every plan change, enabling recovery of 100% of deleted data if needed.
- **SC-004**: The plan change operation completes within 5 seconds end-to-end (including all data operations and cache cleanup).
- **SC-005**: Concurrent plan change attempts by the same player are safely serialized — no partial states or data corruption.
- **SC-006**: Background sync jobs do not overwrite clean-slate data after a plan change — zero data leakage from the previous plan.
- **SC-007**: Player retention rate across season transitions is measurable via plan change history records.
- **SC-008**: The system handles plan changes for up to 1,000 players concurrently without degradation.

## Assumptions

- Players understand that changing plans means losing all progress and starting fresh (communicated by the frontend, out of scope here).
- At least one plan linked to an active season exists in the system for the plan change to be useful.
- The frontend handles the UI for plan selection and season-expired notifications (this spec covers backend only).
- Memory State records are deleted during plan change to ensure mastery/items-learned counters reset to zero (clean slate).
- Voucher expiration is handled independently by the existing season expiration daily job.
- Short-TTL cache keys (rate limits, voucher fail counters, report cooldowns) are allowed to expire naturally rather than being explicitly cleaned.
- Device registrations persist across plan changes (same person, same devices).
