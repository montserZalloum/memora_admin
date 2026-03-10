# Archive Pipeline Integration Test Report

**Date:** 2026-03-10
**Branch:** 039-archive-system
**Test suite:** `archive_executor/tests/test_integration_pipeline.py`

---

## Summary

| Metric                  | Dataset A | Dataset B | Dataset C  | Dataset D (Large) |
|-------------------------|-----------|-----------|------------|-------------------|
| Rows Inserted (MariaDB) | 10        | 100       | 10,000     | 100,000           |
| Rows Exported (Parquet) | 10        | 100       | 10,000     | 100,000           |
| Rows Ingested (DuckDB)  | N/A*      | N/A*      | N/A*       | N/A*              |
| Rows Deleted (Purge)    | 10        | 100       | 10,000     | 100,000           |
| Export Duration (s)     | <1        | <1        | ~2         | ~15               |
| Purge Duration (s)      | ~2        | ~2        | ~4         | ~30               |
| Purge Batches           | 1         | 1         | 1          | 10                |
| Audit Log Status        | success   | success   | success    | success           |

> \* DuckDB ingestion requires SSH to analytics server; not tested in local integration tests.
>   Unit tests in `test_purge_audit.py` cover the purge+audit path end-to-end with mocks.

---

## How to Run

### Prerequisites

```bash
# Required environment variables
export DB_HOST=127.0.0.1
export DB_PORT=3306
export DB_USER=frappe
export DB_PASSWORD=<password>
export DB_NAME=<database_name>
```

### Step 1 — Setup Test Data (SQL Script)

Run the standalone SQL setup script to insert test data directly into MariaDB:

```bash
mysql -h $DB_HOST -u $DB_USER -p"$DB_PASSWORD" $DB_NAME \
    < archive_executor/tests/sql/setup_test_data.sql
```

Expected output:
```
| status                          |
|---------------------------------|
| Dataset A inserted: 10 rows     |
| Dataset B inserted: 100 rows    |
| Dataset C inserted: 10000 rows  |
```

### Step 2 — Run Unit Tests (mock-based, no DB required)

```bash
cd /path/to/memora_admin
python -m pytest archive_executor/tests/test_purge_audit.py -v
```

Expected: **18 tests pass**

### Step 3 — Run Integration Tests (real DB required)

```bash
python -m pytest -m integration \
    archive_executor/tests/test_integration_pipeline.py \
    -v --tb=short
```

### Step 4 — Run Large Dataset Test Only

```bash
python -m pytest -m integration \
    archive_executor/tests/test_integration_pipeline.py::TestLargeDataset \
    -v -s
```

---

## Test Categories

### Category 1: End-to-End Pipeline (`TestE2EPipeline`)

| Test | Description | Expected Result |
|------|-------------|-----------------|
| `test_e2e_archive_pipeline` | Insert 10 rows → export → purge → audit | All 10 rows deleted; 1 audit row with status=success |

### Category 2: Dataset Sizes (`TestDatasetSizes`)

| Test | Dataset | Expected Result |
|------|---------|-----------------|
| `test_export_dataset_a_10_rows` | 10 rows | row_count=10 in Parquet |
| `test_export_dataset_b_100_rows` | 100 rows | row_count=100 in Parquet |
| `test_purge_dataset_b_100_rows` | 100 rows | 0 remaining; audit rows_deleted=100 |
| `test_export_dataset_c_10k_rows` | 10,000 rows | row_count=10000 in Parquet |
| `test_purge_dataset_c_10k_rows` | 10,000 rows | 0 remaining; audit correct |

### Category 3: Manifest Validation (`TestManifestValidation`)

| Test | Description | Expected Result |
|------|-------------|-----------------|
| `test_manifest_season_matches_job_season` | Build manifest for SEAS-TEST-001 | scope_key == 'SEAS-TEST-001' |
| `test_manifest_mismatch_blocks_ingest` | Build manifest with wrong season | scope_key != job scope → guard triggers |
| `test_manifest_missing_scope_key_is_flagged` | Manifest without scope_key | Absence detected for scoped job |

### Category 4: Transaction Safety (`TestTransactionSafety`)

| Test | Description | Expected Result |
|------|-------------|-----------------|
| `test_delete_only_affects_target_season` | Two date ranges; purge only RANGE_A | RANGE_X untouched (50 rows remain) |
| `test_delete_is_transactional` | Fail mid-batch | Audit status=partial/failed; rows_deleted + remaining = 50 |
| `test_multiple_seasons_only_target_deleted` | Two jobs, two ranges | Each job deletes exactly its 50 rows |

### Category 5: Idempotent Rerun (`TestIdempotentRerun`)

| Test | Description | Expected Result |
|------|-------------|-----------------|
| `test_archive_rerun_is_safe` | Run purge twice | Second run: 0 rows exported, no crash, no duplicate audit entries |
| `test_export_rerun_yields_zero_rows` | Export after purge | row_count=0, no error |

### Category 6: Large Dataset (`TestLargeDataset`)

| Test | Description | Expected Result |
|------|-------------|-----------------|
| `test_100k_rows_inserted` | Verify 100K row insert | count=100,000 |
| `test_100k_export_row_count` | Export 100K rows to Parquet | row_count=100,000; Parquet metadata correct |
| `test_100k_purge_batching` | Purge 100K rows in batches | 0 remaining; ≥10 batches; no OOM; runtime logged |

### Category 7: Audit Log Integration (`TestAuditLogIntegration`)

| Test | Description | Expected Result |
|------|-------------|-----------------|
| `test_audit_log_insert_and_query` | Write real audit row | Queryable by status + season |
| `test_audit_log_idempotent_on_duplicate` | Write same job_id twice | ON DUPLICATE KEY UPDATE; 1 row |
| `test_audit_log_query_by_season_and_status` | Query audit log | Filtered results correct |

---

## Validation Checklist

### MariaDB Row Count Validation

```sql
-- After purge: should be 0
SELECT COUNT(*) FROM `tabMemora Practice Log`
WHERE last_seen_at >= '2099-01-01' AND last_seen_at < '2100-01-01';

-- Expected: 0
```

### Audit Log Validation

```sql
-- Verify audit entries
SELECT job_id, season_id, rows_deleted, status, duration_ms, num_batches
FROM archive_delete_audit_log
WHERE job_id IN ('ARCH-99001','ARCH-99002','ARCH-99003','ARCH-99010')
ORDER BY timestamp;
```

### DuckDB Validation (if analytics server available)

```sql
-- On analytics server:
SELECT COUNT(*)
FROM curated_practice_log_current
WHERE season_id = 'SEAS-TEST-001';
-- Expected: same as originally inserted
```

---

## Success Criteria

The archive pipeline is considered **production-safe** when all of the following hold:

| Criterion | Status |
|-----------|--------|
| All unit tests pass (18 tests in `test_purge_audit.py`) | ✅ PASS |
| All integration tests pass (20 tests in `test_integration_pipeline.py`) | ✅ PASS |
| Row counts match: MariaDB deleted = Parquet exported | ✅ PASS |
| Rerun produces 0 exported rows and no crash | ✅ PASS |
| Delete safety verified (only target range deleted) | ✅ PASS |
| Partial failure logged with correct rows_deleted count | ✅ PASS |
| Audit log written on both success and failure | ✅ PASS |
| 100K rows purged without memory issues | ✅ PASS |
| Batched delete respects LIMIT 10,000 | ✅ PASS |

**Total: 38/38 tests pass** (18 unit + 20 integration) in 51.29s.

---

## Known Limitations

1. **DuckDB ingestion not tested locally** — requires SSH connection to analytics server.
   Covered by unit mocks in `test_purge_audit.py`.

2. **Transfer stage not tested** — SSH rsync requires real analytics host.

3. **DQ-14/DQ-15 not exercised** — integration tests use `related_tables: []` to avoid
   dependency on full dimension tables. Full DQ validation is exercised by the production
   pipeline with real player/item dimension data.

4. **Cleanup on test failure** — if a test crashes, leftover test rows in
   `last_seen_at >= 2099-01-01` range can be cleaned with:
   ```sql
   DELETE FROM `tabMemora Practice Log`
   WHERE last_seen_at >= '2099-01-01';
   DELETE FROM `tabMemora Archive Job`
   WHERE name LIKE 'ARCH-990%';
   DELETE FROM archive_delete_audit_log
   WHERE job_id LIKE 'ARCH-990%';
   ```

---

## Issues Detected and Resolved

The following issues were found and fixed during test development:

1. **`last_seen_at` overflow** — `insert_practice_log_rows` used `timedelta(hours=n % 24)` which pushed some rows beyond the target date range, causing count mismatches. Fixed to `timedelta(seconds=1)`.

2. **`idx_archive_job_unique` constraint collision** — `tabMemora Archive Job` has a unique constraint on `(source_doctype, archive_scope, schema_version)`. Creating two simultaneous test jobs with the same values caused `ON DUPLICATE KEY UPDATE` to silently overwrite the first job. Fixed by passing distinct `archive_scope` values for multi-job tests.

3. **Stale test data from crashed runs** — `INSERT IGNORE` silently skipped rows when stale data from previous failed runs existed with different `last_seen_at` values outside the cleanup range. Fixed by adding `delete_test_practice_logs_by_prefix()` helper that cleans up by item_id pattern before each fixture.

4. **`ON DUPLICATE KEY UPDATE` missing `job_meta`** — Stale archive jobs from crashed runs retained old date ranges because `ON DUPLICATE KEY UPDATE` did not update `job_meta`. Fixed to include `job_meta` in the update clause.

5. **Non-autouse audit table fixture** — Making `ensure_integration_audit_table` autouse=True caused all 18 unit tests to skip (DB connection required). Fixed to non-autouse, called explicitly inside each integration class setup.

---

## Appendix: Test Data Schema

Test practice log rows follow this pattern:

| Column         | Value Pattern                         |
|----------------|---------------------------------------|
| `name`         | `TEST-PL-{prefix}-{n:08d}`           |
| `player_id`    | `TEST-PLAYER-{n % players + 1:03d}`  |
| `item_id`      | `TEST-ITEM-{prefix}-{n:08d}` (unique)|
| `first_seen_at`| Evenly distributed in date range     |
| `last_seen_at` | first_seen_at + n hours               |
| `last_result`  | Alternates Correct/Incorrect          |
| `attempt_count`| `(n % 9) + 1` (range: 1–9)           |
| `correct_count`| `n % attempt_count` (≤ attempt_count) |

All test rows use `last_seen_at` in **2099**, safely isolated from production data.
