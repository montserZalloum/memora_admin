# Feature Specification: Progress & Practice Read-Path Performance

**Feature Branch**: `036-read-path-perf`
**Created**: 2026-03-03
**Status**: Draft
**Input**: Reduce latency and backend load for high-traffic progress and practice/hierarchy APIs by removing unnecessary computation, reducing data transfer size, preventing duplicate upstream requests on cache misses, smoothing per-subject fan-out, and enabling production tuning without changing API behavior.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Warm Progress Reads Skip Redundant Computation (Priority: P1)

When a student opens a progress screen during peak traffic, the backend should use previously computed stats instead of re-deriving them from raw data. Today, every progress read decodes the full underlying data structure even when valid precomputed results already exist. This is the single largest steady-state cost on the progress read path.

**Why this priority**: Progress endpoints are the highest-traffic read path in the platform. Eliminating redundant computation on every warm read directly reduces per-request latency and overall backend load for 100k concurrent users.

**Independent Test**: Can be verified by issuing a progress detail request when valid cached stats exist and confirming the response is returned without triggering a full data recomputation.

**Acceptance Scenarios**:

1. **Given** valid precomputed stats exist for a user and subject, **When** a progress detail endpoint is called, **Then** the endpoint returns the response without performing a full data recomputation.
2. **Given** precomputed stats are missing, incomplete, or stale, **When** a progress detail endpoint is called, **Then** the endpoint falls back to the current full computation path and produces an identical response.
3. **Given** the optimized path is active, **When** compared with the current implementation, **Then** the response data and business rules (unlock state, completion percentages) are functionally identical.

---

### User Story 2 - Partial Progress Routes Fetch Only Required Data (Priority: P1)

When the frontend requests progress for a specific track or unit (not the full subject), the backend should retrieve only the data fields needed for that response level. Today, all partial routes fetch the entire stats payload regardless of how much of it they actually use.

**Why this priority**: Partial progress routes (tracks list, track detail, unit detail) are called frequently and currently transfer significantly more data than needed. Reducing data transfer scales with response size and directly cuts per-request overhead.

**Independent Test**: Can be verified by requesting track-level progress and confirming the backend fetches only track-level fields rather than the complete subject stats payload.

**Acceptance Scenarios**:

1. **Given** a tracks-list request for a subject, **When** the route reads stats, **Then** it fetches only track-level summary fields and validation fields required for correctness.
2. **Given** a track-detail request, **When** the route reads stats, **Then** it fetches only the specific track and its unit-level fields.
3. **Given** a unit-detail request, **When** the route reads stats, **Then** it fetches only the specific unit and its topic-level fields.
4. **Given** a full-subject progress request, **When** the route runs, **Then** it may continue using a full data read if that remains the simplest correct approach.

---

### User Story 3 - Cache Misses Coalesce for Shared Data (Priority: P2)

When multiple concurrent requests miss the same cached data (hierarchy structure or practice metadata), only one request should perform the upstream data fetch while others wait briefly for the result. Today, simultaneous cache misses for the same key each independently call the upstream data source, creating unnecessary load.

**Why this priority**: Under burst traffic (e.g., class of students opening the same subject simultaneously), duplicate upstream fetches amplify latency and can overload the source system. Coalescing eliminates this amplification.

**Independent Test**: Can be verified by simulating multiple concurrent requests for the same uncached hierarchy key and confirming only one upstream fetch occurs.

**Acceptance Scenarios**:

1. **Given** multiple concurrent requests miss the same hierarchy cache key, **When** they arrive together, **Then** only one request performs the upstream data fill while others wait briefly for the result.
2. **Given** multiple concurrent requests miss the same practice metadata cache key, **When** they arrive together, **Then** only one request performs the upstream data fill while others wait briefly.
3. **Given** a waiting request exceeds the configured wait timeout, **When** the timeout is reached, **Then** the request degrades gracefully (e.g., performs its own fetch) instead of blocking indefinitely.
4. **Given** a timeout occurs, **When** the request continues, **Then** the system may permit bounded duplicate upstream work rather than failing the user request.

---

### User Story 4 - Practice Hierarchy Evaluates Subject Access Once Per Request (Priority: P2)

When a player requests practice hierarchy, subject-level access should be determined once at the start, not re-evaluated for every track in the subject. Today, the same subject-level check is repeated inside every iteration of the track loop, adding unnecessary work proportional to the number of tracks.

**Why this priority**: This is a straightforward optimization that removes redundant per-track work. The benefit scales with subject size (more tracks = more wasted checks).

**Independent Test**: Can be verified by requesting practice hierarchy for a subject and confirming subject-level access is evaluated once, while per-track checks are still applied individually.

**Acceptance Scenarios**:

1. **Given** a player requests practice hierarchy, **When** the request starts, **Then** subject-level access is computed once before iterating tracks.
2. **Given** per-track access grants must still be respected, **When** the track loop runs, **Then** only track-specific checks are evaluated per track.
3. **Given** the response is compared to current behavior, **When** the optimization is applied, **Then** returned access flags and visible nodes remain unchanged.

---

### User Story 5 - Progress Summary Uses Bounded Concurrency (Priority: P2)

When a player requests their progress summary across all subjects, the backend should process per-subject lookups with a concurrency cap instead of launching all lookups simultaneously. Today, unbounded fan-out creates avoidable load bursts when a player has many accessible subjects.

**Why this priority**: A player with many subjects can trigger a large burst of simultaneous data lookups. Bounding concurrency smooths this into predictable, manageable load.

**Independent Test**: Can be verified by requesting progress summary for a player with many subjects and confirming lookups are processed in bounded batches rather than all at once.

**Acceptance Scenarios**:

1. **Given** a player has many accessible subjects, **When** progress summary is requested, **Then** per-subject work is processed with a concurrency cap (target range: 5-8 concurrent subject tasks).
2. **Given** the concurrency cap is reached, **When** additional subject tasks are pending, **Then** they wait briefly rather than being dropped or failing.
3. **Given** a player has only a small number of accessible subjects (fewer than the cap), **When** the endpoint runs, **Then** behavior remains functionally identical with at most minor scheduling overhead.

---

### User Story 6 - Production Tuning Without Code Changes (Priority: P3)

As an operator, I want to adjust backend connection pool sizes and upstream client limits through environment configuration so that the platform can scale to higher concurrency without requiring code deployments.

**Why this priority**: Some performance headroom is already available operationally through environment settings. Documenting and applying these settings is low-risk and complements the code-level optimizations.

**Independent Test**: Can be verified by adjusting environment configuration values and confirming the service starts with the updated pool sizes and concurrency limits without any code changes.

**Acceptance Scenarios**:

1. **Given** production-tuned values are applied via environment configuration, **When** the service starts, **Then** connection pools and upstream client limits reflect those values.
2. **Given** no code changes are made, **When** environment values are tuned upward, **Then** the service supports higher concurrency before pool exhaustion or upstream contention.

---

### Edge Cases

- What happens when precomputed stats exist but are partially corrupt or missing expected fields? The system must fall back to full recomputation gracefully.
- What happens when cache-fill coalescing encounters a leader failure (the single request performing the fill crashes or times out)? Waiting requests must not deadlock; they should degrade to performing their own fetch.
- What happens when a player's accessible subject count exceeds the concurrency cap significantly (e.g., 50+ subjects)? The system must still return correct results for all subjects, just in bounded batches.
- What happens when partial stats reads request fields that don't exist in the stats hash (e.g., after a schema migration)? The system must detect this and fall back to full computation rather than returning incomplete data.
- What happens when the concurrency semaphore is exhausted and all workers are waiting? The system must not drop requests; pending work waits until a slot opens.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Progress detail endpoints MUST use a stats-first read path that checks for valid precomputed results before performing full data recomputation.
- **FR-002**: The stats-first path MUST skip full data recomputation when precomputed stats are present and valid.
- **FR-003**: If precomputed stats are missing, incomplete, or stale, the system MUST fall back to the existing safe computation path with identical results.
- **FR-004**: Partial progress endpoints (tracks list, track detail, unit detail) MUST read only the data fields required for their specific response level.
- **FR-005**: Full subject progress MAY continue using a complete data read if that remains the simplest correct approach.
- **FR-006**: Hierarchy data cache fills MUST use per-key coalescing so that concurrent misses for the same key trigger at most one upstream fetch per worker process.
- **FR-007**: Practice metadata cache fills MUST use per-key coalescing with the same behavior as hierarchy coalescing.
- **FR-008**: Cache-fill coalescing MUST use a bounded wait timeout (3-10 seconds) to prevent indefinite blocking.
- **FR-009**: On wait timeout, requests MUST degrade gracefully (e.g., perform their own fetch) rather than failing or blocking indefinitely.
- **FR-010**: Practice hierarchy MUST compute subject-level access once per request, outside the per-track iteration loop.
- **FR-011**: Per-track access decisions MUST remain functionally identical to current behavior.
- **FR-012**: Progress summary MUST use bounded concurrency (target range: 5-8 concurrent subject tasks) for per-subject work instead of unbounded fan-out.
- **FR-013**: The progress summary concurrency limit SHOULD be configurable or centrally defined.
- **FR-014**: The concurrency cap MUST preserve response correctness for any number of accessible subjects.
- **FR-015**: All optimizations MUST preserve current API response shapes and business rules with zero behavioral changes.
- **FR-016**: Existing in-process caching and per-key coordination mechanisms already in place MUST remain intact and must not be removed or duplicated.
- **FR-017**: Production scaling settings available through environment configuration MUST be documented as part of rollout.

### Key Entities

- **Precomputed Stats**: Per-user, per-subject summary data containing completion counts, percentages, and unlock states at each hierarchy level (subject, track, unit, topic). Derived from raw progress data but cached separately for fast reads.
- **Hierarchy Structure**: The content tree for a subject (tracks, units, topics, lessons) with structural metadata. Shared across all users of the same subject. Cached with a time-based expiry.
- **Practice Metadata**: Per-subject practice configuration cached alongside hierarchy data. Used by practice endpoints to determine review eligibility and session parameters.
- **Access Grants**: Per-player set of content access rights. Determines which subjects, tracks, and content a player can view. Subject-level grants are constant within a single request.
- **Progress Summary**: Aggregated view across all of a player's accessible subjects, showing per-subject completion percentages and status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Warm reads on progress endpoints (full subject, summary, tracks list, track detail, unit detail) return without performing full data recomputation when valid precomputed stats exist.
- **SC-002**: Partial progress endpoints transfer less data per request compared to current behavior where full stats payload is always fetched.
- **SC-003**: Concurrent cache misses for the same hierarchy key trigger at most one upstream data fetch per worker process during normal operation.
- **SC-004**: Concurrent cache misses for the same practice metadata key trigger at most one upstream data fetch per worker process during normal operation.
- **SC-005**: Practice hierarchy endpoint evaluates subject-level access once per request instead of once per track.
- **SC-006**: Progress summary processes per-subject work within a bounded concurrency limit instead of unbounded simultaneous fan-out.
- **SC-007**: All existing API response contracts remain unchanged (no field additions, removals, or type changes).
- **SC-008**: All existing automated tests for progress, hierarchy, stats, and practice continue to pass without modification.
- **SC-009**: Production throughput tuning can be applied entirely through environment configuration without code changes.

## Assumptions

- The existing per-worker in-process hierarchy cache and per-key stats recompute coalescing are already implemented and working. This feature builds on top of them.
- Precomputed stats, when present and valid, produce results identical to full data recomputation. The stats-first path is a shortcut, not a different computation.
- The number of accessible subjects per player is typically under 20, but the system must handle edge cases with significantly more.
- Cache-fill coalescing timeout of a few seconds is acceptable; exact value is an operational decision.
- The initial progress summary concurrency cap of 5-8 is a reasonable starting point that can be adjusted based on production metrics.
- No changes to the content management system or content structure are required for these optimizations.

## Scope Boundaries

**In scope**:
- Stats-first read path for progress endpoints
- Targeted field reads for partial progress routes
- Cache-fill coalescing for hierarchy and practice metadata
- Subject-level access hoisting in practice hierarchy
- Bounded concurrency for progress summary fan-out
- Documentation of production tuning settings

**Out of scope**:
- API contract changes or new endpoints
- Frontend changes
- Content hierarchy data model changes
- New database indexes or schema changes
- Changes to unlock rules, access control rules, or stats computation semantics
- Write-path optimizations (lesson completion, stage completion)

## Dependencies

- Existing per-worker in-process hierarchy cache (already implemented)
- Existing per-key stats recompute coalescing (already implemented)
- Existing environment-based configuration system for pool sizes and limits

## Risks

- **Stats-first correctness**: Deriving unlock state from precomputed stats must remain exactly consistent with current unlock rules. If stats lack any field needed for unlock decisions, the fallback path must engage.
- **Coalescing timeout tuning**: Too short a timeout wastes the optimization; too long risks user-visible delays. Needs production observation.
- **Full subject route limited benefit**: The full subject progress endpoint may not benefit as much as partial routes because it naturally needs most of the stats payload.
- **Concurrency cap latency trade-off**: Setting the progress summary cap too low can slightly increase latency for players with many subjects. The cap should be validated against real usage patterns.
