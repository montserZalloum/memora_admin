# Feature Specification: Sync Task Tests

**Feature Branch**: `016-sync-task-tests`
**Created**: 2026-02-17
**Status**: Draft
**Input**: User description: "Phase 8: Sync task tests (NEW) — Create Frappe sync task test suite for sync_dirty_wallets, sync_dirty_progress, and flush_interaction_buffer"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Wallet Sync Task Verification (Priority: P1)

A developer needs confidence that `sync_dirty_wallets()` correctly persists Redis wallet state (XP, streak) to MariaDB Player Wallet records and properly cleans up the dirty set after successful syncs.

**Why this priority**: Wallet sync is the most critical sync task — incorrect syncing causes XP loss, which directly affects player experience and retention.

**Independent Test**: Can be fully tested by seeding Redis dirty set and wallet hash, running `sync_dirty_wallets()`, and verifying MariaDB records are updated and dirty set is cleaned up.

**Acceptance Scenarios**:

1. **Given** a player ID in the dirty wallets set and a corresponding wallet hash in Redis, **When** `sync_dirty_wallets()` runs, **Then** the Player Wallet record in MariaDB is updated with the Redis values and the player is removed from the dirty set.
2. **Given** three dirty players, **When** `sync_dirty_wallets()` runs, **Then** all three wallet records are updated and all three are removed from the dirty set.
3. **Given** an empty dirty wallets set, **When** `sync_dirty_wallets()` runs, **Then** no database operations occur and no errors are raised.
4. **Given** a player in the dirty set but no corresponding wallet hash in Redis, **When** `sync_dirty_wallets()` runs, **Then** the player is removed from the dirty set without error.
5. **Given** a player in the dirty set but no Player Wallet record in MariaDB, **When** `sync_dirty_wallets()` runs, **Then** a warning is logged and the player is removed from the dirty set.
6. **Given** three dirty players where one causes a database error, **When** `sync_dirty_wallets()` runs, **Then** the two successful players are synced and removed from dirty, while the failed player remains in the dirty set for retry.
7. **Given** a successful wallet sync, **When** the sync completes, **Then** the `dirty_flag` field on the Player Wallet record is set to `0`.
8. **Given** a successful wallet sync, **When** the sync completes, **Then** a Memora Sync Log document is created with `sync_type="Wallet"`.

---

### User Story 2 - Progress Sync Task Verification (Priority: P1)

A developer needs confidence that `sync_dirty_progress()` correctly reads Redis progress bitmaps, converts them to hex strings, and upserts Memora Structure Progress records in MariaDB.

**Why this priority**: Progress sync preserves lesson completion data — data loss means students re-do completed work.

**Independent Test**: Can be fully tested by seeding Redis dirty progress set and bitmap keys, running `sync_dirty_progress()`, and verifying Structure Progress records contain correct hex-encoded bitmaps.

**Acceptance Scenarios**:

1. **Given** a dirty progress member `"USER-001:SUBJ-001:v1"` with a bitmap in Redis, **When** `sync_dirty_progress()` runs, **Then** a Structure Progress record is created or updated with the hex-encoded bitmap and a completion percentage.
2. **Given** no existing Structure Progress record for the player/subject, **When** `sync_dirty_progress()` runs, **Then** a new record is inserted.
3. **Given** an existing Structure Progress record, **When** `sync_dirty_progress()` runs, **Then** the existing record is updated with the new bitmap hex.
4. **Given** a malformed dirty member (e.g., `"USER-001:SUBJ-001"` missing version), **When** `sync_dirty_progress()` runs, **Then** the malformed member is skipped with a warning log, and other valid members are still processed.
5. **Given** a dirty member but an all-zero (empty) bitmap in Redis, **When** `sync_dirty_progress()` runs, **Then** the record stores an empty hex string and 0% completion.

---

### User Story 3 - Interaction Buffer Flush Verification (Priority: P2)

A developer needs confidence that `flush_interaction_buffer()` correctly reads JSON interaction records from a Redis list, inserts them as Memora Interaction Log documents in MariaDB, and trims the processed entries from the buffer.

**Why this priority**: Interaction data is analytics/audit data — loss is concerning but less immediately visible to users than XP or progress loss.

**Independent Test**: Can be fully tested by pushing JSON items to the Redis interaction buffer list, running `flush_interaction_buffer()`, and verifying Interaction Log documents were created and the buffer was trimmed.

**Acceptance Scenarios**:

1. **Given** JSON items in the interaction buffer, **When** `flush_interaction_buffer()` runs, **Then** Interaction Log documents are created for each valid item and the buffer is trimmed.
2. **Given** an empty buffer, **When** `flush_interaction_buffer()` runs, **Then** no database operations occur and no errors are raised.
3. **Given** a non-JSON item in the buffer, **When** `flush_interaction_buffer()` runs, **Then** the invalid item is skipped, other valid items are still processed, and the buffer is trimmed based on the total fetched count.
4. **Given** an item missing required fields (`player` or `lesson`), **When** `flush_interaction_buffer()` runs, **Then** the incomplete item is skipped and other items are still processed.
5. **Given** a buffer with 1500 items, **When** `flush_interaction_buffer()` runs, **Then** only the first 1000 items are processed (batch size cap) and the remaining 500 stay in the buffer.
6. **Given** three items where one causes an insert error, **When** `flush_interaction_buffer()` runs, **Then** two items are inserted successfully, the LTRIM uses the `inserted` count (2), and the failed item's position relative to the trim boundary determines whether it stays for retry.

---

### Edge Cases

- What happens when Redis is unreachable during a sync task? The task raises an exception that is caught by the Frappe scheduler and logged via `frappe.log_error`.
- What happens when the dirty set contains duplicate entries? Redis sets inherently prevent duplicates, so this is a non-issue.
- What happens when timestamps in interaction data use various formats (ISO with Z, without Z, with/without milliseconds)? The `_parse_timestamp()` helper normalizes all formats to MariaDB-compatible `YYYY-MM-DD HH:MM:SS`.
- What happens when wallet Redis hash has byte-encoded keys vs string keys? The code handles both via dual `.get()` lookups.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Test suite MUST verify `sync_dirty_wallets()` correctly reads from Redis dirty set, fetches wallet hash data, and updates corresponding MariaDB Player Wallet records.
- **FR-002**: Test suite MUST verify `sync_dirty_progress()` correctly reads from Redis dirty set, fetches bitmap data, converts to hex, and upserts MariaDB Structure Progress records.
- **FR-003**: Test suite MUST verify `flush_interaction_buffer()` correctly reads from Redis list, parses JSON, inserts Interaction Log documents, and trims the buffer.
- **FR-004**: Test suite MUST verify all three sync tasks handle empty input gracefully (no errors on empty dirty set / empty buffer).
- **FR-005**: Test suite MUST verify partial failure behavior — successful items are persisted and removed from dirty set while failed items remain for retry.
- **FR-006**: Test suite MUST verify sync audit logging — each sync run creates a Memora Sync Log document with correct `sync_type`, count, and status.
- **FR-007**: Test suite MUST verify input validation — malformed dirty set members and invalid JSON in the buffer are skipped with appropriate warnings.
- **FR-008**: Test suite MUST verify the batch size cap (1000 items) for `flush_interaction_buffer()`.
- **FR-009**: All tests MUST run under `bench run-tests` using `FrappeTestCase` since the sync tasks import `frappe` directly.
- **FR-010**: Tests MUST use real Redis at `redis://127.0.0.1:13000` with key prefix isolation to avoid polluting production data.
- **FR-011**: Tests MUST clean up all Redis keys and MariaDB test records after each test to ensure isolation.

### Key Entities

- **Memora Player Wallet**: Stores player XP, streak, and sync metadata. Updated by `sync_dirty_wallets()`.
- **Memora Structure Progress**: Stores lesson completion bitmaps as hex strings per player/subject. Upserted by `sync_dirty_progress()`.
- **Memora Interaction Log**: Stores individual lesson/stage interaction events. Inserted by `flush_interaction_buffer()`.
- **Memora Sync Log**: Audit trail for sync runs — records sync_type, count, and status.
- **Redis Dirty Sets**: `memora:dirty:wallets` and `memora:dirty:progress` — sets of IDs pending sync to MariaDB.
- **Redis Interaction Buffer**: `memora:buffer:interactions` — list of JSON-encoded interaction events pending flush.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 19 tests (8 wallet + 5 progress + 6 interaction) pass under `bench run-tests` with zero failures.
- **SC-002**: Tests complete within 30 seconds total (sync tasks are fast database operations).
- **SC-003**: Tests achieve full coverage of happy path, empty input, partial failure, and input validation scenarios for all three sync tasks.
- **SC-004**: Tests do not leave residual data in Redis or MariaDB after completion (verified by cleanup assertions).
- **SC-005**: Existing Frappe tests continue to pass with no regressions after adding the new test files.
