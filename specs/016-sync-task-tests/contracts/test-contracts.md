# Test Contracts: Sync Task Tests

**Feature**: 016-sync-task-tests
**Date**: 2026-02-17

This feature produces test files, not APIs. The "contracts" are the test function signatures and their assertion patterns.

## Test File 1: `test_sync_wallets.py`

**Class**: `TestSyncDirtyWallets(FrappeTestCase)`

| Test Method | Redis Setup | MariaDB Setup | Action | Assertion |
|-------------|-------------|---------------|--------|-----------|
| `test_happy_path` | SADD dirty + HSET wallet hash | Create Player Wallet record | `sync_dirty_wallets()` | DB record updated, SISMEMBER=False |
| `test_multiple_dirty` | 3 players in dirty set + 3 hashes | 3 Player Wallet records | `sync_dirty_wallets()` | All 3 updated, dirty set empty |
| `test_empty_dirty_set` | (nothing) | (nothing) | `sync_dirty_wallets()` | No errors, no DB changes |
| `test_missing_wallet_record` | SADD dirty + HSET hash | No wallet record | `sync_dirty_wallets()` | Warning logged, removed from dirty |
| `test_redis_wallet_missing` | SADD dirty (no hash) | Player Wallet exists | `sync_dirty_wallets()` | Removed from dirty, no DB update |
| `test_partial_failure` | 3 players dirty + 3 hashes | 3 wallet records, mock 1 failure | `sync_dirty_wallets()` | 2 synced, 1 remains in dirty |
| `test_dirty_flag_cleared` | SADD dirty + HSET hash | Wallet with dirty_flag=1 | `sync_dirty_wallets()` | dirty_flag=0 in DB |
| `test_sync_log_created` | SADD dirty + HSET hash | Player Wallet record | `sync_dirty_wallets()` | Sync Log with type="Wallet" exists |

## Test File 2: `test_sync_progress.py`

**Class**: `TestSyncDirtyProgress(FrappeTestCase)`

| Test Method | Redis Setup | MariaDB Setup | Action | Assertion |
|-------------|-------------|---------------|--------|-----------|
| `test_bitmap_to_hex_upsert` | SADD dirty member + SETBIT bitmap | (none) | `sync_dirty_progress()` | Structure Progress with correct hex |
| `test_new_record_created` | SADD dirty + bitmap | No existing record | `sync_dirty_progress()` | New PROG doc inserted |
| `test_existing_record_updated` | SADD dirty + bitmap | Existing PROG doc | `sync_dirty_progress()` | PROG doc updated with new hex |
| `test_invalid_dirty_member_format` | SADD malformed member | (none) | `sync_dirty_progress()` | Skipped, warning logged |
| `test_empty_bitmap` | SADD dirty (no bitmap data) | (none) | `sync_dirty_progress()` | Empty hex, 0% completion |

## Test File 3: `test_flush_interactions.py`

**Class**: `TestFlushInteractionBuffer(FrappeTestCase)`

| Test Method | Redis Setup | MariaDB Setup | Action | Assertion |
|-------------|-------------|---------------|--------|-----------|
| `test_happy_path` | RPUSH 3 valid JSON items | (none) | `flush_interaction_buffer()` | 3 Interaction Log docs, buffer empty |
| `test_empty_buffer` | (nothing) | (nothing) | `flush_interaction_buffer()` | No errors, no DB changes |
| `test_invalid_json_skipped` | RPUSH [valid, invalid, valid] | (none) | `flush_interaction_buffer()` | 2 docs created, buffer trimmed |
| `test_missing_fields_skipped` | RPUSH [valid, {no player}, valid] | (none) | `flush_interaction_buffer()` | 2 docs created |
| `test_batch_size_cap` | RPUSH 1500 items | (none) | `flush_interaction_buffer()` | LLEN=500 after flush |
| `test_partial_failure_retry` | RPUSH 3 items, mock 1 insert fail | (none) | `flush_interaction_buffer()` | 2 inserted, LTRIM by 2 |
