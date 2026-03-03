# Product Requirements Document: Progress & Practice Read-Path Performance

**Feature Name**: Progress and Practice Hierarchy Read-Path Performance  
**Proposed Feature Branch**: `036-progress-practice-read-perf`  
**Created**: March 3, 2026  
**Status**: Draft  

**Input**: Reduce latency and backend load for high-traffic `progress` and `practice/hierarchy` APIs by removing unnecessary bitmap work, reducing Redis payload size, preventing cache-miss stampedes, smoothing progress-summary fan-out, and enabling production tuning without changing API behavior.

## 1. Problem Statement

The current backend performs correctly, but several hot read paths still do more work than necessary under load:

- Progress endpoints decode full Redis bitmaps even when valid cached stats already exist.
- Partial progress endpoints fetch the full stats hash even when they only need a small subset of fields.
- Hierarchy and practice metadata cache misses can trigger duplicate concurrent Frappe calls for the same key.
- `practice/hierarchy` repeats subject-level access checks inside the per-track loop.
- Progress summary fans out across all accessible subjects using unbounded `asyncio.gather()`.
- Production scaling settings already exist, but operational gains are capped unless they are actively used in deployment.

These issues increase Redis work, CPU usage, and upstream Frappe pressure during peak traffic, especially when many users hit the same subjects simultaneously.

## 2. Current State

The following optimizations are already implemented and should be treated as baseline, not new scope:

- Per-worker in-process parsed hierarchy cache exists in `HierarchyService`.
- Per-key stats recompute coalescing already exists in `StatsService.get_or_recompute()`.

This PRD only covers the remaining read-path optimizations that are still missing.

## 3. Goals

- Reduce warm-path latency for `progress` and `practice/hierarchy` endpoints.
- Reduce Redis commands and payload size on partial progress routes.
- Prevent cache-miss stampedes from sending multiple identical Frappe calls for the same hierarchy/meta key.
- Prevent progress summary from over-fanning-out under users with many accessible subjects.
- Preserve existing API contracts and response shapes.
- Keep worst-case behavior no worse than today.

## 4. Non-Goals

- No API contract changes.
- No frontend schema changes.
- No Frappe hierarchy SQL redesign.
- No database schema changes or new indexes in this phase.
- No behavior changes to unlock rules, access rules, or stats semantics.

## 5. User Stories

### User Story 1: Warm Progress Reads Avoid Bitmap Decode

As a student opening progress screens during peak traffic, I want the backend to use cached stats first so that repeated reads do not decode the full progress bitmap unnecessarily.

**Why this matters**: Bitmap decode is the main steady-state CPU cost on progress reads.

**Acceptance Scenarios**

1. Given valid stats exist for a user and subject, when a progress detail endpoint is called, then the endpoint returns without decoding the full bitmap.
2. Given stats are missing or stale, when a progress detail endpoint is called, then the endpoint may fall back to the current bitmap-based path.
3. Given the optimized path is used, when compared with the current implementation, then worst-case behavior remains functionally identical.

### User Story 2: Partial Progress Routes Read Only Needed Stats Fields

As the backend serving partial progress screens, I want partial endpoints to fetch only the fields they need so Redis work scales with the response size.

**Why this matters**: `tracks`, `track detail`, and `unit detail` routes do not need the full stats hash.

**Acceptance Scenarios**

1. Given `GET /progress/{subject}/tracks`, when the route reads stats, then it fetches only track-level fields and validation fields required for correctness.
2. Given `GET /progress/{subject}/tracks/{track_id}`, when the route reads stats, then it fetches only the track and unit fields needed for that response.
3. Given `GET /progress/{subject}/tracks/{track_id}/units/{unit_id}`, when the route reads stats, then it fetches only the unit and topic fields needed for that response.
4. Given `GET /progress/{subject}`, when the full subject route runs, then it may continue using a full-hash read if that remains the simplest correct implementation.

### User Story 3: Cache Misses Coalesce for Hierarchy and Practice Metadata

As the platform under burst load, I want only one request per cache key to populate hierarchy or practice metadata so that cache misses do not stampede Frappe.

**Why this matters**: Repeated identical misses create unnecessary upstream load and latency spikes.

**Acceptance Scenarios**

1. Given multiple concurrent requests miss the same hierarchy cache key, when they arrive together, then only one request performs the upstream fill while others wait briefly.
2. Given multiple concurrent requests miss the same practice metadata cache key, when they arrive together, then only one request performs the upstream fill while others wait briefly.
3. Given the waiting request exceeds the configured wait timeout, when the timeout is reached, then the request degrades gracefully instead of blocking indefinitely.
4. Given a timeout occurs, when the request continues, then the system may permit bounded duplicate upstream work rather than failing the request.

### User Story 4: Practice Hierarchy Avoids Repeated Subject Access Checks

As the backend serving `practice/hierarchy`, I want subject-level access to be evaluated once per request so that the route does not repeat the same Redis checks for every track.

**Why this matters**: The current loop repeats subject-level work and adds unnecessary Redis traffic.

**Acceptance Scenarios**

1. Given a player requests practice hierarchy, when the request starts, then subject-level access is computed once before iterating tracks.
2. Given per-track grants must still be respected, when the loop runs, then only track-specific checks are evaluated per track.
3. Given the response is compared to current behavior, when the optimization is applied, then returned access flags and visible nodes remain unchanged.

### User Story 5: Progress Summary Uses Bounded Concurrency

As the backend serving progress summary, I want subject summaries to be fetched with bounded concurrency so that a player with many accessible subjects cannot fan out unlimited simultaneous work.

**Why this matters**: Unbounded `asyncio.gather()` can create avoidable Redis load bursts and event-loop pressure when accessible subject count grows.

**Acceptance Scenarios**

1. Given a player has many accessible subjects, when progress summary is requested, then the endpoint processes subject work with a concurrency cap instead of launching all tasks at once.
2. Given a reasonable cap (for example 5 to 8), when the endpoint runs under load, then Redis pressure is smoother than the current unbounded fan-out.
3. Given the concurrency cap is reached, when additional subject tasks are pending, then they wait briefly rather than being dropped.
4. Given a player has only a small number of accessible subjects, when the endpoint runs, then behavior remains functionally identical, with at most minor latency overhead from the scheduling guard.

### User Story 6: Production Tuning Can Be Activated Without Code Changes

As an operator, I want the shipped scaling settings to be sufficient for this optimization set so deployment can improve throughput without further code changes.

**Why this matters**: Some performance headroom is already available operationally.

**Acceptance Scenarios**

1. Given production values are applied from environment, when the service starts, then Redis pool and Frappe client limits reflect those values.
2. Given no code changes are made, when production env values are tuned, then the service can support higher concurrency before pool exhaustion or upstream contention.

## 6. Functional Requirements

- **FR-001**: Progress detail endpoints MUST use a stats-first read path.
- **FR-002**: The stats-first path MUST skip full bitmap decode when cached stats are present and valid.
- **FR-003**: If stats are missing, incomplete, or stale, the service MUST fall back to the existing safe computation path.
- **FR-004**: Partial progress endpoints MUST support targeted stats reads for only the fields required by that route.
- **FR-005**: Full subject progress MAY continue to use a full stats hash read if that remains the most practical correct implementation.
- **FR-006**: Hierarchy cache fills MUST use per-key cache-fill coalescing.
- **FR-007**: Practice hierarchy metadata cache fills MUST use per-key cache-fill coalescing.
- **FR-008**: Cache-fill coalescing MUST use a bounded wait timeout.
- **FR-009**: On cache-fill wait timeout, requests MUST degrade gracefully and MUST NOT wait indefinitely.
- **FR-010**: `practice/hierarchy` MUST compute subject-level access once per request, outside the per-track loop.
- **FR-011**: Per-track access decisions MUST remain functionally identical to current behavior.
- **FR-012**: Progress summary MUST use bounded concurrency for per-subject work instead of unbounded fan-out.
- **FR-013**: The progress-summary concurrency limit SHOULD be configurable or centrally defined, with an initial target range of 5 to 8 concurrent subject tasks.
- **FR-014**: The progress-summary cap MUST preserve response correctness for any number of accessible subjects.
- **FR-015**: The optimization set MUST preserve all current API response shapes and business rules.
- **FR-016**: Existing in-process hierarchy caching and stats per-key locks MUST remain intact and must not be removed or duplicated.
- **FR-017**: Production scaling settings already present in environment configuration MUST be documented as part of rollout for this feature.

## 7. Nice-to-Have Enhancements

- **NTH-001**: Practice metadata may add a small per-worker parsed in-memory cache to avoid repeated `json.loads()` on Redis hits, mirroring the existing hierarchy local-cache pattern.
- **NTH-002**: Progress summary may alternatively batch Redis reads with pipelining where that proves simpler or faster than semaphore-limited fan-out.

These are not required for the first delivery, but they are valid follow-ups if the initial optimizations are not sufficient.

## 8. Success Criteria

- **SC-001**: Warm reads on partial progress endpoints no longer decode the full bitmap when valid stats are available.
- **SC-002**: Partial progress endpoints reduce Redis payload size compared with current `HGETALL` behavior.
- **SC-003**: Concurrent misses for the same hierarchy key trigger at most one normal cache fill per worker during the common case.
- **SC-004**: Concurrent misses for the same practice metadata key trigger at most one normal cache fill per worker during the common case.
- **SC-005**: `practice/hierarchy` reduces repeated subject-access Redis checks from once per track to once per request.
- **SC-006**: Progress summary avoids unbounded per-subject task fan-out.
- **SC-007**: No endpoint response contracts change.
- **SC-008**: Existing tests for progress, hierarchy, stats, and practice continue to pass.
- **SC-009**: Production tuning can be applied entirely through environment variables already supported by the service.

## 9. Risks and Constraints

- Deriving unlock state from stats must remain exactly consistent with current unlock rules.
- Timeout behavior for cache-fill coalescing must avoid dead waits without creating unnecessary user-facing failures.
- Full `GET /progress/{subject}` may not benefit as much as partial routes because it naturally needs most of the stats payload.
- Progress-summary concurrency caps reduce burst pressure, but if set too low they can slightly increase latency for users with many subjects.
- Production env tuning improves capacity, but it does not replace read-path fixes.

## 10. Recommended Delivery Order

1. Implement stats-first logic for progress endpoints.
2. Add targeted stats reads for partial progress routes.
3. Add cache-fill coalescing with timeout for hierarchy and practice metadata.
4. Hoist subject-level access checks out of the `practice/hierarchy` track loop.
5. Add bounded concurrency for progress summary fan-out.
6. Apply production environment tuning in deployment.
7. Optionally add local parsed cache for practice metadata if needed after measurement.

## 11. Rollout Notes

- This should be shipped as an internal performance optimization release.
- No client changes are required, but frontend teams should prefer the lightweight progress routes over the full subject route where possible.
- Deployment should include production env values for Redis pool size and Frappe client limits.
- The initial progress-summary cap should be conservative (for example 5 to 8) and validated against real subject counts and latency metrics before increasing.
