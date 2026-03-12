# Contract: Snapshot Manifest Schema

**Version**: 1.0 | **Date**: 2026-03-11 | **Kind**: Metadata contract

## Purpose

Each snapshot partition directory contains a `manifest.json` that records provenance, integrity checksums, and row counts. This enables downstream consumers to verify snapshot completeness without reading the Parquet file.

## JSON Schema

```json
{
  "manifest_version": "1.0",
  "dataset_key": "structure_progress_snapshot",
  "kind": "snapshot",
  "batch_id": "<SNAP-{snapshot_date}>",
  "schema_version": "1.0",
  "created_at": "<ISO 8601 UTC timestamp>",
  "source": "memora_admin",
  "scope_key": "<snapshot_date YYYY-MM-DD>",
  "files": [
    {
      "role": "fact",
      "entity": "structure_progress",
      "filename": "fact_structure_progress.parquet",
      "row_count": "<integer>",
      "checksum": "sha256:<hex digest>",
      "size_bytes": "<integer>"
    }
  ]
}
```

## Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `manifest_version` | string | Always `"1.0"`. Bumped on breaking manifest format changes. |
| `dataset_key` | string | Always `"structure_progress_snapshot"`. Identifies the dataset type. |
| `kind` | string | Always `"snapshot"`. Distinguishes from `"archive"` and `"live_sync"`. |
| `batch_id` | string | `SNAP-{snapshot_date}` (e.g., `SNAP-2026-03-08`). Unique per snapshot run. |
| `schema_version` | string | Parquet schema version (e.g., `"1.0"`). Matches contract version. |
| `created_at` | string | ISO 8601 UTC timestamp of when the manifest was generated. |
| `source` | string | Always `"memora_admin"`. Identifies the producing system. |
| `scope_key` | string | The `snapshot_date` value (YYYY-MM-DD). |
| `files` | array | Exactly one entry for the fact Parquet file. |
| `files[].role` | string | Always `"fact"` for v1 (no dimension files). |
| `files[].entity` | string | Always `"structure_progress"`. |
| `files[].filename` | string | Always `"fact_structure_progress.parquet"`. |
| `files[].row_count` | integer | Number of rows in the Parquet file. 0 for empty snapshots. |
| `files[].checksum` | string | `sha256:{hex}` checksum of the Parquet file bytes. |
| `files[].size_bytes` | integer | File size in bytes. |

## Invariants

- Each snapshot directory contains exactly one `manifest.json`.
- `batch_id` is unique across all snapshots (derived from the date).
- `row_count` in the manifest matches the actual Parquet row count.
- `checksum` is computed over the final Parquet file bytes (post-write, pre-rename).
- An empty snapshot (0 source rows with valid plans) produces `row_count: 0` and a valid Parquet file with the correct schema.

## Compatibility

The manifest format is compatible with `archive_executor.manifest.build_manifest()`. The `kind: "snapshot"` field distinguishes snapshot manifests from archive (`kind: "archive"`) and live sync (`kind: "live_sync"`) manifests.
