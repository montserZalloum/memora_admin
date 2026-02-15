# Research: Redemption Flow Tests

**Feature**: 007-redemption-flow-tests | **Date**: 2026-02-15

## R1: PIN Retrieval for Test Assertions

**Question**: How do tests obtain plaintext PINs from generated cards for redemption testing?

**Decision**: Add a `get_pins_from_export(batch_name)` helper that calls `export_for_print()`, parses the CSV from `frappe.local.response.filecontent`, and returns a `dict[str, str]` mapping `serial_no → plaintext PIN`.

**Rationale**: The existing `test_memora_voucher_batch.py` already uses this pattern in `test_export_decrypts_correctly()` (line 139). The helper centralizes the CSV parsing logic. PINs only exist in the encrypted export — the database only stores HMAC hashes.

**Alternatives considered**:
- Direct `decrypt_data()` on encrypted file bytes → more fragile, requires knowing file path format
- Store PINs in a test-only side table → violates Constitution Principle I (no plaintext persistence)

**Implementation**:
```python
def get_pins_from_export(batch_name: str) -> dict[str, str]:
    """Extract serial_no → plaintext PIN mapping from batch export."""
    frappe.set_user("Administrator")  # export requires System Manager
    export_for_print(batch_name)
    csv_content = frappe.local.response.filecontent
    if isinstance(csv_content, bytes):
        csv_content = csv_content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(csv_content))
    return {row["serial_no"]: row["pin"] for row in reader}
```

## R2: Grant Components Gap in make_product_grant()

**Question**: The existing `make_product_grant()` fixture doesn't add `grant_components` child rows. The `get_grant_keys()` function returns `[]` for such grants. Since `all([])` is `True` in Python, `redeem_voucher()` will always return `ALREADY_OWNED` for grants without components.

**Decision**: Enhance `make_product_grant()` to accept an optional `grant_components` parameter (list of `{"target_doctype": str, "target_name": str}` dicts). When provided, these are added as `Memora Grant Component` child rows.

**Rationale**: Redemption tests require grants with actual components so `get_grant_keys()` returns non-empty lists. Without this fix, `all([])` → True → every redemption returns `ALREADY_OWNED`.

**Alternatives considered**:
- Create grant components in each test → too verbose, violates DRY
- Monkey-patch `get_grant_keys()` → fragile, doesn't test the real code path

**Impact**: Backward compatible — existing tests that don't pass `grant_components` continue to work unchanged. Only redemption tests use the new parameter.

**Dependency**: Tests need a `Memora Subject` document to exist as the `target_name` for grant components. The test must either create one or use an existing one in the DB.

## R3: Preview Helper Function

**Question**: The existing `redeem_card_by_pin()` helper computes HMAC from plaintext PIN for redemption. No equivalent exists for `preview_voucher()`.

**Decision**: Add a `preview_card_by_pin(pin, player_id)` helper that computes HMAC and calls `preview_voucher()`.

**Rationale**: Follows the same pattern as `redeem_card_by_pin()`. Avoids duplicating HMAC computation in every preview test.

**Implementation**:
```python
def preview_card_by_pin(pin: str, player_id: str) -> dict:
    """Preview a voucher card using plaintext PIN."""
    hmac_secret = frappe.conf.get("voucher_hmac_secret")
    pin_hmac = compute_hmac(pin, hmac_secret)
    return preview_voucher(pin_hmac=pin_hmac, player_id=player_id)
```

## R4: SEASON_INACTIVE Test Strategy

**Question**: How to test the `SEASON_INACTIVE` error path without creating a new season (which triggers MySQL partitioning constraints)?

**Decision**: Temporarily modify the existing season's `end_date` to a past date, execute the redemption, assert the error, then restore the original end_date. Use a try/finally block to ensure cleanup.

**Rationale**: The spec assumption (line 136) explicitly states: "Tests for `SEASON_INACTIVE` will modify the season's end date to simulate expiration rather than creating a new expired season."

**Risk mitigation**: The `finally` block restores the original date to prevent test pollution. Tests run sequentially within the same class, so concurrent modification isn't an issue.

## R5: ALREADY_OWNED Test Strategy

**Question**: How to set up a player who already owns a specific grant's access keys?

**Decision**: Create a `Memora Player Subscription` record directly via `frappe.get_doc()` for the player with the relevant `access_key` (matching what `get_grant_keys()` returns for the grant). This simulates a prior redemption.

**Rationale**: Creating the subscription record directly is faster than performing a full redemption flow just for setup. The ALREADY_OWNED check only queries `Memora Player Subscription` records.

## R6: Timing-Safe Comparison Verification (FR-011)

**Question**: How to verify that `hmac.compare_digest()` is used without a runtime timing attack test?

**Decision**: Use `inspect.getsource()` to read the source code of `redeem_voucher()` and assert that `hmac.compare_digest` or `compare_digest` appears in the source text.

**Rationale**: The spec assumption (line 137) states: "The timing-safe comparison test (FR-011) is a code-level assertion (inspecting source) rather than a runtime timing attack test." Source inspection is deterministic and doesn't require timing measurements.

## R7: Test Independence and Isolation

**Question**: How to ensure 22 tests don't interfere with each other when sharing a database?

**Decision**: Use `setUpClass()` for shared expensive setup (batch generation, allocation) and per-test `setUp()` for lightweight card selection. Each test uses a different allocated card from the batch to avoid state conflicts.

**Rationale**: Generating a batch takes ~1s. With 22 tests, generating per-test would blow past the 60s budget. A shared batch with per-test card selection balances isolation and speed.

**Card allocation strategy**:
- Create batch with sufficient quantity (e.g., 30 cards)
- Allocate all cards in `setUpClass()`
- Each test picks a unique card by index or by querying for "Allocated" status
- Tests that modify card state (redemption) consume one card each

## R8: Redemption Log Query Strategy

**Question**: How to query for the specific log entry created by a single test when multiple tests create logs?

**Decision**: Query by both `player` and `card` fields, and filter by `timestamp` >= test start time. Since each test uses a unique card, the `card` filter is sufficient for most cases. For INVALID_PIN tests (where card is NULL in the log), filter by `pin_masked` value.

**Rationale**: The Redemption Log is immutable and append-only. Filtering by card name uniquely identifies the log entry for that test's redemption attempt.

## R9: Subject DocType for Grant Components

**Question**: A `Memora Grant Component` needs `target_doctype="Memora Subject"` and `target_name=<subject_name>`. Does a subject exist in the test DB?

**Decision**: Create a minimal `Memora Subject` in `setUpClass()` if one doesn't exist, or query for an existing one. The subject doesn't need tracks/units/topics — only its `name` field is used as the `target_name` in grant components.

**Rationale**: The grant key format is `SUB-{target_name}`. The actual subject content doesn't matter for redemption tests — only the access key string matters for ownership checks.
