# Research: Batch Lifecycle Integration Tests

**Feature**: 005-batch-lifecycle-tests
**Date**: 2026-02-15

## R1: Test Target Functions and Their Validation Paths

**Decision**: Tests will target two distinct API layers:
- **Guard rail tests** (FR-007 through FR-010, FR-013): Call `generate_batch()` which performs validation before enqueueing
- **Happy path tests** (FR-001 through FR-006): Call `generate_cards_job()` directly via `generate_batch_sync()` helper (bypasses queue)
- **Export tests** (FR-011, FR-012): Call `export_for_print()` directly
- **Rollback test** (FR-014): Call `generate_cards_job()` with a monkeypatched failure

**Rationale**: `generate_batch()` contains all input validation (`status != "Draft"`, `quantity <= 0`, `quantity > MAX_BATCH_QUANTITY`, missing HMAC secret). `generate_cards_job()` performs the actual card creation. The existing `generate_batch_sync()` helper already calls `generate_cards_job()` directly.

**Alternatives considered**:
- Testing only through `generate_batch()` — rejected because it enqueues async work that can't be awaited in a test
- Mocking the queue — unnecessary complexity since `generate_cards_job()` is the real unit of work

## R2: Export Function Test Approach

**Decision**: The `export_for_print()` test needs the `System Manager` role set for `frappe.session.user` and the encrypted file to exist on disk. The function reads the file from disk (not DB), decrypts it, and serves it via `frappe.local.response`.

**Rationale**: The export function:
1. Checks `System Manager` role
2. Reads `encrypted_file_url` from batch
3. Opens file from site path
4. Decrypts with `decrypt_data()`
5. Appends to `export_log` child table
6. Sets `frappe.local.response.filecontent`

The test should verify the CSV content via `frappe.local.response.filecontent` and the audit log via batch reload.

**Alternatives considered**:
- Calling `decrypt_data()` directly on the file — rejected because it skips audit log validation
- Testing at the HTTP level — rejected because FrappeTestCase doesn't set up a full HTTP server

## R3: HMAC Secret Removal for Missing-Config Test

**Decision**: For `test_generate_no_hmac_secret_fails`, temporarily remove `voucher_hmac_secret` from `frappe.conf` during the test, then restore it in cleanup. Use `frappe.conf.voucher_hmac_secret` attribute manipulation.

**Rationale**: The `generate_batch()` function reads `frappe.conf.get("voucher_hmac_secret")`. Temporarily setting it to `None` or empty string simulates the missing-config scenario.

**Alternatives considered**:
- Using `unittest.mock.patch` — possible but less direct than attribute manipulation for Frappe's config object
- Creating a separate site config — too complex for a single test

## R4: Rollback Test Strategy

**Decision**: For `test_generate_rollback_on_failure`, monkeypatch `frappe.db.bulk_insert` to raise an exception after the serial block is reserved but before cards are inserted. Verify that the `except` block in `generate_cards_job()` calls `frappe.db.rollback()`.

**Rationale**: The `generate_cards_job()` function wraps all operations in a try/except that calls `frappe.db.rollback()` on failure. The most natural failure point is `bulk_insert` since it's after serial reservation but before any state changes are committed.

**Alternatives considered**:
- Causing a real DB error (e.g., invalid column) — fragile and depends on DB internals
- Patching `create_encrypted_export` — also viable but `bulk_insert` is earlier in the pipeline

## R5: Card Schema — No Plaintext PIN Column

**Decision**: The `test_generate_hmac_stored` test should verify:
1. `pin_hmac` is a non-empty string on every card
2. The card DocType schema has NO `pin` field (confirmed: only `pin_hmac` exists)
3. No column named `pin` exists in `tabMemora Voucher Card` table

**Rationale**: The card schema (from DocType JSON) confirms only `pin_hmac` exists as a Data field. There is no `pin` field. The plaintext PIN exists only transiently in memory during generation and in the encrypted export file.

## R6: Existing Test Season and Fixtures

**Decision**: All tests will use `season="SEAS-00027"` when creating product grants, consistent with established test infrastructure pattern from Phase 2.

**Rationale**: Creating new seasons triggers MySQL partitioning constraints (documented in CLAUDE.md). The existing season `SEAS-00027` is the standard test fixture.

## R7: Test Isolation Strategy

**Decision**: Each test method creates its own batch and grant. FrappeTestCase handles DB rollback between tests automatically. No explicit cleanup needed.

**Rationale**: FrappeTestCase wraps each test in a savepoint that rolls back after the test. This provides natural isolation. The existing quickstart tests (Phase 2) confirm this pattern works.

## R8: Export File Path Verification

**Decision**: For `test_generate_encrypted_file`, verify that `batch.encrypted_file_url` is set AND that the file exists on disk using `os.path.exists(frappe.get_site_path(url.lstrip("/")))`.

**Rationale**: `generate_cards_job()` creates a File doc and sets `encrypted_file_url` to the `file_url` from the File doc. The file content is written to disk by Frappe's file handling. Checking both the field and the file ensures the full chain works.
