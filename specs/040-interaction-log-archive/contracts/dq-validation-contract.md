# Contract: Generic DQ Validation Engine

## Current State

`validator.py` has `validate_fact_quality()` hardcoded for Practice Log fields (16 rules). This must be generalized.

## New Interface

```python
def validate_fact_quality_generic(
    fact_path: str,
    dq_rules: list[dict],
    dimension_paths: dict[str, str] | None = None,
    scope_date_from: str | None = None,
    scope_date_to: str | None = None,
) -> dict:
    """Validate fact data against DQ rules defined in the archive type YAML.

    Args:
        fact_path: Path to the fact Parquet file.
        dq_rules: List of DQ rule dicts from the archive type YAML.
        dimension_paths: Map of dimension entity name → Parquet file path.
        scope_date_from: Archive scope start date (inclusive).
        scope_date_to: Archive scope end date (exclusive).

    Returns:
        Dict with: passed (bool), results (list), warnings (list).
    """
```

## DQ Rule Types

### not_null
```yaml
{id: DQ-01, type: not_null, column: name}
```
Checks that the specified column has zero nulls.

### enum_values
```yaml
{id: DQ-07, type: enum_values, column: event_type, values: [Started, Completed, Failed, Skipped]}
```
Checks that all non-null values are in the allowed set.

### min_value
```yaml
{id: DQ-08, type: min_value, column: time_spent, min: 0}
```
Checks that `MIN(column) >= min`. Nulls are skipped.

### max_value
```yaml
{id: DQ-XX, type: max_value, column: some_col, max: 100}
```
Checks that `MAX(column) <= max`. Nulls are skipped.

### column_lte_column
```yaml
{id: DQ-XX, type: column_lte_column, left: first_seen_at, right: last_seen_at}
```
Checks that `left <= right` for all rows.

### scope_range
```yaml
{id: DQ-10, type: scope_range, column: timestamp}
```
Checks that all values of `column` fall within `[scope_date_from, scope_date_to)`.

### referential
```yaml
{id: DQ-11, type: referential, column: player, dimension: player}
```
Checks that all values of `column` exist in the corresponding dimension Parquet. Uses the dimension's `id_column` (first column or named `{entity}_id`).

### unique_key
```yaml
{id: DQ-13, type: unique_key, columns: [name]}
```
Checks that the combination of `columns` has no duplicates.

## Backward Compatibility

The existing `validate_fact_quality()` function is preserved as-is for Practice Log (no `dq_rules` in its YAML). The pipeline code (`run.py`) checks whether `dq_rules` exists in the archive type schema:
- If present: call `validate_fact_quality_generic()`
- If absent: fall back to existing `validate_fact_quality()`

This allows incremental migration — Practice Log can adopt generic DQ rules later.
