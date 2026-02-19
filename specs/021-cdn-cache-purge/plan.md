# Implementation Plan: Cloudflare CDN Cache Purge Integration

**Branch**: `021-cdn-cache-purge` | **Date**: 2026-02-19 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/021-cdn-cache-purge/spec.md`

## Summary

Integrate Cloudflare CDN cache purge into the existing build pipeline so that published JSON content files are automatically purged from Cloudflare's edge cache. The feature adds a `CloudflarePurgeService` that calls the Cloudflare v4 API, hooks into `build_worker.py` after successful publish, and provides admin configuration and a manual purge button in Memora Settings.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15)
**Primary Dependencies**: Frappe Framework (ORM, whitelist API, background jobs), `requests` (HTTP client, already available)
**Storage**: MariaDB via Frappe ORM (Memora Settings singleton), no new tables
**Testing**: `frappe.tests.utils.FrappeTestCase` for Frappe-side code, `unittest.mock` for HTTP mocking
**Target Platform**: Linux server (production: x.conanacademy.com)
**Project Type**: Web (Frappe admin + background job)
**Performance Goals**: Cache purge completes within 5 seconds for typical builds (<30 files)
**Constraints**: Cloudflare API limits 30 URLs per purge request; purge must never fail the build
**Scale/Scope**: Builds produce 1-500 files; 100k concurrent mobile users consuming CDN content

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevant? | Status | Notes |
|-----------|-----------|--------|-------|
| I. Self-Healing Cache Architecture | No | PASS | Feature purges external CDN cache, not Redis. No Redis keys added. |
| II. Sub-20ms Game API Performance | No | PASS | Purge runs in Frappe background job, not in FastAPI hot path. |
| III. Content Hierarchy Integrity | Tangential | PASS | Feature is downstream of build pipeline — does not modify content structure. |
| IV. Double-Gate Access Control | No | PASS | No access control changes. |
| V. Cryptographic Voucher Security | No | PASS | API token stored in Frappe Password field (encrypted). No custom crypto. |
| VI. Financial Precision | No | PASS | No financial calculations. |
| VII. Auditable State Machines | Tangential | PASS | Build Queue state machine unchanged. Purge is fire-and-forget, no new states. |
| VIII. Test-First Coverage | Yes | PASS | Tests planned for service class (mocked HTTP) and integration. |

**Post-design re-check**: All gates still pass. No constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/021-cdn-cache-purge/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 research
├── data-model.md        # Phase 1 data model
├── quickstart.md        # Phase 1 quickstart
├── contracts/           # Phase 1 contracts
│   └── cloudflare-purge-api.md
└── checklists/
    └── requirements.md  # Spec quality checklist
```

### Source Code (repository root)

```text
memora_admin/memora_admin/
├── doctype/memora_settings/
│   ├── memora_settings.json    # MODIFY: add cloudflare_zone_id, update options/labels/depends_on
│   ├── memora_settings.py      # MODIFY: add purge_all_cdn_cache() whitelist method
│   └── memora_settings.js      # MODIFY: add "Purge CDN Cache" button
├── services/cdn/
│   ├── __init__.py             # CREATE: package init, export CloudflarePurgeService
│   ├── cloudflare.py           # CREATE: CloudflarePurgeService class
│   └── utils.py                # CREATE: get_purge_service() factory
└── tasks/
    └── build_worker.py         # MODIFY: add _purge_cdn_cache() after successful publish
```

**Structure Decision**: Follows existing project layout. New `services/cdn/` package mirrors existing `services/build/` pattern. No new directories outside the established structure.

## Implementation Details

### Task 1: Update Memora Settings DocType Schema

**File**: `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json`

Changes:
1. Add `cloudflare_zone_id` field (Data type) after `storage_provider` in `field_order` and `fields` arrays
2. Update `storage_provider` options from `"AWS S3\nCloudflare R2"` to `"Local Only\nCloudflare CDN"`
3. Relabel `access_key` from `"Access Key"` to `"Cloudflare API Token"`
4. Add `depends_on` to `cloudflare_zone_id`: `"eval:doc.cdn_enabled && doc.storage_provider=='Cloudflare CDN'"`
5. Add `depends_on` to `access_key`: `"eval:doc.cdn_enabled && doc.storage_provider=='Cloudflare CDN'"`
6. Add `depends_on` to `cdn_base_url`: `"eval:doc.cdn_enabled"`

### Task 2: Create CloudflarePurgeService

**File**: `memora_admin/memora_admin/services/cdn/cloudflare.py`

Class with:
- `__init__(zone_id, api_token, cdn_base_url)`: Store config
- `purge_files(filenames: list[str]) -> bool`:
  - Build full URLs: `{cdn_base_url}/files/cdn/{filename}` (with normalization)
  - Batch into groups of 30
  - POST each batch to Cloudflare API
  - 1 retry with 2s delay on failure (skip retry for 4xx)
  - Log results via `frappe.log_error()` on failure
  - Return True if all batches succeed
- `purge_all() -> bool`:
  - POST `{"purge_everything": true}`
  - Same retry/logging pattern
- `_make_request(payload: dict) -> bool`: Internal helper for HTTP + retry

### Task 3: Create get_purge_service() Factory

**File**: `memora_admin/memora_admin/services/cdn/utils.py`

- Read from `frappe.get_single("Memora Settings")`
- Check `cdn_enabled`, `storage_provider == "Cloudflare CDN"`, presence of `cloudflare_zone_id`, `access_key`, `cdn_base_url`
- Return `None` with `logger.warning()` if any missing
- Return `CloudflarePurgeService(zone_id, api_token, cdn_base_url)` if configured

### Task 4: Create Package Init

**File**: `memora_admin/memora_admin/services/cdn/__init__.py`

```python
from memora_admin.memora_admin.services.cdn.cloudflare import CloudflarePurgeService

__all__ = ["CloudflarePurgeService"]
```

### Task 5: Integrate Purge into Build Worker

**File**: `memora_admin/memora_admin/tasks/build_worker.py`

Add `_purge_cdn_cache(files)` function:
- Import `get_purge_service` from `services.cdn.utils`
- Flatten files using same pattern as publisher's `_flatten_files()`
- Extract filenames
- Call `purge_service.purge_files(filenames)` if service is configured
- Wrap in try/except — never propagate exceptions

Call site: In `_process_single_build()`, after line 127 (`_clear_retry_count`), before the success log line.

### Task 6: Add Manual Purge Whitelist Method

**File**: `memora_admin/memora_admin/doctype/memora_settings/memora_settings.py`

Add `purge_all_cdn_cache()` as a `@frappe.whitelist()` function:
- Call `get_purge_service()`
- `frappe.throw()` if not configured
- Call `purge_all()`
- `frappe.msgprint()` with green/red indicator based on result

### Task 7: Add Purge Button to Settings JS

**File**: `memora_admin/memora_admin/doctype/memora_settings/memora_settings.js`

Replace commented-out code with:
- `refresh(frm)` handler
- Conditional "Purge CDN Cache" button when `cdn_enabled` is checked
- `frappe.call()` to the whitelist method

## Complexity Tracking

No constitution violations to justify. Feature is straightforward:
- 3 new files (service class, factory, package init)
- 4 modified files (settings JSON/PY/JS, build worker)
- No new abstractions beyond the single service class
