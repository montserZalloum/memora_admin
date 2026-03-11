# Contract: Safety Gates for Production Cleanup

## Module

`archive_executor/safety_gates.py` (new module)

## Purpose

Provides mandatory pre-cleanup checks that MUST all pass before the production table partition can be dropped. Any single gate failure blocks cleanup entirely.

## API

```python
from archive_executor.safety_gates import check_all_gates, GateResult

result: GateResult = check_all_gates(config, season_name="SEAS-00003", season_seq=3)
# result.passed: bool
# result.gates: list of individual gate results
# result.blockers: list of failed gate descriptions (empty if all passed)
```

## Gates

### Gate 1: Archive Validation (FR-012)

**Check**: A Memora Archive Job for this season exists with status `Completed` or `Purged`.

```sql
SELECT name, status, row_count, file_checksum
FROM `tabMemora Archive Job`
WHERE source_doctype = 'Memora Memory State'
  AND archive_scope = %s
  AND schema_version = 'v1'
  AND status IN ('Completed', 'Purged')
LIMIT 1
```

**Parameter**: `archive_scope = f"season_{season_seq}"`

**Pass**: At least one row returned.
**Fail**: "No validated archive found for season_N. Archive must complete before cleanup."

### Gate 2: Active Player Linkage (FR-013)

**Check**: No player profiles are currently linked to this season.

```sql
SELECT COUNT(*) AS cnt FROM `tabMemora Player Profile`
WHERE season = %s
```

**Parameter**: `season_name` (e.g., `SEAS-00003`)

**Pass**: `cnt == 0`
**Fail**: "N active player profiles still linked to season SEAS-XXXXX. Reassign players before cleanup."

### Gate 3: Active Plan Linkage (FR-014)

**Check**: No published academic plans are linked to this season.

```sql
SELECT COUNT(*) AS cnt FROM `tabMemora Academic Plan`
WHERE season = %s AND is_published = 1
```

**Parameter**: `season_name` (e.g., `SEAS-00003`)

**Pass**: `cnt == 0`
**Fail**: "N published academic plans still linked to season SEAS-XXXXX. Unpublish or reassign plans before cleanup."

### Gate 4: Partition Exists

**Check**: The target partition exists and matches the expected naming pattern.

```sql
SELECT PARTITION_NAME
FROM INFORMATION_SCHEMA.PARTITIONS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'tabMemora Memory State'
  AND PARTITION_NAME = %s
```

**Parameter**: `f"p_season_{season_seq}"`

**Pass**: Partition found.
**Fail**: "Partition p_season_N not found on tabMemora Memory State. Cannot DROP non-existent partition."

## Return Type

```python
@dataclass
class GateCheck:
    gate_name: str     # e.g., "archive_validation"
    passed: bool
    message: str       # Human-readable description
    details: dict      # Gate-specific context (row counts, job IDs, etc.)

@dataclass
class GateResult:
    passed: bool               # True only if ALL gates pass
    gates: list[GateCheck]     # All individual results
    blockers: list[str]        # Failed gate messages (empty if passed)
    season_name: str
    season_seq: int
    checked_at: str            # ISO timestamp
```

## Integration with Purge

In `purge.py`, before executing `DROP PARTITION`:

```python
from .safety_gates import check_all_gates

result = check_all_gates(config, season_name, season_seq)
if not result.passed:
    log.warning("cleanup_blocked", blockers=result.blockers)
    # Do NOT proceed — leave job in Completed state for retry later
    return

# All gates passed — proceed with DROP PARTITION
```

## Logging

All gate checks are logged at INFO level with gate name, result, and timing. Blocked gates are logged at WARNING level with the blocker message.
