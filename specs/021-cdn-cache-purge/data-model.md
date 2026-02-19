# Data Model: Cloudflare CDN Cache Purge Integration

**Date**: 2026-02-19
**Feature**: 021-cdn-cache-purge

## Entity Changes

### Memora Settings (modified — singleton DocType)

**New field**:

| Field | Type | Label | Depends On | Notes |
|-------|------|-------|------------|-------|
| `cloudflare_zone_id` | Data | Cloudflare Zone ID | `eval:doc.cdn_enabled && doc.storage_provider=='Cloudflare CDN'` | Added after `storage_provider` in field_order |

**Modified fields**:

| Field | Change | Before | After |
|-------|--------|--------|-------|
| `storage_provider` | Options updated | `AWS S3\nCloudflare R2` | `Local Only\nCloudflare CDN` |
| `access_key` | Label renamed | `Access Key` | `Cloudflare API Token` |
| `access_key` | depends_on added | (none) | `eval:doc.cdn_enabled && doc.storage_provider=='Cloudflare CDN'` |
| `cdn_base_url` | depends_on added | (none) | `eval:doc.cdn_enabled` |

**Unchanged fields** (kept for backward compatibility):
- `secret_key`: Kept as-is, unused by this feature

### CloudflarePurgeService (new — service class, no DocType)

Not a database entity. A Python service class that:
- Accepts `zone_id`, `api_token`, `cdn_base_url` in constructor
- Calls Cloudflare API v4 for cache purge operations
- Returns success/failure booleans

### Build Worker (modified — no schema change)

No data model change. Only logic change: calls purge service after successful publish.

## Relationships

```
Memora Settings (singleton)
    ├── cdn_enabled (toggle)
    ├── storage_provider (select)
    ├── cloudflare_zone_id (data)
    ├── access_key → used as Cloudflare API Token
    └── cdn_base_url
         │
         ▼
CloudflarePurgeService (runtime)
    ├── purge_files(filenames) → Cloudflare API
    └── purge_all() → Cloudflare API
         │
         ▼
Build Worker (_process_single_build)
    └── _purge_cdn_cache(files) → calls get_purge_service() → CloudflarePurgeService
```

## Validation Rules

- `cloudflare_zone_id`: No validation at save time (validated at purge time by Cloudflare API response)
- `access_key`: Frappe encrypts Password fields automatically — no custom encryption needed
- `cdn_base_url`: Must be a valid URL when CDN is enabled (standard Frappe Data field, no custom validation)
- `storage_provider`: Select field — values constrained by options list

## State Transitions

No state machines introduced. The purge operation is stateless and fire-and-forget:
1. Build completes → purge invoked
2. Purge succeeds → logged as info
3. Purge fails → logged as warning + Frappe Error Log entry
4. No state is persisted about purge results
