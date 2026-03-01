# Feature Specification: Admin Announcement System

**Feature Branch**: `032-admin-announcements`
**Created**: 2026-02-28
**Status**: Draft
**Input**: User description: "Admin Announcement System — bilingual announcements with plan-based targeting, duration controls, display frequency options, and Redis caching for 50K concurrent users"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Admin Creates and Publishes a Global Announcement (Priority: P1)

An administrator needs to broadcast a holiday greeting to all players. They open the Announcement form in Frappe Desk, fill in the Arabic and English title and body, select "All Players" as the target audience, choose "Date Range" as the duration type with start and end dates, set display frequency to "Once Per Day", and check "Is Published". The announcement is immediately available to all players on their next Home screen load.

**Why this priority**: This is the core use case — creating and publishing an announcement that reaches all players. Without this, the feature delivers zero value.

**Independent Test**: Can be fully tested by creating an announcement in Frappe Desk, publishing it, and verifying it appears in the API response for any player.

**Acceptance Scenarios**:

1. **Given** an admin is on the Announcement form, **When** they fill all required fields (title AR/EN, body AR/EN), select "All Players", choose "Date Range" with valid dates, set frequency to "Once Per Day", and check "Is Published", **Then** the announcement is saved and the API returns it for any authenticated player.
2. **Given** an announcement is published with "All Players" targeting, **When** a player calls the announcements API, **Then** the response includes the announcement with title and body in the player's preferred language.
3. **Given** an announcement is published, **When** the admin unchecks "Is Published", **Then** the announcement no longer appears in API responses.

---

### User Story 2 - Player Views Active Announcements on Home Screen (Priority: P1)

A player opens the Home screen of the mobile app. The app calls the announcements API, which returns all active announcements for that player. The app displays them as compact banners showing the title in the player's preferred language (Arabic or English). Tapping a banner reveals the full body text. The app applies display frequency rules locally.

**Why this priority**: This is the player-facing counterpart of Story 1. Together they form the minimum viable announcement system.

**Independent Test**: Can be tested by calling the API with a valid player session and verifying the response contains correctly localized announcements sorted by creation date descending.

**Acceptance Scenarios**:

1. **Given** there are two active announcements (one global, one for the player's plan), **When** the player calls the announcements API, **Then** both announcements are returned, sorted newest first.
2. **Given** a player has `preferred_lang` set to "ar", **When** they receive an announcement, **Then** the title and body fields contain Arabic content only.
3. **Given** a player has `preferred_lang` set to "en", **When** they receive an announcement, **Then** the title and body fields contain English content only.
4. **Given** no active announcements exist, **When** the player calls the announcements API, **Then** the response is an empty array.

---

### User Story 3 - Admin Targets Announcement to Specific Plans (Priority: P2)

An administrator wants to notify only students on specific academic plans about a new feature. They create an announcement, select "Specific Plans" as the target audience, link the target plans, set the duration and frequency, and publish. Only players on those plans see the announcement.

**Why this priority**: Plan-based targeting adds segmentation value, but the system is useful even without it (all-player announcements cover most use cases).

**Independent Test**: Can be tested by creating a plan-targeted announcement and verifying it appears only for players on the targeted plans, not for players on other plans.

**Acceptance Scenarios**:

1. **Given** an announcement targets "Plan A" and "Plan B", **When** a player on "Plan A" calls the API, **Then** the announcement is included in the response.
2. **Given** an announcement targets "Plan A" and "Plan B", **When** a player on "Plan C" calls the API, **Then** the announcement is NOT included in the response.
3. **Given** an announcement targets "Specific Plans" with no plans selected, **When** the admin tries to save, **Then** validation prevents saving (target plans required when audience is "Specific Plans").

---

### User Story 4 - Admin Uses Fixed Duration Mode (Priority: P2)

An administrator wants to publish a maintenance notice visible for exactly 3 days starting now. They create the announcement, select "Fixed Duration", enter 3 for duration days, and publish. The system computes an effective end date and automatically stops serving the announcement after 3 days.

**Why this priority**: Fixed duration is a convenience feature that simplifies the admin workflow for time-limited announcements.

**Independent Test**: Can be tested by creating a fixed-duration announcement and verifying the computed effective end date equals publish date + duration days.

**Acceptance Scenarios**:

1. **Given** an announcement with "Fixed Duration" of 5 days is published on 2026-03-01, **When** the effective dates are computed, **Then** `effective_start_date` is 2026-03-01 and `effective_end_date` is 2026-03-06.
2. **Given** an announcement's `effective_end_date` has passed, **When** a player calls the API, **Then** the announcement is NOT returned even if `is_published` is still checked.
3. **Given** an admin selects "Fixed Duration", **When** they view the form, **Then** "Duration Days" is visible and "Start Date"/"End Date" fields are hidden.

---

### User Story 5 - Admin Edits a Live Announcement (Priority: P3)

An administrator notices a typo in a published announcement. They open the document, correct the text, and save. The updated content is reflected in the next API call from any player.

**Why this priority**: Editing is important for corrections but is a secondary workflow.

**Independent Test**: Can be tested by editing a published announcement's body text and verifying the API returns the updated content.

**Acceptance Scenarios**:

1. **Given** a published announcement with body "Old text", **When** the admin changes the body to "New text" and saves, **Then** the next API call returns "New text".
2. **Given** a published "All Players" announcement, **When** the admin changes targeting to "Specific Plans" and saves, **Then** the announcement disappears from responses for players not on the newly targeted plans.

---

### User Story 6 - Admin Deletes an Announcement (Priority: P3)

An administrator decides an announcement is no longer needed and deletes it from Frappe Desk. The announcement immediately stops appearing for all players.

**Why this priority**: Deletion is a cleanup action; unpublishing covers the same need for most cases.

**Independent Test**: Can be tested by deleting a published announcement and verifying it no longer appears in API responses.

**Acceptance Scenarios**:

1. **Given** a published announcement, **When** the admin deletes it, **Then** the next API call for any player does not include it.

---

### Edge Cases

- **Player changes plan**: The player's next API call uses their new plan. Announcements for the old plan disappear; announcements for the new plan appear. Client-side frequency tracking (by announcement ID) persists across plan changes.
- **Admin changes target from "All" to "Specific Plans"**: Cache is rebuilt for both global and plan-specific keys. Players not on the newly targeted plans stop seeing the announcement.
- **Admin changes target from "Specific Plans" to "All"**: Cache is rebuilt for global key and old plan-specific keys are cleaned up. All players now see the announcement.
- **Multiple active announcements**: All are returned; frontend handles stacking/scrolling.
- **Announcement with Fixed Duration — admin publishes later**: `effective_end_date` is computed at the time `is_published` is first set to true.
- **Player clears local storage**: They may re-see "Once" announcements — accepted tradeoff for stateless backend.
- **No active announcements**: API returns empty array `[]`.
- **Cache miss**: System hydrates from database, caches the result, and returns it. No user-facing impact.
- **Overlapping date ranges**: Multiple announcements with overlapping active periods are all returned — no conflict resolution needed.
- **Admin sets end date before start date**: Form validation prevents this.
- **Duration days set to 0 or negative**: Form validation requires duration days >= 1.
- **Admin re-publishes after unpublishing**: For "Fixed Duration", the original effective end date is preserved (not recomputed).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a form for admins to create announcements with bilingual content (Arabic title, English title, Arabic body, English body) — all four fields required.
- **FR-002**: System MUST support two target audience modes: "All Players" (default) and "Specific Plans" (with multi-plan selection).
- **FR-003**: System MUST require at least one target plan when audience is "Specific Plans"; validation MUST prevent saving without plans selected.
- **FR-004**: System MUST support two mutually exclusive duration types: "Date Range" (explicit start/end dates) and "Fixed Duration" (number of days from publish).
- **FR-005**: System MUST compute and store an `effective_start_date` and `effective_end_date` for all announcements regardless of duration type, enabling uniform date filtering.
- **FR-006**: For "Fixed Duration" announcements, `effective_end_date` MUST be computed as the publish timestamp plus the specified duration in days.
- **FR-007**: System MUST support four display frequency options: "Always", "Once", "Once Per Day", "Once Per Session" — included in the API response for client-side enforcement.
- **FR-008**: System MUST only serve announcements where `is_published` is true AND the current date falls within the effective date range (inclusive).
- **FR-009**: System MUST return announcement content in the player's preferred language only (based on `preferred_lang` from their profile).
- **FR-010**: System MUST return announcements sorted by creation date descending (newest first).
- **FR-011**: System MUST return plan-targeted announcements only to players whose current plan matches one of the targeted plans.
- **FR-012**: System MUST return global announcements ("All Players") to every player regardless of their plan.
- **FR-013**: System MUST cache announcement data to serve reads from cache, not from the database directly.
- **FR-014**: System MUST invalidate and rebuild the cache when an announcement is created, updated, or deleted.
- **FR-015**: System MUST handle cache misses by hydrating from the database, storing in cache, and returning results seamlessly.
- **FR-016**: The announcements API MUST NOT perform any write operations — purely read-only with no view tracking or state changes.
- **FR-017**: System MUST allow admins to edit any field on a published announcement, with changes reflected in the next API call.
- **FR-018**: System MUST allow admins to delete announcements, immediately removing them from API responses.
- **FR-019**: Form MUST show/hide fields contextually: "Target Plans" visible only when audience is "Specific Plans"; "Start Date"/"End Date" visible only for "Date Range"; "Duration Days" visible only for "Fixed Duration".
- **FR-020**: System MUST validate that end date is after start date for "Date Range" announcements.
- **FR-021**: System MUST validate that duration days is at least 1 for "Fixed Duration" announcements.

### Key Entities

- **Announcement**: The core entity. Contains bilingual title and body, target audience type, duration configuration, display frequency, publish status, and computed effective dates. Has a one-to-many relationship with Target Plans when audience is "Specific Plans".
- **Announcement Target Plan**: A child record linking an Announcement to a specific Academic Plan. Only exists when the parent announcement's audience is "Specific Plans".
- **Academic Plan** *(existing)*: Represents a player's subscription plan. Referenced by announcements for targeting.
- **Player Profile** *(existing)*: Contains the player's `preferred_lang` (for language selection) and `plan` (for targeting).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Admin can create, publish, edit, and delete an announcement within 1 minute using the admin interface.
- **SC-002**: Published announcements appear in the player's API response on the next request after publishing.
- **SC-003**: Plan-targeted announcements are visible only to players on the targeted plans — zero cross-plan leakage.
- **SC-004**: The announcement retrieval responds within 10ms under normal load when served from cache.
- **SC-005**: Cache is updated within 2 seconds of an admin action (create/edit/delete).
- **SC-006**: The system handles 50,000 concurrent users fetching announcements without direct database queries on reads.
- **SC-007**: Expired announcements are never returned to players, even if cached data hasn't been explicitly refreshed.
- **SC-008**: Bilingual content is correctly served — Arabic-preference players receive Arabic, English-preference players receive English.

## Assumptions

- Players have a `preferred_lang` field on their profile that is always set (defaults to "ar" if unset).
- Each player belongs to exactly one academic plan at any time.
- The number of active announcements at any given time is small (typically < 20), making it practical to return all in a single API response.
- Display frequency enforcement is entirely the mobile client's responsibility; the backend has no mechanism to enforce it.
- Announcements are plain text only (no rich media, images, or HTML) for v1.
- The "Fixed Duration" effective end date is computed when `is_published` is first set to true. Re-publishing after unpublishing preserves the original end date.
- Admins have appropriate permissions to create/edit/delete announcement documents.

## Scope Boundary

### In Scope

- Admin announcement management (CRUD with desk UI)
- Bilingual content (Arabic + English)
- Plan-based targeting (all players or specific plans)
- Two duration modes (date range, fixed duration)
- Four display frequency options
- Cached read API for players
- Cache invalidation on admin actions

### Out of Scope

- Push notifications
- Real-time delivery (WebSocket)
- Rich media content (images, links, buttons)
- Analytics or view tracking
- Targeting by grade, major, or season
- Placement on screens other than Home
- Server-side display frequency tracking
- A/B testing
- Priority or custom ordering
