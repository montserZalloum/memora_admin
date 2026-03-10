# PRD: Practice Log Analytics Pipeline

**Dataset**: `tabMemora Practice Log`
**Version**: 1.0
**Date**: 2026-03-10
**Status**: Approved — ready for implementation

---

## 1. Summary

This PRD defines the full end-to-end analytics pipeline for `tabMemora Practice Log`, a high-volume cumulative-state table that records per-student, per-item practice behavior in the Memora educational platform.

**Objectives**:
- Keep production MariaDB lean by archiving closed-season practice data
- Make archived and in-season practice data queryable in DuckDB
- Enable 6 analytical domains: item difficulty, student behavior, practice effectiveness, content quality, cohort analysis, intervention readiness
- Deliver a robust, validated, extensible data pipeline from MariaDB → Parquet → DuckDB

**Scope**: Archive pipeline + live sync pipeline + DuckDB raw/curated/mart layers + data quality rules + operational monitoring

---

## 2. Source Table Definition

### 2.1 Table Identity

| Property | Value |
|----------|-------|
| Table name | `tabMemora Practice Log` |
| Type | Raw SQL (not a Frappe DocType) |
| Managed by | `setup.py:623-645` (`_ensure_practice_log_table()`) |
| ORM | None — raw SQL only |
| Canonical DDL | `setup.py` is the single source of truth |
| Schema versioning | Any column change = explicit schema version bump |

### 2.2 Schema (v1)

```sql
CREATE TABLE IF NOT EXISTS `tabMemora Practice Log` (
    `player_id`     VARCHAR(140) NOT NULL,
    `item_id`       VARCHAR(36) NOT NULL,
    `first_seen_at` DATETIME NOT NULL,
    `last_seen_at`  DATETIME NOT NULL,
    `last_result`   ENUM('Correct', 'Incorrect') NOT NULL,
    `attempt_count` INT UNSIGNED NOT NULL DEFAULT 1,
    `correct_count` INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (`player_id`, `item_id`),
    KEY `idx_item_id` (`item_id`),
    KEY `idx_player_seen_item` (`player_id`, `last_seen_at`, `item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Nature**: Cumulative state table — one row per (player, item) pair. Not an event stream.

### 2.3 Indexes

| Index | Columns | Purpose |
|-------|---------|---------|
| PRIMARY KEY | `(player_id, item_id)` | UPSERT, per-player lookups |
| `idx_item_id` | `(item_id)` | CASCADE DELETE when Review Items removed |
| `idx_player_seen_item` | `(player_id, last_seen_at, item_id)` | Session filtering JOINs |
| `idx_last_seen_at` **(NEW)** | `(last_seen_at)` | Archive export, live sync, purge range queries |

### 2.4 Mutation Pattern

```sql
INSERT INTO `tabMemora Practice Log`
    (player_id, item_id, first_seen_at, last_seen_at, last_result, attempt_count, correct_count)
VALUES (%s, %s, %s, %s, %s, 1, %s)
ON DUPLICATE KEY UPDATE
    last_seen_at = VALUES(last_seen_at),
    last_result = VALUES(last_result),
    attempt_count = attempt_count + 1,
    correct_count = correct_count + VALUES(correct_count)
```

### 2.5 Volume

- **Projected**: ~500M rows at 100K concurrent users (~5K items per student)
- **Growth**: Continuous during active seasons; high-volume UPSERT on every practice batch submit (1–20 items per submit)

---

## 3. Grain, Keys, and Relationships

### 3.1 Grain

One row per `(player_id, item_id)` — represents the cumulative practice state for a single student on a single review item.

### 3.2 Primary Key

Composite: `(player_id, item_id)`. No auto-increment. Logical FK (no physical FOREIGN KEY constraint).

### 3.3 Relationships

| Related Table | Join Column | Cardinality | Role |
|---------------|-------------|-------------|------|
| `Memora Player Profile` | `player_id` | N:1 | Student identity, demographics, plan, cohort |
| `Memora Review Item` | `item_id` | N:1 | Item content, subject/topic/lesson hierarchy |
| `Memora Season` | (temporal via `last_seen_at`) | N:1 (indirect) | Archive scoping, reporting period |
| `Memora Academic Plan` | (via Player Profile `plan`) | N:1 (indirect) | Plan-based segmentation and filtering |

**Season 1:N Plan**: Each Academic Plan belongs to exactly one Season. Each Player belongs to one Plan.

---

## 4. Source of Truth

| Range | Authoritative Source | DuckDB Role |
|-------|---------------------|-------------|
| Active (current season, unarchived) | MariaDB | Point-in-time analytical copy (acceptable staleness up to daily sync SLA) |
| Archived (closed season, purged) | DuckDB Parquet | Authoritative — MariaDB rows deleted |

**Discrepancy rule**: During active range, MariaDB wins. DuckDB live-sync data is a delayed analytical replica. Reconciliation happens on the next sync cycle or via repair/backfill.

---

## 5. Time Fields

| Field | Role | Purpose |
|-------|------|---------|
| `last_seen_at` | **Primary** | Archive scoping, purge boundaries, live-sync boundary, partitioning, time-series reporting |
| `first_seen_at` | Secondary | First-exposure cohorting, time-to-mastery analysis, practice span calculation |

**Timezone**: DATETIME stored without timezone info. Reporting timezone defined at the mart layer.

---

## 6. Archive Design

### 6.1 Trigger

Season closure: `is_published = 0 AND end_date < CURDATE()`.

Detected by daily scheduled task `check_seasons_for_archive()` at 01:20. Creates one `Memora Archive Job` per `(source_doctype, archive_scope, schema_version)`.

### 6.2 Scope

| Property | Value |
|----------|-------|
| Scope key | Season ID (e.g., `SEAS-00027`) |
| Filter column | `last_seen_at` |
| Filter | `WHERE last_seen_at >= season.start_date AND last_seen_at < season.end_date` |
| Stored in | Archive Job `meta.query_filter` JSON |

### 6.3 Cross-Season Row Behavior

**Design constraint**: `tabMemora Practice Log` has no `season_id` column. A `(player_id, item_id)` row that was active in Season A but updated in Season B will appear **only in Season B's archive** (because `last_seen_at` moved forward). Cumulative counters include attempts from all seasons.

**Consequence**: The detailed archived dataset is not a season-pure historical fact table. **Season-specific analytics must come from season-scoped aggregate marts/rollups, not from detailed archived rows alone.**

### 6.4 Post-Archive Action

- **Default**: `Delete` — purge from MariaDB after successful archive
- **Purge preconditions**:
  1. Parquet export completed successfully
  2. Manifest/archive metadata committed
  3. All validation checks passed (16 DQ rules)
  4. Archived dataset queryable in DuckDB
- **Grace period**: Short configurable delay before purge execution
- **Purge mechanism**: Batched DELETE in 10K-row chunks with 2-second sleep between batches

### 6.5 Publish Mode

- **Mode**: `replace` — one authoritative published version per `(dataset_key, archive_scope, schema_version)`
- **Retention**: Current version + 1 previous version for rollback
- **Rebuild**: Re-exportable from MariaDB if data hasn't been purged yet

---

## 7. Live Sync Design

### 7.1 Configuration

| Property | Value |
|----------|-------|
| Mode | Full snapshot (entire current-scope table) |
| Frequency | Daily |
| Retention | Each sync replaces prior live state (no accumulation) |
| Scope | Active/unarchived range only |

### 7.2 Scope Determination

Live sync includes all data for seasons that are **NOT yet archived-and-validated**. Scope exclusion signal: Archive Job status reaches `Completed` (or equivalent validated state).

### 7.3 Handoff Protocol

1. When a season closes and Archive Job is created, the season enters a **transition state**
2. Live sync **continues including** the closed season during transition
3. Once archive is fully validated and queryable, the archived dataset becomes authoritative
4. Live sync then **excludes** that season going forward
5. Curated layer applies precedence: archive wins for completed seasons, eliminating any transitional overlap

**Design choice**: Temporary overlap (safe) over temporary gap (data unavailable). Overlap is handled by curated-layer precedence rules.

---

## 8. Fact Table Export Design

### 8.1 Exported Columns

**Source columns** (all 7, no exclusions):

| Column | Type |
|--------|------|
| `player_id` | VARCHAR(140) |
| `item_id` | VARCHAR(36) |
| `first_seen_at` | DATETIME |
| `last_seen_at` | DATETIME |
| `last_result` | ENUM |
| `attempt_count` | INT UNSIGNED |
| `correct_count` | INT UNSIGNED |

**Export-time metadata columns** (added to Parquet, not in source table):

**For archive exports**:

| Column | Source | Purpose |
|--------|--------|---------|
| `archive_scope` | Archive Job `archive_scope` | Season ID for this batch |
| `archive_job_id` | Archive Job `name` | Export run identifier |
| `schema_version` | Archive Job `schema_version` | Schema contract version |
| `exported_at` | Current timestamp at export | Lineage/debugging |

**For live sync exports**:

| Column | Source | Purpose |
|--------|--------|---------|
| `scope_type` | Literal `'live'` | Distinguishes from archive rows |
| `sync_batch_id` | Generated sync run identifier | Identifies the specific sync run |
| `schema_version` | Sync YAML `version` | Schema contract version |
| `synced_at` | Current timestamp at sync | When this snapshot was taken |

### 8.2 Grain

One row per `(player_id, item_id)` — same as source. Single fact table, no split.

---

## 9. Dimensions Design

### 9.1 Dimension Inventory

| Dimension | Version | Source | Scope | New/Existing |
|-----------|---------|--------|-------|--------------|
| Player | v2 | `Memora Player Profile` | Batch-scoped | New version |
| Review Item | v1 | `Memora Review Item` | Batch-scoped | Existing |
| Season | v1 | `Memora Season` | Scope-specific | New |
| Plan | v1 | `Memora Academic Plan` | Batch-scoped | New |

### 9.2 Player Dimension (v2)

**Why v2**: v1 preserved for backward compatibility. v2 adds plan enrichment and removes PII.

| Field | Source | Notes |
|-------|--------|-------|
| `player_id` | `name` | Primary key |
| `grade` | `grade` | Segmentation |
| `major` | `major` | Segmentation |
| `season_id` | `season` | Season link (renamed for consistency) |
| `plan_id` | `plan` | Plan link |
| `plan_name` | Joined from Academic Plan | Denormalized for convenience |

**Excluded (privacy)**:
- `mobile` — direct PII, not analytically needed
- `display_name` — user-identifying, restricted to presentation layer
- `gender` — excluded by default unless explicitly approved

**Snapshot**: Point-in-time at archive. Survives upstream changes.

### 9.3 Review Item Dimension (v1)

| Field | Source |
|-------|--------|
| `item_id` | `item_id` |
| `subject` | `subject` |
| `topic` | `topic` |
| `lesson` | `lesson` |
| `question_text` | `question_text` |
| `difficulty` | `difficulty` |
| `item_type` | `item_type` |

**Snapshot**: Point-in-time at archive. Critical — archived data must survive content deletion in production.

### 9.4 Season Dimension (v1)

| Field | Source |
|-------|--------|
| `season_id` | `name` |
| `season_title` | `season_title` |
| `start_date` | `start_date` |
| `end_date` | `end_date` |

### 9.5 Plan Dimension (v1)

| Field | Source |
|-------|--------|
| `plan_id` | `name` |
| `plan_name` | `plan_name` |
| `grade` | `grade` |
| `major` | `major` |
| `season_id` | `season` |
| `is_published` | `is_published` |

### 9.6 Batch Scoping

All dimensions are **batch-scoped**: only records referenced by the fact rows in that batch are exported. No full-table dimension exports.

---

## 10. Manifest Contract

### 10.1 Manifest Fields

| Field | Value |
|-------|-------|
| `dataset_key` | `practice_log` |
| `kind` | `archive` or `live_sync` |
| `scope_key` | **Archive**: Season ID (e.g., `SEAS-00027`). **Live sync**: `active_snapshot` |
| `schema_version` | `v1` |
| `batch_id` | **Archive**: Archive Job name (e.g., `ARCH-00042`). **Live sync**: sync run ID (e.g., `SYNC-20260310-0200`) |

### 10.2 Files Array

Each file entry includes: `file_name`, `role`, `entity`, `schema_version`, `row_count`, `checksum`.

| File | Role | Entity | Schema |
|------|------|--------|--------|
| `fact_practice_log.parquet` | `fact` | `practice_log` | v1 |
| `dim_player.parquet` | `dimension` | `player` | v2 |
| `dim_review_item.parquet` | `dimension` | `review_item` | v1 |
| `dim_season.parquet` | `dimension` | `season` | v1 |
| `dim_plan.parquet` | `dimension` | `plan` | v1 |

### 10.3 Validation Rules

**Hard-fail** (block publish):
1. All required files present (fact + all declared dimensions)
2. Dimension referential coverage (all FKs in fact exist in their dimension)
3. Schema version match (file versions = manifest-declared versions)
4. No duplicate `(player_id, item_id)` in fact
5. Checksum verification (all files)

**Warning** (valid but flagged):
6. Row count = 0 — legitimate empty batch

---

## 11. Dataset Registry Entry

| Field | Value |
|-------|-------|
| `dataset_key` | `practice_log` |
| `kind` | `archive` + `live_sync` |
| `source_doctype` | `tabMemora Practice Log` |
| `schema_version` | `v1` |
| `grain` | One row per `(player_id, item_id)` |
| `dimensions` | `player.v2`, `review_item.v1`, `season.v1`, `plan.v1` |
| `publish_mode` | `replace` |
| `retention_versions` | 1 previous |
| `is_active` | `true` |

**Primary analytical filter dimensions**: `season_id`, `plan_id`, `grade`, `major`, `subject`, `topic`, `lesson`

---

## 12. DuckDB Analytical Layers

### 12.1 Raw Layer

Two separate raw sources, no unified canonical raw view:

| Raw Source | Data |
|------------|------|
| `raw_practice_log_archive` | Archived Parquet data (closed seasons) |
| `raw_practice_log_live` | Live sync snapshot (current season) |

**Design principle**: Close to physical artifacts, clear provenance, independently inspectable. Precedence logic applied at curated layer, not raw.

### 12.2 Curated Layer

**Unified authoritative model** — `curated_practice_log`:

1. Union `raw_practice_log_archive` + `raw_practice_log_live`
2. Apply precedence: archive wins for completed seasons
3. Deduplicate on `(player_id, item_id)` — last-write-wins
4. Join with dimensions: Player v2, Review Item v1, Season v1, Plan v1
5. Produce derived fields

**Derived fields**:

| Field | Formula | Notes |
|-------|---------|-------|
| `correct_rate` | `correct_count / NULLIF(attempt_count, 0)` | Safe zero-division |
| `incorrect_count` | `attempt_count - correct_count` | |
| `practice_span_days` | `DATEDIFF(last_seen_at, first_seen_at)` | Duration of engagement |
| `days_since_last_seen` | `DATEDIFF(CURRENT_DATE, last_seen_at)` | Recency signal |
| `source_layer` | `'archive'` or `'live'` | Lineage |

**Deferred**: `is_mastered` — requires formal mastery threshold (pushed to mart layer).

### 12.3 Marts Layer

#### Mart 1: Item Difficulty

| Property | Value |
|----------|-------|
| Grain | `item_id × season_id × plan_id` |
| Type | Materialized, partitioned by `season_id` |

| Metric | Formula |
|--------|---------|
| `total_players` | `COUNT(DISTINCT player_id)` |
| `avg_correct_rate` | `AVG(correct_rate)` |
| `median_attempts` | `MEDIAN(attempt_count)` |
| `avg_attempts` | `AVG(attempt_count)` |
| `pct_never_correct` | `COUNT(correct_count=0) / total` |
| `avg_practice_span_days` | `AVG(practice_span_days)` |

Dimension attributes: `subject`, `topic`, `lesson`, `plan_name`, `grade`.
Rollups: `item × season`, `item × plan`, `item` (global all-time).

#### Mart 2a: Student Practice Behavior

| Property | Value |
|----------|-------|
| Grain | `player_id × season_id` |
| Type | Materialized, partitioned by `season_id` |

| Metric | Formula |
|--------|---------|
| `total_items_practiced` | `COUNT(DISTINCT item_id)` |
| `total_attempts` | `SUM(attempt_count)` |
| `total_correct` | `SUM(correct_count)` |
| `overall_correct_rate` | `total_correct / total_attempts` |
| `avg_attempts_per_item` | `total_attempts / total_items_practiced` |
| `items_single_attempt` | `COUNT WHERE attempt_count = 1` |
| `items_multi_attempt` | `COUNT WHERE attempt_count > 1` |
| `avg_practice_span_days` | `AVG(practice_span_days)` |
| `first_practice_date` | `MIN(first_seen_at)` |
| `last_practice_date` | `MAX(last_seen_at)` |

Player attributes: `plan_id`, `plan_name`, `grade`, `major`.

> **Note**: `plan_name` is denormalized from the Plan dimension into the Player snapshot for convenience. `plan_type` does not exist on `Memora Academic Plan` and is not included.

#### Mart 2b: Student Subject Practice

| Property | Value |
|----------|-------|
| Grain | `player_id × season_id × subject` |
| Type | Materialized, partitioned by `season_id` |

Same field set as Mart 2a, scoped per subject. Enables cross-subject comparison per student.

#### Mart 3: Practice Effectiveness

| Property | Value |
|----------|-------|
| Grain | `attempt_count_bucket × season_id × plan_id` |
| Type | Materialized, partitioned by `season_id` |

Buckets: 1, 2-3, 4-5, 6-10, 11+

| Metric | Formula |
|--------|---------|
| `total_pairs` | `COUNT` of (player, item) pairs in bucket |
| `avg_correct_rate` | `AVG(correct_rate)` |
| `pct_last_correct` | `% WHERE last_result = 'Correct'` |
| `pct_never_correct` | `% WHERE correct_count = 0` |
| `avg_correct_count` | `AVG(correct_count)` |

**Documented limitation**: Individual student trajectory analysis from this dataset is approximate (cumulative state, not event stream).

#### Mart 4: Content Quality Signals

| Property | Value |
|----------|-------|
| Grain | `subject × topic × season_id × plan_id` |
| Type | Materialized, partitioned by `season_id` |

| Metric | Formula |
|--------|---------|
| `total_items` | `COUNT(DISTINCT item_id)` |
| `total_players` | `COUNT(DISTINCT player_id)` |
| `total_attempts` | `SUM(attempt_count)` |
| `avg_correct_rate` | `AVG(correct_rate)` |
| `avg_attempts_per_item` | `SUM(attempt_count) / total_items` |
| `pct_items_low_accuracy` | `% of items below threshold` |
| `pct_players_struggling` | `% of players below threshold` |

Thresholds defined at reporting time, not hardcoded.

**Optional drill-down**: `Mart 4b` at lesson level (`subject × topic × lesson × season_id × plan_id`). Not partitioned.

#### Mart 5: Cohort/Segment Analysis

| Property | Value |
|----------|-------|
| Grain | `season × plan`, `season × grade`, `season × plan × grade` |
| Type | Rollup views on Mart 2a (not standalone materialized) |

Materialize only if query performance justifies it.

#### Mart 6: Intervention Readiness

| Property | Value |
|----------|-------|
| Grain | `player_id × season_id` |
| Type | Materialized (on top of Mart 2a + curated), partitioned by `season_id` |

| Metric | Formula |
|--------|---------|
| `items_never_correct` | `COUNT WHERE correct_count = 0` |
| `pct_items_never_correct` | `items_never_correct / total_items_practiced` |
| `avg_days_since_last_seen` | `AVG(days_since_last_seen)` |
| `struggling_topics` | `COUNT(DISTINCT topic) WHERE topic correct_rate < threshold` |

Thresholds expected to evolve over time.

---

## 13. Privacy and Sensitive Data

| Field | Treatment |
|-------|-----------|
| `mobile` | **Excluded** — direct PII, not analytically needed |
| `display_name` | **Excluded** — add only in restricted presentation layer |
| `gender` | **Excluded by default** — include only if explicitly approved |
| `grade`, `major` | **Included** — analytically useful for segmentation |

**Privacy stance**: Minimal-data-by-default. Demographic fields are opt-in, not automatic.

---

## 14. Data Quality Rules (16 Hard-Fail Validations)

### 14.1 Null Checks (7)

| Rule | Column |
|------|--------|
| DQ-01 | `player_id IS NOT NULL` |
| DQ-02 | `item_id IS NOT NULL` |
| DQ-03 | `first_seen_at IS NOT NULL` |
| DQ-04 | `last_seen_at IS NOT NULL` |
| DQ-05 | `last_result IS NOT NULL` |
| DQ-06 | `attempt_count IS NOT NULL` |
| DQ-07 | `correct_count IS NOT NULL` |

### 14.2 Value/Scope Constraints (6)

| Rule | Constraint |
|------|-----------|
| DQ-08 | `attempt_count >= 1` |
| DQ-09 | `correct_count >= 0` |
| DQ-10 | `correct_count <= attempt_count` |
| DQ-11 | `last_result IN ('Correct', 'Incorrect')` |
| DQ-12 | `first_seen_at <= last_seen_at` |
| DQ-13 | `last_seen_at` within declared archive scope date range |

### 14.3 Referential Checks (2)

| Rule | Constraint |
|------|-----------|
| DQ-14 | Every `player_id` in fact exists in `dim_player` |
| DQ-15 | Every `item_id` in fact exists in `dim_review_item` |

### 14.4 Uniqueness (1)

| Rule | Constraint |
|------|-----------|
| DQ-16 | No duplicate `(player_id, item_id)` pairs in fact |

### 14.5 Warnings (Non-Blocking)

- Row count = 0 — legitimate empty batch, flagged but valid
- Volume anomalies, distribution shifts — monitoring signals only

---

## 15. Acceptance Tests (19 Scenarios)

### Core Tests (12)

| # | Test | Type |
|---|------|------|
| AT-01 | Export round-trip — Parquet row count matches source query | Functional |
| AT-02 | All 16 DQ rules pass on exported batch | Functional |
| AT-03 | Manifest completeness — all files present, checksums valid, per-file metadata correct | Functional |
| AT-04 | Raw archive layer queryable in DuckDB | Integration |
| AT-05 | Raw live layer queryable in DuckDB | Integration |
| AT-06 | Curated layer applies precedence, deduplicates, joins dimensions correctly | Integration |
| AT-07 | Derived fields compute correctly (correct_rate, incorrect_count, practice_span_days, days_since_last_seen, source_layer) | Integration |
| AT-08 | Mart 1 (Item Difficulty) aggregates match manual calculation | Integration |
| AT-09 | Mart 2a (Student Behavior) per-student rollup matches manual calculation | Integration |
| AT-10 | No duplicate (player_id, item_id) in curated after union + dedup | Integrity |
| AT-11 | Handoff correctness — after archive validation, live sync excludes archived season; no double counting | Integration |
| AT-12 | Purge safety — after purge, MariaDB rows gone; DuckDB still returns archived data | Functional |

### Edge-Case Tests (6)

| # | Test | Type |
|---|------|------|
| AT-13 | Empty batch — season with zero practice rows: export succeeds, manifest valid, DQ passes | Edge case |
| AT-14 | Cross-season row — (player, item) spanning seasons lands only in latest season's archive | Edge case |
| AT-15 | Handoff overlap — temporary live/archive overlap does not cause double counting in curated | Edge case |
| AT-16 | Delete propagation — production deletes in live layer; archived historical data preserved | Edge case |
| AT-17 | Batch-scoped dimensions — exactly referenced members, no missing, no excess | Edge case |
| AT-18 | Privacy contract — mobile, display_name absent from archived player snapshot | Edge case |

### Recovery Test (1)

| # | Test | Type |
|---|------|------|
| AT-19 | Rebuild/backfill — re-export same scope produces identical output (idempotent) | Recovery |

---

## 16. Required Production Changes

### 16.1 DDL

```sql
CREATE INDEX idx_last_seen_at ON `tabMemora Practice Log` (last_seen_at);
```

**Impact**: Minor write-side cost (UPSERT maintains one additional index). No behavior change to existing read/write paths.

### 16.2 Schema Files

| File | Action | Details |
|------|--------|---------|
| `player.v2.yaml` | Create | Plan-enriched, PII-excluded player dimension |
| `season.v1.yaml` | Create | Season dimension |
| `plan.v1.yaml` | Create | Academic Plan dimension |
| `practice_log.v1.yaml` | Update | Add export-time metadata columns, reference player.v2, season.v1, plan.v1 |
| `practice_log_live.v1.yaml` | Update | Same dimension updates, scope exclusion logic |

### 16.3 Code Changes

| Component | Change | Details |
|-----------|--------|---------|
| Exporter | Enhance | Add export-time columns to Parquet output |
| Validator | Enhance | Per-file checksums/row counts, referential checks, 16 DQ rules |
| Live sync | Enhance | Scope exclusion for archived-and-validated seasons |
| Monitoring | Add | Freshness, stuck-state, retry exhaustion, validation lag alerts |

### 16.4 No Changes Required

- UPSERT logic (`practice.py:418-472`) — unchanged
- Session filtering queries (`practice.py:93-400`) — unchanged
- Cascade delete (`review_items.py:260-290`) — unchanged
- Source table schema — no new columns added

**All changes are additive. No existing production business logic is modified.**

---

## 17. Ownership and Operations

### 17.1 Ownership

| Role | Responsibility |
|------|----------------|
| **Business owner** | Product/analytics — defines analytical questions, validates mart/KPI semantic correctness |
| **Technical owner** | Engineering/data platform — maintains pipeline, monitors health, handles failures |

### 17.2 Operational Monitoring

**Existing** (retained):
- 3 retry attempts per failed job
- Daily failure notification at 06:00

**New safeguards**:

| Alert | Trigger |
|-------|---------|
| Live sync freshness | No successful sync within 24h SLA |
| Archive validation lag | Export complete but not validated within defined window |
| Retry exhaustion | All 3 retries exhausted — escalate immediately |
| Stuck handoff/purge | Job stuck between stages beyond threshold |

**Purge remains blocked** unless validation succeeds and archive is queryable.

---

## 18. Unresolved Questions

| # | Question | Impact | Owner |
|---|----------|--------|-------|
| 1 | What is the formal mastery threshold for `is_mastered`? | Mart 6 intervention readiness scoring | Business owner |
| 2 | What are the exact `pct_items_low_accuracy` and `pct_players_struggling` thresholds for Mart 4? | Content quality signal sensitivity | Business owner |
| 3 | Should `gender` be included in the player dimension if an approved use case emerges? | Privacy policy | Business + legal |
| 4 | What is the exact grace period between archive validation and purge execution? | Operational safety window | Technical owner |
| 5 | What is the exact time window for archive validation lag alerts? | Monitoring sensitivity | Technical owner |
| 6 | Should Mart 5 (Cohort/Segment) be materialized if query volume grows? | DuckDB performance | Technical owner |

---

## 19. Files To Be Produced

| # | File | Purpose |
|---|------|---------|
| 1 | `archive_schemas/dimensions/player.v2.yaml` | Player dimension (plan-enriched, PII-excluded) |
| 2 | `archive_schemas/dimensions/season.v1.yaml` | Season dimension |
| 3 | `archive_schemas/dimensions/plan.v1.yaml` | Plan dimension |
| 4 | `archive_schemas/archive_types/practice_log.v1.yaml` (update) | Export-time metadata + new dimension refs |
| 5 | `archive_schemas/sync_types/practice_log_live.v1.yaml` (update) | Dimension updates + scope exclusion |
| 6 | Dataset registry entry | `practice_log` registration |
| 7 | DuckDB raw layer definitions | `raw_practice_log_archive`, `raw_practice_log_live` |
| 8 | DuckDB curated layer definition | `curated_practice_log` with derived fields |
| 9 | DuckDB mart definitions | 5 materialized marts (1, 2a, 2b, 3, 4, 6) + 1 rollup view family (5) + 1 optional drill-down (4b) |
| 10 | DQ validation rules implementation | 16 hard-fail rules |
| 11 | Acceptance test specifications | 19 test scenarios |
| 12 | Monitoring/alerting configuration | 4 new alert types |
| 13 | DDL migration | `idx_last_seen_at` index |
