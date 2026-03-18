# Feature Specification: Live Challenge Waiting Room Reactions (Backend Only)

**Feature Branch**: `050-waiting-room-reactions`
**Created**: 2026-03-17
**Status**: Draft
**Input**: User description: "Add lightweight, anonymous, real-time reactions to the Live Challenge Waiting Room — backend only. Students can tap heart, fire, or clap reactions. Purely cosmetic, ephemeral, no database writes. Server aggregates taps into burst broadcasts via existing WebSocket channel."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Tap a Reaction in Waiting Room (Priority: P1)

A student waiting in a Live Challenge waiting room taps one of the three supported reactions (heart, fire, clap). The backend accepts the tap, validates it, and counts it toward the next aggregated burst. Within a short window, all connected participants receive a single burst message showing how many of each reaction occurred.

**Why this priority**: This is the core loop of the feature — accepting taps, aggregating, and broadcasting. Without it, nothing else matters.

**Independent Test**: Can be tested by sending a reaction tap message over the waiting-room WebSocket while the room is in `waiting` state and verifying that a `waiting_room_reaction_burst` message is broadcast to all room participants within the configured flush window.

**Acceptance Scenarios**:

1. **Given** a room in `waiting` state with connected participants, **When** a participant sends `{"type": "waiting_room_reaction_tap", "reaction": "heart"}`, **Then** the tap is accepted and counted toward the next burst window.
2. **Given** accumulated taps in the current flush window, **When** the flush interval elapses (default 300ms), **Then** a single `waiting_room_reaction_burst` message is broadcast to all room participants containing aggregated counts and intensity tiers for each reaction type with non-zero count.
3. **Given** a participant sends a reaction with an invalid type (e.g., `"laugh"`), **When** the backend processes the message, **Then** the tap is silently dropped and no error is sent to the client.

---

### User Story 2 - Per-User Rate Limiting (Priority: P1)

A student tapping reactions very rapidly (faster than 3 taps/sec sustained, or more than 6 taps in 2 seconds) has excess taps silently dropped. The student remains fully connected to the waiting room and can still participate in the challenge. No disconnect, no error message, no impact on waiting-room functionality.

**Why this priority**: Without rate limiting, a single abusive client can flood the room. This is a safety requirement that ships with P1.

**Independent Test**: Can be tested by sending taps faster than the rate limit and verifying that only the allowed number are counted in subsequent burst broadcasts, with no disconnection or error events sent to the client.

**Acceptance Scenarios**:

1. **Given** a participant in a waiting room, **When** they send 10 taps in 1 second, **Then** at most 3 are counted (sustained limit), the rest are silently dropped, and the participant remains connected.
2. **Given** a participant in a waiting room, **When** they send 6 taps in under 2 seconds followed by a pause, **Then** all 6 are accepted (burst allowance), and subsequent taps resume normal rate limiting.
3. **Given** a rate-limited participant, **When** the rate limit window expires, **Then** the participant can send reactions at the normal rate again.

---

### User Story 3 - Room-Level Degradation Under Load (Priority: P2)

When many students are tapping simultaneously and reaction volume exceeds the room-level cap (default 250 reactions/sec), the backend compresses or drops excess taps. Burst broadcasts continue at a stable cadence, but with capped counts and elevated intensity tiers. The `degraded` flag in the outgoing message is set to `true`.

**Why this priority**: Important for production stability but not required for basic functionality. The feature works without degradation — it just becomes unsafe at scale.

**Independent Test**: Can be tested by simulating high-volume taps from many users against a single room and verifying that burst messages continue at the flush interval, counts are capped, intensity tiers reflect compression, and the `degraded` field is `true`.

**Acceptance Scenarios**:

1. **Given** a room receiving 500 taps/sec across all users, **When** the flush interval fires, **Then** the burst message has `degraded: true` and effective counts are compressed to the room cap.
2. **Given** a degraded room where tap volume drops back below the cap, **When** the next flush interval fires, **Then** the burst message returns to `degraded: false` with accurate counts.
3. **Given** a room at the cap limit, **When** additional taps arrive beyond the cap, **Then** excess taps are silently dropped and core room functions (countdown, state transitions) are unaffected.

---

### User Story 4 - Immediate Cutoff on Room Transition (Priority: P1)

When the waiting room countdown ends and the room transitions to `exam` state, all reaction processing stops immediately. No buffered reactions leak into the exam phase. Ephemeral counters expire shortly after.

**Why this priority**: Reactions leaking into exam state would be a user-facing bug and could confuse students. This is a hard requirement.

**Independent Test**: Can be tested by transitioning a room from `waiting` to `exam` while taps are being sent, and verifying that no burst messages are emitted after the transition and that new taps are rejected.

**Acceptance Scenarios**:

1. **Given** a room in `waiting` state with active reaction processing, **When** the room transitions to `exam`, **Then** the flush loop stops, no more burst messages are broadcast, and any buffered taps are discarded.
2. **Given** a room that has transitioned to `exam`, **When** a participant sends a reaction tap, **Then** the tap is silently rejected.
3. **Given** a room that has transitioned to `exam`, **When** 15 seconds pass, **Then** all ephemeral counters for that room's reactions have expired.

---

### User Story 5 - Resilience to Backend Failure (Priority: P2)

If the ephemeral storage layer becomes unavailable or reaction-related operations fail, the waiting room continues to function normally. Countdown proceeds, room transitions work, and students can still start the exam. The reaction feature simply becomes a no-op.

**Why this priority**: Reactions are non-critical. The system must never let cosmetic candy break core exam flow.

**Independent Test**: Can be tested by simulating storage unavailability and verifying that room state transitions, countdown, and exam start all proceed without errors, while reaction taps are silently dropped.

**Acceptance Scenarios**:

1. **Given** ephemeral storage is unavailable, **When** a participant sends a reaction tap, **Then** the tap is silently dropped with no error to the client and no impact on waiting-room functionality.
2. **Given** storage becomes unavailable mid-session, **When** the countdown reaches zero, **Then** the room transitions to `exam` normally.
3. **Given** storage recovers after a failure, **When** participants send reaction taps, **Then** reaction processing resumes automatically.

---

### Edge Cases

- What happens when a room has zero participants tapping during a flush window? No burst message is emitted (empty windows are suppressed).
- What happens when a participant sends a reaction tap but is not a valid member of the room? The tap is silently dropped after session validation fails.
- What happens when two rooms are active simultaneously? Each room maintains independent counters, rate limits, and flush loops — no cross-room interference.
- What happens when a single participant reconnects mid-session? Rate limit state may reset (keys are short-TTL), which is acceptable for cosmetic traffic.
- What happens if the flush loop takes longer than the flush interval? The current window is emitted late; the next window starts from the current time. No accumulated drift.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept reaction taps only when the room is in `waiting` state and MUST silently reject taps in any other state.
- **FR-002**: System MUST accept only three reaction types: `heart`, `fire`, `clap`. Any other value MUST be silently dropped.
- **FR-003**: Outgoing burst messages MUST NOT contain any user-identifying information (player ID, name, avatar, join order, or sender metadata).
- **FR-004**: System MUST NOT write reaction data to any persistent store (SQL tables, document stores, analytics pipelines, archive jobs, or audit logs). Only ephemeral short-TTL counters are permitted.
- **FR-005**: System MUST enforce per-user rate limiting with a sustained limit of 3 taps/sec and a burst allowance of 6 taps in 2 seconds. Excess taps MUST be silently dropped.
- **FR-006**: System MUST aggregate taps into time-based windows (default 300ms) and broadcast at most one burst message per room per window.
- **FR-007**: Each burst message MUST include aggregated counts per reaction type (omitting zero-count reactions), an intensity tier (`low`, `medium`, `high`), a `degraded` flag, room ID, window duration, and server timestamp.
- **FR-008**: System MUST enforce a room-level cap on effective processed reactions (default 250/sec). Excess taps beyond the cap MUST be compressed or dropped.
- **FR-009**: Under heavy load, system MUST set `degraded: true` in burst messages and MAY increase the aggregation window, cap emitted counts, or compress intensity tiers.
- **FR-010**: When the room transitions out of `waiting` state, system MUST immediately stop accepting taps, stop the flush loop, discard buffered reactions, and expire ephemeral keys within a short TTL (15 seconds or less).
- **FR-011**: System MUST validate that the tap sender is an authenticated participant of the specific waiting room session before accepting the tap.
- **FR-012**: Rate limiting MUST NOT disconnect the user or affect their ability to participate in the waiting room or transition to the exam.
- **FR-013**: If ephemeral storage is unavailable or reaction processing fails, the waiting room MUST continue functioning normally — countdown, room transitions, and exam start MUST be unaffected. Reactions degrade to no-op.
- **FR-014**: System MUST use the existing waiting-room real-time channel for reaction transport. No separate polling or HTTP endpoint is required.
- **FR-015**: Client-provided count, intensity, or metadata fields MUST be ignored. Only the `reaction` field from the client message is trusted.

### Key Entities

- **Reaction Tap**: An individual user action — one of `heart`, `fire`, `clap`. Exists only as an ephemeral event; never persisted.
- **Reaction Bucket**: A time-windowed counter accumulating tap counts by reaction type for a specific room. Short TTL (default 10 seconds).
- **Rate Limit Token**: A per-user, per-room tracker of recent tap frequency. Short TTL (default 3 seconds).
- **Burst Message**: The aggregated broadcast payload sent to all room participants at each flush interval, containing counts, intensity tiers, and degradation status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Participants see reaction activity within 500ms of tapping (end-to-end perceived delay), with ideal path around 300ms.
- **SC-002**: No individual tap ever triggers a direct broadcast — all outgoing messages are aggregated bursts, verified by zero per-tap fanout events in operational metrics.
- **SC-003**: A single user sending 100 taps in 10 seconds results in at most 30 counted taps (3/sec sustained limit), with no disconnection or error messages.
- **SC-004**: A room with 500+ simultaneous tappers continues to emit stable burst messages at the configured cadence without impacting countdown accuracy or room transition timing.
- **SC-005**: Zero reaction data records exist in any persistent store after any waiting room session.
- **SC-006**: When a room transitions from `waiting` to `exam`, reaction bursts stop within one flush interval and no late reactions appear in the exam context.
- **SC-007**: With ephemeral storage unavailable, 100% of room transitions (waiting to exam) complete successfully with no user-visible errors.
- **SC-008**: Outgoing burst messages contain zero user-identifying fields — verified by schema validation of all emitted payloads.
- **SC-009**: Room-level degradation activates when tap volume exceeds the configured cap, compressing output while maintaining burst cadence.
- **SC-010**: All ephemeral keys for a room's reactions expire within 15 seconds of the room leaving `waiting` state.

## Assumptions

- The existing real-time session layer used by waiting-room live updates supports multiplexing additional message types without architectural changes.
- Room state (`waiting`, `exam`, etc.) is accessible to the reaction handler via the existing room state management system.
- Participant session validation (confirming a user belongs to a specific room) is already available in the real-time handler context.
- The feature will be gated behind a feature flag for incremental rollout.
- Intensity tier thresholds are configurable but ship with sensible defaults (e.g., low: 1-10 taps, medium: 11-50 taps, high: 51+ taps per reaction per window).
- The flush interval, rate limits, and room cap are configurable at the operational level for tuning during rollout.

## Scope Boundaries

### In Scope
- Accepting and validating reaction taps from waiting-room participants
- Aggregating taps into time-windowed bursts per room
- Broadcasting anonymous burst messages to all room participants
- Per-user rate limiting (token bucket / sliding window)
- Room-level shaping and graceful degradation under load
- Immediate cutoff when room exits `waiting` state
- Operational metrics for observability

### Out of Scope
- Frontend animation, rendering, sound, or vibration behavior
- Mobile app-specific behavior
- Persistent storage or analytics/reporting of reaction data
- Reaction history or replay capability
- Per-user identity, attribution, or sender visibility in any form
- Use of reactions outside the waiting room context
- Leaderboards, rewards, scores, or any gameplay impact from reactions
