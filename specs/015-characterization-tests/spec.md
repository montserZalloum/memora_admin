# Feature Specification: Characterization Tests for Known Bugs

**Feature Branch**: `015-characterization-tests`
**Created**: 2026-02-17
**Status**: Draft
**Input**: User description: "Phase 7: Characterization tests for FINDING-01 (XP hydration failure), FINDING-02 (interaction buffer LTRIM risk), and FINDING-03 (stats double-counting race condition)"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Document XP Hydration Failure Bug (Priority: P1)

A developer or QA engineer needs a test that proves the XP hydration failure (FINDING-01) exists by asserting the current buggy behavior. When the external data source is unreachable during a lesson completion, the wallet hydration silently fails and the XP increment starts from zero instead of the player's actual balance, effectively resetting their XP. This is the highest severity finding (CRITICAL) because it causes real data loss for players.

**Why this priority**: This is the most impactful bug — it causes silent XP resets for real players whenever the Frappe API is temporarily unavailable. Documenting it with a test ensures the bug is tracked and verifiable.

**Independent Test**: Can be fully tested by simulating a Frappe API failure during XP award and verifying the XP resets to the awarded amount only (ignoring prior balance). When the bug is fixed, the assertion flips to expect old_xp + awarded_xp.

**Acceptance Scenarios**:

1. **Given** a player with 500 XP stored in the persistent data source but no wallet data in the cache, **When** the external data source is unreachable and a lesson completion awards 50 XP, **Then** the resulting XP total is 50 (not 550), demonstrating the bug
2. **Given** a player with a populated wallet cache, **When** a lesson completion awards 50 XP, **Then** the XP correctly increments to old_xp + 50 (the bug only manifests on cache miss with unreachable data source)

---

### User Story 2 - Document Interaction Buffer Trim Boundary Bug (Priority: P2)

A developer needs a test that proves the interaction buffer LTRIM boundary issue (FINDING-02) exists. During the periodic flush of queued interaction records, if some records fail to persist while others succeed, the trim operation uses the count of successfully inserted items to determine how many items to remove from the head of the queue. This can cause failed items in the middle of the batch to be silently dropped, since the trim removes items sequentially from the head regardless of which specific items failed.

**Why this priority**: Medium severity — interaction data loss is non-critical (analytics/audit trail) but the silent data dropping makes debugging harder.

**Independent Test**: Can be fully tested by queuing 5 interaction items, making item at position 1 fail, and verifying the trim boundary is based on inserted count rather than actual processed positions.

**Acceptance Scenarios**:

1. **Given** an interaction buffer with 5 items where item at position 1 fails to persist, **When** the flush operation runs, **Then** the trim removes the first N items based on the inserted count (where N = number of successfully inserted items), potentially dropping the failed item without retry
2. **Given** an interaction buffer where all items succeed, **When** the flush operation runs, **Then** all items are removed from the buffer correctly (bug only manifests on partial failure)

---

### User Story 3 - Document Stats Double-Counting Race Condition (Priority: P3)

A developer needs a test that proves the stats cold-start race condition (FINDING-03) exists. When two concurrent lesson completions both encounter an empty stats cache, both independently compute stats from the current completion bitmap and write the result. If the second completion's stats overwrite the first, subsequent incremental updates may produce inaccurate counts, leading to double-counted or missing completions in the stats display.

**Why this priority**: Low severity — the stats cache has a 1-hour TTL and auto-corrects on expiry. The race condition requires specific concurrent timing that is unlikely in typical usage. However, documenting it prevents confusion during debugging.

**Independent Test**: Can be tested by simulating two concurrent session completions that both trigger cold-start stats computation, and verifying the resulting stats values show the race condition's effect.

**Acceptance Scenarios**:

1. **Given** no stats cache exists for a player-subject pair, **When** two concurrent session completions both trigger cold-start stats computation, **Then** the final stats may show inconsistent completion counts (documenting the race)
2. **Given** a populated stats cache exists, **When** a session completion occurs, **Then** stats are correctly incremented via atomic operations (no race condition in the hot path)

---

### Edge Cases

- What happens when the external data source recovers mid-test (FINDING-01)? The hydration succeeds and the bug does not manifest — tests must ensure the data source stays unreachable for the duration of the XP award operation.
- What happens when all 5 interaction items fail (FINDING-02)? The inserted count is 0, LTRIM(0, -1) keeps all items — no data loss in this edge case.
- What happens when only one concurrent session completion triggers cold start (FINDING-03)? The other sees an existing cache and uses incremental updates — no race.
- FINDING-04 (OTP hardcoded to "1111") is intentionally excluded — this is a known development convenience, not a production bug.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The test suite MUST contain a test class for FINDING-01 (XP hydration failure) that asserts the current buggy behavior where XP resets to 0 when the data source is unreachable during wallet hydration
- **FR-002**: The test suite MUST contain a test class for FINDING-02 (interaction buffer LTRIM boundary) that asserts the current behavior where the trim boundary is based on inserted count, potentially dropping failed items
- **FR-003**: The test suite MUST contain a test class for FINDING-03 (stats double-counting race) that demonstrates the race condition when concurrent session completions both trigger cold-start stats computation
- **FR-004**: Each test class MUST include a detailed docstring describing the finding's severity, location in source code, current behavior, and expected behavior after fix
- **FR-005**: Each test MUST be structured so that when the underlying bug is fixed, the assertion can be flipped (changed from asserting buggy behavior to asserting correct behavior) with minimal test modification
- **FR-006**: All characterization tests MUST run within the existing test infrastructure (shared test runner, same isolation patterns, same fixtures)
- **FR-007**: FINDING-04 (hardcoded OTP value) MUST be excluded from characterization tests, as it is a deliberate development convenience
- **FR-008**: Each test MUST include a comment or marker indicating it documents a known bug (e.g., a pytest marker or naming convention) to distinguish it from standard regression tests

### Key Entities

- **Finding**: A documented known bug with severity level (CRITICAL, MEDIUM, LOW), source code location, current behavior description, and expected behavior after fix
- **Characterization Test**: A test that passes by asserting buggy behavior; when the bug is fixed, the test should fail, signaling the developer to flip the assertion to verify the fix

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All characterization tests pass when run against the current codebase, confirming they accurately document existing behavior
- **SC-002**: Each of the 3 documented findings (FINDING-01, FINDING-02, FINDING-03) has at least one dedicated test that would fail if the underlying bug were fixed
- **SC-003**: The total characterization test file produces 6 or more individual test cases covering the 3 findings
- **SC-004**: Characterization tests complete execution within 10 seconds total (consistent with the existing test suite's performance standards)
- **SC-005**: Characterization tests can be identified and run separately from regular tests (via naming convention, markers, or file-level isolation)
