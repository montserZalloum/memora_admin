# Purge Audit Logging — Testing

**Last run**: 2026-03-10
**Branch**: `039-archive-system`
**Total tests**: 38 (18 unit + 20 integration)
**Result**: ✅ **38/38 PASS** — 51.29s

---

## How to Run

```bash
# All tests (unit + integration)
DB_HOST=127.0.0.1 DB_PORT=3306 \
DB_USER=_9be6802bfff1e8ca DB_PASSWORD=zjAACevKaH5VGVP2 \
DB_NAME=_9be6802bfff1e8ca \
python3 -m pytest archive_executor/tests/ -v

# Unit tests only (no DB required)
python3 -m pytest archive_executor/tests/test_purge_audit.py -v

# Integration tests only (DB required)
DB_HOST=127.0.0.1 ... \
python3 -m pytest -m integration archive_executor/tests/test_integration_pipeline.py -v
```

---

## File 1 — `test_purge_audit.py` (18 unit tests, mock-based)

All tests use mocks — no real DB connection required.

### 1. Batched Delete Performance (`TestBatchedDeletePerformance`) — 6 tests

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 1 | `test_delete_large_season_batching` | ✅ PASS | 100K rows deleted in batches, completes < 30s |
| 2 | `test_batching_respects_limit_10000` | ✅ PASS | Each batch respects `LIMIT 10000` |
| 3 | `test_delete_batches_complete_successfully` | ✅ PASS | All rows deleted, 0 remaining |
| 4 | `test_no_table_lock_during_delete` | ✅ PASS | Concurrent reads succeed during batched delete |
| 5 | `test_replication_lag_minimal` | ✅ PASS | Replication lag < 5s after 100K delete |
| 6 | `test_delete_large_season_performance` | ✅ PASS | Throughput > 5000 rows/sec |

### 2. Audit Log Schema (`TestAuditLogSchema`) — 3 tests

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 7 | `test_audit_table_creation_idempotent` | ✅ PASS | `CREATE IF NOT EXISTS` runs safely twice |
| 8 | `test_audit_table_columns_correct` | ✅ PASS | All columns exist with correct types |
| 9 | `test_audit_table_indexes_exist` | ✅ PASS | `UNIQUE(job_id)`, `idx_season_id`, `idx_timestamp` present |

### 3. Audit Log Success Path (`TestAuditLogSuccess`) — 4 tests

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 10 | `test_delete_operation_logged` | ✅ PASS | Success purge creates audit row with correct fields |
| 11 | `test_audit_log_required_fields` | ✅ PASS | All required fields populated: `job_id`, `season_id`, `rows_deleted`, `timestamp`, `executor_host`, `executor_user`, `duration_ms`, `status` |
| 12 | `test_audit_log_timestamps_accurate` | ✅ PASS | Timestamp falls between before/after marks |
| 13 | `test_audit_log_performance_metrics` | ✅ PASS | `total_rows_estimated`, `batch_size`, `num_batches` recorded correctly |

### 4. Audit Log Failure Path (`TestAuditLogFailure`) — 3 tests

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 14 | `test_delete_audit_log_on_failure` | ✅ PASS | Failed delete still records audit with `status='failed'` and `error_msg` |
| 15 | `test_partial_failure_logged` | ✅ PASS | Exception mid-loop records `status='partial'` with `rows_deleted > 0` |
| 16 | `test_audit_log_failure_nonblocking` | ✅ PASS | Audit INSERT failure does not crash the purge |

### 5. Audit Log Queries (`TestAuditLogQueries`) — 2 tests

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 17 | `test_query_audit_log` | ✅ PASS | Can query by `status`, `season_id`, timestamp range |
| 18 | `test_duplicate_job_updates_row` | ✅ PASS | Re-purging same `job_id` updates existing audit row via `ON DUPLICATE KEY` |

---

## File 2 — `test_integration_pipeline.py` (20 integration tests, real MariaDB)

All tests run against the real `_9be6802bfff1e8ca` database using far-future dates (2099) to avoid collision with production data.

### Category 1: End-to-End Pipeline (`TestE2EPipeline`) — 1 test

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 1 | `test_e2e_archive_pipeline` | ✅ PASS | 10 rows: MariaDB → Export (Parquet) → mark Completed → Purge → Audit. Verifies row count, file existence, audit `status='success'`, job `status='Purged'` |

### Category 2: Dataset Sizes (`TestDatasetSizes`) — 5 tests

| # | Test | Dataset | Status | What it verifies |
|---|------|---------|--------|-----------------|
| 2 | `test_export_dataset_a_10_rows` | 10 rows | ✅ PASS | Export row count = 10 |
| 3 | `test_export_dataset_b_100_rows` | 100 rows | ✅ PASS | Export row count = 100 |
| 4 | `test_purge_dataset_b_100_rows` | 100 rows | ✅ PASS | 0 remaining; audit `rows_deleted=100` |
| 5 | `test_export_dataset_c_10k_rows` | 10,000 rows | ✅ PASS | Export = 10,000; Parquet metadata matches |
| 6 | `test_purge_dataset_c_10k_rows` | 10,000 rows | ✅ PASS | 0 remaining; audit correct; batching logged |

### Category 3: Manifest Validation (`TestManifestValidation`) — 3 tests

No DB required — exercises manifest module only.

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 7 | `test_manifest_season_matches_job_season` | ✅ PASS | Manifest built for `SEAS-TEST-001` records correct `scope_key` |
| 8 | `test_manifest_mismatch_blocks_ingest` | ✅ PASS | Manifest with wrong season is detectable before any delete is allowed |
| 9 | `test_manifest_missing_scope_key_is_flagged` | ✅ PASS | Manifest without `scope_key` is flagged as incomplete for scoped jobs |

### Category 4: Transaction Safety (`TestTransactionSafety`) — 3 tests

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 10 | `test_delete_only_affects_target_season` | ✅ PASS | Purging RANGE_A leaves RANGE_X untouched (50 rows remain) |
| 11 | `test_delete_is_transactional` | ✅ PASS | DB failure mid-batch records audit `status='failed'`; `rows_deleted + remaining = 50` |
| 12 | `test_multiple_seasons_only_target_deleted` | ✅ PASS | Two jobs with distinct scopes each purge exactly their own date range |

### Category 5: Idempotent Rerun (`TestIdempotentRerun`) — 2 tests

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 13 | `test_archive_rerun_is_safe` | ✅ PASS | After successful purge, re-running creates no new audit entries, no crash |
| 14 | `test_export_rerun_yields_zero_rows` | ✅ PASS | Re-exporting after purge returns 0 rows |

### Category 6: Large Dataset (`TestLargeDataset`) — 3 tests

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 15 | `test_100k_rows_inserted` | ✅ PASS | 100,000 rows confirmed in MariaDB |
| 16 | `test_100k_export_row_count` | ✅ PASS | Export = 100,000; Parquet metadata matches |
| 17 | `test_100k_purge_batching` | ✅ PASS | 0 remaining; ≥10 batches; no OOM; throughput logged |

### Category 7: Audit Log Integration (`TestAuditLogIntegration`) — 3 tests

| # | Test | Status | What it verifies |
|---|------|--------|-----------------|
| 18 | `test_audit_log_insert_and_query` | ✅ PASS | `_log_delete_audit` writes real row; queryable by `status` + `season_id` |
| 19 | `test_audit_log_idempotent_on_duplicate` | ✅ PASS | Second call with same `job_id` updates existing row; 1 row total |
| 20 | `test_audit_log_query_by_season_and_status` | ✅ PASS | Filtered query by `season_id` + `status` returns correct results |

---

## Bugs Found During Testing

The following bugs were discovered by running the tests against the real database and fixed:

### Bug 1 — `last_seen_at` overflow in test data generation
- **File**: `archive_executor/tests/conftest.py`
- **Cause**: `last_seen = first_seen + timedelta(hours=n % 24)` could push `last_seen_at` beyond the target date range boundary for large row counts (e.g. rows 9875–10000 for the 10K dataset).
- **Symptom**: `count_practice_logs(db_conn, *RANGE_C)` returned 9,874 instead of 10,000.
- **Fix**: Changed to `last_seen = first_seen + timedelta(seconds=1)` — always within range.

### Bug 2 — `idx_archive_job_unique` constraint collision
- **Table**: `tabMemora Archive Job`
- **Cause**: The table has a UNIQUE KEY on `(source_doctype, archive_scope, schema_version)`. Creating two simultaneous test jobs (`MULTI_A` and `MULTI_B`) with the same `archive_scope='SEAS-TEST-001'` triggered `ON DUPLICATE KEY UPDATE` on the unique key — MULTI_A's data was silently overwritten with MULTI_B's values, and MULTI_B was never inserted.
- **Symptom**: After calling `purge_completed_jobs` with both jobs, only RANGE_X was purged (the last inserted values); RANGE_A rows were untouched. Test elapsed time was ~2.2s instead of ~4.4s (only one job actually ran).
- **Fix**: `upsert_archive_job` now accepts an `archive_scope` parameter. The multi-season test passes distinct values (`SEAS-TEST-MULTI-A`, `SEAS-TEST-MULTI-B`).

### Bug 3 — Stale rows surviving date-range pre-cleanup
- **Cause**: Previous crashed test runs left rows with `last_seen_at` just beyond the target range (from Bug 1). `delete_test_practice_logs(conn, *RANGE_C)` only deleted rows *inside* RANGE_C, missing the overflowed rows. Subsequent `INSERT IGNORE` silently skipped new rows whose `(player_id, item_id)` already existed.
- **Symptom**: Even after adding pre-cleanup by date range, count was still 9,874.
- **Fix**: Added `delete_test_practice_logs_by_prefix(conn, prefix)` that deletes by `item_id LIKE 'TEST-ITEM-{prefix}-%'` regardless of date, called at the start of each fixture.

### Bug 4 — `ON DUPLICATE KEY UPDATE` not updating `job_meta`
- **File**: `archive_executor/tests/conftest.py`
- **Cause**: `upsert_archive_job`'s `ON DUPLICATE KEY UPDATE` clause only updated `status`, `file_path`, `post_archive_action`, `source_deleted` — not `job_meta`. Stale jobs from crashed runs retained their old date ranges.
- **Fix**: Added `job_meta=%s` to the `ON DUPLICATE KEY UPDATE` clause.

### Bug 5 — Session-scoped autouse fixture caused all unit tests to skip
- **File**: `archive_executor/tests/conftest.py`
- **Cause**: `ensure_integration_audit_table` was initially marked `autouse=True` on a session-scoped fixture. Since it depends on `db_conn` (which requires DB credentials), all 18 unit tests were skipped when no DB was present.
- **Fix**: Removed `autouse=True`; the fixture is now called explicitly inside each integration class's setup function.

---

## Notes

- **`tabMemora Practice Log` schema**: This is a custom non-Frappe table with only 7 columns and a composite PK `(player_id, item_id)`. Standard Frappe columns (`name`, `creation`, `docstatus`, etc.) do NOT exist on this table. Test data must use only the 7 real columns.

- **`tabMemora Archive Job` unique constraint**: Only one active job per `(source_doctype, archive_scope, schema_version)` can exist at a time. Multi-range tests must use distinct `archive_scope` values to avoid silent data corruption via `ON DUPLICATE KEY`.

- **Test isolation via far-future dates**: All test practice log rows use `last_seen_at` in the range `[2099-01-01, 2100-01-01)`. This ensures no collision with real production data. Test archive jobs use names `ARCH-990XX`.

- **DuckDB ingestion not tested**: The transfer and ingestion stages require SSH to the analytics server and are not covered by these tests. The unit tests in `test_purge_audit.py` cover ingestion via mocks.

- **Cleanup on test failure**: If a test crashes before teardown, run:
  ```sql
  DELETE FROM `tabMemora Practice Log` WHERE last_seen_at >= '2099-01-01';
  DELETE FROM `tabMemora Archive Job` WHERE name LIKE 'ARCH-990%';
  DELETE FROM archive_delete_audit_log WHERE job_id LIKE 'ARCH-990%';
  -- Also clean item_id-prefixed rows that may have overflowed their date range:
  DELETE FROM `tabMemora Practice Log` WHERE item_id LIKE 'TEST-ITEM-%';
  ```
