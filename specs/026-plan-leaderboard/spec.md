# Feature Specification: Plan-Scoped Leaderboard

**Feature Branch**: `026-plan-leaderboard`
**Created**: 2026-02-24
**Status**: Draft
**Input**: User description: "Leaderboard improvements: plan-scoped daily/weekly, remove all-time, top 20 + my rank, no pagination"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View Top Students in My Plan (Priority: P1)

A student opens the leaderboard and immediately sees the top 20 students from their own academic plan (same grade + major + season). They do not see students from other plans. The leaderboard defaults to the weekly view.

**Why this priority**: Core value proposition — fair competition among peers at the same academic level. Without this, nothing else matters.

**Independent Test**: Can be fully tested by having two students on different plans earn XP and verifying each only sees peers from their own plan on the leaderboard.

**Acceptance Scenarios**:

1. **Given** a Grade 12 Scientific student opens the leaderboard, **When** the top students load, **Then** only other Grade 12 Scientific students (same plan, same season) appear in the list.
2. **Given** fewer than 20 students have earned XP in a plan this week, **When** the student views the weekly leaderboard, **Then** only those students who earned XP appear (no padding, no empty rows).
3. **Given** a student has earned XP today, **When** they switch between daily and weekly views, **Then** the list updates to show the correct top 20 for each time period, all scoped to their plan.

---

### User Story 2 - See My Rank Among Plan Peers (Priority: P1)

A student sees their own rank, XP, and the 2 neighbors above and below them — all within their plan. They also see how much XP they need to overtake the student directly above them.

**Why this priority**: Personal motivation — seeing your position and the gap to the next rank is the primary driver of competitive engagement.

**Independent Test**: Can be tested by having a student earn XP and verifying their rank endpoint returns correct position relative to plan peers only, with accurate neighbor data and XP gap.

**Acceptance Scenarios**:

1. **Given** a student is ranked #15 in their plan's weekly leaderboard, **When** they view "My Rank", **Then** they see their rank (#15), their XP, the 2 students above (#13, #14), the 2 students below (#16, #17), and the XP needed to reach #14.
2. **Given** a student has not earned any XP in the current period, **When** they view "My Rank", **Then** they see themselves as unranked with a message indicating they need to earn XP to appear on the leaderboard.
3. **Given** a student is ranked #1 in their plan, **When** they view "My Rank", **Then** `xp_to_next` is absent (they are already first) and only neighbors below are shown.

---

### User Story 3 - Filter Leaderboard by Subject (Priority: P2)

A student filters the leaderboard by a specific subject (e.g., Physics) to see how they rank among plan peers in that subject only. The subject dropdown shows only subjects available in their plan.

**Why this priority**: Adds depth to competition but is an enhancement over the core plan-scoped board. Valuable for students who want subject-specific motivation.

**Independent Test**: Can be tested by having students in the same plan earn XP in different subjects and verifying the subject filter shows only XP earned in the selected subject.

**Acceptance Scenarios**:

1. **Given** a student selects "Physics" from the subject dropdown, **When** the leaderboard loads, **Then** rankings reflect only Physics XP earned by plan peers in the selected time period.
2. **Given** a student's plan includes 4 subjects, **When** they open the subject filter, **Then** only those 4 subjects appear as options (plus an "Overall" option showing all XP).
3. **Given** a student has earned XP in Physics but not Chemistry, **When** they filter by Chemistry, **Then** they appear as unranked in that subject's leaderboard.

---

### User Story 4 - Dual-Write for Future Global Leaderboard (Priority: P3)

The system continues writing XP to global (non-plan-scoped) leaderboard keys in the background, even though these are not currently displayed to students. This preserves the option to introduce a global leaderboard in the future.

**Why this priority**: Low-effort insurance policy. Writing to global keys is a side-effect of the existing pipeline — removing it would save negligible resources but would lose data.

**Independent Test**: Can be tested by verifying that after a student earns XP, both the plan-scoped and global sorted sets contain the updated score.

**Acceptance Scenarios**:

1. **Given** a student completes a lesson and earns 50 XP, **When** the leaderboard update runs, **Then** both plan-scoped keys and global keys are updated with the XP.
2. **Given** global keys exist with data, **When** no endpoint reads them, **Then** they expire naturally based on existing TTL rules (daily: 30d, weekly: 90d).

---

### Edge Cases

- **Student with no plan assigned**: The system returns an empty leaderboard with a clear indicator that the student is not enrolled in a plan, rather than an error.
- **Plan with only 1 student**: The student sees themselves as #1 with no neighbors. Top 20 shows only them.
- **Student changes plan mid-season**: XP already recorded under the old plan stays there. New XP goes to the new plan's leaderboard. The student sees their rank in their current plan only.
- **Tied scores**: Dense ranking applies — two students with the same XP share the same rank. The student who earned the XP first ranks higher (existing tie-breaking behavior preserved).
- **Daily/weekly boundary (midnight Amman time)**: A student earning XP at 23:59 sees it on today's board; XP earned at 00:01 goes to the next day's board. Existing timezone handling (Asia/Amman) is preserved.
- **New plan with zero activity**: Both top students and my rank return empty results with `total_players: 0`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST scope all leaderboard queries to the requesting student's current academic plan. No cross-plan data is visible.
- **FR-002**: System MUST resolve the student's plan automatically from their profile — no manual plan selection by the student.
- **FR-003**: System MUST support only two time periods: **daily** and **weekly**. The all-time leaderboard type MUST be removed from the read endpoints.
- **FR-004**: The "top students" endpoint MUST return a fixed maximum of 20 entries (not configurable by the client). The `limit` and pagination parameters MUST be removed.
- **FR-005**: The "my rank" endpoint MUST return the student's rank, XP, XP needed to overtake the next-higher student, and exactly 2 neighbors above and 2 below (within the plan scope).
- **FR-006**: System MUST support an optional subject filter. When provided, rankings reflect only XP earned in that subject within the student's plan.
- **FR-007**: System MUST continue writing XP to global (non-plan-scoped) leaderboard sorted sets for daily and weekly time periods as a background operation. These keys are not read by any current endpoint.
- **FR-008**: When a student changes plans, previously earned XP MUST remain attributed to the old plan. New XP MUST be attributed to the student's current plan at the time of earning.
- **FR-009**: Plan-scoped daily keys MUST expire within 48 hours after the day ends. Plan-scoped weekly keys MUST expire within 8 days after the week ends.
- **FR-010**: The subject filter dropdown data MUST include only subjects that belong to the student's current plan.
- **FR-011**: System MUST return the total number of ranked students in the plan for the given time period and subject filter.

### Key Entities

- **Academic Plan**: Defines a grade + major + season combination (e.g., "Grade 12 Scientific — Fall 2026"). Students on the same plan compete against each other.
- **Leaderboard Entry**: A student's rank, display name, XP, and optional avatar within a plan-scoped leaderboard for a given time period and optional subject.
- **Plan-Scoped Sorted Set**: A ranked collection of students within a single plan, for a specific time period, optionally filtered by subject. This is the primary data structure backing all leaderboard queries.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Students see only peers from their own academic plan on every leaderboard view — zero cross-plan leakage.
- **SC-002**: Leaderboard loads (both top students and my rank) complete within 20 milliseconds under normal load.
- **SC-003**: The system supports 100,000 concurrent students across all plans without degradation in leaderboard response times.
- **SC-004**: Daily and weekly plan-scoped keys auto-expire within their defined TTL windows (48h daily, 8d weekly) with no manual cleanup required.
- **SC-005**: Students who change plans see their new plan's leaderboard immediately, with no stale data from the old plan.

## Assumptions

- Each student belongs to exactly one plan at any given time. The `plan` field on the player profile is the single source of truth.
- The existing Islamic week convention (Friday through Thursday, Asia/Amman timezone) is preserved for weekly boundaries.
- The existing dense ranking algorithm and tie-breaking logic (earlier achiever ranks higher) apply unchanged within plan-scoped boards.
- Global (non-plan-scoped) keys are write-only — no endpoint reads them. They serve as a data reserve for potential future features.
- The subject filter uses the same `subject_id` values already present in the system. No new subject discovery mechanism is needed.
- The "all-time" leaderboard type is removed from both read endpoints and the `lb_type` parameter validation. Write-side behavior for global keys may retain all-time writes at the implementer's discretion.
