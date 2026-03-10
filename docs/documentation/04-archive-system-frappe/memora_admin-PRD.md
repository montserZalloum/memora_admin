PRD: Memora-Side Enablement for Practice Log Analytics

Project: Memora Application
Dataset: tabMemora Practice Log
Date: 2026-03-10
Status: Ready for implementation

1. Purpose

This PRD defines the Memora application responsibilities required to enable the Practice Log analytics pipeline.

The Memora side is responsible for:

preserving correct production behavior,

exposing stable archive/live-sync contracts,

preparing exportable fact/dimension data,

and supporting safe archive handoff.

The analytics project is responsible for:

DuckDB ingestion,

raw/curated/mart modeling,

reporting,

and downstream analytical consumption.

2. Goal

Enable tabMemora Practice Log to be safely archived and live-synced for analytics without changing the business behavior of the production practice system.

3. In Scope
Production-side responsibilities owned by Memora

Source table support for archive/live-sync

Archive trigger logic based on season closure

Export contract support

Dimension snapshot support

Manifest generation support

Validation hooks/contract checks on export batches

Live-sync/archive handoff signaling

Operational monitoring hooks relevant to production/archive execution

Out of Scope

DuckDB raw modeling

DuckDB curated layer

DuckDB marts

BI/dashboard/report implementation

Analytical KPI definitions beyond what is needed for export contracts

4. Source Dataset
Table

tabMemora Practice Log

Nature

Raw SQL table

Not a Frappe DocType

Cumulative-state table

One row per (player_id, item_id)

Canonical schema

The source table schema remains the current canonical schema already defined in Memora.
No new source columns should be added just for analytics.

Important rule

Do not convert this table into:

an event log,

a season-owned fact table,

or an ORM-managed DocType.

5. Business Rules to Preserve

Practice Log remains a mutable cumulative-state table.

last_seen_at is the operational timestamp used for:

archive scoping,

purge scoping,

live-sync/archive handoff.

Archive eligibility is based on season closure, specifically:

is_published = 0

and end_date < today

Season scoping is temporal, not FK-based:

the source table has no season_id column

archive scoping uses last_seen_at against season date boundaries

Cross-season rows are allowed to land only in the latest season archive if last_seen_at moved forward.

Existing production behavior must not change:

UPSERT logic unchanged

session filtering unchanged

cascade delete unchanged

6. Functional Requirements
6.1 Add Archive/Sync Index Support

Memora must add a production index:

CREATE INDEX idx_last_seen_at ON `tabMemora Practice Log` (last_seen_at);
Purpose

Support:

archive export range scans,

live sync range scans,

purge range deletes.

Constraint

This is the only required production DDL change for this dataset.

6.2 Archive Trigger Behavior

Memora must create archive jobs for Practice Log when a season becomes archive-eligible.

Archive eligibility

A season is eligible when:

Memora Season.is_published = 0

and Memora Season.end_date < current date

Trigger mechanism

The existing scheduled season check should continue to detect ended seasons and create one archive job per:

source_doctype

archive_scope

schema_version

Uniqueness

Duplicate archive jobs for the same scope/version must be prevented.

6.3 Archive Scope Definition

For Practice Log, archive scope must be season-based and temporal.

Scope rules

archive_scope = season_id

filter_column = last_seen_at

date_from = season.start_date

date_to = season.end_date

Query shape
WHERE last_seen_at >= :date_from
  AND last_seen_at < :date_to
Important limitation

Because the source table has no season_id, this export is not season-pure at detailed row level.

This limitation must be documented in code comments / schema docs where relevant.

6.4 Live Sync Support

Memora must continue supporting daily live sync for active/unarchived seasons.

Live-sync rules

full snapshot mode

replaces prior live detailed state

no historical accumulation in live layer

excludes seasons that have already become archived-and-authoritative

Handoff rule

When a season closes:

live sync may continue including it temporarily

until archive is fully validated/queryable

then live sync must stop including it

The Memora side must expose the status/signal needed for this handoff.

6.5 Export-Time Metadata Support

Memora must support adding export metadata to the exported fact dataset without changing the source table.

Required export-time metadata

archive_scope

archive_job_id

schema_version

exported_at

Rule

These fields are added at export time to Parquet/output artifacts, not stored in the production source table.

6.6 Dimension Snapshot Support

Memora must provide the production-side snapshot sources needed for batch-scoped dimensions.

Required dimensions

player.v2

review_item.v1

season.v1

plan.v1

Required behavior

Dimension exports must be batch-scoped, meaning:

only records referenced by the exported fact batch are included

no full-table dimension export

6.7 Player Dimension v2

Memora must provide a new player.v2 export contract.

Include

player_id

grade

major

season_id or season link in normalized form

plan_id

plan_name

Include only if actually present and confirmed

plan_type

Exclude

mobile

display_name

gender by default

Rule

Do not mutate player.v1.
Create player.v2 as a new version.

6.8 New Dimension Contracts

Memora must add:

season.v1

plan.v1

Season fields

season_id

season_title

start_date

end_date

Plan fields

plan_id

plan_name

grade

major

season_id

is_published

6.9 Practice Log Schema Contract Updates

Memora must update:

practice_log.v1.yaml

practice_log_live.v1.yaml

Required updates

include export metadata columns

reference new dimension versions

document live-sync replacement semantics

document scope exclusion behavior for archived-and-validated seasons

6.10 Manifest Support

Memora must produce or support a manifest that includes per-file metadata for:

fact file

player dimension

review item dimension

season dimension

plan dimension

Required per-file metadata

file name

role

entity

schema version

row count

checksum

6.11 Validation Rules

Memora-side export validation must support publish-blocking checks for:

missing required files

duplicate (player_id, item_id) in fact

referential breaks to dimensions

schema version mismatches

checksum mismatches

logical fact invalidity:

nulls

invalid counts

invalid enum values

first_seen_at > last_seen_at

last_seen_at outside archive scope

row_count = 0 must remain valid but flagged.

6.12 Delete and Purge Safety

Memora must preserve current delete semantics:

live deletes remain hard deletes

cascade delete behavior unchanged

Post-archive behavior

Default archive action for Practice Log should remain:

Delete after successful validation

Purge preconditions

Purge must not happen until:

export completed,

manifest committed,

validation passed,

archive is queryable,

grace period elapsed.

6.13 Monitoring and Alerts

Existing retries and morning failure notifications are not enough on their own.

Memora-side operational safeguards must include:

live sync freshness alert

archive validation lag alert

retry exhaustion alert

stuck-state alert between export/validation/purge stages

7. Non-Functional Requirements

No change to production business semantics

Minimal production schema impact

Additive implementation only

Export contracts must be versioned

Privacy by default

Archive jobs must be idempotent per scope/version

Range export paths must remain safe for large-table operation

8. Explicit Non-Goals

This PRD does not require Memora to implement:

DuckDB curated logic

analytical marts

dashboard queries

mastery scoring

season-pure historical reconstruction for detailed rows

9. Deliverables Owned by Memora

DDL migration for idx_last_seen_at

player.v2.yaml

season.v1.yaml

plan.v1.yaml

updated practice_log.v1.yaml

updated practice_log_live.v1.yaml

export metadata support in fact output

per-file manifest metadata support

validation enhancements

live-sync/archive exclusion signaling

monitoring/alerting enhancements

10. Acceptance Criteria

Memora-side work is complete when:

idx_last_seen_at exists in production migration code

archive jobs are created correctly for ended seasons

Practice Log export uses season date windows via last_seen_at

exported fact includes required metadata columns

batch-scoped dimensions export correctly

player.v2, season.v1, and plan.v1 exist and are usable

manifest includes per-file checksum and row count

hard-fail validation blocks bad batches

empty batch remains valid

live sync excludes archived-and-validated seasons

no production practice behavior changed

purge remains blocked until validation success

11. Handoff to Analytics Project

The Memora project hands off the following stable contracts to the analytics project:

Practice Log fact export

Player v2 dimension

Review Item v1 dimension

Season v1 dimension

Plan v1 dimension

manifest contract

archive/live-sync status semantics

validation outcomes

The analytics project then owns:

DuckDB ingestion

raw/curated models

marts

reporting