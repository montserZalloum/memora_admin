# Phase 27: Memory State Redesign (Item-Level FSRS) - Research

**Researched:** 2026-02-11
**Domain:** MariaDB schema redesign, RANGE partitioning, Frappe autoincrement, item-level spaced repetition
**Confidence:** HIGH

## Summary

Phase 27 replaces the composite-string PK on Memora Memory State (`format:{season}-{subject}-{player}-{stage_id}`) with BIGINT AUTO_INCREMENT, shifts from per-stage to per-item FSRS tracking, and adds RANGE partitioning by `season_seq` for instant archival of old seasons.

The MariaDB 10.6 environment has a critical constraint: `UUID_TO_BIN()` and `BIN_TO_UUID()` do **not exist** in MariaDB (they are MySQL 8.0+ functions). MariaDB 10.7+ added a native UUID data type, but 10.6 lacks both features. BINARY(16) storage is still correct, but conversion must use `UNHEX(REPLACE(uuid, '-', ''))` for writes and `LOWER(CONCAT(SUBSTR(HEX(col),1,8),'-',...))` for reads. Polyfill stored functions should be created.

Frappe v15 natively supports `autoname: "autoincrement"` which creates `name bigint primary key` using a MariaDB sequence (not column-level AUTO_INCREMENT). This works for partitioning since the PK can be made composite: `(name, season_seq)`. However, partitioning must be done via raw SQL in `after_migrate` since Frappe has no partitioning awareness.

**Primary recommendation:** Use Frappe's built-in autoincrement for the BIGINT PK, create polyfill stored functions for UUID<->BINARY(16) conversion, and manage all partitioning + composite indexes via the existing `after_migrate` hook pattern.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Frappe | 15.93.0 | DocType ORM, autoincrement PK via sequence | Already in use, built-in BIGINT autoname support |
| MariaDB | 10.6.22 | RANGE partitioning, BINARY(16) UUID storage | Already deployed, supports all needed partition ops |
| fsrs | 6.3.0 | Spaced repetition Card/Scheduler API | Already in use, item-level processing is same API |
| Python uuid | stdlib | UUID v4 generation for item_id | Standard library, `uuid.uuid4()` |
| redis.asyncio | (installed) | FSRS cache per item | Already in use |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | (installed) | Request/response models with UUID fields | API layer item_id validation |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| BINARY(16) for UUID | MariaDB native UUID type | Requires MariaDB 10.7+, not available on current 10.6 |
| UUID_TO_BIN/BIN_TO_UUID | UNHEX/HEX polyfill functions | Same result, just manual function creation required |
| Frappe autoincrement | Manual ALTER TABLE | Frappe autoincrement handles sequence creation automatically |
| RANGE partitioning | No partitioning (single table) | Cannot do instant DROP PARTITION for old seasons at 2.5B rows |

## Architecture Patterns

### Recommended Changes to DocType Schema

```
Memora Memory State (DocType JSON changes):
  autoname: "autoincrement"        # was: format:{season}-{subject}-{player}-{stage_id}
  + field: item_id (Binary)        # BINARY(16) — added via after_migrate raw SQL
  + field: season_seq (Int)        # INT — partition key
  + field: stage_id (Data)         # retained for stage context
  + field: lesson (Link)           # retained
  + field: subject (Link)          # retained
  + field: player (Link)           # retained
  + field: stability (Float)       # retained
  + field: difficulty (Float)      # retained
  + field: next_review (Date)      # changed from Datetime -> Date (already clamped to midnight)
  - field: season (Link)           # replaced by season_seq

Memora Interaction Log (DocType JSON changes):
  + field: item_id (Data)          # CHAR(36) UUID string — optional, for item-level tracking

Memora Season (DocType JSON changes):
  + field: season_seq (Int)        # Sequential integer for partitioning (1, 2, 3...)
```

### Pattern 1: Frappe Autoincrement with Partitioning via after_migrate

**What:** Frappe creates `name bigint primary key` automatically. We then ALTER the table in `after_migrate` to add RANGE partitioning, modify the PK to be composite, and add unique/composite indexes.

**When to use:** Always -- this is the only way to combine Frappe's ORM with MariaDB partitioning.

**Critical sequence in after_migrate:**

```python
# 1. Frappe creates table with: name bigint primary key
# 2. after_migrate modifies:
#    a. DROP PRIMARY KEY, ADD PRIMARY KEY (name, season_seq)
#    b. ADD UNIQUE INDEX (player, item_id, season_seq)
#    c. ADD INDEX (player, subject, next_review, season_seq)
#    d. PARTITION BY RANGE (season_seq)

def _setup_memory_state_partitioning():
    """Idempotent: Convert Memory State table to RANGE partitioned."""
    # Check if already partitioned
    result = frappe.db.sql("""
        SELECT PARTITION_NAME FROM INFORMATION_SCHEMA.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'tabMemora Memory State'
        AND PARTITION_NAME IS NOT NULL
        LIMIT 1
    """)
    if result:
        return  # Already partitioned

    # Get current active season_seq for initial partition
    active_seq = frappe.db.get_value("Memora Season", {"is_published": 1}, "season_seq") or 1

    frappe.db.sql_ddl("""
        ALTER TABLE `tabMemora Memory State`
        DROP PRIMARY KEY,
        ADD PRIMARY KEY (name, season_seq),
        ADD UNIQUE INDEX idx_player_item_season (player, item_id, season_seq),
        ADD INDEX idx_review_query (player, subject, next_review, season_seq),
        PARTITION BY RANGE (season_seq) (
            PARTITION p_season_{seq} VALUES LESS THAN ({next_seq}),
            PARTITION p_future VALUES LESS THAN MAXVALUE
        )
    """.format(seq=active_seq, next_seq=active_seq + 1))
```

### Pattern 2: UUID BINARY(16) Polyfill Functions

**What:** Create stored functions to convert UUID string <-> BINARY(16) since MariaDB 10.6 lacks UUID_TO_BIN/BIN_TO_UUID.

**When to use:** All SQL queries involving item_id column.

```sql
-- Create in after_migrate (idempotent via DROP IF EXISTS)
DROP FUNCTION IF EXISTS UUID_TO_BIN;
CREATE FUNCTION UUID_TO_BIN(uuid CHAR(36))
RETURNS BINARY(16) DETERMINISTIC NO SQL
RETURN UNHEX(REPLACE(uuid, '-', ''));

DROP FUNCTION IF EXISTS BIN_TO_UUID;
CREATE FUNCTION BIN_TO_UUID(b BINARY(16))
RETURNS CHAR(36) DETERMINISTIC NO SQL
BEGIN
    DECLARE hexStr CHAR(32);
    SET hexStr = HEX(b);
    RETURN LOWER(CONCAT(
        SUBSTR(hexStr, 1, 8), '-',
        SUBSTR(hexStr, 9, 4), '-',
        SUBSTR(hexStr, 13, 4), '-',
        SUBSTR(hexStr, 17, 4), '-',
        SUBSTR(hexStr, 21)
    ));
END;
```

### Pattern 3: Adding New Partitions for New Seasons

**What:** When a new season starts, REORGANIZE the MAXVALUE partition to create a bounded partition + new MAXVALUE.

**When to use:** When a new season is created with a new season_seq.

```sql
-- Cannot use ADD PARTITION when MAXVALUE exists
-- Must REORGANIZE the catch-all partition
ALTER TABLE `tabMemora Memory State`
REORGANIZE PARTITION p_future INTO (
    PARTITION p_season_2 VALUES LESS THAN (3),
    PARTITION p_future VALUES LESS THAN MAXVALUE
);
```

### Pattern 4: Item UUID Generation in Stage Config Editor

**What:** When admin creates/edits stage content (matching pairs, sentence words, etc.), each sub-element gets a UUID item_id stored in config_json.

**When to use:** In the JavaScript stage config editor dialogs (game_lesson.js).

```javascript
// Generate UUID v4 in JavaScript
function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16 | 0;
        var v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// Example: Matching pairs with item_id
pairs: values.pairs_table.map((p, index) => ({
    item_id: p.item_id || generateUUID(),  // preserve existing, generate for new
    right: p.item_1,
    left: p.item_2
}))
```

### Pattern 5: Frappe BINARY(16) Column via after_migrate

**What:** Frappe's DocType JSON does not support BINARY column type. The `item_id` field must be defined as `Data` in the DocType JSON (so Frappe Desk can display it), but the actual column type is overridden to BINARY(16) via raw SQL in after_migrate.

**When to use:** For the item_id column on Memora Memory State.

```python
def _ensure_item_id_binary_column():
    """Convert item_id column from varchar to BINARY(16)."""
    # Check current column type
    col_info = frappe.db.sql("""
        SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'tabMemora Memory State'
        AND COLUMN_NAME = 'item_id'
    """)
    if col_info and 'binary(16)' in str(col_info[0][0]).lower():
        return  # Already BINARY(16)

    frappe.db.sql_ddl("""
        ALTER TABLE `tabMemora Memory State`
        MODIFY COLUMN `item_id` BINARY(16) NOT NULL
    """)
```

### Anti-Patterns to Avoid

- **Using UUID_TO_BIN() directly in SQL:** Does not exist in MariaDB 10.6 -- will fail at runtime. Must use polyfill functions.
- **Frappe autoname with composite string:** The whole point of this phase is eliminating the ~80-byte composite string PK.
- **Using ADD PARTITION with MAXVALUE:** MariaDB requires REORGANIZE PARTITION when MAXVALUE partition exists.
- **Defining BINARY column in DocType JSON:** Frappe does not support BINARY field type. Use Data in JSON + raw SQL override.
- **Omitting season_seq from unique/primary indexes:** MariaDB RANGE partitioning REQUIRES all unique indexes include the partition column.
- **Using Frappe `frappe.db.add_index()` for unique indexes:** This function does not support UNIQUE constraint. Must use raw `frappe.db.sql_ddl()`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| BIGINT PK | Manual ALTER TABLE + sequence | Frappe `autoname: "autoincrement"` | Frappe handles sequence creation, name generation, and document lifecycle |
| UUID generation | Custom random string | `uuid.uuid4()` (Python) / crypto.randomUUID (JS) | Standard, collision-resistant, well-tested |
| FSRS computation | Custom spaced repetition math | `fsrs` library Card/Scheduler | Proven algorithm with proper research backing |
| UUID binary conversion | Inline UNHEX/HEX in every query | Stored function polyfills (UUID_TO_BIN/BIN_TO_UUID) | DRY, avoids copy-paste errors in 10+ query locations |
| Partition management | Manual season tracking | `after_migrate` hook + INFORMATION_SCHEMA checks | Idempotent, survives bench migrate, same pattern as existing composite index |

**Key insight:** Frappe's autoincrement + raw SQL partitioning is the only viable approach. Frappe manages the `name` column lifecycle, raw SQL manages partitioning -- they coexist because Frappe generates `name` values via sequence before INSERT, not via column-level AUTO_INCREMENT.

## Common Pitfalls

### Pitfall 1: UUID_TO_BIN Does Not Exist in MariaDB 10.6

**What goes wrong:** SQL queries using `UUID_TO_BIN()` fail with "Function UUID_TO_BIN does not exist" error.
**Why it happens:** MariaDB took a different approach than MySQL -- instead of conversion functions (MySQL 8.0), MariaDB added a native UUID type (10.7+). Version 10.6 has neither.
**How to avoid:** Create polyfill stored functions in `after_migrate`. Always use the polyfill function names. Test with `SELECT UUID_TO_BIN(UUID())` early.
**Warning signs:** Any "function does not exist" errors in SQL queries involving item_id.

### Pitfall 2: MAXVALUE Partition Blocks ADD PARTITION

**What goes wrong:** `ALTER TABLE ADD PARTITION` fails with error when a MAXVALUE partition exists.
**Why it happens:** MariaDB cannot add a partition "after" MAXVALUE -- the range is already fully covered.
**How to avoid:** Always use `REORGANIZE PARTITION p_future INTO (...)` instead of `ADD PARTITION`.
**Warning signs:** Error 1493 "VALUES LESS THAN value must be strictly increasing for each partition."

### Pitfall 3: Frappe Autoincrement Name is BIGINT, Not String

**What goes wrong:** Code that does `f"...{doc.name}..."` or string comparisons on name may behave differently.
**Why it happens:** With autoincrement, `doc.name` is an integer, not a string like `"SEAS-00001-SUBJ-00001-..."`.
**How to avoid:** All existing code that references memory state by the old composite name format (`{season}-{subject}-{player}-{stage_id}`) must be rewritten to query by `(player, item_id, season_seq)`.
**Warning signs:** `isinstance(doc.name, int)` returns True; string formatting may produce unexpected results.

### Pitfall 4: Composite PK Change Breaks Existing Frappe Document Operations

**What goes wrong:** After modifying PK to `(name, season_seq)`, Frappe's internal `get_doc("Memora Memory State", name_value)` still works because it queries `WHERE name = ?`, but other operations may be affected.
**Why it happens:** Frappe assumes `name` is the sole PK. Adding `season_seq` to the PK is transparent to Frappe's ORM since all lookups are by `name` alone, and the unique index on `name` within each partition satisfies this.
**How to avoid:** Verify that `frappe.db.set_value()`, `frappe.get_doc()`, and `frappe.db.exists()` all work correctly after the PK change. The BIGINT `name` is still globally unique (sequence-generated), so Frappe lookups by name remain correct -- the composite PK `(name, season_seq)` is only required by MariaDB's partitioning constraint.
**Warning signs:** Duplicate key errors or Frappe ORM exceptions on insert/update.

### Pitfall 5: Frappe DocType JSON Does Not Support BINARY Field Type

**What goes wrong:** Setting fieldtype to "Binary" or similar in DocType JSON causes Frappe errors.
**Why it happens:** Frappe's field types are: Data, Int, Float, Datetime, Link, etc. There is no "Binary" type.
**How to avoid:** Define `item_id` as fieldtype `Data` in the DocType JSON. Override the column type to BINARY(16) via raw SQL in `after_migrate`. For Frappe Desk display, use a virtual field or read method that calls BIN_TO_UUID.
**Warning signs:** Frappe migration errors or field type validation failures.

### Pitfall 6: Season_seq Must Be Populated Before Partitioning

**What goes wrong:** INSERT fails because `season_seq` is required for partition routing but wasn't set.
**Why it happens:** With RANGE partitioning by `season_seq`, every INSERT must include a valid `season_seq` value.
**How to avoid:** The FSRS processor and review submit API must look up the active season's `season_seq` and include it in every Memory State record. Default to 0 is NOT valid (no partition for 0 if partitions start at 1).
**Warning signs:** "Table has no partition for value X" errors on INSERT.

### Pitfall 7: Interaction Buffer Does Not Include item_id

**What goes wrong:** FSRS processor cannot create item-level Memory States because the interaction data from Redis buffer lacks item_id.
**Why it happens:** The session end API currently sends `stage_id` per stage, not `item_id` per item.
**How to avoid:** Update the EndSessionRequest model and Lua script to accept per-item results. The interaction JSON pushed to Redis must include `item_id` for each item result.
**Warning signs:** FSRS processor creating stage-level records instead of item-level, or skipping items entirely.

### Pitfall 8: Review Query Must Use Partition Pruning

**What goes wrong:** Review queries scan all partitions instead of just the active season, degrading from <5ms to seconds.
**Why it happens:** If `season_seq` is not in the WHERE clause, MariaDB cannot prune partitions.
**How to avoid:** All review queries MUST include `AND season_seq = ?` (current season). The composite index `(player, subject, next_review, season_seq)` enables partition pruning when `season_seq` is specified.
**Warning signs:** EXPLAIN showing "partitions: all" instead of a single partition name.

## Code Examples

### Creating Memory State Record with BIGINT PK and BINARY UUID

```python
# Source: Frappe autoincrement + polyfill pattern
import uuid

# Generate item UUID
item_uuid = str(uuid.uuid4())

# Insert via raw SQL (bypasses Frappe ORM for BINARY column)
frappe.db.sql("""
    INSERT INTO `tabMemora Memory State`
    (name, season_seq, subject, player, item_id, stage_id, lesson,
     stability, difficulty, next_review, creation, modified, owner, modified_by, docstatus, idx)
    VALUES
    (%(name)s, %(season_seq)s, %(subject)s, %(player)s,
     UUID_TO_BIN(%(item_id)s), %(stage_id)s, %(lesson)s,
     %(stability)s, %(difficulty)s, %(next_review)s,
     NOW(6), NOW(6), 'Administrator', 'Administrator', 0, 0)
""", {
    "name": frappe.db.get_next_sequence_val("Memora Memory State"),
    "season_seq": active_season_seq,
    "subject": subject,
    "player": player,
    "item_id": item_uuid,
    "stage_id": stage_id,
    "lesson": lesson,
    "stability": card.stability,
    "difficulty": card.difficulty,
    "next_review": next_review_naive,
})
```

### Review Query with Partition Pruning

```python
# Source: Existing reviews.py pattern adapted for item-level + partition pruning
rows = frappe.db.sql("""
    SELECT ms.name, BIN_TO_UUID(ms.item_id) as item_id,
           ms.stage_id, ms.lesson,
           ms.stability, ms.difficulty, ms.next_review,
           ls.stage_type
    FROM `tabMemora Memory State` ms
    INNER JOIN `tabMemora Lesson Stage` ls
        ON ls.name = ms.stage_id AND ls.parent = ms.lesson
    WHERE ms.player = %(player)s
      AND ms.subject = %(subject)s
      AND ms.next_review <= %(today)s
      AND ms.season_seq = %(season_seq)s
    ORDER BY ms.next_review ASC
    LIMIT %(fetch_limit)s
""", {
    "player": player_id,
    "subject": subject_id,
    "today": today,
    "season_seq": active_season_seq,
    "fetch_limit": limit + 1,
}, as_dict=True)
```

### Lookup Memory State by Player + Item + Season

```python
# Source: Adapted from current fsrs_processor.py pattern
existing = frappe.db.sql("""
    SELECT name, stability, difficulty, next_review
    FROM `tabMemora Memory State`
    WHERE player = %(player)s
      AND item_id = UUID_TO_BIN(%(item_id)s)
      AND season_seq = %(season_seq)s
    LIMIT 1
""", {
    "player": player_id,
    "item_id": item_uuid_str,
    "season_seq": active_season_seq,
}, as_dict=True)
```

### Mastery Query Updated for Item-Level

```python
# Source: Adapted from profile.py get_memory_mastery
result = frappe.db.sql("""
    SELECT
        COALESCE(SUM(CASE WHEN stability >= 21.0 THEN 1 ELSE 0 END), 0) as mature,
        COALESCE(SUM(CASE WHEN stability > 0 AND stability < 21.0 THEN 1 ELSE 0 END), 0) as learning,
        COALESCE(SUM(CASE WHEN stability = 0 THEN 1 ELSE 0 END), 0) as new_items
    FROM `tabMemora Memory State`
    WHERE player = %(player)s
      AND season_seq = %(season_seq)s
      {subject_filter}
""", {"player": player_id, "season_seq": active_season_seq, "subject": subject_id}, as_dict=True)
```

### JavaScript UUID Generation for Stage Config Editor

```javascript
// Source: Standard UUID v4 generation
function generateItemUUID() {
    // Use crypto.randomUUID() if available (modern browsers)
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    // Fallback for older environments
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16 | 0;
        var v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// Usage in matching dialog save:
pairs: values.pairs_table.map((p, index) => ({
    item_id: p.item_id || generateItemUUID(),
    id: String(index + 1),
    right: p.item_1,
    left: p.item_2
}))
```

### after_migrate Partition Setup (Complete)

```python
def after_migrate():
    """Ensure custom indexes, partitioning, and polyfill functions exist."""
    _ensure_uuid_polyfill_functions()
    _ensure_item_id_binary_column()
    _ensure_memory_state_partitioning()


def _ensure_uuid_polyfill_functions():
    """Create UUID_TO_BIN and BIN_TO_UUID polyfill stored functions."""
    # Check if function exists
    result = frappe.db.sql("""
        SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_SCHEMA = DATABASE()
        AND ROUTINE_NAME = 'UUID_TO_BIN'
    """)
    if result:
        return

    frappe.db.sql_ddl("DROP FUNCTION IF EXISTS UUID_TO_BIN")
    frappe.db.sql_ddl("""
        CREATE FUNCTION UUID_TO_BIN(uuid CHAR(36))
        RETURNS BINARY(16) DETERMINISTIC NO SQL
        RETURN UNHEX(REPLACE(uuid, '-', ''))
    """)

    frappe.db.sql_ddl("DROP FUNCTION IF EXISTS BIN_TO_UUID")
    frappe.db.sql_ddl("""
        CREATE FUNCTION BIN_TO_UUID(b BINARY(16))
        RETURNS CHAR(36) DETERMINISTIC NO SQL
        BEGIN
            DECLARE hexStr CHAR(32);
            SET hexStr = HEX(b);
            RETURN LOWER(CONCAT(
                SUBSTR(hexStr, 1, 8), '-',
                SUBSTR(hexStr, 9, 4), '-',
                SUBSTR(hexStr, 13, 4), '-',
                SUBSTR(hexStr, 17, 4), '-',
                SUBSTR(hexStr, 21)
            ));
        END
    """)


def _ensure_memory_state_partitioning():
    """Set up RANGE partitioning on Memora Memory State."""
    # Check if already partitioned
    result = frappe.db.sql("""
        SELECT PARTITION_NAME FROM INFORMATION_SCHEMA.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'tabMemora Memory State'
        AND PARTITION_NAME IS NOT NULL
        LIMIT 1
    """)
    if result:
        return  # Already partitioned

    active_seq = frappe.db.get_value("Memora Season", {"is_published": 1}, "season_seq") or 1

    # Must do all PK/index changes + partitioning in one ALTER TABLE
    frappe.db.sql_ddl(f"""
        ALTER TABLE `tabMemora Memory State`
        DROP PRIMARY KEY,
        ADD PRIMARY KEY (name, season_seq),
        PARTITION BY RANGE (season_seq) (
            PARTITION p_season_{active_seq} VALUES LESS THAN ({active_seq + 1}),
            PARTITION p_future VALUES LESS THAN MAXVALUE
        )
    """)

    # Add unique and composite indexes (separate ALTER for clarity)
    frappe.db.sql_ddl("""
        ALTER TABLE `tabMemora Memory State`
        ADD UNIQUE INDEX idx_player_item_season (player, item_id, season_seq),
        ADD INDEX idx_review_query (player, subject, next_review, season_seq)
    """)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Composite string PK (~80 bytes) | BIGINT PK (8 bytes) | Phase 27 | 90% PK storage reduction, faster index lookups |
| Per-stage Memory State | Per-item Memory State | Phase 27 | Finer granularity, each sub-element tracked individually |
| VARCHAR(36) UUID | BINARY(16) UUID | Phase 27 | 56% column storage reduction (36 -> 16 bytes) |
| Single unpartitioned table | RANGE partitioned by season | Phase 27 | Instant old-season archival via DROP PARTITION |
| Frappe format:... autoname | Frappe autoincrement | Phase 27 | Standard BIGINT sequence, no string parsing overhead |

**Deprecated/outdated:**
- `UUID_TO_BIN()` / `BIN_TO_UUID()`: Do NOT exist in MariaDB 10.6. Must use polyfill stored functions.
- `format:{season}-{subject}-{player}-{stage_id}` autoname: Replaced by `autoincrement` in this phase.
- Stage-level FSRS tracking: Replaced by item-level in this phase.

## Open Questions

### 1. Frappe Desk Display of BINARY(16) Columns

- **What we know:** Frappe's Data field type maps to varchar. If we override to BINARY(16) in SQL, Frappe Desk list view and form view will show raw binary garbage for `item_id`.
- **What's unclear:** Whether Frappe's list view SQL can be overridden to use BIN_TO_UUID, or if a virtual/computed column approach is better.
- **Recommendation:** Add a computed virtual column `item_id_display` in after_migrate that calls BIN_TO_UUID, OR just accept that admins will rarely need to browse Memory State records directly (they have 2.5B+ rows). For Desk display, override the DocType's `db_query` or add a read-only Data field populated in before_load.

### 2. Concurrent Auto-Increment with Compound PK on Partitioned Table

- **What we know:** MariaDB JIRA MDEV-21842 documents a bug where AUTO_INCREMENT with compound PK on partitioned tables can produce duplicate values under concurrent inserts.
- **What's unclear:** Whether this affects Frappe's sequence-based approach (which doesn't use column-level AUTO_INCREMENT but rather `SELECT nextval()` before insert).
- **Recommendation:** Since Frappe uses MariaDB sequences (not column-level AUTO_INCREMENT), MDEV-21842 should NOT apply. The sequence generates unique values independently of the table's PK structure. Verify with a concurrency test during implementation.

### 3. How to Handle Existing Stage-Level Memory States During Transition

- **What we know:** The roadmap says "No data migration needed: System is new, no existing production data."
- **What's unclear:** Whether there are any existing Memory State records from testing/staging that need to be handled.
- **Recommendation:** Since no data migration is needed per the roadmap decision, the cleanest approach is: (1) change autoname to autoincrement, (2) truncate the table if any test data exists, (3) apply partitioning. If bench migrate fails due to existing rows with string names, TRUNCATE first.

### 4. Idempotent Column Type Override

- **What we know:** Frappe's migrate will try to recreate/modify the `item_id` column based on the DocType JSON definition (Data -> varchar). Our after_migrate then overrides to BINARY(16).
- **What's unclear:** Whether each `bench migrate` will revert the BINARY(16) back to varchar, forcing re-conversion every time.
- **Recommendation:** Test the migrate cycle. If Frappe reverts the column type, the after_migrate hook will re-apply the BINARY(16) override. This is acceptable as an idempotent operation but may cause brief downtime during migration. Alternative: use a custom DocType controller to prevent Frappe from modifying the column.

## Sources

### Primary (HIGH confidence)
- MariaDB 10.6.22 installed on production server (verified via `mariadb --version`)
- Frappe 15.93.0 source code at `/home/corex/aurevia-bench/apps/frappe/frappe/database/mariadb/schema.py` -- confirms `name bigint primary key` for autoincrement
- Frappe sequence module at `/home/corex/aurevia-bench/apps/frappe/frappe/database/sequence.py` -- confirms MariaDB sequence-based ID generation (not column AUTO_INCREMENT)
- FSRS library v6.3.0 installed (`pip show fsrs`) -- Card/Scheduler/Rating API unchanged from current usage
- Existing codebase: `memora_memory_state.json`, `fsrs_processor.py`, `reviews.py`, `sessions.py`, `game_lesson.js`, `setup.py`

### Secondary (MEDIUM confidence)
- [MariaDB RANGE Partitioning Documentation](https://mariadb.com/docs/server/server-usage/partitioning-tables/partitioning-types/range-partitioning-type) -- CREATE TABLE syntax, REORGANIZE PARTITION requirement with MAXVALUE
- [MariaDB Partition Maintenance](https://mariadb.com/docs/server/server-usage/partitioning-tables/partition-maintenance) -- DROP PARTITION for instant archival, no more than 50 partitions recommended
- [MariaDB GUID/UUID Performance](https://mariadb.com/kb/en/guiduuid-performance/) -- UNHEX(REPLACE()) pattern for BINARY(16) storage, polyfill functions
- [UUID_TO_BIN/BIN_TO_UUID Polyfill](https://gist.github.com/jamesgmarks/56502e46e29a9576b0f5afea3a0f595c) -- MariaDB polyfill stored function implementation
- [MDEV-15854](https://jira.mariadb.org/browse/MDEV-15854) -- Confirms UUID_TO_BIN/BIN_TO_UUID NOT implemented in MariaDB (MySQL 8.0 only)
- [Frappe DocType Naming](https://docs.frappe.io/framework/user/en/basics/doctypes/naming) -- autoincrement option, gap behavior, limitations
- [py-fsrs GitHub](https://github.com/open-spaced-repetition/py-fsrs) -- FSRS 6, Card/Scheduler API, JSON serialization

### Tertiary (LOW confidence)
- [MDEV-21842](https://jira.mariadb.org/browse/MDEV-21842) -- AUTO_INCREMENT duplicate issue with compound PK on partitioned tables (may not apply to sequence-based approach)
- [MariaDB AUTO_INCREMENT Documentation](https://mariadb.com/docs/server/reference/data-types/auto_increment) -- AUTO_INCREMENT constraints in partitioned tables

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries verified installed with exact versions
- Architecture: HIGH -- Frappe autoincrement mechanism verified in source code; MariaDB 10.6 partitioning syntax verified in official docs; UUID polyfill pattern verified
- Pitfalls: HIGH -- MariaDB 10.6 UUID_TO_BIN absence confirmed via JIRA ticket; MAXVALUE/REORGANIZE confirmed in docs; autoincrement BIGINT behavior confirmed in Frappe source
- Code examples: MEDIUM -- patterns adapted from existing codebase, but specific SQL DDL operations need runtime testing (especially ALTER TABLE with simultaneous PK change + partitioning)

**Research date:** 2026-02-11
**Valid until:** 2026-03-11 (stable -- MariaDB 10.6, Frappe 15.x are both LTS)
