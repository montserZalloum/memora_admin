# Quickstart: Cloudflare CDN Cache Purge Integration

**Date**: 2026-02-19
**Feature**: 021-cdn-cache-purge

## What This Feature Does

Automatically purges Cloudflare's CDN cache for JSON content files after the build pipeline publishes them. This ensures mobile app users receive fresh content immediately instead of waiting for cache TTL expiration.

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json` | Modify | Add `cloudflare_zone_id` field, update `storage_provider` options, relabel `access_key`, add `depends_on` |
| `memora_admin/memora_admin/doctype/memora_settings/memora_settings.py` | Modify | Add `purge_all_cdn_cache()` whitelisted method |
| `memora_admin/memora_admin/doctype/memora_settings/memora_settings.js` | Modify | Add "Purge CDN Cache" button |
| `memora_admin/memora_admin/services/cdn/__init__.py` | Create | Package init, exports `CloudflarePurgeService` |
| `memora_admin/memora_admin/services/cdn/cloudflare.py` | Create | `CloudflarePurgeService` class with `purge_files()` and `purge_all()` |
| `memora_admin/memora_admin/services/cdn/utils.py` | Create | `get_purge_service()` factory function |
| `memora_admin/memora_admin/tasks/build_worker.py` | Modify | Add `_purge_cdn_cache()` call after successful publish |

## How It Works

```
Content Editor saves lesson
        │
        ▼
Build Queue (debounced)
        │
        ▼
Build Worker: _process_single_build()
        │
        ├── 1. generate_subject_json() or generate_plan_json()
        ├── 2. publish_to_cdn()  ← writes files locally (unchanged)
        ├── 3. _notify_cache_invalidation()  ← Redis pubsub (unchanged)
        └── 4. _purge_cdn_cache()  ← NEW: purges Cloudflare edge cache
                │
                ├── get_purge_service() reads Memora Settings
                ├── Constructs URLs: {cdn_base_url}/files/cdn/{filename}
                ├── Batches into groups of 30
                └── POST to Cloudflare API (best-effort, never fails build)
```

## Configuration

In Memora Settings (Frappe admin panel):

1. Check "CDN Enabled"
2. Select "Cloudflare CDN" as Storage Provider
3. Enter CDN Base URL (e.g., `https://cdn.memora.app`)
4. Enter Cloudflare Zone ID
5. Enter Cloudflare API Token (with `Zone.Cache Purge` permission)

## Key Design Decisions

1. **Best-effort purge**: Purge failure never fails the build. Files remain correct on origin; Cloudflare serves fresh content after TTL.
2. **Batch at 30**: Cloudflare limits 30 URLs per purge request. Service auto-batches larger builds.
3. **Single retry**: One retry with 2s delay for transient failures. No retry on 4xx (config errors).
4. **Reuse `access_key`**: Existing Password field repurposed for Cloudflare API token. Frappe handles encryption.
5. **Orchestrator-level hook**: Purge is called from `build_worker.py`, not `publisher.py`, keeping publisher focused on local file operations.
