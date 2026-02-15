# Voucher System — Test Suite + Gap Fixes

## Context

The voucher system is fully implemented (generation, allocation, redemption, invoicing, rate limiting, consignment billing, season expiration) but has **zero test coverage** — all 6 DocType test files are empty stubs. Additionally, two real gaps exist:

1. **`expire_season_cards()` doesn't update batch counters** — after expiring cards, `voided_count` and `allocated_count` are stale
2. **No batch auto-close** — when all cards reach terminal states, the batch stays Active forever

The ChatGPT plan had several false gaps (Allocation Card child table, `sales_invoice` custom field, Customer custom fields, rate limiting, consignment invoicing) that all **already exist**. This revised plan removes those and focuses on real work.

---

## Phase 1: Code Fixes (2 real gaps)

### 1A — Add `expired_count` field to Batch DocType + fix `expire_season_cards()`

**Why separate from `voided_count`**: Expired (season-based, automatic) and Void (manual, with reason) are semantically different. Separate counters enable better reporting.

**Files to modify:**
- `memora_admin/memora_admin/doctype/memora_voucher_batch/memora_voucher_batch.json` — add `expired_count` Int field (read_only, default 0) after `voided_count`
- `memora_admin/tasks/season_expiration.py` — after the SQL UPDATE on line 49-57, count expired cards per batch and update `expired_count`. Also recount `allocated_count` (some Allocated cards may have been expired).

**Implementation in `expire_season_cards()`** — after `affected = frappe.db.sql("SELECT ROW_COUNT()")[0][0]` (line 59), add:
```python
if affected:
    # Recount expired and allocated for this batch
    expired_count = frappe.db.count("Memora Voucher Card", {"batch": batch_name, "status": "Expired"})
    allocated_count = frappe.db.count("Memora Voucher Card", {"batch": batch_name, "status": "Allocated"})
    frappe.db.set_value("Memora Voucher Batch", batch_name, {
        "expired_count": expired_count,
        "allocated_count": allocated_count,
    }, update_modified=True)
```

### 1B — Implement batch auto-close

**Files to modify:**
- `memora_admin/memora_admin/api/voucher.py` — add `_maybe_close_batch(batch_name)` helper function. Wire into:
  - `redeem_voucher()` at line 689 (after `redeemed_count` update)
  - `void_card()` at line 356 (after `voided_count` update)
- `memora_admin/tasks/season_expiration.py` — call `_maybe_close_batch()` after counter update inside the batch loop

**`_maybe_close_batch()` logic:**
```python
def _maybe_close_batch(batch_name):
    """Auto-close batch when all cards reach terminal states."""
    batch_status = frappe.db.get_value("Memora Voucher Batch", batch_name, "status")
    if batch_status != "Active":
        return
    non_terminal = frappe.db.count(
        "Memora Voucher Card",
        {"batch": batch_name, "status": ["in", ("Available", "Allocated")]},
    )
    if non_terminal == 0:
        frappe.db.set_value("Memora Voucher Batch", batch_name, "status", "Closed", update_modified=True)
```

---

## Phase 2: Test Infrastructure

**Testing framework:** Frappe standard — `FrappeTestCase` + `bench run-tests`

### 2A — Shared fixture factory

**New file:** `memora_admin/memora_admin/tests/__init__.py` (empty)
**New file:** `memora_admin/memora_admin/tests/voucher_fixtures.py`

Factory functions (each returns the created doc):
- `make_batch(quantity=10, pin_length=12, face_value=5, grants=None, status="Draft")` — creates batch with Batch Grant children
- `make_product_grant(item_code, plan=None)` — creates Memora Product Grant (+ Academic Plan + Season if `plan` given)
- `make_season(start_date, end_date, is_published=1)` — creates Memora Season
- `make_customer(name, requires_approval=0, commission_type=None, commission_value=None)` — creates Customer with voucher custom fields
- `make_player(name)` — creates Memora Player Profile
- `make_allocation(batch, customer, allocation_type="Allocate", sale_model="Prepaid")` — creates allocation doc

### 2B — Shared test helpers

**New file:** `memora_admin/memora_admin/tests/voucher_helpers.py`

- `generate_batch_sync(batch_name)` — calls `generate_cards_job()` directly (bypasses queue)
- `get_card_statuses(batch_name)` — returns `{"Available": 5, "Allocated": 3, ...}`
- `fill_and_complete_allocation(allocation)` — drives allocation through full workflow
- `redeem_card_by_pin(pin, hmac_secret, player_id, grant_id, ip="127.0.0.1")` — computes HMAC, calls `redeem_voucher`
- `assert_batch_counters(test_case, batch_name, **expected)` — asserts counter fields match

### 2C — Ensure test prerequisites

- Verify `voucher_hmac_secret` is set in test site config
- Verify `MEMORA-VOUCHER-CARD` Item exists (created by `setup.py`)

---

## Phase 3: Unit Tests — Crypto & Generator (~18 tests)

**File:** `memora_admin/memora_admin/services/voucher/test_generator.py`

| Test | What it verifies |
|------|-----------------|
| `test_pin_length_default` | Default PIN is 12 chars |
| `test_pin_length_custom` | Respects 14, 16 |
| `test_pin_alphabet_only` | No ambiguous chars (0, O, 1, I, L) |
| `test_pin_uniqueness` | 1000 PINs are all unique |
| `test_hmac_deterministic` | Same PIN + secret → same hash |
| `test_hmac_different_pins` | Different PINs → different hashes |
| `test_hmac_different_secrets` | Different secrets → different hashes |
| `test_hmac_hex_format` | Output is 64-char hex (SHA-256) |
| `test_reserve_first_block` | Starts at VCH-000001 (or next available) |
| `test_reserve_contiguous` | Two sequential calls produce contiguous ranges |
| `test_reserve_format` | Serials match `VCH-NNNNNN` 6-digit zero-padded |
| `test_reserve_count` | Returns exactly N serials |
| `test_csv_headers` | First row is `serial_no,pin,product_names,face_value` |
| `test_csv_row_count` | N cards → N+1 rows |
| `test_csv_content_matches` | Serial/pin values match input |

**File:** `memora_admin/memora_admin/services/voucher/test_crypto.py`

| Test | What it verifies |
|------|-----------------|
| `test_encrypt_decrypt_roundtrip` | `decrypt(encrypt(data)) == data` |
| `test_encrypted_differs_from_plaintext` | Encrypted bytes != original |
| `test_wrong_secret_fails` | Decrypt with different secret raises error |

---

## Phase 4: Unit Tests — Commission & Invoice (~18 tests)

**File:** `memora_admin/memora_admin/services/voucher/test_commission.py`

| Test | What it verifies |
|------|-----------------|
| `test_no_commission` | None/empty → full face value, zero commission |
| `test_percentage_commission` | 10% of 5.00 = 0.50 commission, 4.50 net |
| `test_fixed_commission` | Fixed 1.00 → 1.00 commission, 4.00 net |
| `test_decimal_precision` | No float rounding errors (33.33% of 10.00) |
| `test_quantity_multiplication` | `net_total = net_per_card × quantity` |
| `test_zero_face_value` | face_value=0 → all zeros |
| `test_unknown_commission_type` | Unrecognized type → zero commission |
| `test_resolve_priority_grant_override` | Grant-level commission takes precedence |
| `test_resolve_priority_library_default` | Falls to Customer fields when no grant override |
| `test_resolve_priority_zero` | No commission anywhere → (None, None) |

**File:** `memora_admin/memora_admin/services/voucher/test_invoice.py`

| Test | What it verifies |
|------|-----------------|
| `test_invoice_created_and_submitted` | Sales Invoice docstatus=1 |
| `test_invoice_customer_correct` | Customer matches library |
| `test_invoice_item_code` | Uses MEMORA-VOUCHER-CARD |
| `test_invoice_amount` | Rate matches net_per_card, qty matches count |
| `test_credit_note_is_return` | is_return=1, return_against set |
| `test_credit_note_negative_qty` | Qty is negated |
| `test_credit_note_submitted` | docstatus=1 |
| `test_prepaid_invoice_full_flow` | Allocation → commission → invoice → linked |

---

## Phase 5: Integration Tests — Batch Lifecycle (~14 tests)

**File:** `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py` (populate existing stub)

| Test | What it verifies |
|------|-----------------|
| `test_generate_creates_cards` | Draft → generate → N cards with status=Available |
| `test_generate_status_transition` | Batch status Draft → Generated |
| `test_generate_counters` | `generated_count = quantity`, others = 0 |
| `test_generate_encrypted_file` | `encrypted_file_url` is set, file exists |
| `test_generate_serial_format` | All cards have VCH-NNNNNN serial |
| `test_generate_hmac_stored` | All cards have pin_hmac, no plaintext PIN in DB |
| `test_generate_non_draft_fails` | Generated batch → ValidationError |
| `test_generate_zero_quantity_fails` | quantity=0 → error |
| `test_generate_exceeds_max_fails` | quantity=1001 → error |
| `test_generate_no_hmac_secret_fails` | Missing site config → error |
| `test_export_decrypts_correctly` | CSV matches generated cards |
| `test_export_audit_logged` | `export_log` child table gets new row |
| `test_generate_already_generated_fails` | Can't generate twice |
| `test_generate_rollback_on_failure` | No partial cards on failure |

---

## Phase 6: Integration Tests — Allocation Flow (~22 tests)

**File:** `memora_admin/memora_admin/doctype/memora_voucher_allocation/test_memora_voucher_allocation.py` (populate existing stub)

| Test | What it verifies |
|------|-----------------|
| `test_fill_allocate_gets_available_cards` | Fills from Available cards |
| `test_fill_return_gets_allocated_cards` | Fills from Allocated cards of library |
| `test_fill_quantity_limit` | Respects quantity parameter |
| `test_fill_non_draft_fails` | Can't fill on non-Draft |
| `test_submit_auto_approve` | Library without approval → Draft → Completed |
| `test_submit_requires_approval` | Library with flag → Pending Approval |
| `test_submit_no_cards_fails` | Empty allocation → error |
| `test_submit_mismatched_batch_fails` | Cards from wrong batch → error |
| `test_approve_pending` | Pending Approval → Completed |
| `test_reject_pending` | Pending Approval → Rejected |
| `test_approve_non_pending_fails` | Can't approve Draft/Completed |
| `test_cards_become_allocated` | Card status → Allocated |
| `test_cards_assigned_library` | card.library = allocation.customer |
| `test_cards_assigned_sale_model` | card.sale_model matches allocation |
| `test_batch_allocated_count_updated` | Batch counter reflects reality |
| `test_batch_activated_on_first_allocation` | Generated → Active on first completion |
| `test_prepaid_allocation_creates_invoice` | Sales Invoice created and submitted |
| `test_prepaid_invoice_amount_with_commission` | Correct net after commission |
| `test_prepaid_invoice_linked_to_allocation` | allocation.sales_invoice set |
| `test_invalid_transition_draft_to_completed` | Can't skip steps |
| `test_invalid_transition_completed_to_draft` | Terminal state enforced |
| `test_return_cards_become_available` | Allocated → Available |

---

## Phase 7: Integration Tests — Redemption Flow (~22 tests)

**File:** `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py` (populate existing stub — redemption tests here since they center on card state)

| Test | What it verifies |
|------|-----------------|
| `test_preview_valid_pin` | Returns face_value + available grants |
| `test_preview_filters_owned_grants` | Already-owned grants excluded |
| `test_preview_all_owned_returns_error` | ALL_GRANTS_OWNED error |
| `test_redeem_success` | Card → Redeemed, subscription transaction created |
| `test_redeem_creates_subscription_transaction` | TRX exists with Completed status |
| `test_redeem_card_fields_set` | redeemed_by, redeemed_at, redeemed_grant, subscription_transaction |
| `test_redeem_batch_counter_updated` | redeemed_count incremented |
| `test_redeem_log_success` | Redemption Log with status=Success |
| `test_redeem_invalid_pin` | Wrong HMAC → INVALID_PIN |
| `test_redeem_not_allocated` | Available card → NOT_ALLOCATED |
| `test_redeem_already_redeemed` | Redeemed card → ALREADY_REDEEMED |
| `test_redeem_expired_card` | Expired card → EXPIRED |
| `test_redeem_void_card` | Void card → VOID |
| `test_redeem_batch_inactive` | Non-Active batch → BATCH_INACTIVE |
| `test_redeem_season_inactive` | Ended season → SEASON_INACTIVE |
| `test_redeem_grant_not_in_batch` | Wrong grant → GRANT_NOT_IN_BATCH |
| `test_redeem_already_owned` | Player owns grant → ALREADY_OWNED |
| `test_every_error_code_logged` | Each error creates correct Redemption Log |
| `test_log_pin_masked` | Only last 4 chars prefixed with **** |
| `test_log_ip_address_stored` | IP captured |
| `test_timing_safe_comparison` | Code review: `hmac.compare_digest` used |
| `test_redeem_auto_closes_batch` | Last card redeemed → batch Closed (tests P1B fix) |

---

## Phase 8: Integration Tests — Return, Void & Expiration (~20 tests)

**File:** `memora_admin/memora_admin/tests/test_return_void.py`

| Test | What it verifies |
|------|-----------------|
| `test_return_cards_become_available` | Allocated → Available |
| `test_return_clears_library_and_allocation` | Fields nulled |
| `test_return_sets_return_allocation` | return_allocation set |
| `test_return_batch_counters_updated` | allocated_count decremented |
| `test_prepaid_return_creates_credit_note` | Credit Note created |
| `test_return_credit_note_groups_by_invoice` | One CN per original invoice |
| `test_void_available_card` | Available → Void with reason |
| `test_void_allocated_card` | Allocated → Void with reason |
| `test_void_redeemed_fails` | Can't void Redeemed card |
| `test_void_requires_reason` | Empty reason → error |
| `test_void_updates_batch_counter` | voided_count incremented |
| `test_void_auto_closes_batch` | Last non-terminal voided → batch Closed (tests P1B fix) |
| `test_void_batch_voids_all_non_terminal` | Available + Allocated → Void |
| `test_void_batch_preserves_terminal` | Redeemed cards untouched |
| `test_void_batch_closes_batch` | Batch → Closed |

**File:** `memora_admin/memora_admin/tests/test_season_expiration.py`

| Test | What it verifies |
|------|-----------------|
| `test_expire_cards_ended_season` | Cards with past end_date → Expired |
| `test_expire_only_non_terminal` | Redeemed/Void unaffected |
| `test_expire_sets_void_reason` | void_reason = "Season Ended" |
| `test_expire_updates_counters` | `expired_count` updated (tests P1A fix) |
| `test_expire_active_season_untouched` | Future end_date cards not expired |

---

## Phase 9: Integration Tests — Existing Features (~12 tests)

**File:** `memora_admin/memora_admin/tests/test_consignment_billing.py`

| Test | What it verifies |
|------|-----------------|
| `test_monthly_invoice_created` | Invoice exists for previous month's redeemed consignment cards |
| `test_monthly_invoice_groups_by_library` | One invoice per library |
| `test_monthly_invoice_commission_applied` | Net amount after commission |
| `test_monthly_invoice_marks_cards` | Cards get sales_invoice to prevent double-invoicing |
| `test_monthly_invoice_skips_already_invoiced` | No double invoicing |
| `test_monthly_invoice_no_cards_noop` | No cards → no invoice created |

**File:** `memora_admin/memora_admin/tests/test_batch_auto_close.py`

| Test | What it verifies |
|------|-----------------|
| `test_batch_auto_closes_all_redeemed` | All redeemed → Closed |
| `test_batch_auto_closes_mixed_terminal` | Redeemed + Void + Expired → Closed |
| `test_batch_stays_open_with_available` | Some Available → stays Active |
| `test_batch_stays_open_with_allocated` | Some Allocated → stays Active |
| `test_auto_close_after_season_expiration` | Expire last cards → Closed |
| `test_auto_close_only_active_batches` | Generated batch unaffected |

---

## Phase 10: Hardening & Regression (~18 tests)

**File:** `memora_admin/memora_admin/tests/test_regression.py`

| Test | What it verifies |
|------|-----------------|
| `test_batch_counters_consistent_after_lifecycle` | Generate → allocate → redeem → void → counters match |
| `test_counter_recount_vs_stored` | SQL COUNT matches stored counters |
| `test_batch_with_one_card` | Minimum batch works end-to-end |
| `test_batch_with_max_cards` | 1000 cards generates correctly |
| `test_multiple_grants_per_batch` | 3+ grants, different selections |
| `test_reallocation_of_returned_cards` | Returned cards → re-allocated to different library |
| `test_card_redeemed_for_each_grant` | Different cards, different grants, same batch |
| `test_export_after_partial_void` | Export still works after some cards voided |
| `test_complete_prepaid_lifecycle` | Draft → Generate → Allocate(Prepaid) → Invoice → Redeem → Closed |
| `test_complete_consignment_lifecycle` | Draft → Generate → Allocate(Consignment) → Redeem → Monthly Invoice → Closed |
| `test_complete_return_lifecycle` | Allocate → Return → Credit Note → Re-allocate → Redeem |

---

## Execution Order

```
P1 (Code Fixes) → P2 (Test Infra) → P3 (Unit: Crypto) ──┐
                                    → P4 (Unit: Commission) ─┤
                                                              ├→ P5 (Batch) → P6 (Allocation) → P7 (Redemption)
                                                              │                                       │
                                                              │                    ┌──────────────────┤
                                                              │                    ▼                  ▼
                                                              │              P8 (Return/Void)   P9 (Existing)
                                                              │                    │                  │
                                                              │                    └────────┬─────────┘
                                                              │                             ▼
                                                              └──────────────────────► P10 (Regression)
```

## File Summary

| File | Action | Phase |
|------|--------|-------|
| `memora_admin/memora_admin/doctype/memora_voucher_batch/memora_voucher_batch.json` | Add `expired_count` field | P1 |
| `memora_admin/tasks/season_expiration.py` | Add counter update + auto-close call | P1 |
| `memora_admin/memora_admin/api/voucher.py` | Add `_maybe_close_batch()`, wire into redeem + void | P1 |
| `memora_admin/memora_admin/tests/__init__.py` | Create (empty) | P2 |
| `memora_admin/memora_admin/tests/voucher_fixtures.py` | Create fixture factory | P2 |
| `memora_admin/memora_admin/tests/voucher_helpers.py` | Create test helpers | P2 |
| `memora_admin/memora_admin/services/voucher/test_generator.py` | Create | P3 |
| `memora_admin/memora_admin/services/voucher/test_crypto.py` | Create | P3 |
| `memora_admin/memora_admin/services/voucher/test_commission.py` | Create | P4 |
| `memora_admin/memora_admin/services/voucher/test_invoice.py` | Create | P4 |
| `memora_admin/memora_admin/doctype/memora_voucher_batch/test_memora_voucher_batch.py` | Populate stub | P5 |
| `memora_admin/memora_admin/doctype/memora_voucher_allocation/test_memora_voucher_allocation.py` | Populate stub | P6 |
| `memora_admin/memora_admin/doctype/memora_voucher_card/test_memora_voucher_card.py` | Populate stub | P7 |
| `memora_admin/memora_admin/tests/test_return_void.py` | Create | P8 |
| `memora_admin/memora_admin/tests/test_season_expiration.py` | Create | P8 |
| `memora_admin/memora_admin/tests/test_consignment_billing.py` | Create | P9 |
| `memora_admin/memora_admin/tests/test_batch_auto_close.py` | Create | P9 |
| `memora_admin/memora_admin/tests/test_regression.py` | Create | P10 |

**Total: ~145 tests across 18 files**

## Verification

After each phase:
1. `bench run-tests --app memora_admin --module memora_admin.memora_admin.<test_module>` for the specific test file
2. After all phases: `bench run-tests --app memora_admin` for full suite
3. Manual verification of auto-close: generate batch → allocate all → redeem all → confirm batch status = Closed
4. Manual verification of expired_count: create batch with ended season → run `expire_season_cards()` → confirm `expired_count` updated
