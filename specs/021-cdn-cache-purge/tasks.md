# Tasks: Cloudflare CDN Cache Purge Integration

**Input**: Design documents from `/specs/021-cdn-cache-purge/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cloudflare-purge-api.md, quickstart.md

**Tests**: Not explicitly requested in spec. Omitted.

**Organization**: Tasks grouped by user story. US2 (CDN Configuration) is folded into the Foundational phase since it is a blocking prerequisite for US1 and US3 (as noted in the spec: "Configuration must exist before automatic or manual purge can work").

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Create the CDN service package structure

- [X] T001 Create CDN service package init in `memora_admin/memora_admin/services/cdn/__init__.py`

---

## Phase 2: Foundational + US2 - CDN Configuration & Service Infrastructure (P2)

**Purpose**: Settings schema changes (US2) and shared service class that ALL other stories depend on

**US2 Goal**: Administrators can configure Cloudflare CDN settings (zone ID, API token, base URL) through Memora Settings with conditional field visibility

**Independent Test (US2)**: Navigate to Memora Settings, enable CDN, select "Cloudflare CDN" as provider, verify zone ID and API token fields appear. Disable CDN and verify they hide.

- [X] T002 [P] [US2] Update Memora Settings DocType schema: add `cloudflare_zone_id` field, update `storage_provider` options to `Local Only\nCloudflare CDN`, relabel `access_key` to "Cloudflare API Token", add `depends_on` for conditional visibility in `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json`
- [X] T003 [P] Create `CloudflarePurgeService` class with `purge_files()`, `purge_all()`, and `_make_request()` methods (batching at 30 URLs, 1 retry with 2s delay, no retry on 4xx, URL normalization) in `memora_admin/memora_admin/services/cdn/cloudflare.py`
- [X] T004 Create `get_purge_service()` factory that reads Memora Settings and returns `CloudflarePurgeService | None` in `memora_admin/memora_admin/services/cdn/utils.py`
- [X] T005 [US2] Update package init to export `CloudflarePurgeService` in `memora_admin/memora_admin/services/cdn/__init__.py`

**Checkpoint**: CDN settings configurable in admin panel. `get_purge_service()` returns a configured service or `None`. US2 acceptance scenarios 1-4 testable.

---

## Phase 3: User Story 1 - Automatic Cache Purge on Content Publish (Priority: P1)

**Goal**: After a successful content build, automatically purge the published files from Cloudflare's edge cache so mobile app users receive fresh content immediately.

**Independent Test**: Trigger a content build, verify that `_purge_cdn_cache()` is called with the correct file list and Cloudflare API receives purge requests for the published URLs. Verify build success is unaffected by purge failures.

### Implementation for User Story 1

- [X] T006 [US1] Add `_purge_cdn_cache(files)` function that flattens nested file list, extracts filenames, and calls `purge_service.purge_files()` with full try/except protection in `memora_admin/memora_admin/tasks/build_worker.py`
- [X] T007 [US1] Call `_purge_cdn_cache(files)` in `_process_single_build()` after `_clear_retry_count` (line ~127) and before the success log in `memora_admin/memora_admin/tasks/build_worker.py`

**Checkpoint**: Content builds automatically purge Cloudflare cache. Purge failures never fail the build. Batching works for builds with >30 files. US1 acceptance scenarios 1-4 testable.

---

## Phase 4: User Story 3 - Manual Full Cache Purge (Priority: P3)

**Goal**: Administrators can trigger a full Cloudflare zone cache purge from the Memora Settings page for emergency operations (bulk migration, DNS change, suspected cache corruption).

**Independent Test**: Click "Purge CDN Cache" button on Settings page, verify Cloudflare full zone purge is triggered and success/failure message is displayed. Verify button is hidden when CDN is disabled.

### Implementation for User Story 3

- [X] T008 [US3] Add `purge_all_cdn_cache()` whitelist method that calls `get_purge_service().purge_all()` with `frappe.throw()` if not configured and `frappe.msgprint()` for result in `memora_admin/memora_admin/doctype/memora_settings/memora_settings.py`
- [X] T009 [US3] Add "Purge CDN Cache" button in `refresh(frm)` handler, conditionally visible when `cdn_enabled` is checked, calling the whitelist method via `frappe.call()` in `memora_admin/memora_admin/doctype/memora_settings/memora_settings.js`

**Checkpoint**: Manual full cache purge available in admin UI. Button hidden when CDN disabled. Success/failure feedback displayed. US3 acceptance scenarios 1-4 testable.

---

## Phase 5: Polish & Validation

**Purpose**: End-to-end validation and edge case handling

- [X] T010 Run quickstart.md validation: verify full flow (settings config -> content build -> automatic purge -> manual purge)
- [X] T011 Verify edge cases: empty build (zero files skipped), >30 files batched correctly, double-slash URL normalization, partial batch failure logging

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - start immediately
- **Foundational + US2 (Phase 2)**: Depends on Phase 1 - BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 (needs `get_purge_service()` and settings schema)
- **US3 (Phase 4)**: Depends on Phase 2 (needs `get_purge_service()` and settings schema). Independent of US1.
- **Polish (Phase 5)**: Depends on Phases 3 and 4

### User Story Dependencies

- **US2 (P2)**: Foundational — folded into Phase 2. Must complete first.
- **US1 (P1)**: Can start after Phase 2 completes. No dependency on US3.
- **US3 (P3)**: Can start after Phase 2 completes. No dependency on US1. **Can run in parallel with US1.**

### Within Phase 2

```
T002 (schema JSON) ──┐
                      ├── T005 (package init) ── Phase 2 complete
T003 (service class) ─┤
                      │
T004 (factory) ───────┘  (depends on T003)
```

- T002 and T003 can run in parallel [P] (different files, no dependencies)
- T004 depends on T003 (imports `CloudflarePurgeService`)
- T005 depends on T003 (exports `CloudflarePurgeService`)

### Parallel Opportunities

- **Phase 2**: T002 and T003 can run in parallel (different files)
- **Phase 3 + Phase 4**: US1 and US3 can run in parallel after Phase 2 (different files, no cross-dependencies)
- **Phase 4**: T008 and T009 are in different files (Python vs JS) but T009 calls T008's method, so sequential

---

## Parallel Example: Phases 3 & 4

```bash
# After Phase 2 completes, launch US1 and US3 in parallel:
Task: "T006+T007 [US1] Add _purge_cdn_cache() in build_worker.py"
Task: "T008+T009 [US3] Add manual purge in memora_settings.py + .js"
```

---

## Implementation Strategy

### MVP First (US2 + US1 Only)

1. Complete Phase 1: Setup (package init)
2. Complete Phase 2: Foundational + US2 (settings schema + service + factory)
3. Complete Phase 3: US1 (build worker integration)
4. **STOP and VALIDATE**: Configure settings, trigger a build, verify Cloudflare receives purge requests
5. Deploy — content freshness problem is solved

### Incremental Delivery

1. Phase 1 + Phase 2 → Admin can configure CDN settings (US2 complete)
2. Phase 3 → Automatic purge on builds (US1 complete — MVP!)
3. Phase 4 → Manual purge button (US3 complete — full feature)
4. Phase 5 → Validation and edge cases

---

## Notes

- Total: 11 tasks (1 setup + 4 foundational/US2 + 2 US1 + 2 US3 + 2 polish)
- 3 new files created, 4 existing files modified
- No new database tables — only 1 new field + 3 field modifications on existing Memora Settings singleton
- Purge is always best-effort: build success is never affected by purge failures
- `requests` library (synchronous) used for HTTP — appropriate for Frappe background job context
