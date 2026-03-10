-- =============================================================================
-- Archive Pipeline Integration Test Data Setup
-- =============================================================================
-- Purpose: Generate synthetic test data in MariaDB for SEAS-TEST-001
-- Run:     mysql -u <user> -p <dbname> < setup_test_data.sql
--
-- Datasets:
--   A = 10 rows    (test_a)
--   B = 100 rows   (test_b)
--   C = 10,000 rows (test_c)
--
-- All test rows use last_seen_at in range [2099-01-01, 2100-01-01)
-- to avoid any collision with real production data.
-- =============================================================================

-- Allow recursive CTEs large enough for 10K rows
SET @@max_recursive_iterations = 11000;

-- =============================================================================
-- Step 0: Ensure audit log table exists
-- =============================================================================
CREATE TABLE IF NOT EXISTS `archive_delete_audit_log` (
    `id`                   INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `job_id`               VARCHAR(140) NOT NULL,
    `season_id`            VARCHAR(140),
    `rows_deleted`         BIGINT NOT NULL DEFAULT 0,
    `timestamp`            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `executor_host`        VARCHAR(255),
    `executor_user`        VARCHAR(140),
    `duration_ms`          BIGINT NOT NULL DEFAULT 0,
    `status`               VARCHAR(20) NOT NULL DEFAULT 'pending',
    `error_msg`            TEXT,
    `total_rows_estimated` BIGINT NOT NULL DEFAULT 0,
    `batch_size`           INT NOT NULL DEFAULT 10000,
    `num_batches`          INT NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_job_id` (`job_id`),
    KEY `idx_season_id` (`season_id`),
    KEY `idx_status` (`status`),
    KEY `idx_timestamp` (`timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- =============================================================================
-- Step 1: Clean up any existing test data for SEAS-TEST-001
-- =============================================================================
DELETE FROM `tabMemora Practice Log`
WHERE `last_seen_at` >= '2099-01-01 00:00:00'
  AND `last_seen_at` <  '2100-01-01 00:00:00';

DELETE FROM `tabMemora Player Profile`
WHERE `name` LIKE 'TEST-PLAYER-%';

DELETE FROM `tabMemora Archive Job`
WHERE `name` IN (
    'ARCH-99001', 'ARCH-99002', 'ARCH-99003',
    'ARCH-99004', 'ARCH-99005', 'ARCH-99010'
);

DELETE FROM `archive_delete_audit_log`
WHERE `job_id` IN (
    'ARCH-99001', 'ARCH-99002', 'ARCH-99003',
    'ARCH-99004', 'ARCH-99005', 'ARCH-99010'
);


-- =============================================================================
-- Step 2: Insert test player profiles (shared across all datasets)
-- =============================================================================
-- 20 test players, all in season SEAS-TEST-001
WITH RECURSIVE player_nums(n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM player_nums WHERE n < 20
)
INSERT INTO `tabMemora Player Profile`
    (name, creation, modified, modified_by, owner, docstatus, idx,
     grade, major, season, plan)
SELECT
    CONCAT('TEST-PLAYER-', LPAD(n, 3, '0')),
    NOW(), NOW(),
    'test@test.com',
    'test@test.com',
    0, n,
    CONCAT('Grade-', (n % 4) + 1),
    IF(n % 2 = 0, 'Science', 'Arts'),
    'SEAS-TEST-001',
    CONCAT('PLAN-TEST-', LPAD((n % 3) + 1, 3, '0'))
FROM player_nums
ON DUPLICATE KEY UPDATE modified = NOW();


-- =============================================================================
-- Step 3: Dataset A — 10 rows (label prefix A)
-- =============================================================================
WITH RECURSIVE nums(n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM nums WHERE n < 10
)
INSERT INTO `tabMemora Practice Log`
    (name, creation, modified, modified_by, owner, docstatus, idx,
     player_id, item_id,
     first_seen_at, last_seen_at,
     last_result, attempt_count, correct_count)
SELECT
    CONCAT('TEST-PL-A-', LPAD(n, 8, '0')),
    NOW(), NOW(),
    'test@test.com',
    'test@test.com',
    0, n,
    CONCAT('TEST-PLAYER-', LPAD((n % 5) + 1, 3, '0')),
    CONCAT('TEST-ITEM-A-', LPAD(n, 8, '0')),
    DATE_ADD('2099-01-01 00:00:00', INTERVAL n HOUR),
    DATE_ADD('2099-01-01 12:00:00', INTERVAL n DAY),
    IF(n % 2 = 0, 'Correct', 'Incorrect'),
    (n % 9) + 1,
    n % ((n % 9) + 1)
FROM nums
ON DUPLICATE KEY UPDATE modified = NOW();

SELECT CONCAT('Dataset A inserted: ', ROW_COUNT(), ' rows') AS status;


-- =============================================================================
-- Step 4: Dataset B — 100 rows (label prefix B)
-- =============================================================================
WITH RECURSIVE nums(n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM nums WHERE n < 100
)
INSERT INTO `tabMemora Practice Log`
    (name, creation, modified, modified_by, owner, docstatus, idx,
     player_id, item_id,
     first_seen_at, last_seen_at,
     last_result, attempt_count, correct_count)
SELECT
    CONCAT('TEST-PL-B-', LPAD(n, 8, '0')),
    NOW(), NOW(),
    'test@test.com',
    'test@test.com',
    0, n,
    CONCAT('TEST-PLAYER-', LPAD((n % 10) + 1, 3, '0')),
    CONCAT('TEST-ITEM-B-', LPAD(n, 8, '0')),
    DATE_ADD('2099-02-01 00:00:00', INTERVAL n HOUR),
    DATE_ADD('2099-02-01 12:00:00', INTERVAL n DAY),
    IF(n % 3 = 0, 'Correct', 'Incorrect'),
    (n % 9) + 1,
    n % ((n % 9) + 1)
FROM nums
ON DUPLICATE KEY UPDATE modified = NOW();

SELECT CONCAT('Dataset B inserted: ', ROW_COUNT(), ' rows') AS status;


-- =============================================================================
-- Step 5: Dataset C — 10,000 rows (label prefix C, cross-join generation)
-- =============================================================================
-- Use cross-join of two 100-row sets to generate 10,000 distinct row numbers
WITH RECURSIVE h(n) AS (
    SELECT 0 UNION ALL SELECT n+1 FROM h WHERE n < 99
),
full_nums AS (
    SELECT (a.n * 100 + b.n + 1) AS n
    FROM h a
    CROSS JOIN h b
)
INSERT INTO `tabMemora Practice Log`
    (name, creation, modified, modified_by, owner, docstatus, idx,
     player_id, item_id,
     first_seen_at, last_seen_at,
     last_result, attempt_count, correct_count)
SELECT
    CONCAT('TEST-PL-C-', LPAD(n, 8, '0')),
    NOW(), NOW(),
    'test@test.com',
    'test@test.com',
    0, n,
    CONCAT('TEST-PLAYER-', LPAD((n % 20) + 1, 3, '0')),
    CONCAT('TEST-ITEM-C-', LPAD(n, 8, '0')),
    DATE_ADD('2099-03-01 00:00:00', INTERVAL n HOUR),
    DATE_ADD('2099-03-01 12:00:00', INTERVAL n DAY),
    IF(n % 2 = 0, 'Correct', 'Incorrect'),
    (n % 9) + 1,
    n % ((n % 9) + 1)
FROM full_nums
ON DUPLICATE KEY UPDATE modified = NOW();

SELECT CONCAT('Dataset C inserted: ', ROW_COUNT(), ' rows') AS status;


-- =============================================================================
-- Step 6: Verify inserted counts
-- =============================================================================
SELECT
    'Dataset A (10 rows)'   AS dataset,
    COUNT(*)                AS row_count
FROM `tabMemora Practice Log`
WHERE `name` LIKE 'TEST-PL-A-%'

UNION ALL

SELECT
    'Dataset B (100 rows)',
    COUNT(*)
FROM `tabMemora Practice Log`
WHERE `name` LIKE 'TEST-PL-B-%'

UNION ALL

SELECT
    'Dataset C (10K rows)',
    COUNT(*)
FROM `tabMemora Practice Log`
WHERE `name` LIKE 'TEST-PL-C-%'

UNION ALL

SELECT
    'Total test rows',
    COUNT(*)
FROM `tabMemora Practice Log`
WHERE `last_seen_at` >= '2099-01-01 00:00:00'
  AND `last_seen_at` <  '2100-01-01 00:00:00';


-- =============================================================================
-- Step 7: Insert test archive jobs
-- =============================================================================
-- Job 1: Pending — for export test (Dataset A)
INSERT INTO `tabMemora Archive Job`
    (name, creation, modified, modified_by, owner, docstatus, idx,
     source_doctype, archive_scope, schema_version, archive_type,
     status, priority, retry_count, post_archive_action, source_deleted,
     job_meta)
VALUES
    ('ARCH-99001', NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0,
     'Memora Practice Log', 'SEAS-TEST-001', 'v1', 'practice_log',
     'Pending', 'Normal', 0, 'Delete', 0,
     JSON_OBJECT(
         'query_filter', JSON_OBJECT(
             'date_from',     '2099-01-01',
             'date_to',       '2099-02-01',
             'filter_column', 'last_seen_at'
         ),
         'export_columns', JSON_ARRAY(
             'player_id', 'item_id', 'first_seen_at', 'last_seen_at',
             'last_result', 'attempt_count', 'correct_count'
         ),
         'schema_snapshot', JSON_OBJECT(
             'columns', JSON_ARRAY(
                 JSON_OBJECT('name', 'player_id',    'type', 'VARCHAR(140)'),
                 JSON_OBJECT('name', 'item_id',      'type', 'VARCHAR(36)'),
                 JSON_OBJECT('name', 'first_seen_at','type', 'DATETIME'),
                 JSON_OBJECT('name', 'last_seen_at', 'type', 'DATETIME'),
                 JSON_OBJECT('name', 'last_result',  'type', 'VARCHAR(20)'),
                 JSON_OBJECT('name', 'attempt_count','type', 'INT'),
                 JSON_OBJECT('name', 'correct_count','type', 'INT')
             ),
             'primary_key', JSON_ARRAY('player_id', 'item_id')
         ),
         'related_tables', JSON_ARRAY()
     )
    )
ON DUPLICATE KEY UPDATE status = 'Pending', modified = NOW();

-- Job 2: Completed — for purge test (Dataset B)
INSERT INTO `tabMemora Archive Job`
    (name, creation, modified, modified_by, owner, docstatus, idx,
     source_doctype, archive_scope, schema_version, archive_type,
     status, priority, retry_count, post_archive_action, source_deleted,
     job_meta)
VALUES
    ('ARCH-99002', NOW(), NOW(), 'test@test.com', 'test@test.com', 0, 0,
     'Memora Practice Log', 'SEAS-TEST-001', 'v1', 'practice_log',
     'Completed', 'Normal', 0, 'Delete', 0,
     JSON_OBJECT(
         'query_filter', JSON_OBJECT(
             'date_from',     '2099-02-01',
             'date_to',       '2099-03-01',
             'filter_column', 'last_seen_at'
         ),
         'export_columns', JSON_ARRAY(
             'player_id', 'item_id', 'first_seen_at', 'last_seen_at',
             'last_result', 'attempt_count', 'correct_count'
         ),
         'schema_snapshot', JSON_OBJECT(
             'columns', JSON_ARRAY(
                 JSON_OBJECT('name', 'player_id',    'type', 'VARCHAR(140)'),
                 JSON_OBJECT('name', 'item_id',      'type', 'VARCHAR(36)'),
                 JSON_OBJECT('name', 'first_seen_at','type', 'DATETIME'),
                 JSON_OBJECT('name', 'last_seen_at', 'type', 'DATETIME'),
                 JSON_OBJECT('name', 'last_result',  'type', 'VARCHAR(20)'),
                 JSON_OBJECT('name', 'attempt_count','type', 'INT'),
                 JSON_OBJECT('name', 'correct_count','type', 'INT')
             ),
             'primary_key', JSON_ARRAY('player_id', 'item_id')
         ),
         'related_tables', JSON_ARRAY()
     )
    )
ON DUPLICATE KEY UPDATE status = 'Completed', modified = NOW();

SELECT 'Archive jobs created' AS status;


-- =============================================================================
-- Step 8: Final verification
-- =============================================================================
SELECT
    'tabMemora Practice Log (test rows)'  AS verification_item,
    COUNT(*) AS count
FROM `tabMemora Practice Log`
WHERE `last_seen_at` >= '2099-01-01' AND `last_seen_at` < '2100-01-01'

UNION ALL

SELECT
    'tabMemora Player Profile (test rows)',
    COUNT(*)
FROM `tabMemora Player Profile`
WHERE `name` LIKE 'TEST-PLAYER-%'

UNION ALL

SELECT
    'tabMemora Archive Job (test jobs)',
    COUNT(*)
FROM `tabMemora Archive Job`
WHERE `name` IN ('ARCH-99001','ARCH-99002');

SELECT 'Setup complete. Run Python integration tests next.' AS message;
