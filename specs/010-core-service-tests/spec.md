# Feature Specification: Core Service Tests (Phase 2)

**Feature Branch**: `010-core-service-tests`
**Created**: 2026-02-17
**Status**: Draft
**Input**: Phase 2: Core Service Tests (~30 tests, 3 files) from FASTAPI_TEST_PLAN.md

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Verify Access Control Integrity (Priority: P1)

As a developer, I need to verify that the AccessService correctly manages player access grants and revocations so that access control rules are enforced without bugs in production.

**Why this priority**: Access control is the security boundary that prevents unauthorized content access. Any bug here allows unauthorized users to access paid content or denies legitimate users access. This is the highest security concern.

**Independent Test**: Full AccessService test suite can be run independently. Tests verify SADD/SREM operations, plan-based fallbacks, and hydration from Frappe.

**Acceptance Scenarios**:

1. **Given** a player with no access, **When** granting access to a subject, **Then** the key is added to the Redis access set and the method returns the count of new keys
2. **Given** a player with access, **When** granting the same subject again, **Then** the method returns 0 (idempotent)
3. **Given** a player with access, **When** revoking access, **Then** the key is removed from the Redis set
4. **Given** a purchased plan with free subjects, **When** checking access for a free subject, **Then** access is granted even without explicit grants
5. **Given** Redis cache is empty, **When** checking access, **Then** the service hydrates from Frappe and populates the cache
6. **Given** no FrappeClient injected, **When** hydration is needed, **Then** hydration is skipped gracefully with a warning log

---

### User Story 2 - Verify Progress Tracking Accuracy (Priority: P1)

As a developer, I need to verify that the ProgressService correctly tracks which lessons have been completed so that player progress is accurately recorded and never lost.

**Why this priority**: Progress tracking is critical to the student experience. Inaccurate progress breaks the learning journey, misleads students, and corrupts analytics. This is equally important as access control.

**Independent Test**: Full ProgressService test suite can be run independently, testing lesson completion, replay detection, bitmap operations, and dirty tracking.

**Acceptance Scenarios**:

1. **Given** a lesson that hasn't been completed, **When** marking it complete, **Then** the progress bitmap bit is set and the method returns False (indicating first completion, not replay)
2. **Given** a lesson that was already completed, **When** completing again, **Then** the method returns True (replay detected)
3. **Given** a lesson completion, **When** recording it, **Then** the progress is marked dirty for eventual sync to MariaDB
4. **Given** multiple completed lessons, **When** counting completed items, **Then** BITCOUNT returns the correct count
5. **Given** Redis cache is empty, **When** fetching progress, **Then** the service hydrates from Frappe using the hex-encoded bitmap
6. **Given** progress completion operations, **When** Redis contains the bitmap, **Then** GETBIT returns accurate completion status

---

### User Story 3 - Verify Wallet/XP Management Correctness (Priority: P1)

As a developer, I need to verify that the WalletService correctly tracks XP, streaks, and marks dirty wallets for sync so that player gamification rewards are accurate and persistent.

**Why this priority**: XP and streaks drive engagement. Inaccurate XP awards undermine motivation. Streak bugs break the habit-forming cycle. Data loss causes player frustration.

**Independent Test**: Full WalletService test suite can be run independently, including XP awards, streak logic via Lua scripts, and hydration.

**Acceptance Scenarios**:

1. **Given** a player wallet, **When** awarding XP, **Then** the total is incremented and the player is added to the dirty set
2. **Given** first lesson completion with no prior streak, **When** updating streak via Lua, **Then** streak is set to 1
3. **Given** a streak from yesterday, **When** completing a lesson today, **Then** streak increments atomically via Lua script
4. **Given** a streak from 2 days ago, **When** completing a lesson today, **Then** streak resets to 1 (missed day)
5. **Given** a completion on the same day, **When** updating streak, **Then** no change occurs and was_updated returns False
6. **Given** a replay completion, **When** updating streak with is_replay flag, **Then** streak remains unchanged via Lua conditional
7. **Given** Redis wallet is empty, **When** fetching wallet, **Then** the service calls Frappe to hydrate total_xp and current_streak
8. **Given** wallet data in Redis, **When** calling hydrate again, **Then** no Frappe call is made (no redundant hydration)

### Edge Cases

- **Access Service**: What happens when check_access_with_plan is called with a subject that exists in the plan's free_subjects set but also has an explicit grant? (Explicit grant takes priority)
- **Progress Service**: What happens if SETBIT is called on a lesson index beyond typical bounds? (Redis handles gracefully)
- **Wallet Service**: What happens if streak_date comparison uses different date formats in Lua vs Redis? (Dates must use consistent format)
- **Hydration Failure**: What if Frappe returns malformed data (missing fields, wrong types) during hydration? (Should be handled gracefully with logging)
- **Dirty Tracking**: What if a service is marked dirty but never synced? (Should not crash, sync task handles eventually)
- **Prefix Isolation**: What happens if Redis operations use wrong key prefix? (Tests use test_prefix isolation to verify correct prefixing)

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

#### AccessService Requirements

- **FR-AS-001**: System MUST support `grant_access(player_id, keys)` which SADDs keys to player's access set and returns count of new keys added
- **FR-AS-002**: System MUST support `revoke_access(player_id, keys)` which SREMs keys from access set
- **FR-AS-003**: System MUST support `check_access(player_id, key)` which returns True if key exists in access set via SISMEMBER
- **FR-AS-004**: System MUST support `check_access_with_plan(player_id, key)` which returns True if explicit grant OR plan's free subjects contain key
- **FR-AS-005**: System MUST support `ensure_hydrated(player_id)` which calls Frappe `get_player_access_keys` if Redis set is missing and populates the set
- **FR-AS-006**: System MUST handle missing FrappeClient gracefully by skipping hydration and logging a warning
- **FR-AS-007**: System MUST support `key_prefix` parameter in constructor for test isolation

#### ProgressService Requirements

- **FR-PS-001**: System MUST support `complete_lesson(user, subject, lesson_id)` which SETBIT on the progress bitmap and returns whether the bit was previously set (replay detection)
- **FR-PS-002**: System MUST support `is_complete(user, subject, lesson_id)` which GETBIT on the progress bitmap
- **FR-PS-003**: System MUST support `get_completed_count(user, subject)` which BITCOUNT the bitmap
- **FR-PS-004**: System MUST mark completed lessons as dirty by adding `user:subject:vX` to dirty set for eventual sync
- **FR-PS-005**: System MUST support `ensure_hydrated(user, subject, version)` which calls Frappe `get_player_progress` if Redis bitmap is missing and restores via SETRANGE with hex data
- **FR-PS-006**: System MUST use consistent Redis key naming: `{prefix}progress:{user}:{subject}:v{version}`
- **FR-PS-007**: System MUST handle version numbers consistently in dirty tracking format

#### WalletService Requirements

- **FR-WS-001**: System MUST support `award_xp(player_id, amount)` which HINCRBY the xp field and adds player to dirty set
- **FR-WS-002**: System MUST support `get_wallet(player_id)` which HGETALL the wallet hash, returning xp and streak with defaults (xp: 0, streak: 0)
- **FR-WS-003**: System MUST support `update_streak(player_id, is_replay)` which executes Lua script for atomic streak updates based on date comparison
- **FR-WS-004**: System MUST implement Lua script logic: if is_replay=True, return current streak unchanged; if today matches streak_date, no change; if yesterday, increment; if earlier, reset to 1
- **FR-WS-005**: System MUST use Amman timezone dates (ARGV[1]=today, ARGV[2]=yesterday) for streak date comparisons
- **FR-WS-006**: System MUST support `ensure_hydrated(player_id)` which calls Frappe `get_player_wallet` if Redis hash is missing and seeds HSET with total_xp and current_streak
- **FR-WS-007**: System MUST mark wallet updates as dirty only when was_updated=True
- **FR-WS-008**: System MUST use consistent Redis hash fields: xp, streak, streak_date

### Key Entities *(include if feature involves data)*

- **AccessService**: Manages player access grants and plan-based free content access. Key Redis resource: `{prefix}access:{player_id}` (set), `{prefix}plan:{plan_id}:free_subjects` (set). Source of truth: Memora Player Subscription in MariaDB.

- **ProgressService**: Tracks which lessons have been completed via bitmap. Key Redis resource: `{prefix}progress:{user}:{subject}:v{version}` (bitmap/string). Source of truth: Memora Structure Progress in MariaDB.

- **WalletService**: Manages player XP and streak data. Key Redis resources: `{prefix}wallet:{player_id}` (hash with xp, streak, streak_date fields), `memora:dirty:wallets` (set of player_ids). Source of truth: Memora Player Profile in MariaDB.

- **FrappeClient**: HTTP client that makes calls to Frappe backend. Used by services for hydration and API interactions. Must be injected via dependency injection.

- **Dirty Tracking Keys**: `memora:dirty:progress` (set of `"user:subject:vX"` entries), `memora:dirty:wallets` (set of player_ids). Used to track which data needs syncing to MariaDB.

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: All 11 AccessService tests MUST pass, covering grant, revoke, check_access, check_access_with_plan, and hydration scenarios
- **SC-002**: All 8 ProgressService tests MUST pass, covering lesson completion, replay detection, bitmap operations, and hydration
- **SC-003**: All 12 WalletService tests MUST pass, covering XP awards, streak logic, Lua script execution, and hydration
- **SC-004**: 100% of test scenarios MUST execute successfully without flaky failures (tests pass consistently on multiple runs)
- **SC-005**: All hydration paths MUST work correctly both with FrappeClient present and when missing
- **SC-006**: Redis key prefix isolation MUST be enforced - no test should pollute another test's data
- **SC-007**: Lua scripts (e.g., STREAK_UPDATE_SCRIPT) MUST execute atomically and produce correct results in all branching scenarios
- **SC-008**: All dirty tracking operations MUST correctly mark records for eventual sync without false positives/negatives

### Coverage Metrics

- **SC-009**: AccessService test coverage MUST include all public methods (grant_access, revoke_access, check_access, check_access_with_plan, ensure_hydrated)
- **SC-010**: ProgressService test coverage MUST include bitmap SETBIT/GETBIT/BITCOUNT operations and hex hydration
- **SC-011**: WalletService test coverage MUST include XP operations, all streak scenarios (first completion, consecutive, missed day, same day, replay), and both hydration paths
- **SC-012**: Edge cases MUST be tested: idempotent operations, missing data, empty results, and error conditions

---

## Assumptions *(document reasonable defaults)*

- **Assumption 1**: FrappeClient will be injected via FastAPI dependency injection. Tests mock this via conftest fixtures.
- **Assumption 2**: Redis is available at production URL (`redis://127.0.0.1:13000`) and uses prefix isolation for test data. Tests never use FLUSHDB.
- **Assumption 3**: Hydration calls are successful if Frappe returns expected data shape. Tests will mock success cases.
- **Assumption 4**: Date operations use Amman timezone. Tests use `wallet.get_amman_today()` and `get_amman_yesterday()` utilities.
- **Assumption 5**: Lua scripts are atomic and will execute on Redis without network partition during test execution.
- **Assumption 6**: Each test is independent and can run in any order due to prefix-isolated Redis keys.
- **Assumption 7**: conftest.py fixtures (redis_client, test_prefix, mock_frappe, cleanup_keys) are already implemented from Phase 1.

---

## Technical Context *(from FASTAPI_TEST_PLAN.md)*

### Infrastructure

These tests run via `python -m pytest` in `fastapi_app/tests/` directory with these key fixtures from conftest.py:

- `redis_client`: Real Redis at `redis://127.0.0.1:13000` with prefix isolation
- `test_prefix`: Unique `test:{uuid}:` prefix for each test
- `mock_frappe`: Mocked FrappeClient with AsyncMock
- `cleanup_keys`: Auto-cleanup fixture that SCAN+DEL test keys after each test

### Service Constructor Patterns

All services accept:
- `redis_client` (required): Redis connection
- `key_prefix` (optional, default="memora:"): For test isolation
- `frappe_client` (optional, default=None): For hydration calls

### Redis Key Naming Conventions

- `{prefix}access:{player_id}` - Access grants set
- `{prefix}progress:{user}:{subject}:v{version}` - Lesson bitmap
- `{prefix}wallet:{player_id}` - Player wallet hash
- `memora:dirty:progress` - Progress entries needing sync
- `memora:dirty:wallets` - Wallets needing sync
- `{prefix}plan:{plan_id}:free_subjects` - Plan's free content

---

## Notes

- This specification focuses on **testing the three core services** that manage access control, progress tracking, and gamification rewards. These are foundational services used by all other FastAPI endpoints.
- Tests use real Redis with prefix isolation rather than mocking Redis, ensuring tests verify actual Redis behavior.
- Lua scripts are a critical part of wallet service (streak logic). Tests must validate both Lua correctness and integration with Redis.
- Hydration pattern is used throughout to ensure cache resilience: if Redis data is missing, it's rebuilt from Frappe on first access.
- No implementation details are included here - the spec focuses on **what** each service must do, not **how** to implement it.
