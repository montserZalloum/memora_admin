# Research: Analytics Lakehouse

**Feature**: 046-analytics-lakehouse | **Date**: 2026-03-12

## R1: DuckDB as Analytics Engine

**Decision**: Use DuckDB as the embedded analytics database on the Analytics Server.

**Rationale**:
- Native Parquet reader with Hive partition pruning — zero ETL into a separate database format
- `read_parquet()` with `hive_partitioning=true` and `union_by_name=true` handles schema evolution (FR-017)
- Single-file database, no server process to manage
- Excellent for OLAP workloads: columnar storage, vectorized execution
- Python API matches the analytics team's existing skills

**Alternatives considered**:
- **ClickHouse**: Overkill for single-server analytics; requires server process management
- **Apache Spark**: Too heavyweight; DuckDB handles 10B row scans in seconds on commodity hardware
- **Plain Parquet queries (no DB)**: No view abstraction, no aggregates, no duplicate tracking

## R2: CLI Framework

**Decision**: Use Click for the `memora-analytics` CLI tool.

**Rationale**:
- Standard Python CLI framework, well-tested
- JSON output mode is easy (just `json.dumps` to stdout)
- Matches the contract already defined in `ingestion.py` — each subcommand returns JSON to stdout, logs to stderr
- No dependency on Frappe or any web framework

**Alternatives considered**:
- **argparse**: More boilerplate, no subcommand grouping
- **Typer**: Adds pydantic dependency; Click is simpler for our needs
- **Fire**: Too magical, poor control over output format

## R3: Ingestion Strategy — DuckDB Tables vs Parquet Views

**Decision**: Hybrid approach — DuckDB views over Parquet files for archive data, DuckDB tables for live/recent layers.

**Rationale**:
- Archive data is immutable Parquet → views with `read_parquet('lake/**/*.parquet', hive_partitioning=true)` give partition pruning for free
- Live data needs atomic swap (staging → final) → use DuckDB tables (`CREATE OR REPLACE TABLE ... AS SELECT * FROM read_parquet(...)`)
- Recent layer is a rolling materialized window → DuckDB table rebuilt by `refresh-recent`
- Aggregates (daily/monthly) are small → DuckDB tables rebuilt by `refresh-aggregates`
- This avoids duplicating immutable archive Parquet into DuckDB storage while keeping mutable layers fast

**Alternatives considered**:
- **All views (no tables)**: Live swap requires atomic replace; views can't do this safely
- **All tables (COPY INTO)**: Doubles storage for archive data; pointless since Parquet is already columnar

## R4: SCD2 Player History Dimension

**Decision**: Build SCD2 logic as a Frappe doc_event hook on `Memora Player Profile` (plan change detection) + daily reconciliation task.

**Rationale**:
- `Memora Player Plan History` DocType already exists with `previous_plan`, `new_plan`, `changed_at` fields
- The DocType records change events but does NOT produce the SCD2 dimension format (valid_from, valid_to, is_current)
- SCD2 transformation happens at dimension export time: query `tabMemora Player Plan History` ordered by `changed_at`, derive `valid_from`/`valid_to` windows
- No new DocType needed — reuse existing change log

**Alternatives considered**:
- **Maintain SCD2 columns directly in `tabMemora Player Plan History`**: Fragile — closing `valid_to` on every insert requires transactional safety; better to derive at export time from the ordered event log
- **Separate SCD2 table**: Unnecessary indirection; the event log IS the source data

## R5: Dimension Refresh Strategy

**Decision**: Event-driven refresh (Frappe `doc_events` hooks) + daily reconciliation safety net.

**Rationale**:
- Dimension Parquet files are small (< 100K rows each) — full refresh is fast (< 5s)
- Event hooks on `Memora Player Profile`, `Memora Academic Plan`, `Memora Season`, `Memora Review Item`, `Memora Lesson` trigger export + transfer
- Daily reconciliation at 04:00 catches any missed events
- Dimensions are exported by `archive_executor/exporter.py` already — just need to trigger it outside of archive job context

**Alternatives considered**:
- **CDC/binlog streaming**: Massively over-engineered for 5 small dimension tables
- **Only daily refresh (no hooks)**: Up to 24h stale; FR-015 says "refresh on source change events"

## ~~R6: Compaction Strategy~~ (Removed from production contract)

> `compact` is an analytics-only maintenance utility. It is not called by the production executor and is not part of the production-to-analytics integration contract.

## R7: Health Check Design

**Decision**: Four independent health checks run as subcommands of `memora-analytics verify`.

**Rationale**:
- Each check is independent and can be run in isolation for debugging
- Combined into single `verify` command for daily cron
- Returns structured JSON with per-check pass/fail + details
- Checks: (1) duplicate detection via GROUP BY HAVING COUNT > 1, (2) manifest checksum via SHA-256 file scan, (3) dimension coverage via LEFT JOIN IS NULL, (4) partition file size distribution

**Alternatives considered**:
- **Great Expectations**: Heavy dependency for 4 checks; overkill
- **dbt tests**: Requires dbt project setup; we're not using dbt

## R8: Combined View Architecture

**Decision**: `practice_log_combined` = UNION ALL of archive view + live table, with exclusion boundary enforced at live sync export time (not at query time).

**Rationale**:
- Archive and live data never overlap because `live_sync.py` already excludes archived date ranges during export (via `scope_exclusion` in sync type YAML)
- No need for query-time dedup — the data is already clean
- Simple UNION ALL gives DuckDB maximum optimization opportunity
- Other combined views follow the same pattern: `{fact}_combined` = archive UNION ALL live

**Alternatives considered**:
- **Query-time dedup (QUALIFY ROW_NUMBER)**: Unnecessary overhead since exclusion is enforced at export
- **Single table (INSERT archive + live)**: Violates append-only Parquet policy; live data changes daily

## R9: Deployment Model

**Decision**: `analytics_cli/` is packaged as a pip-installable CLI tool deployed to `/opt/analytics/` on the Analytics Server.

**Rationale**:
- Matches existing `archive_executor/` pattern — standalone Python package, no Frappe
- Installed via `pip install -e .` or `pip install .` in a dedicated venv
- Entry point: `memora-analytics` (Click group)
- Called by production executor via `ssh analytics_server /opt/analytics/memora-analytics <command>`
- DuckDB path and lake paths configured via environment variables or CLI flags

**Alternatives considered**:
- **REST API on analytics server**: Over-engineered; SSH CLI is simpler and already implemented in `ingestion.py`
- **Embedded in archive_executor**: Wrong — analytics code shouldn't run on production server
