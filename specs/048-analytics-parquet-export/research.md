# Research: Analytics Parquet Dataset Export

**Branch**: `048-analytics-parquet-export` | **Date**: 2026-03-13

## R-001: Extending the Existing analytics_exporter Module

**Question**: Feature 047 built a standalone `analytics_exporter/` module with 12 datasets. Feature 048 adds 18 new datasets (from the PRD) that partially overlap with the existing 12. Should we create a separate module or extend the existing one?

**Decision**: Extend the existing `analytics_exporter/` module. The PRD's datasets are a superset that replaces the 047 set with richer schemas (more columns per dataset) and adds entirely new datasets.

**Rationale**:
- The existing infrastructure (config.py, db.py, exporter.py, watermark.py, validator.py, run.py) is fully reusable. No new dependencies needed.
- The PRD redefines some existing datasets with more columns (e.g., `dim_player` replaces the simple player fields, `dim_content_hierarchy` is a denormalized version of the 5 separate hierarchy files, `dim_academic_plan` adds grade/major titles and subject lists).
- The `_export_full_snapshot_dataset()` function in run.py is already generic — it loads any YAML schema and exports it. New datasets only need a YAML file and a registration in `KNOWN_DATASETS` + `orchestrate_exports()`.
- The manifest generation (FR-022) is the only new infrastructure needed — a small function modeled after `archive_executor/manifest.py`.

**Alternatives considered**:
- *New module `analytics_exporter_v2/`*: Duplicates infrastructure for no benefit.
- *Merge into archive_executor*: Conflates two pipelines with different lifecycles (archive is job-scoped; analytics is dataset-scoped).

---

## R-002: PRD Dataset Mapping to Existing 047 Datasets

**Question**: The PRD defines 18 datasets. 047 already implemented 12. How do they overlap?

**Decision**: Map PRD datasets to their 047 equivalents and identify what's new vs. what needs schema expansion.

| PRD Dataset | 047 Equivalent | Action |
|---|---|---|
| `dim_player` | *(none)* | **NEW** — 8 columns from tabMemora Player Profile |
| `dim_content_hierarchy` | subjects + tracks + units + topics + lessons (5 files) | **REPLACE** — single denormalized file with JOINs; drop the 5 separate files |
| `dim_review_item` | item_mapping | **REPLACE** — richer schema: adds stage_id, stage_type, question_text, correct_choice |
| `dim_season` | seasons | **EXPAND** — add is_published column |
| `dim_academic_plan` | academic_plans + grade_majors | **REPLACE** — single denormalized file with grade/major titles + subject_list; drop grade_majors |
| `fact_interaction` | *(none — excluded in 047)* | **NEW** — date-range filtered interaction log |
| `fact_memory_state` | *(none)* | **NEW** — season-scoped, binary→UUID, decimal→float |
| `fact_practice` | practice_log | **KEEP** — same 7-column schema, same incremental mode |
| `fact_subscription` | *(none)* | **NEW** — subscription + transaction JOIN |
| `fact_voucher` | *(none)* | **NEW** — card + batch + allocation JOIN |
| `fact_challenge` | *(none)* | **NEW** — two files: attempt + detail |
| `fact_structure_progress` | *(none)* | **NEW** — 4 columns from Structure Progress |
| `fact_player_wallet` | *(none)* | **NEW** — 7 columns from Player Wallet |
| `dim_lesson_stage` | *(none)* | **NEW** — stage + settings JOIN |
| `fact_content_report` | *(none)* | **NEW** — 8 columns from Content Report |
| `fact_live_challenge` | *(none)* | **NEW** — two files: event + participation |
| `fact_archive_job` | *(none)* | **NEW** — 11 columns from Archive Job |
| `fact_task_run` | *(none)* | **NEW** — two files: task run log + build queue |

**Summary**: Keep practice_log (unchanged). Replace/expand 5 existing datasets. Add 12 entirely new datasets. Drop grades, majors, grade_majors as standalone files (absorbed into dim_academic_plan). Drop 5 separate hierarchy files (replaced by dim_content_hierarchy).

---

## R-003: Manifest Generation for analytics_exporter

**Question**: The PRD requires every Parquet file to have a manifest.json with SHA-256 checksum and row count. The existing analytics_exporter has no manifest generation. How to add it?

**Decision**: Add a `manifest.py` module to `analytics_exporter/` modeled after `archive_executor/manifest.py`, but simplified for the analytics use case.

**Manifest format**:
```json
{
  "manifest_version": "1.0",
  "dataset_key": "dim_player",
  "kind": "analytics",
  "schema_version": "1.0",
  "created_at": "2026-03-13T12:00:00Z",
  "source": "memora_admin",
  "files": [
    {
      "filename": "dim_player.parquet",
      "row_count": 364,
      "checksum": "sha256:abc123...",
      "size_bytes": 12345
    }
  ]
}
```

**Implementation**:
1. After `export_snapshot()` or `export_incremental()` returns `(path, row_count)`:
2. Compute `sha256` of the output file.
3. Get `size_bytes` via `os.path.getsize()`.
4. Write `manifest.json` to the same directory as the Parquet file, named `{dataset}.manifest.json`.
5. For multi-file datasets (challenge, live_challenge, task_run), the manifest `files` array contains multiple entries.

**Naming**: `{dataset}.manifest.json` (e.g., `dim_player.manifest.json`) rather than a single `manifest.json` for all datasets — this allows per-dataset integrity verification and avoids conflicts when datasets export independently.

**Alternatives considered**:
- *Single manifest.json for all datasets*: Requires all datasets to export in one run; breaks independent dataset filtering via `ANALYTICS_DATASETS`.
- *No manifest, just checksums in watermark*: Watermark is for incremental state, not integrity; mixing concerns.

---

## R-004: Multi-File Datasets (Atomic Export)

**Question**: fact_challenge, fact_live_challenge, and fact_task_run each produce two output files. FR-026 requires atomic success/failure. How to handle this?

**Decision**: Export both files of a multi-file dataset within a single try/except block. If either export fails, both files are deleted (if they exist) and the dataset is marked as failed.

**Implementation pattern**:
```python
def _export_multi_file_dataset(config, log, schema_names):
    """Export a multi-file dataset atomically."""
    results = []
    try:
        for name in schema_names:
            result = _export_full_snapshot_dataset(config, log, name)
            if not result.success:
                raise ExportError(f"{name} failed: {result.error or result.violations}")
            results.append(result)
        # Write combined manifest
        write_manifest(config, dataset_key, results)
        return results
    except Exception:
        # Clean up any partial files
        for r in results:
            if os.path.exists(r.output_path):
                os.remove(r.output_path)
        raise
```

**Alternatives considered**:
- *Write to temp dir then move*: Over-engineering for datasets with <1000 rows. Direct write + cleanup on error is sufficient.
- *Treat each file as independent dataset*: Violates FR-026 atomicity requirement.

---

## R-005: Memory State Export — Binary UUID and Decimal Handling

**Question**: `tabMemora Memory State` has `item_id` as `BINARY(16)` and `stability`/`difficulty` as `DECIMAL(21,9)`. How to handle these in the export pipeline?

**Decision**: Handle conversions in SQL via `BIN_TO_UUID()` and `CAST(... AS DOUBLE)`, matching the PRD's exact queries.

**Rationale**:
- `BIN_TO_UUID(item_id)` converts BINARY(16) to UUID text (e.g., `550e8400-e29b-41d4-a716-446655440000`) directly in SQL. No Python-side conversion needed.
- `CAST(stability AS DOUBLE)` and `CAST(difficulty AS DOUBLE)` convert DECIMAL to DOUBLE in SQL. The existing `_coerce_value()` in exporter.py already handles `decimal.Decimal` → `float` as a safety net, but the SQL CAST avoids the issue at source.
- Season-scoped export: The YAML schema uses a parameterized SQL with `WHERE season_seq = %s`. The orchestrator loops over active seasons and exports one file per season, or a single file for all seasons depending on the caller.

**Memory State mode**: `season_scoped` — a new mode alongside `full_snapshot` and `incremental_watermark`. The orchestrator must know which season(s) to export. For simplicity, export all seasons into a single file (the PRD query supports `WHERE season_seq = :target_season_seq` but a full export is also valid). The analytics server can filter by `season_seq` within the Parquet file.

**Decision on season scoping**: Export ALL seasons into a single `fact_memory_state.parquet` file using:
```sql
SELECT name AS ms_id, player AS player_id,
       BIN_TO_UUID(item_id) AS item_id,
       season_seq, subject AS subject_id, lesson AS lesson_id,
       CAST(stability AS DOUBLE) AS stability,
       CAST(difficulty AS DOUBLE) AS difficulty,
       next_review, last_review,
       state AS fsrs_state, step AS fsrs_step
FROM `tabMemora Memory State`
ORDER BY season_seq, name
```
The table has only 103 rows currently (spec notes). Full export is acceptable. The `season_seq` column in the output allows the analytics server to filter per-season.

**Alternatives considered**:
- *Python-side conversion*: Risk of raw bytes leaking into Parquet if `_coerce_value` misses the type. SQL-side conversion is safer.
- *One file per season*: Adds complexity to orchestrator and manifest; unnecessary given the small row count.

---

## R-006: Interaction Log Date-Range Filtering

**Question**: `fact_interaction` is the largest table (10,906+ rows, growing daily). The PRD specifies `WHERE timestamp BETWEEN :from_date AND :to_date`. How to parameterize the date range?

**Decision**: Add new environment variables `ANALYTICS_INTERACTION_FROM` and `ANALYTICS_INTERACTION_TO` (ISO date strings). Default to "last 30 days" if not specified.

**Rationale**:
- The interaction log is the only dataset that requires date-range filtering (all others are full snapshots or self-scoped).
- Using env vars keeps the interface consistent with the existing config pattern.
- Default to last 30 days: `from_date = today - 30 days`, `to_date = today`. This ensures reasonable exports without configuration on first run.
- The YAML schema SQL uses `%s` placeholders: `WHERE timestamp BETWEEN %s AND %s`.

**Config additions**:
```python
analytics_interaction_from: str | None  # ISO date, e.g., "2026-02-11"
analytics_interaction_to: str | None    # ISO date, e.g., "2026-03-13"
```

**Alternatives considered**:
- *Always full export*: Too expensive as the table grows.
- *Watermark-based incremental*: Interaction log grows append-only with timestamps; a date range is more intuitive and useful for the analytics server than a watermark delta.

---

## R-007: Denormalized Content Hierarchy Query

**Question**: The PRD's `dim_content_hierarchy` uses subqueries for `stage_count` and `stage_types`. The existing 047 design exported 5 separate normalized files. Which approach is correct for 048?

**Decision**: Follow the PRD — export a single denormalized `dim_content_hierarchy.parquet` with JOINs and subqueries.

**SQL** (from PRD):
```sql
SELECT
    l.name AS lesson_id, l.lesson_title,
    l.subject AS subject_id, sub.subject_title,
    l.track AS track_id, t.track_title,
    l.unit AS unit_id, u.unit_title,
    l.topic AS topic_id, tp.topic_title,
    l.base_xp, l.max_hearts, l.is_reviewable, l.bit_index,
    (SELECT COUNT(*) FROM `tabMemora Lesson Stage` ls
     WHERE ls.parent = l.name) AS stage_count,
    (SELECT GROUP_CONCAT(DISTINCT ls.stage_type) FROM `tabMemora Lesson Stage` ls
     WHERE ls.parent = l.name) AS stage_types
FROM `tabMemora Lesson` l
LEFT JOIN `tabMemora Subject` sub ON sub.name = l.subject
LEFT JOIN `tabMemora Track` t ON t.name = l.track
LEFT JOIN `tabMemora Unit` u ON u.name = l.unit
LEFT JOIN `tabMemora Topic` tp ON tp.name = l.topic
WHERE l.is_published = 1;
```

**Rationale**: The analytics server benefits from a pre-joined flat table. It avoids requiring the analytics server to perform 4 JOINs on every query. The denormalized format is standard for analytical dimension tables.

---

## R-008: Existing Dataset Cleanup Strategy

**Question**: Feature 048 replaces/expands several 047 datasets. What happens to the old YAML schemas and run.py dispatch code?

**Decision**: Replace in-place. Remove old YAML schemas that are superseded, update `KNOWN_DATASETS` and `orchestrate_exports()` in run.py.

**Removals**:
- `schemas/subjects.yaml`, `tracks.yaml`, `units.yaml`, `topics.yaml`, `lessons.yaml` → replaced by `dim_content_hierarchy.yaml`
- `schemas/item_mapping.yaml` → replaced by `dim_review_item.yaml`
- `schemas/academic_plans.yaml`, `grade_majors.yaml` → replaced by `dim_academic_plan.yaml`
- `schemas/grades.yaml`, `majors.yaml` → absorbed into `dim_academic_plan.yaml` (grade/major titles from JOINs)

**Kept as-is**:
- `schemas/practice_log.yaml` → renamed to `fact_practice.yaml` (same content, new dataset key)
- `schemas/seasons.yaml` → replaced by `dim_season.yaml` (adds `is_published`)

**KNOWN_DATASETS update**: Replace 12 old names with 18 new names matching the PRD.

**Alternatives considered**:
- *Keep old datasets alongside new*: Creates confusion about which datasets are canonical. The PRD explicitly defines the target dataset catalog.
