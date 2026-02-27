# Feature Specification: Locust Load Test Suite

**Feature Branch**: `030-locust-load-tests`
**Created**: 2026-02-27
**Status**: Draft
**Input**: User description: "Load Testing with Locust — 100k User Simulation"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Basic Load Test Against FastAPI Sidecar (Priority: P1)

As a developer, I want to run a load test suite that simulates realistic player behavior against the FastAPI sidecar so I can validate that the system handles real concurrency without timeouts, connection pool exhaustion, or degraded response times.

**Why this priority**: This is the core purpose of the feature — without the ability to run a basic load test, nothing else matters. Validates the scaling optimizations introduced in the concurrency-scaling work.

**Independent Test**: Can be fully tested by running `locust --headless -u 10 -r 5 --run-time 30s` and verifying all 4 user types execute their flows and produce aggregated stats.

**Acceptance Scenarios**:

1. **Given** Locust is installed and the FastAPI sidecar is running, **When** a developer runs the load test with 10 users for 30 seconds, **Then** all 4 user behavior profiles execute their flows and produce per-endpoint stats output.
2. **Given** test player accounts exist in the system, **When** simulated users authenticate via the login endpoint, **Then** tokens are obtained and reused for all subsequent requests within each user lifecycle.
3. **Given** a load test is running, **When** the system returns rate-limited responses, **Then** those responses are treated as expected behavior (not counted as failures).

---

### User Story 2 - Simulate Realistic Traffic Distribution (Priority: P1)

As a developer, I want the load test to model actual production traffic patterns with weighted user behavior profiles so the test results reflect real-world usage, not synthetic benchmarks.

**Why this priority**: Unrealistic traffic distribution would produce misleading results. The 4 behavior profiles (Dashboard 40%, Lesson 35%, Browser 15%, Leaderboard 10%) mirror actual player behavior.

**Independent Test**: Can be tested by running a 2-minute test with 100 users and verifying that per-endpoint request counts roughly match the expected distribution.

**Acceptance Scenarios**:

1. **Given** a load test with 100+ users, **When** the test completes, **Then** dashboard-related endpoints account for approximately 40% of total requests.
2. **Given** a running load test, **When** lesson-player users execute their flow, **Then** each simulated lesson includes a realistic think time (3-10 seconds) between session start and session end.
3. **Given** a running load test, **When** browser users execute their flow, **Then** they drill down through the content hierarchy (subject -> tracks -> units) with appropriate pauses between requests.

---

### User Story 3 - Scale to 100k Simulated Users (Priority: P2)

As a developer, I want to progressively ramp up from 100 to 100,000 simulated users following a defined scaling ladder so I can identify the breaking point and validate the system meets its 100k concurrent user target.

**Why this priority**: The ultimate goal is 100k validation, but it requires the basic test suite (P1) to work first. The scaling ladder approach ensures issues are found and fixed incrementally.

**Independent Test**: Can be tested by running the 5-stage scaling ladder and checking that response times and error rates remain within acceptable thresholds at each stage.

**Acceptance Scenarios**:

1. **Given** the load test suite passes at Stage 1 (100 users), **When** the developer increases to Stage 2 (1,000 users), **Then** the system maintains p99 response time below 500ms and error rate below 1%.
2. **Given** the load test is running at 10,000+ users, **When** system resources are monitored, **Then** connection counts and memory usage do not grow unboundedly.
3. **Given** a completed load test run, **When** the developer checks the output, **Then** per-endpoint stats, failure breakdown, and time-series data are available for analysis.

---

### User Story 4 - Configure Test Data Without Code Changes (Priority: P2)

As a developer, I want test player accounts, subjects, and lessons to be defined in a separate configuration file so I can adapt the load test to different environments without modifying the test code.

**Why this priority**: Different environments have different test data. Separating config from code prevents accidental credential exposure and makes the suite portable.

**Independent Test**: Can be tested by modifying the configuration file to point to different player accounts and subjects, then running the load test and verifying it uses the new configuration.

**Acceptance Scenarios**:

1. **Given** test player accounts are defined in a config file, **When** a simulated user starts, **Then** it picks a random player from the configured pool for authentication.
2. **Given** test subjects and lessons are defined in a config file, **When** lesson and browser users execute their flows, **Then** they use only the configured subjects and lessons.
3. **Given** no real credentials are in the codebase, **When** the load test files are committed to version control, **Then** only placeholder/example values are present in the configuration.

---

### Edge Cases

- What happens when login is rate-limited during on_start? The user proceeds without a token and skips authenticated endpoints gracefully.
- What happens when a session expires before session-end is called? The response is treated as expected under heavy load, not as a test failure.
- What happens when the config file has no test players defined? The suite will fail at startup — this is acceptable (user must seed data first).
- What happens when the browser user receives an empty tracks list? The user flow stops drilling down and moves to the next iteration.
- What happens when all test players are rate-limited simultaneously? New user spawns will fail login but continue retrying on the next cycle — degraded but not stuck.
- What happens when the target server is not running? Connection errors are reported for all requests — visible in the stats output.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Suite MUST provide 4 distinct user behavior profiles with configurable weights: DashboardUser (40%), LessonPlayer (35%), BrowserUser (15%), LeaderboardChecker (10%).
- **FR-002**: Each simulated user MUST authenticate once on startup and reuse the token for all subsequent requests.
- **FR-003**: Simulated users MUST include realistic think times between requests (2-15 seconds depending on profile) to model human behavior.
- **FR-004**: The lesson player profile MUST simulate a full lesson flow: start session, think time (3-10s), end session with stage results, check wallet.
- **FR-005**: The browser user profile MUST drill down through the content hierarchy: subject overview, tracks, track detail, unit detail.
- **FR-006**: All parameterized endpoint paths MUST use aggregated request naming so stats are grouped by endpoint pattern, not by individual resource IDs.
- **FR-007**: Rate-limited responses (HTTP 429) MUST be treated as expected behavior, not test failures.
- **FR-008**: Session-expired responses on session end MUST be treated as expected under load.
- **FR-009**: Test player accounts, subjects, and lesson IDs MUST be defined in a Python configuration file (`config.py` with dicts/lists), not hardcoded in the test code. The config file is imported directly by Locust user classes.
- **FR-010**: Suite MUST support both interactive (web UI) mode and headless (CLI) mode with structured output.
- **FR-011**: Suite MUST NOT modify any production code or seed data directly into data stores.
- **FR-012**: Suite MUST include documentation with a recommended 5-stage scaling ladder: Stage 1 (100 users) → Stage 2 (1,000) → Stage 3 (10,000) → Stage 4 (50,000) → Stage 5 (100,000).
- **FR-013**: Suite MUST run on a single machine (single Locust master process). Documentation MUST include instructions for distributed mode (master + workers) for reaching 100k users, but the suite itself does not implement distributed orchestration.

### Key Entities

- **Test Player**: A pre-created player account with mobile number and password, used by simulated users for authentication.
- **User Behavior Profile**: A weighted user class that defines a specific pattern of endpoint usage (dashboard checking, lesson playing, content browsing, or leaderboard checking).
- **Load Test Run**: A single execution of the suite with a specific user count, spawn rate, and duration, producing stats and failure reports.
- **Think Time**: A randomized pause between requests within a user profile, modeling real human interaction speed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 4 user behavior profiles complete their flows successfully with 10 simulated users for 30 seconds (sanity check passes).
- **SC-002**: At 1,000 simulated users, p99 response time remains below 500ms for all endpoints.
- **SC-003**: At 10,000 simulated users, error rate (excluding expected rate-limited responses) stays below 1%.
- **SC-004**: At 100,000 simulated users, the system operates for 15 minutes without connection exhaustion, unbounded memory growth, or cascading failures.
- **SC-005**: Per-endpoint stats, failure breakdown, and time-series data are available as structured output after each headless run.
- **SC-006**: The load test suite can be configured for a new environment by editing a single configuration file.
- **SC-007**: Traffic distribution across user profiles matches the configured weights within a 10% margin at steady state.

## Clarifications

### Session 2026-02-27

- Q: Should the suite support distributed Locust mode (master/worker) for the 100k target? → A: Document distributed mode in README but don't build specific support into the suite.
- Q: What format should the test configuration file use? → A: Python file (`config.py` with dicts/lists), as specified in the PRD.
- Q: What are the 5 scaling ladder stages? → A: 100 → 1,000 → 10,000 → 50,000 → 100,000 (per PRD).

## Assumptions

- Test player accounts will be pre-created before running load tests (the suite does not auto-create them).
- The target server is running and accessible when tests execute.
- At least 3 test player accounts are needed for basic testing; 100-500 accounts are recommended for realistic 100k simulation (accounts are reused across virtual users).
- The authentication endpoint returns a token and player identifier in its response.
- The authentication header format includes a bearer token and device identifier.
- Locust is installed separately and is not a project dependency.
