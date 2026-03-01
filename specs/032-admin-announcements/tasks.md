# Tasks: Admin Announcement System

**Input**: Design documents from `/specs/032-admin-announcements/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/announcements.yaml, research.md, quickstart.md

**Tests**: Not explicitly requested — test tasks are excluded.

**Organization**: Tasks grouped by user story. US1 (Admin Creates) + US2 (Player Views) are combined as the MVP since they are both P1 and neither delivers value alone.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Frappe module**: `memora_admin/memora_admin/` (DocTypes, API, events)
- **FastAPI sidecar**: `fastapi_app/` (endpoints, services, models, core)
- **Hooks**: `memora_admin/hooks.py` (Frappe app hooks at repo root)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Register Redis key builder and Pydantic response models used by all subsequent phases.

- [X] T001 Add `announcements_active_key()` builder function and `ANNOUNCEMENTS_CACHE_TTL = 300` constant to `fastapi_app/core/redis_keys.py`. Follow existing pattern: docstring with Redis type (STRING), producers, consumers, TTL. Add cross-reference comment for any Lua scripts if applicable.
- [X] T002 [P] Create Pydantic response models in `fastapi_app/models/announcements.py`: `AnnouncementItem` (id, title, body, display_frequency, created_at) and `AnnouncementsResponse` (announcements: list[AnnouncementItem]). Match the schema in `contracts/announcements.yaml`.

---

## Phase 2: Foundational (DocType Schemas + Hooks Registration)

**Purpose**: Create both Frappe DocType schemas and register hooks. MUST complete before any user story implementation.

**CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Create `Memora Announcement Target Plan` child DocType directory and schema (`istable: 1`) with a single `plan` Link field to `Memora Academic Plan` in `memora_admin/memora_admin/doctype/memora_announcement_target_plan/`. Include `__init__.py` and `memora_announcement_target_plan.json`.
- [X] T004 Create `Memora Announcement` parent DocType directory and schema with all fields per `data-model.md` in `memora_admin/memora_admin/doctype/memora_announcement/`. Fields: `title_ar` (Data), `title_en` (Data), `body_ar` (Small Text), `body_en` (Small Text), `target_audience` (Select: All Players/Specific Plans), `target_plans` (Table → Memora Announcement Target Plan), `duration_type` (Select: Date Range/Fixed Duration), `start_date` (Date), `end_date` (Date), `duration_days` (Int), `effective_start_date` (Date, read-only), `effective_end_date` (Date, read-only), `display_frequency` (Select: Always/Once/Once Per Day/Once Per Session), `is_published` (Check). Use `depends_on` expressions for conditional visibility. Autoname: `ANN-.#####.`. Include `__init__.py` and `memora_announcement.json`.
- [X] T005 [P] Create JS form script for conditional field visibility and UX polish in `memora_admin/memora_admin/doctype/memora_announcement/memora_announcement.js`. Handle: hide target_plans when audience is "All Players", hide start_date/end_date when duration is "Fixed Duration", hide duration_days when duration is "Date Range". Use `frappe.ui.form.on` with `refresh` and field `onchange` triggers.
- [X] T006 Register `Memora Announcement` doc_events in `memora_admin/hooks.py`: wire `after_insert`, `on_update`, and `on_trash` to `memora_admin.memora_admin.events.announcement_sync.on_announcement_changed`. Add to existing `doc_events` dict.

**Checkpoint**: DocTypes visible in Frappe Desk; hooks registered (handler not yet implemented).

---

## Phase 3: US1+US2 — Admin Creates Global Announcement + Player Views on Home Screen (Priority: P1) — MVP

**Goal**: Admin creates a "Date Range" / "All Players" announcement via Frappe Desk; player calls `GET /api/v1/announcements/?lang=ar` and receives localized announcements sorted newest first.

**Independent Test**: Create announcement in Frappe Desk with "All Players" + "Date Range", publish it, call API with valid JWT + lang=ar, verify response contains the announcement with Arabic content.

### Implementation for US1+US2

- [X] T007 [US1] Implement `MemoraAnnouncement` document class with `validate()` in `memora_admin/memora_admin/doctype/memora_announcement/memora_announcement.py`. Validation rules for MVP: (1) `title_ar` and `title_en` max 140 chars, (2) if `duration_type == "Date Range"`: require `start_date` and `end_date`, validate `end_date > start_date`, (3) compute `effective_start_date = start_date` and `effective_end_date = end_date` for Date Range. Inherit from `frappe.model.document.Document`.
- [X] T008 [P] [US1] Create Frappe whitelist API `get_active_announcements()` in `memora_admin/memora_admin/api/announcements.py`. Query all `Memora Announcement` where `is_published=1`, filter by `effective_start_date <= today <= effective_end_date` in Python, include child table `target_plans` data (list of plan IDs). Return list of dicts matching the cached data shape from `data-model.md`. Decorate with `@frappe.whitelist(allow_guest=False)`.
- [X] T009 [P] [US1] Create cache invalidation handler `on_announcement_changed(doc, method)` in `memora_admin/memora_admin/events/announcement_sync.py`. Use two-pronged pattern: (1) `r.delete(announcements_active_key())` via `get_memora_redis()`, (2) publish `{"type": "announcements"}` to `memora:cache:invalidate` channel. Import key builder from `fastapi_app.core.redis_keys`.
- [X] T010 [US2] Create `AnnouncementService` in `fastapi_app/services/announcements.py`. Methods: (1) `get_active_announcements()` — GET from Redis cache key, on miss call `FrappeClient.call("memora_admin.api.announcements.get_active_announcements")`, cache result with `ANNOUNCEMENTS_CACHE_TTL`, return list of dicts. (2) `get_for_player(player_plan: str | None, lang: str)` — call `get_active_announcements()`, filter by date range (today within effective dates), filter by targeting (include "all" always; include "specific_plans" only if `player_plan` in `target_plans`), select `title_{lang}` and `body_{lang}`, sort by `created_at` desc, return list of `AnnouncementItem`. (3) `invalidate()` — DEL cache key. Constructor takes `redis` and `frappe_client` params.
- [X] T011 [US2] Add `AnnouncementServiceDep` to `fastapi_app/api/deps.py`. Inject `redis` pool and `FrappeClient` into `AnnouncementService`. Follow existing pattern (e.g., `CatalogServiceDep` or `AccessServiceDep`).
- [X] T012 [US2] Create GET `/api/v1/announcements/` endpoint in `fastapi_app/api/v1/endpoints/announcements.py`. Accept `lang: Literal["ar", "en"] = "ar"` query param. Require `TokenPayload` via existing auth dependency. For MVP, call `service.get_for_player(player_plan=None, lang=lang)` — passing `None` for plan means only "All Players" announcements are returned. Return `AnnouncementsResponse`.
- [X] T013 [US2] Register announcements router in `fastapi_app/api/v1/router.py`. Import and include the announcements router with appropriate prefix and tags.
- [X] T014 [US2] Add pubsub handler for `"announcements"` invalidation type in the FastAPI cache invalidation listener. Find the existing pubsub subscriber (likely in `fastapi_app/main.py` lifespan or a dedicated module), add a case for `type == "announcements"` that calls `AnnouncementService.invalidate()`.

**Checkpoint**: Admin creates "All Players" + "Date Range" announcement → player sees it via API in preferred language. Edit/delete triggers cache invalidation.

---

## Phase 4: US3 — Admin Targets Announcement to Specific Plans (Priority: P2)

**Goal**: Admin creates an announcement targeting specific academic plans; only players on those plans see it via the API.

**Independent Test**: Create announcement targeting "Plan A". Player on "Plan A" sees it. Player on "Plan B" does not.

### Implementation for US3

- [X] T015 [US3] Add target plans validation to `validate()` in `memora_admin/memora_admin/doctype/memora_announcement/memora_announcement.py`: if `target_audience == "Specific Plans"` and `len(self.target_plans) == 0`, raise `frappe.throw("At least one target plan is required when audience is 'Specific Plans'")`.
- [X] T016 [US3] Update announcements endpoint to resolve player's current plan and pass it to the service in `fastapi_app/api/v1/endpoints/announcements.py`. Look up player's plan from their profile/session in Redis (follow existing pattern for resolving player plan from `TokenPayload.sub`). Pass `player_plan` to `service.get_for_player(player_plan=plan, lang=lang)`.

**Checkpoint**: Plan-targeted announcements visible only to matching-plan players; "All Players" announcements still visible to everyone.

---

## Phase 5: US4 — Admin Uses Fixed Duration Mode (Priority: P2)

**Goal**: Admin creates announcement with "Fixed Duration" of N days; system computes `effective_end_date = publish_date + N days` and auto-expires.

**Independent Test**: Create "Fixed Duration" announcement with 3 days, verify `effective_end_date = today + 3`. After expiry, verify API no longer returns it.

### Implementation for US4

- [X] T017 [US4] Add Fixed Duration validation and effective date computation to `validate()` in `memora_admin/memora_admin/doctype/memora_announcement/memora_announcement.py`: (1) if `duration_type == "Fixed Duration"`, require `duration_days >= 1`, (2) if `is_published` is being set to true for the first time (check `self._doc_before_save`), compute `effective_start_date = today()` and `effective_end_date = today() + timedelta(days=self.duration_days)`, (3) if already published (re-save), preserve existing effective dates.

**Checkpoint**: Fixed Duration announcements auto-compute expiry dates and stop appearing after expiry.

---

## Phase 6: US5+US6 — Edit and Delete Announcements (Priority: P3)

**Goal**: Admin edits a live announcement's content or targeting; changes reflected immediately. Admin deletes an announcement; it disappears from API responses.

**Independent Test**: Edit published announcement body → next API call returns updated text. Delete published announcement → next API call omits it.

### Implementation for US5+US6

- [X] T018 [US5][US6] Verify edit and delete cache invalidation works end-to-end. Both `on_update` (edit) and `on_trash` (delete) hooks were wired in T006 and implemented in T009. Test by: (1) editing a published announcement's body text and verifying updated content in API response, (2) changing targeting from "All Players" to "Specific Plans" and verifying non-targeted players no longer see it, (3) deleting a published announcement and verifying it no longer appears. If any scenario fails, debug the hook chain.

**Checkpoint**: Edit and delete immediately reflected in API responses via cache invalidation.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation and performance verification.

- [X] T019 Run full `quickstart.md` end-to-end validation: create announcement, verify API response, edit and verify update, test plan targeting, delete and verify removal.
- [X] T020 [P] Verify performance target: announcement retrieval < 10ms from Redis cache. Use `curl -w "%{time_total}"` or structlog timing. Confirm single Redis GET + JSON parse path under normal load.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (T001 needed for T009 key import)
- **US1+US2 (Phase 3)**: Depends on Phase 2 completion — BLOCKS on DocType schemas and hooks
- **US3 (Phase 4)**: Depends on Phase 3 (extends validate + endpoint)
- **US4 (Phase 5)**: Depends on Phase 3 (extends validate). Can run in parallel with Phase 4.
- **US5+US6 (Phase 6)**: Depends on Phase 3 (verification of existing hooks). Can run in parallel with Phases 4-5.
- **Polish (Phase 7)**: Depends on all user story phases (Phases 3-6) being complete

### User Story Dependencies

- **US1+US2 (P1)**: Can start after Foundational — no dependencies on other stories
- **US3 (P2)**: Extends US1+US2 infrastructure (adds validate rule + plan lookup). Independent of US4.
- **US4 (P2)**: Extends US1+US2 infrastructure (adds validate logic). Independent of US3.
- **US5+US6 (P3)**: Verification only — no new code. Independent of US3/US4.

### Within Phase 3 (MVP)

1. T007 (Document class) — no dependency within phase
2. T008 [P] (Frappe API) + T009 [P] (Cache invalidation) — can run in parallel
3. T010 (Service) — depends on T001 (redis key), T002 (models)
4. T011 (Deps) — depends on T010
5. T012 (Endpoint) — depends on T010, T011
6. T013 (Router) — depends on T012
7. T014 (Pubsub) — depends on T010

### Parallel Opportunities

```
Phase 1:  T001 ─┬─ T002 [P]
                 │
Phase 2:  T003 ─── T004 ─┬─ T005 [P]
                          └─ T006

Phase 3:  T007 ─┬─ T008 [P]
                ├─ T009 [P]
                └─ T010 ── T011 ── T012 ── T013
                           T014 (parallel with T012-T013)

Phase 4-6 can overlap:
          T015 ─── T016  (US3)
          T017           (US4, parallel with US3)
          T018           (US5+US6, parallel with US3/US4)
```

---

## Implementation Strategy

### MVP First (US1+US2 Only)

1. Complete Phase 1: Setup (redis keys, models)
2. Complete Phase 2: Foundational (DocType schemas, hooks)
3. Complete Phase 3: US1+US2 (full stack for global announcements)
4. **STOP and VALIDATE**: Create announcement in Desk → verify in API
5. Deploy/demo if ready — "All Players" announcements work end-to-end

### Incremental Delivery

1. Phase 1+2 → Infrastructure ready
2. Phase 3 (US1+US2) → MVP: global announcements work end-to-end
3. Phase 4 (US3) → Plan-based targeting enabled
4. Phase 5 (US4) → Fixed Duration mode available
5. Phase 6 (US5+US6) → Verify edit/delete (should already work)
6. Phase 7 → Polish and performance validation

### Suggested MVP Scope

**Phases 1-3 (T001-T014)** deliver a complete, working announcement system for "All Players" with "Date Range" duration. This covers the core use case and validates the full stack (Frappe DocType → Redis cache → FastAPI API → player response).
