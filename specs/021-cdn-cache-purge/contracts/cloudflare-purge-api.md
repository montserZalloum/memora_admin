# Contract: Cloudflare Purge API Integration

**Date**: 2026-02-19
**Feature**: 021-cdn-cache-purge

## External API (Cloudflare v4)

### Purge by URL

```
POST https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache
Authorization: Bearer {api_token}
Content-Type: application/json

{
  "files": [
    "https://cdn.memora.app/files/cdn/_subjects.json",
    "https://cdn.memora.app/files/cdn/track_TRK-00001.json",
    "https://cdn.memora.app/files/cdn/plans/PLAN-00001/manifest.json"
  ]
}
```

**Constraints**:
- Max 30 URLs per request
- URLs must be fully qualified (scheme + host + path)

**Success Response** (200):
```json
{
  "success": true,
  "errors": [],
  "messages": [],
  "result": { "id": "..." }
}
```

**Error Response** (4xx/5xx):
```json
{
  "success": false,
  "errors": [{ "code": 1012, "message": "Request must contain one of..." }],
  "messages": []
}
```

### Purge Everything

```
POST https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache
Authorization: Bearer {api_token}
Content-Type: application/json

{
  "purge_everything": true
}
```

**Same response format as above.**

## Internal Service Contracts

### CloudflarePurgeService

```python
class CloudflarePurgeService:
    def __init__(self, zone_id: str, api_token: str, cdn_base_url: str) -> None: ...
    def purge_files(self, filenames: list[str]) -> bool: ...
    def purge_all(self) -> bool: ...
```

**`purge_files(filenames)`**:
- Input: Relative file paths from build pipeline (e.g., `["_subjects.json", "plans/PLAN-00001/manifest.json"]`)
- Constructs full URLs: `{cdn_base_url}/files/cdn/{filename}`
- Batches into groups of 30
- Returns `True` if all batches succeed, `False` if any fail
- Never raises exceptions

**`purge_all()`**:
- No input (uses stored zone_id)
- Sends `{"purge_everything": true}`
- Returns `True`/`False`
- Never raises exceptions

### get_purge_service() Factory

```python
def get_purge_service() -> CloudflarePurgeService | None: ...
```

- Returns `None` if CDN not enabled or not configured
- Returns `None` with warning log if required fields missing
- Returns configured `CloudflarePurgeService` instance otherwise

### Build Worker Integration

```python
def _purge_cdn_cache(files: list[dict]) -> None: ...
```

- Input: Raw file list from generator (potentially nested with `children`)
- Flattens nested structure to extract all filenames
- Calls `get_purge_service()` → `purge_files(filenames)`
- Silently returns if CDN not configured
- Never raises exceptions

## Frappe Whitelist API

### purge_all_cdn_cache

```
POST /api/method/memora_admin.memora_admin.doctype.memora_settings.memora_settings.purge_all_cdn_cache
```

**Auth**: System Manager role (Frappe session)
**Input**: None
**Output**: `frappe.msgprint()` with success/failure indicator
**Error**: `frappe.throw()` if CDN not configured
