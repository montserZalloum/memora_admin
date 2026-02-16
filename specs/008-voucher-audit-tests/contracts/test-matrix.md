# Test-to-Requirement Traceability Matrix

**Phase 1 output** | **Date**: 2026-02-16

## Functional Requirements → Test Coverage

| FR | Requirement | Test File | Test Method(s) | Status |
|----|-------------|-----------|----------------|--------|
| FR-001 | Concurrent redemption → exactly one success via SELECT FOR UPDATE | `test_redemption_edge.py` | `test_already_redeemed_returns_error` | New |
| FR-002 | Roll back card if subscription fails | `test_security_audit.py` | `test_redemption_atomicity_gap` (documents gap as `# TODO: FIX`) | New |
| FR-003 | Validate player_id existence before redemption | `test_redemption_edge.py` | `test_invalid_pin_returns_error`, `test_preview_invalid_pin` | New |
| FR-004 | Partial grant ownership → redemption proceeds | `test_redemption_edge.py` | `test_partial_grant_ownership_allows_redemption`, `test_all_grants_owned_returns_error` | New |
| FR-005 | Empty product_grant_id → validation error | `test_redemption_edge.py` | `test_empty_grant_id_returns_error`, `test_grant_not_in_batch_returns_error` | New |
| FR-006 | Full allocation lifecycle test coverage | `test_allocation_flow.py` | 23 existing tests | Existing (phase 006) |
| FR-007 | Prepaid allocation invoice test | `test_allocation_flow.py` | `test_prepaid_creates_linked_sales_invoice` | Existing (phase 006) |
| FR-008 | Prepaid return credit note test | `test_invoice.py` | `test_credit_note_is_return_with_reference` | Existing (phase 004) |
| FR-009 | Batch voiding with mixed states | `test_voiding.py` | `test_void_batch_with_mixed_states`, `test_void_batch_requires_reason`, `test_void_draft_batch_raises_error`, `test_void_closed_batch_raises_error` | New |
| FR-010 | Single card voiding with auto-close | `test_voiding.py` | `test_void_available_card`, `test_void_allocated_card`, `test_void_redeemed_card_raises_error`, `test_void_card_triggers_auto_close` | New |
| FR-011 | Commission Decimal precision | `test_commission.py` | 11 existing tests | Existing (phase 004) |
| FR-012 | Batch counter accuracy | `test_counter_integrity.py` | `test_full_lifecycle_counter_accuracy`, `test_counters_after_void_batch` | New |
| FR-013 | Recount idempotency | `test_counter_integrity.py` | `test_recount_idempotency`, `test_auto_close_only_active_batches`, `test_auto_close_on_all_terminal_cards` | New |
| FR-014 | Allocation state machine (valid + invalid) | `test_allocation_flow.py` | `test_invalid_skip_transition_rejected`, `test_terminal_state_blocks_transitions` | Existing (phase 006) |
| FR-015 | Card state machine (valid + invalid) | `test_voiding.py` + `test_redemption_edge.py` | Various error code tests | New |
| FR-016 | Security gaps documented with `# TODO: SECURITY-FIX` | `test_security_audit.py` | `test_no_rate_limiting_on_redemption`, `test_any_user_can_redeem_for_other_player`, `test_season_check_fails_open_on_exception`, `test_reallocation_steals_cards_from_other_library`, `test_stale_cards_in_allocation_accepted` | New |
| FR-017 | HMAC secret absence during redemption | `test_security_audit.py` | `test_hmac_uses_timing_safe_comparison`, `test_hmac_secret_absent_redemption_behavior` | New |
| FR-018 | Voiding deletes encrypted export file | `test_voiding.py` | `test_void_batch_deletes_encrypted_file` | New |
| FR-019 | Batch auto-activation on first allocation | `test_allocation_flow.py` | `test_batch_transitions_generated_to_active` | Existing (phase 006) |
| FR-020 | Return allocation clears card fields | `test_allocation_flow.py` | `test_returned_cards_cleared_with_return_allocation` | Existing (phase 006) |

## Success Criteria → Test Coverage

| SC | Criterion | Met By |
|----|-----------|--------|
| SC-001 | 25+ new tests for uncovered edge cases | 30-39 new tests across 4 files |
| SC-002 | Allocation lifecycle ≥12 tests | 23 existing (phase 006) — already exceeded |
| SC-003 | Voiding ≥8 tests | 9 new tests in `test_voiding.py` |
| SC-004 | Security/fraud ≥6 tests | 7 new tests in `test_security_audit.py` |
| SC-005 | Financial Decimal precision ≥5 tests | 11 existing (phase 004) — already exceeded |
| SC-006 | Counter integrity ≥4 tests | 5 new tests in `test_counter_integrity.py` |
| SC-007 | All new tests <30s total | Small batches (10 cards), no threading |
| SC-008 | Zero test pollution | Each class uses setUpClass/tearDownClass with cleanup |
| SC-009 | Flaws 1-3, gaps 4-6 documented with TODO markers | 5 tests with `# TODO: SECURITY-FIX`, 2 with `# TODO: FIX` |

## Detected Logical Flaws → Test Coverage

| Flaw | Severity | Test | Marker |
|------|----------|------|--------|
| 1. No redemption atomicity | Critical | `test_security_audit.py::test_redemption_atomicity_gap` | `# TODO: FIX` |
| 2. No player ownership validation | Critical | `test_security_audit.py::test_any_user_can_redeem_for_other_player` | `# TODO: SECURITY-FIX` |
| 3. Season check fails open | Critical | `test_security_audit.py::test_season_check_fails_open_on_exception` | `# TODO: SECURITY-FIX` |
| 4. No rate limiting | High | `test_security_audit.py::test_no_rate_limiting_on_redemption` | `# TODO: SECURITY-FIX` |
| 5. Re-allocation steals cards | High | `test_security_audit.py::test_reallocation_steals_cards_from_other_library` | `# TODO: SECURITY-FIX` |
| 6. Stale cards in allocation | High | `test_security_audit.py::test_stale_cards_in_allocation_accepted` | `# TODO: FIX` |
| 7. Invoice failure silent | Medium | Covered by code review (not testable without mocking) | — |
| 8. Missing input validation | Medium | `test_redemption_edge.py::test_empty_grant_id_returns_error` | — |
| 9. No duplicate PIN detection | Medium | Covered by `test_generator.py::test_1000_pins_are_unique` (probabilistic) | — |
| 10. Export path traversal | Medium | Mitigated by Frappe file handling; not testable without attack vector | — |

## Constitution Principle Coverage

| Principle | Tests Verifying Compliance |
|-----------|--------------------------|
| I. Cryptographic Security | `test_hmac_uses_timing_safe_comparison`, `test_hmac_secret_absent_redemption_behavior`, all PIN-based tests |
| II. Auditable Lifecycle | All error code tests verify Redemption Log entries; `test_void_*` tests verify state transitions; counter integrity tests |
| III. Financial Precision | Existing phase 004 tests (11 commission + 8 invoice); no new financial tests needed |
| IV. Self-Healing Architecture | N/A (no Redis operations in test scope) |
| V. Test-First Coverage | This entire feature fulfills this principle |

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cryptographic Security | PASS | HMAC comparison and secret handling are tested |
| II. Auditable Lifecycle | PASS | All state transitions have positive + negative tests; Redemption Log immutability verified |
| III. Financial Precision | PASS | Already fully covered by phases 004; no duplication needed |
| IV. Self-Healing Architecture | N/A | No Redis operations |
| V. Test-First Coverage | PASS | 30-39 new tests close all identified coverage gaps |

**Re-check result**: PASS — design aligns with constitution.
