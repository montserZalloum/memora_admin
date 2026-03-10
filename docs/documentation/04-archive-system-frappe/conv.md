# Dataset Onboarding Session: tabMemora Practice Log

**Date**: 2026-03-09 / 2026-03-10
**Status**: Phase 14 complete — PRD generation in progress
**Dataset**: `tabMemora Practice Log`
**Goal**: Full end-to-end analytics pipeline onboarding

---

## Phase 0 — Task Definition ✅ LOCKED

### Phase 0.1 — Definition

- **Dataset**: `tabMemora Practice Log` (raw SQL table, not a Frappe DocType)
- **Nature**: Cumulative state table per `(player_id, item_id)` — not an event stream
- **Goal**: Full end-to-end analytics pipeline: archive contract → manifest → DuckDB raw → curated → marts → DQ rules → PRD
- **Operational outcome**: Keep production MariaDB lean, archived data queryable in DuckDB, extensible for future reporting

**6 analytical domains**:

1. **Item difficulty** — lowest correct rate, most attempts before mastery, disproportionately difficult items
2. **Student practice behavior** — practice intensity, unique items over time, revisit vs abandon patterns
3. **Practice effectiveness** — frequency vs outcomes correlation, attempts before improvement, repeated exposure leading to correctness
4. **Content quality signals** — confusing/misleading items, subjects/topics/units with low accuracy or high retry counts
5. **Cohort and segment analysis** — patterns by cohort, grade, subject, plan; identifiable segments (high-effort/low-accuracy, low-effort/high-accuracy)
6. **Intervention and personalization readiness** — persistent struggle detection, content areas for adaptive review

**Important modeling note**: This is a cumulative state table, not a full event stream. Treat it as a behavioral fact source for mastery/repetition/accuracy analytics, but not as a perfect session-event dataset.

### Phase 0.2 — Flow Type

- **Flow type**: Live sync + Archive (both required)
- **Live sync**: Current active/unarchived range → daily full snapshot into DuckDB
- **Archive**: Closed historical ranges → permanent Parquet in DuckDB
- **Unified view**: DuckDB combines both sources, non-overlapping to prevent double counting
- **Rationale**: In-season visibility needed for intervention, item difficulty, and behavior monitoring — can't wait for season end

---

## Phase 1 — Understanding the Source Data ✅ LOCKED

### Phase 1.1–1.3 — Table Definition

- **Source**: `setup.py:623-645` is canonical DDL
- **Table type**: Raw SQL, not Frappe DocType, no ORM
- **PK**: `(player_id, item_id)` — composite, one row per student per item
- **7 columns**: all NOT NULL, no JSON/blob/text fields
- **Schema drift rule**: Any future column changes = explicit schema version bump, not implicit

**Schema**:

| Column | Type | Nullable | Default | Purpose |
|--------|------|----------|---------|---------|
| `player_id` | VARCHAR(140) | NO | — | FK → Memora Player Profile |
| `item_id` | VARCHAR(36) | NO | — | FK → Memora Review Item (UUID) |
| `first_seen_at` | DATETIME | NO | — | First encounter |
| `last_seen_at` | DATETIME | NO | — | Most recent encounter |
| `last_result` | ENUM('Correct','Incorrect') | NO | — | Latest answer |
| `attempt_count` | INT UNSIGNED | NO | 1 | Total attempts |
| `correct_count` | INT UNSIGNED | NO | 0 | Correct attempts |

### Phase 1.4–1.5 — Indexes, Performance, Volume

- **Existing indexes**: PK `(player_id, item_id)`, `idx_item_id(item_id)`, `idx_player_seen_item(player_id, last_seen_at, item_id)` — all serve production read/write paths
- **New index required**: Dedicated `idx_last_seen_at(last_seen_at)` for archive export, live sync, and purge range queries
- **Volume assumption**: Unknown exact count, but growth-bound high — design for large table (projected ~500M rows at 100K users)
- **Validation**: EXPLAIN against real production data after index is added

### Phase 1.6 — Relationships

- **Core dimensions (required)**:
  1. `Memora Player Profile` — student-level analysis, cohorting
  2. `Memora Review Item` — item difficulty, content quality, hierarchy context
  3. Season/archive-window metadata — scoping live vs archived ranges
  4. Time dimension (derived) — daily/weekly/monthly/seasonal reporting from `first_seen_at`/`last_seen_at`
- **Recommended extension**: Plan/subscription/cohort data — enables segment comparison
- **Optional future**: Memory State/FSRS — mastery correlation, not a blocker for initial pipeline

---

## Phase 2 — Business Rules and Source of Truth ✅ LOCKED

### Phase 2.2 — Source of Truth

- **Active range**: MariaDB authoritative, DuckDB live sync = point-in-time analytical copy (acceptable staleness up to daily sync SLA)
- **Archived range**: DuckDB Parquet authoritative, MariaDB rows purged
- **Discrepancy handling**: MariaDB wins during active range; next sync cycle or repair/backfill reconciles
- **Key modeling note**: Mutable cumulative-state table → daily snapshots are analytically useful, not operationally canonical

### Phase 2.3 — Official Time Field

- **Primary time field**: `last_seen_at` — drives archive scoping, purge, partitioning, live-sync boundary, time-series reporting
- **Secondary time field**: `first_seen_at` — retained for first-exposure cohorting, time-to-mastery analysis, exposure age
- **Timezone**: DATETIME stored as-is (no TZ info in column), reporting timezone to be defined at mart layer

---

## Phase 3 — Change and Delete Semantics ✅ LOCKED

### Phase 3.1 — Update Semantics

- **Rows are mutable**: UPSERT on every batch submit updates `last_seen_at`, `last_result`, increments counters
- **UPSERT pattern**:
  ```sql
  INSERT ... ON DUPLICATE KEY UPDATE
      last_seen_at = VALUES(last_seen_at),
      last_result = VALUES(last_result),
      attempt_count = attempt_count + 1,
      correct_count = correct_count + VALUES(correct_count)
  ```
- **Latest cumulative state is sufficient** at the detailed (player, item) level
- **No full daily snapshot history** per row — too expensive at scale
- **Historical trending**: Captured via daily aggregate marts (per day/item/student/subject/cohort), not via retaining every daily row state

### Phase 3.2 — Delete Semantics

- **Hard deletes only** — no soft delete mechanism
- **Two delete paths**: cascade (item removed) and archive purge (post-export cleanup)
- **Live sync layer**: Deletes propagate — next sync cycle reflects the deletion
- **Archived layer**: Deletes do NOT retroactively propagate — historical behavior data is preserved
- **Modeling implication**: Archive must snapshot enough item dimension context at export time so facts remain interpretable even if the source Review Item is later deleted

### Phase 3.3 — Idempotency and Uniqueness

- **Deduplication key**: `(player_id, item_id)` — same as source PK
- **Cross-batch overlap**: If the same row appears in multiple archive batches, the **latest batch's version replaces** the earlier one in the curated layer (last-write-wins)
- **No duplicate cumulative snapshots** retained at the detailed level
- **Historical trending**: Derived from aggregate marts, not from row-level version history
- **Double counting prevention**: Curated layer enforces `(player_id, item_id)` uniqueness

---

## Phase 4 — Archive Scope Definition ✅ LOCKED

### Phase 4.1 — Archive Trigger

- **Archive trigger**: Season closure (`is_published=0`, `end_date < today`) — detected by daily task at 01:20
- **Execution**: Archive Job created per `(source_doctype, archive_scope, schema_version)` — unique constraint prevents duplicates
- **Scoping mechanism**: Temporal via `last_seen_at` range from season `start_date`/`end_date` — no `season_id` column on source table
- **Cross-season rows**: Acceptable that a row lands only in the latest season's archive — cumulative counters carry forward
- **Season-specific analytics**: Must come from season-scoped aggregate marts, not from detailed archive rows alone
- **Documented limitation**: Detailed archive is not a season-pure fact table

**How season scoping works today (from codebase)**:

1. `Memora Season` has `start_date`, `end_date`, `is_published` (1=active, 0=ended)
2. Daily task `check_seasons_for_archive()` (01:20) detects ended seasons
3. Creates Archive Job with `meta.query_filter = {date_from, date_to, filter_column: "last_seen_at"}`
4. Executor exports rows in that `last_seen_at` range to Parquet
5. `(source_doctype, archive_scope, schema_version)` unique constraint prevents duplicate jobs

### Phase 4.2 — Scope Definition

- **Scope key**: Season ID (e.g., `SEAS-00027`)
- **Scope filter**: `WHERE last_seen_at >= season.start_date AND last_seen_at < season.end_date`
- **Filter column**: `last_seen_at`
- **Query filter stored in**: Archive Job `meta` JSON field

### Phase 4.3 — Post-Archive Behavior

- **Post-archive action**: `Delete` (default)
- **Purge preconditions**: Export complete + manifest committed + validation checks passed + archive queryable in DuckDB
- **Grace period**: Short configurable delay before purge execution
- **Purge mechanism**: Batched deletes (10K chunks, 2s sleep) — already implemented

---

## Phase 5 — Export Data Design ✅ LOCKED

### Phase 5.1 — Fact Table Design

- **Grain**: One row per `(player_id, item_id)` — cumulative state
- **Source columns**: All 7 exported as-is, no exclusions
- **Export-time metadata columns added to Parquet**:
  - `archive_scope` — season ID from Archive Job
  - `archive_job_id` — specific export run identifier
  - `schema_version` — schema contract version
  - `exported_at` — optional, for lineage/debugging
- **No derived business metrics at export time**
- **Single fact table** — no split needed

### Phase 5.2 — Dimensions Design

- **Player dimension** (`player.v2` — new version):
  - Core: `player_id`, `grade`, `major`, `season`
  - Enriched at archive: `plan_id`, `plan_name`, `plan_type` (minimal cohort/segmentation context)
  - Snapshot: Point-in-time at archive — survives upstream changes
  - Deeper subscription lifecycle details: **not** included, join later if needed

- **Review Item dimension** (`review_item.v1` — existing):
  - Core: `item_id`, `subject`, `topic`, `lesson`, `question_text`, `difficulty`, `item_type`
  - Snapshot: Point-in-time at archive — critical for surviving content deletion

- **Season dimension** (`season.v1` — new):
  - Core: `season_id`, `season_title`, `start_date`, `end_date`
  - Small, mostly static — snapshotted once per archive

- **Plan dimension** (`plan.v1` — new):
  - Source: `Memora Academic Plan`
  - Core: `plan_id`, `plan_name`, `grade`, `major`, `season_id`, `is_published`
  - Relationship: Season 1:N Plan (each plan belongs to exactly one season)

### Phase 5.3 — Batch-Scoped Dimensions

- **Default**: Batch-scoped dimensions — only export dimension records referenced by the batch's fact rows
- Player snapshot: Only players appearing in the exported practice log rows
- Review Item snapshot: Only items appearing in the exported practice log rows
- Season: Scope-specific to the archive batch's season
- No full-table dimension exports

### Phase 5.4 — Sensitive Data and Privacy

- **`mobile`**: Excluded entirely — direct PII, not needed
- **`display_name`**: Excluded from base archive — add only in restricted presentation layer if needed
- **`gender`**: Excluded by default — include only if explicitly approved analytical use case exists
- **`grade`, `major`**: Included — analytically useful for segmentation
- **Privacy stance**: Minimal-data-by-default, demographic fields are opt-in

---

## Phase 6 — Live Sync Design ✅ LOCKED

- **Mode**: Full snapshot, daily, replaces prior live state in DuckDB
- **Scope**: Active/unarchived range — determined by which seasons are NOT yet archived-and-validated
- **Dimensions**: Same as archive, batch-scoped to snapshot
- **Handoff**: Option 2 — live sync continues including a closed season until archive is fully validated and queryable
- **Overlap handling**: Transitional only — curated/query layer applies precedence rule (archive wins once promoted, live version excluded for that season)
- **Signal for exclusion**: Archive Job status reaches a "validated and queryable" state (e.g., `Ingested` or `Completed`)

---

## Phase 7 — Data Contracts and Files ✅ LOCKED

### Phase 7.1 — Archive YAML

The existing `archive_types/practice_log.v1.yaml` needs updates:

- Add export-time metadata columns (`archive_scope`, `archive_job_id`, `schema_version`, `exported_at`)
- Confirm dimension references include the enriched Player snapshot (with plan fields, minus PII)
- Add Season and Plan as dimensions

### Phase 7.2 — Sync YAML

The existing `sync_types/practice_log_live.v1.yaml` needs:

- Same dimension references as archive
- Explicit retention logic: each sync replaces prior snapshot (no accumulation)
- Scope filter logic: exclude seasons with completed/validated archive jobs

### Phase 7.3 — Dimension YAMLs

- `player.v1.yaml` — preserved intact for backward compatibility
- `player.v2.yaml` — **new version** with plan fields (`plan_id`, `plan_name`, `plan_type`), excluding PII (`mobile`, `display_name`, `gender`)
- `review_item.v1.yaml` — existing, sufficient as-is
- `season.v1.yaml` — **new** (`season_id`, `season_title`, `start_date`, `end_date`)
- `plan.v1.yaml` — **new** (`plan_id`, `plan_name`, `grade`, `major`, `season_id`, `is_published`)
- Practice Log archive + live-sync YAMLs reference: `player.v2`, `review_item.v1`, `season.v1`, `plan.v1`

---

## Phase 8 — Manifest Definition ✅ LOCKED

### Phase 8.1 — Contract

| Field | Value |
|-------|-------|
| `dataset_key` | `practice_log` |
| `kind` | `archive` or `live_sync` |
| `scope_key` | Season ID (e.g., `SEAS-00027`) |
| `schema_version` | `v1` |
| `batch_id` | Archive Job name (e.g., `ARCH-00042`) |

**Files in manifest** (all with per-file metadata: `file_name`, `role`, `entity`, `schema_version`, `row_count`, `checksum`):

| File | Role | Entity | Schema Version |
|------|------|--------|----------------|
| `fact_practice_log.parquet` | `fact` | `practice_log` | v1 |
| `dim_player.parquet` | `dimension` | `player` | v2 |
| `dim_review_item.parquet` | `dimension` | `review_item` | v1 |
| `dim_season.parquet` | `dimension` | `season` | v1 |
| `dim_plan.parquet` | `dimension` | `plan` | v1 |

### Phase 8.2 — Validation Contract

**Hard-fail validations** (manifest invalid — block publish):

1. All required files present (fact + all declared dimensions)
2. Dimension referential coverage (`player_id` → dim_player, `item_id` → dim_review_item)
3. Schema version match (file versions = manifest-declared versions)
4. No duplicate PKs in fact (no repeated `(player_id, item_id)`)
5. Checksum verification (all files)

**Warning** (valid but flagged):

6. Row count = 0 — legitimate empty batch, not a contract failure

---

## Phase 9 — Dataset Registry ✅ LOCKED

### Phase 9.1 — Registry Entry

| Field | Value |
|-------|-------|
| `dataset_key` | `practice_log` |
| `kind` | `archive` + `live_sync` |
| `source_doctype` | `tabMemora Practice Log` (raw SQL) |
| `schema_version` | `v1` |
| `grain` | One row per `(player_id, item_id)` — cumulative state |
| `dimensions` | `player.v2`, `review_item.v1`, `season.v1`, `plan.v1` |
| `publish_mode` | `replace` |
| `retention_versions` | 1 previous version for rollback |
| `is_active` | `true` |

### Phase 9.2 — Publish Policies

- **Archive publish mode**: `replace` — one authoritative version per `(dataset_key, archive_scope, schema_version)`
- **Retention**: Current + 1 previous version for rollback
- **Rebuild**: Can re-export from MariaDB if data hasn't been purged yet

**Primary analytical filter dimensions**:
- `season_id` (via Season dimension)
- `plan_id` (via Plan dimension or Player snapshot)
- `grade`, `major` (via Player snapshot)
- `subject`, `topic`, `lesson` (via Review Item dimension)

---

## Phase 10 — DuckDB Analytical Layers ✅ LOCKED

### Phase 10.1 — Raw Layer

- **Two separate raw sources**:
  - `raw_practice_log_archive` — archived Parquet data
  - `raw_practice_log_live` — live sync snapshot data
- **No unified raw view as canonical** — optional convenience union for exploration only
- **Precedence logic**: Applied at curated layer, not raw
- **Raw design principle**: Close to physical artifacts, clear provenance, independently inspectable

### Phase 10.2 — Curated Layer

**Curated layer responsibilities**:
1. Union archive + live raw sources
2. Apply precedence (archive wins for completed seasons)
3. Deduplicate on `(player_id, item_id)` — last-write-wins
4. Join with dimensions (Player v2, Review Item v1, Season v1, Plan v1)
5. Produce derived fields

**Curated derived fields**:

| Field | Formula | Notes |
|-------|---------|-------|
| `correct_rate` | `correct_count / NULLIF(attempt_count, 0)` | Safe zero-division handling |
| `incorrect_count` | `attempt_count - correct_count` | Simple derivation |
| `practice_span_days` | `DATEDIFF(last_seen_at, first_seen_at)` | Renamed from exposure_age_days |
| `days_since_last_seen` | `DATEDIFF(CURRENT_DATE, last_seen_at)` | Recency / intervention signal |
| `source_layer` | `'archive'` or `'live'` | Lineage / debugging |

**Deferred to marts**: `is_mastered` — requires a formal mastery threshold definition

### Phase 10.3 — Marts Layer

| # | Mart | Grain | Type | Partitioned by Season |
|---|------|-------|------|-----------------------|
| 1 | Item Difficulty | `item × season × plan` | Materialized | Yes |
| 2a | Student Practice Behavior | `player × season` | Materialized | Yes |
| 2b | Student Subject Practice | `player × season × subject` | Materialized | Yes |
| 3 | Practice Effectiveness | `attempt_bucket × season × plan` | Materialized | Yes |
| 4 | Content Quality Signals | `subject × topic × season × plan` | Materialized | Yes |
| 4b | Content Quality (Lesson) | `subject × topic × lesson × season × plan` | Optional drill-down | No |
| 5 | Cohort/Segment Analysis | `season × plan`, `season × grade`, etc. | Rollup views on Mart 2a | No |
| 6 | Intervention Readiness | `player × season` | Materialized (on top of 2a) | Yes |

**Mart details**:

**Mart 1 — Item Difficulty**: `total_players`, `avg_correct_rate`, `median_attempts`, `avg_attempts`, `pct_never_correct`, `avg_practice_span_days` + dimension attributes. Rollups derived downstream: `item × season`, `item × plan`, `item` (global).

**Mart 2a — Student Practice Behavior**: `total_items_practiced`, `total_attempts`, `total_correct`, `overall_correct_rate`, `avg_attempts_per_item`, `items_single_attempt`, `items_multi_attempt`, `avg_practice_span_days`, `first_practice_date`, `last_practice_date` + player dimension attributes.

**Mart 2b — Student Subject Practice**: Same field set as 2a but scoped per subject. Separate mart, not overloaded into 2a.

**Mart 3 — Practice Effectiveness**: `attempt_count_bucket` (1, 2-3, 4-5, 6-10, 11+), `total_pairs`, `avg_correct_rate`, `pct_last_correct`, `pct_never_correct`, `avg_correct_count`. Population-level analysis. Individual student trajectory is approximate (cumulative state, not event stream).

**Mart 4 — Content Quality Signals**: `total_items`, `total_players`, `total_attempts`, `avg_correct_rate`, `avg_attempts_per_item`, `pct_items_low_accuracy`, `pct_players_struggling`. Thresholds defined at reporting time, not hardcoded.

**Mart 5 — Cohort/Segment Analysis**: Rollup views on Mart 2a. Grains: `season × plan`, `season × grade`, `season × plan × grade`. Materialize only if performance justifies it.

**Mart 6 — Intervention Readiness**: Separate mart built on top of 2a + curated. Fields: `items_never_correct`, `pct_items_never_correct`, `avg_days_since_last_seen`, `struggling_topics`. Thresholds expected to evolve over time.

### Phase 10.4 — Performance

- **Large materialized marts** (1, 2a, 2b, 3, 4, 6): Partitioned by `season_id`
- **Rollup views / small marts** (5, 4b): No partitioning required
- **Rationale**: Season is the primary filter; partition pruning keeps scans focused as seasons accumulate

---

## Phase 11 — Data Quality and Acceptance Tests ✅ LOCKED

### Phase 11.1 — Data Quality Rules (16 hard-fail validations)

**Null checks (7)**:
- `player_id` NOT NULL
- `item_id` NOT NULL
- `first_seen_at` NOT NULL
- `last_seen_at` NOT NULL
- `last_result` NOT NULL
- `attempt_count` NOT NULL
- `correct_count` NOT NULL

**Value/scope constraints (6)**:
- `attempt_count >= 1`
- `correct_count >= 0`
- `correct_count <= attempt_count`
- `last_result IN ('Correct', 'Incorrect')`
- `first_seen_at <= last_seen_at`
- `last_seen_at` within declared archive scope date range

**Referential checks (2)**:
- Every `player_id` in fact exists in `dim_player`
- Every `item_id` in fact exists in `dim_review_item`

**Uniqueness (1)**:
- No duplicate `(player_id, item_id)` pairs in fact

**Warnings** (non-blocking):
- Row count = 0 — legitimate empty batch, flagged but valid
- Volume anomalies, distribution shifts — monitoring signals

### Phase 11.2 — Acceptance Tests (18 scenarios)

**Core acceptance tests (12)**:

| # | Test | Type |
|---|------|------|
| 1 | Export round-trip — Parquet row count matches source query | Functional |
| 2 | All 16 DQ rules pass on exported batch | Functional |
| 3 | Manifest completeness — all files present, checksums valid, per-file metadata correct | Functional |
| 4 | Raw archive layer queryable in DuckDB | Integration |
| 5 | Raw live layer queryable in DuckDB | Integration |
| 6 | Curated layer applies precedence, deduplicates, joins dimensions correctly | Integration |
| 7 | Derived fields compute correctly (correct_rate, incorrect_count, practice_span_days, days_since_last_seen, source_layer) | Integration |
| 8 | Mart 1 (Item Difficulty) aggregates match manual calculation | Integration |
| 9 | Mart 2a (Student Behavior) per-student rollup matches manual calculation | Integration |
| 10 | No duplicate (player_id, item_id) in curated after union + dedup | Integrity |
| 11 | Handoff correctness — after archive validation, live sync excludes archived season; no double counting | Integration |
| 12 | Purge safety — after purge, MariaDB rows gone; DuckDB still returns archived data | Functional |

**Edge-case tests (6)**:

| # | Test | Type |
|---|------|------|
| 13 | Empty batch — season with zero practice rows: export succeeds, manifest valid, DQ passes, publishable | Edge case |
| 14 | Cross-season row — (player, item) spanning seasons lands only in latest season's archive by last_seen_at | Edge case |
| 15 | Handoff overlap transition — temporary live/archive overlap does not cause double counting in curated | Edge case |
| 16 | Delete propagation — production deletes reflected in live layer; archived historical data preserved | Edge case |
| 17 | Batch-scoped dimension correctness — dimensions contain exactly referenced members, no missing, no excess | Edge case |
| 18 | Privacy contract — mobile, display_name absent from archived player snapshot | Edge case |

**Recovery test**:

| # | Test | Type |
|---|------|------|
| 19 | Rebuild/backfill — re-export same scope produces identical output (idempotent) | Recovery |

---

## Phase 12 — Operations and Ownership ✅ LOCKED

### Phase 12.1 — Ownership

- **Business owner**: Product/analytics side — defines questions, validates semantic correctness of marts/KPIs
- **Technical owner**: Engineering/data platform — maintains pipeline, monitors health, handles failures
- **Model**: Split ownership, close collaboration

### Phase 12.2 — Operations and Monitoring

- **Existing baseline**: 3 retries + daily failure notification at 06:00 — retained
- **Additional safeguards**:
  1. **Live sync freshness**: Alert if no successful sync within SLA (e.g., 24h)
  2. **Archive validation lag**: Alert if export complete but not validated within defined window
  3. **Retry exhaustion escalation**: Explicit alert when all retries exhausted, not just daily summary
  4. **Stuck handoff/purge**: Alert if job stuck between stages too long
- **Purge**: Remains blocked unless validation succeeds

---

## Phase 13 — Production Impact ✅ LOCKED

### Phase 13.1 — Production Changes

| Change | Type | Details |
|--------|------|---------|
| Add `idx_last_seen_at` index | DDL | `CREATE INDEX idx_last_seen_at ON \`tabMemora Practice Log\` (last_seen_at)` |
| Create `player.v2.yaml` | Schema file | New dimension version with plan fields, minus PII |
| Create `season.v1.yaml` | Schema file | New dimension definition |
| Create `plan.v1.yaml` | Schema file | New dimension definition |
| Update `practice_log.v1.yaml` | Schema file | Add export-time metadata columns, reference new dimensions |
| Update `practice_log_live.v1.yaml` | Schema file | Same dimension updates, scope exclusion logic |
| Exporter changes | Code | Add export-time columns to Parquet output |
| Validation logic | Code | Per-file checksums/row counts, referential checks, DQ rules |
| Live sync scope filter | Code | Exclude seasons with completed/validated archive jobs |
| Monitoring alerts | Code/Config | Freshness, stuck-state, retry exhaustion alerts |

### Phase 13.2 — Code Impact

- **All changes are additive** — no modifications to existing UPSERT, session filtering, or cascade delete
- **Only direct production impact**: New `idx_last_seen_at` index (minor write-side cost for UPSERT maintaining one extra index)
- **No new DocType fields, no schema migration on source table**

---

## Phase 14 — Final Review ✅ LOCKED

### Phase 14.1 — Decision Summary

| Area | Decision |
|------|----------|
| Dataset | `tabMemora Practice Log` — raw SQL, cumulative state per (player, item) |
| Flow | Live sync (daily full snapshot) + Archive (season-scoped) |
| Primary time field | `last_seen_at` |
| Source of truth | Active: MariaDB; Archived: DuckDB Parquet |
| Archive trigger | Season closure (`is_published=0`) |
| Scope filter | `last_seen_at` range from season dates |
| Post-archive | Delete (after validation + grace period) |
| Cross-season rows | Latest season only — documented limitation |
| Publish mode | Replace (1 previous retained for rollback) |
| Fact columns | All 7 source + 4 export-time metadata |
| Dimensions | Player v2, Review Item v1, Season v1, Plan v1 — batch-scoped |
| Privacy | Exclude mobile, display_name, gender; keep grade, major |
| Raw layer | Separate archive + live sources |
| Curated layer | Unified with precedence, dedup, 5 derived fields |
| Marts | 7 marts (5 materialized, 1 rollup views, 1 optional drill-down) |
| DQ rules | 16 hard-fail validations |
| Acceptance tests | 19 scenarios |
| Handoff | Live continues until archive validated; archive wins in curated |
| Production impact | Additive only — 1 new index, no behavior changes |

### Phase 14.2 — Consistency Checks

- Live sync + archive non-overlapping: Confirmed — curated precedence rule handles transitional overlap
- Dimensions compatible with registry: New versions (player.v2, plan.v1, season.v1) — no conflict with existing
- Grain clear across all layers: Fact (player × item), curated (player × item), marts (various documented grains)
- Source of truth defined: Active = MariaDB, archived = DuckDB
- DQ and acceptance tests written: 16 DQ rules + 19 acceptance scenarios

### Phase 14.3 — Cross-Season Limitation (Explicit Statement)

**Design constraint**: `tabMemora Practice Log` is a mutable cumulative-state table with no `season_id` column. Archive scoping uses `last_seen_at` date ranges from season boundaries.

**Consequence**: A `(player_id, item_id)` row that spans multiple seasons will appear only in the archive batch for the season containing its final `last_seen_at`. Its cumulative counters include attempts from all seasons.

**Therefore**:
- The detailed archived dataset is **not** a season-pure historical fact table
- **Season-specific analytics must come from season-scoped aggregate marts/rollups, not from detailed archived rows alone**
- Exact season-attribution of individual practice events is not possible from this dataset without a separate event log

---

## Key Codebase References

### Core Files
1. `memora_admin/memora_admin/setup.py:623-645` — Table DDL
2. `memora_admin/api/practice.py:418-472` — UPSERT function
3. `memora_admin/api/practice.py:93-400` — READ functions (session filtering)
4. `memora_admin/api/review_items.py:260-290` — CASCADE DELETE
5. `fastapi_app/services/practice.py:1483-1505` — FastAPI integration

### Archive Files
1. `archive_schemas/archive_types/practice_log.v1.yaml` — Archive schema
2. `archive_schemas/sync_types/practice_log_live.v1.yaml` — Live sync schema
3. `archive_schemas/dimensions/player.v1.yaml` — Player dimension (v1, preserved)
4. `archive_schemas/dimensions/review_item.v1.yaml` — Review Item dimension
5. `archive_executor/exporter.py:55-162` — Export logic
6. `archive_executor/purge.py:60-146` — Purge logic
7. `archive_executor/live_sync.py` — Live sync logic

### Trigger and Scheduling
1. `memora_admin/tasks/archive_trigger.py` — Season-based archive job creation (daily 01:20)
2. `memora_admin/tasks/archive_stale_pause.py` — Sync pause handling
3. `memora_admin/tasks/archive_notify.py` — Failure notifications (daily 06:00)

### DocTypes
1. `memora_admin/doctype/memora_season/memora_season.json` — Season lifecycle
2. `memora_admin/doctype/memora_archive_job/memora_archive_job.json` — Archive Job definition
3. `memora_admin/doctype/memora_player_profile/memora_player_profile.json` — Player Profile
4. `memora_admin/doctype/memora_academic_plan/memora_academic_plan.json` — Academic Plan

---

## Files To Be Produced

1. `player.v2.yaml` — New player dimension (plan-enriched, PII-excluded)
2. `season.v1.yaml` — New season dimension
3. `plan.v1.yaml` — New plan dimension
4. Updated `practice_log.v1.yaml` — Export-time metadata + new dimension references
5. Updated `practice_log_live.v1.yaml` — Dimension updates + scope exclusion logic
6. Dataset registry entry for `practice_log`
7. DuckDB raw layer definitions (archive + live)
8. DuckDB curated layer definition (unified + derived fields)
9. DuckDB mart definitions (7 marts)
10. DQ validation rules specification
11. Acceptance test specification (19 scenarios)
12. Monitoring/alerting configuration
13. DDL for `idx_last_seen_at` index
14. **Final PRD document**
