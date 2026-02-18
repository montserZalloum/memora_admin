# Research: Fix Export For Print Includes Redeemed Cards

**Date**: 2026-02-18 | **Branch**: `020-fix-export-redeemed-cards`

## Root Cause Analysis

### Current Flow (Buggy)

1. `generate_cards_job()` (voucher.py:83) creates all cards and builds a CSV containing **every** card's `serial_no` + plaintext `pin`
2. The CSV is encrypted via Fernet and stored on disk as `{batch_name}_export.enc`
3. `export_for_print()` (voucher.py:230) decrypts the stored file and serves it **as-is**
4. No filtering is applied — redeemed, voided, allocated, and expired cards are all included

### Why PINs Are Only in the Encrypted File

- PINs are stored as HMAC-SHA256 hashes (`pin_hmac`) in `tabMemora Voucher Card` — plaintext is never in the DB
- The only source of plaintext PINs is the encrypted export file generated at batch creation time
- This is by design per Constitution Principle V (Cryptographic Voucher Security)

## Solution Decision

### Decision: Filter at Export Time

**Approach**: Decrypt the master CSV → parse rows → query DB for Available serial_nos → filter CSV rows → rebuild and serve filtered CSV.

**Rationale**:
- The encrypted file must remain as-is (it's the only source of plaintext PINs)
- Regenerating PINs is impossible (they're one-way HMAC'd in the DB)
- Filtering at download time is O(n) where n ≤ 1000 (max batch size) — trivially fast
- No schema changes required
- No new dependencies needed

### Alternatives Considered

| Alternative | Why Rejected |
|-------------|--------------|
| Regenerate encrypted file on each card status change | Over-engineered; would require re-encryption on every redemption/void — adds latency to hot paths |
| Store plaintext PINs in DB | Violates Constitution Principle V — PINs must never be persisted in the database |
| Mark rows in encrypted file | Would require decrypt→modify→re-encrypt on every status change; file corruption risk |

## Implementation Details

### Filtering Logic (voucher.py:export_for_print)

1. Decrypt the full CSV (existing code)
2. Parse CSV into rows using `csv.DictReader`
3. Query DB: `SELECT serial_no FROM tabMemora Voucher Card WHERE batch = %s AND status = 'Available'`
4. Build a set of available serial numbers for O(1) lookup
5. Filter CSV rows: only include rows where `serial_no` is in the available set
6. Rebuild CSV with `csv.writer` (same format: `serial_no, pin, product_names, face_value`)
7. If zero rows remain, raise `frappe.throw("No available cards to export.")`
8. Log actual exported count (not `batch.generated_count`)
9. Serve filtered CSV

### Performance Analysis

- Max batch size: 1,000 cards (hard limit in `MAX_BATCH_QUANTITY`)
- Decrypt: ~1ms (Fernet, 1KB CSV for 1000 cards)
- CSV parse: ~1ms
- DB query: ~1ms (indexed by `batch` + `status`)
- Set intersection: ~0.01ms
- CSV rebuild: ~1ms
- **Total overhead**: ~4ms additional vs current flow — well within 5s target (SC-004)

### Impact on Test Helper

`voucher_helpers.py:get_pins_from_export()` returns **all** PINs from the export. After this fix, it will only return PINs for Available cards. This is the **correct** behavior for test helpers used in redemption flows — you can only redeem Available cards. No test changes needed.

### Impact on Existing Tests

- `test_export_decrypts_correctly` (test_memora_voucher_batch.py:139): Tests a freshly generated batch where all 10 cards are Available. **No change** — all 10 rows will still appear.
- `test_export_audit_logged` (test_memora_voucher_batch.py:177): Tests that an export_log entry is created. **Needs update** — card_count will now reflect available count (still 10 for fresh batch, so actually no change).

## Constitution Compliance

| Principle | Status | Notes |
|-----------|--------|-------|
| V. Cryptographic Voucher Security | Compliant | PIN plaintext still only in encrypted file; no DB storage of plaintext |
| VII. Auditable State Machines | Compliant | Export log now records accurate card_count |
| VIII. Test-First Coverage | Required | New tests needed for filtered export behavior |
