# Research: Cloudflare CDN Cache Purge Integration

**Date**: 2026-02-19
**Feature**: 021-cdn-cache-purge

## R1: Cloudflare Purge Cache API

**Decision**: Use `POST /client/v4/zones/{zone_id}/purge_cache` with Bearer token auth.

**Rationale**: This is Cloudflare's official v4 API for cache purge. It supports both per-file purge (`{"files": [...]}`) and full zone purge (`{"purge_everything": true}`).

**Key constraints**:
- **30 URLs per request** for file-based purge (confirmed via Cloudflare docs)
- Rate limits vary by plan tier (Free: 1000 urls/min, Business: higher)
- Bearer token auth: `Authorization: Bearer {api_token}`
- Token requires `Zone.Cache Purge` permission scope

**Alternatives considered**:
- Purge by prefix: Requires Enterprise plan, not suitable
- Purge by tag: Requires Enterprise plan, not suitable
- Purge by host: Too broad — would purge non-CDN content

## R2: Integration Point in Build Worker

**Decision**: Add `_purge_cdn_cache(files)` call in `build_worker.py:_process_single_build()` at line 127 (after `_clear_retry_count`, before the log line), using the original `files` list from the generator.

**Rationale**: The `files` variable at this point contains the full generator output with `filename` keys. Using this (rather than hooking into `publisher.py`) keeps the purge concern in the orchestrator layer and avoids modifying the publisher's single-responsibility (local file writing).

**File structure from generators**:
- Subject generator: `_subjects.json`, `{subject_id}_b.json`, `track_{id}.json`, `unit_{id}.json`, `topic_{id}.json`, `lesson_{id}.json`
- Plan generator: `plans/{plan_id}/manifest.json`, `plans/{plan_id}/subjects/{subject_id}/_h.json`, `plans/{plan_id}/subjects/{subject_id}/units/{unit_id}_c.json`, `lessons/{lesson_id}.json`

**Note**: Publisher's `_flatten_files()` recursively flattens nested `children` arrays. The purge helper must do the same flattening to get all filenames.

## R3: HTTP Client Choice

**Decision**: Use Python `requests` library (synchronous).

**Rationale**: The build worker runs as a Frappe background job (synchronous context). `requests` is already available in the Frappe environment. No need for `httpx` or async — the purge runs after build completion in a background worker, not in a request handler.

**Alternatives considered**:
- `httpx`: Would add a dependency; async not needed in Frappe job context
- `urllib3`: Lower-level, more code for the same result
- `frappe.integrations.utils.make_post_request`: Frappe wrapper around requests, adds unnecessary coupling

## R4: Memora Settings Field Layout

**Decision**: Add `cloudflare_zone_id` field after `storage_provider`, update `storage_provider` options to `"Local Only\nCloudflare CDN"`, relabel `access_key` to "Cloudflare API Token", add `depends_on` for conditional visibility.

**Rationale**: Reuses existing `access_key` Password field (already encrypted by Frappe) rather than creating a new field. This avoids a migration step. The `secret_key` field is kept but unused — no harm in leaving it for potential future use.

**Field visibility rules**:
- `cdn_base_url`: visible when `cdn_enabled` is checked
- `cloudflare_zone_id`: visible when `cdn_enabled` AND `storage_provider == "Cloudflare CDN"`
- `access_key`: visible when `cdn_enabled` AND `storage_provider == "Cloudflare CDN"`

## R5: Error Handling Strategy

**Decision**: Best-effort with single retry. Purge failures log to Frappe Error Log but never raise exceptions or fail the build.

**Rationale**: Cache purge is a side effect of a successful build. The build's primary job (generating + publishing files to local disk) is already complete. If purge fails, Cloudflare will serve fresh content when TTL expires naturally. Failing the build over a purge error would cause unnecessary requeues and re-generation.

**Retry strategy**:
- 1 retry on any HTTP error (network, 5xx, rate limit)
- 2-second delay between attempts
- No retry on 4xx (configuration error — won't self-heal)
- Log all failures to `frappe.log_error()` for visibility in Error Log

## R6: URL Construction

**Decision**: `{cdn_base_url}/files/cdn/{filename}` with normalization to prevent double slashes.

**Rationale**: Files are stored at `{frappe_site}/public/files/cdn/{filename}` by the `LocalStorageBackend`. Cloudflare proxies the origin, so the public URL is `{cdn_base_url}/files/cdn/{filename}`. The `cdn_base_url` comes from Memora Settings (e.g., `https://cdn.memora.app`).

**Normalization**: Strip trailing slash from `cdn_base_url` and leading slash from filename before joining.
