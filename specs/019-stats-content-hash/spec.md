# Feature Specification: Stats Cache Staleness Detection

**Feature Branch**: `019-stats-content-hash`
**Created**: 2026-02-18
**Status**: Draft
**Input**: PRD: `.planning/prd/stats-hash-staleness.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Accurate Progress After Content Changes (Priority: P1)

A student opens their progress page for a subject. An editor recently added a new lesson to that subject. The student sees the correct completion percentage reflecting the new total number of lessons, rather than a stale 100% based on outdated totals.

**Why this priority**: This is the core problem — stale stats mislead students about their actual progress. Without this, students may believe they've completed a subject when new content exists.

**Independent Test**: Can be tested by adding a lesson to a subject where a student has cached stats, then verifying the progress endpoint returns updated totals on the next request.

**Acceptance Scenarios**:

1. **Given** a student has completed 2/2 lessons (100%) and their stats are cached, **When** an editor adds a 3rd lesson and the student requests their progress, **Then** the response shows 2/3 lessons completed (66.7%).
2. **Given** a student has completed 5/10 lessons (50%) and their stats are cached, **When** an editor removes 2 uncompleted lessons and the student requests their progress, **Then** the response shows 5/8 lessons completed (62.5%).
3. **Given** a student has cached stats for a subject, **When** no content changes have occurred and the student requests their progress, **Then** the cached stats are returned without recomputation.

---

### User Story 2 - Seamless Migration for Existing Users (Priority: P1)

Students with existing cached stats (generated before this feature was deployed) continue to see correct progress. The system detects that their stats lack the new validation field and automatically refreshes them — no manual intervention or data migration required.

**Why this priority**: With 100k+ users, any migration that requires downtime or manual intervention is unacceptable. Self-healing ensures zero disruption.

**Independent Test**: Can be tested by querying the progress endpoint for a user whose stats cache was populated before deployment (no validation field present), and verifying the stats are recomputed correctly.

**Acceptance Scenarios**:

1. **Given** a student has stats cached without a validation field (pre-migration), **When** the student requests their progress, **Then** the system recomputes and caches fresh stats with the validation field.
2. **Given** a student has stats cached without a validation field, **When** no content has changed, **Then** the recomputed stats still show the same correct values (no data loss).

---

### User Story 3 - No Performance Degradation on Normal Operations (Priority: P1)

During normal operations (no content changes), students experience no noticeable slowdown when checking their progress or completing lessons. The validation adds negligible overhead to every request.

**Why this priority**: The system serves 100k+ concurrent users with sub-20ms response targets. Any measurable performance regression would be unacceptable at this scale.

**Independent Test**: Can be tested by benchmarking progress endpoint response times before and after the feature is deployed, confirming no measurable increase during normal (non-stale) operations.

**Acceptance Scenarios**:

1. **Given** a student's stats are cached and up-to-date, **When** the student requests their progress, **Then** the response time remains within existing performance targets (no measurable overhead).
2. **Given** a student completes a lesson, **When** the system updates their completion counters, **Then** the update performance is identical to current behavior (no changes to the completion path).

---

### User Story 4 - Zero Write Storm on Content Changes (Priority: P2)

When a content editor adds, removes, or reorganizes lessons, the system does not perform any bulk writes to user stats caches. Each user's stats are validated and refreshed individually on their next request, spreading the load naturally over time.

**Why this priority**: Preventing write storms is a scaling requirement. With 100k users per subject, eager invalidation would generate 100k write operations, potentially breaching the system's performance SLA.

**Independent Test**: Can be tested by monitoring write operations to stats cache keys during and after a content change, confirming zero writes occur until individual users request their progress.

**Acceptance Scenarios**:

1. **Given** a subject has many users with cached stats, **When** an editor adds a lesson, **Then** zero writes occur to any user's stats cache at the time of the content change.
2. **Given** a content change has occurred, **When** users request their progress at different times, **Then** each user's stats are refreshed individually on their first request after the change.

---

### Edge Cases

- What happens when the subject hierarchy cache expires and is rebuilt with the same structure? Stats remain valid — no unnecessary recomputation occurs because the validation fingerprint is unchanged.
- What happens when a content change occurs while a student is mid-session completing a lesson? The lesson completion updates the existing stats counters. On the student's next progress read, the system detects the structural change and recomputes, incorporating both the new content and the just-completed lesson.
- What happens when two content changes occur in quick succession? The validation fingerprint changes twice. The student recomputes once on their next read, getting the latest structure.
- What happens when the cache store is flushed? Both stats and hierarchy caches are lost. The existing self-healing mechanisms rebuild both from scratch, including the new validation field.
- What happens when an admin reorders tracks without adding or removing lessons? The fingerprint may change, triggering a recompute. This is a harmless false positive (~4ms cost) and reordering is a rare operation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST compute a deterministic structural fingerprint from the subject hierarchy whenever it is built, capturing all fields that affect completion totals: bit range, excluded bits (sorted), track IDs, unit IDs, topic IDs, per-topic lesson counts, lesson IDs, and lesson bit indices.
- **FR-002**: System MUST store the structural fingerprint as a field in the hierarchy data so it is available to all consumers without additional computation.
- **FR-003**: System MUST embed the current structural fingerprint into each user's stats cache when stats are computed or recomputed.
- **FR-004**: System MUST compare the hierarchy's structural fingerprint against the user's cached stats fingerprint on every progress read request.
- **FR-005**: System MUST trigger a full stats recomputation when the fingerprints do not match (stale detection).
- **FR-006**: System MUST skip recomputation when the fingerprints match (fresh stats confirmation).
- **FR-007**: System MUST treat stats that lack a fingerprint (pre-migration) as stale and trigger recomputation, storing the fingerprint upon recomputation (self-healing migration).
- **FR-008**: System MUST NOT modify the existing lesson completion write path — incremental counter updates remain unchanged.
- **FR-009**: System MUST NOT perform any writes to user stats caches when content changes occur — validation happens lazily on each user's next read.
- **FR-010**: System MUST apply the staleness check to all progress endpoints that read from the stats cache (subject-level, track-level, and unit-level progress).
- **FR-011**: System MUST NOT apply the staleness check to endpoints that read directly from bitmaps (lesson-level progress), since those are already correct by design.
- **FR-012**: The structural fingerprint MUST change when lessons are added, removed, or reorganized (no false negatives — stale stats are always detected).
- **FR-013**: The structural fingerprint MUST NOT change when non-structural fields are modified (e.g., XP values, linearity flags, free content flags) — these do not affect completion totals.

### Key Entities

- **Subject Hierarchy**: The tree structure of a subject (tracks, units, topics, lessons) with a structural fingerprint computed at build time. The fingerprint changes only when the structure affecting completion totals changes.
- **Stats Cache**: Per-user, per-subject cached aggregation of completion counts (total and completed) at subject, track, unit, and topic levels. Now includes a structural fingerprint for freshness validation.
- **Progress Bitmap**: Per-user, per-subject bit array where each bit represents a lesson's completion state. This is the source of truth for recomputing stats — it is NOT modified by this feature.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a content change, the next progress request for any affected user returns correct totals — the stale window is reduced from up to 1 hour to zero (detected on next read).
- **SC-002**: During normal operations (no content changes), progress requests complete within existing performance targets with no measurable increase in response time.
- **SC-003**: When a content change affects a subject with cached stats for many users, zero writes occur to stats caches at the time of the change — recomputation is spread across individual user requests.
- **SC-004**: Recomputation of stale stats (when triggered) completes within the existing response time budget, adding no more than 5ms to the request.
- **SC-005**: Pre-existing cached stats (without a fingerprint) are automatically refreshed on the next user request — no manual migration or downtime required.
- **SC-006**: The lesson completion path (counter increments) experiences zero performance change.
