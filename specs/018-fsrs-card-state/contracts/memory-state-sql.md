# SQL Contracts: Memory State Operations

**Feature**: 018-fsrs-card-state
**Date**: 2026-02-18

All operations use raw SQL via `frappe.db.sql()`. Frappe ORM is forbidden on this table.

## DDL: Add New Columns

**Location**: `setup.py` → `_ensure_fsrs_state_columns()`

```sql
-- Check if columns exist (idempotent)
SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
AND TABLE_NAME = 'tabMemora Memory State'
AND COLUMN_NAME IN ('state', 'step', 'last_review')

-- Add state column (instant operation on MariaDB InnoDB)
ALTER TABLE `tabMemora Memory State`
ADD COLUMN `state` TINYINT DEFAULT NULL

-- Add step column
ALTER TABLE `tabMemora Memory State`
ADD COLUMN `step` TINYINT DEFAULT NULL

-- Add last_review column
ALTER TABLE `tabMemora Memory State`
ADD COLUMN `last_review` DATETIME(6) DEFAULT NULL
```

## DML: Lookup Memory State (Updated)

**Location**: `fsrs_processor.py` → `_lookup_memory_state()`
**Location**: `reviews.py` → `submit_reviews()` (inline)

```sql
SELECT name, stability, difficulty, next_review, state, step, last_review
FROM `tabMemora Memory State`
WHERE player = %(player)s
  AND item_id = UUID_TO_BIN(%(item_id)s)
  AND season_seq = %(season_seq)s
LIMIT 1
```

## DML: Update Memory State (Updated)

**Location**: `fsrs_processor.py` → `_update_memory_state()`
**Location**: `reviews.py` → `submit_reviews()` (inline)

```sql
UPDATE `tabMemora Memory State`
SET stability = %(stability)s,
    difficulty = %(difficulty)s,
    next_review = %(next_review)s,
    state = %(state)s,
    step = %(step)s,
    last_review = %(last_review)s,
    modified = NOW(6)
WHERE name = %(name)s
  AND season_seq = %(season_seq)s
```

## DML: Insert Memory State (Updated)

**Location**: `fsrs_processor.py` → `_insert_memory_state()`

```sql
INSERT INTO `tabMemora Memory State`
(name, season, season_seq, subject, player, item_id, stage_id, lesson,
 stability, difficulty, next_review, state, step, last_review,
 creation, modified, owner, modified_by, docstatus, idx)
VALUES
(%(name)s, %(season)s, %(season_seq)s, %(subject)s, %(player)s,
 UUID_TO_BIN(%(item_id)s), %(stage_id)s, %(lesson)s,
 %(stability)s, %(difficulty)s, %(next_review)s,
 %(state)s, %(step)s, %(last_review)s,
 NOW(6), NOW(6), 'Administrator', 'Administrator', 0, 0)
```

## Unchanged Queries

These queries do NOT need modification (they don't use the new columns):

| Query | Location | Reason |
|-------|----------|--------|
| `get_review_overview` | `reviews.py:62-74` | COUNT + GROUP BY, no new column needed |
| `get_due_items` | `reviews.py:92-117` | Display-only fields, no card reconstruction |
| `get_memory_mastery` | `profile.py:198-210` | Stability-based classification, unchanged |
| `get_items_learned_count` | `profile.py:107-116` | Simple COUNT, no new column needed |
| Remaining due count | `reviews.py:238-250` | Simple COUNT, no new column needed |
