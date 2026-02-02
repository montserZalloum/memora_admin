# Phase 6: Build Pipeline - Context

**Gathered:** 2026-02-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Content changes trigger JSON generation and CDN upload with cache invalidation. Covers: build queue with debouncing, hierarchy/bitmap/lesson JSON generation, mock CDN upload abstraction, and Redis pub/sub for FastAPI cache invalidation. Does NOT include: scheduled sync to MariaDB (Phase 7).

</domain>

<decisions>
## Implementation Decisions

### Build trigger behavior
- Build scope is **per-subject** — only rebuild the affected subject's JSON files
- Multiple edits within 2-minute debounce window **merge subjects** into one build job
- Triggers on **Lesson and above** (Subject, Track, Unit, Topic, Lesson) — Stage edits don't trigger builds
- **Force Build button on Subject** DocType form for manual rebuild

### JSON output structure
- **Separate files per level** with child IDs only:
  - `_subjects.json` → subjects + track IDs
  - `track_{id}.json` → units + topic IDs
  - `unit_{id}.json` → topics + lesson IDs
  - `topic_{id}.json` → lessons (id, title, url)
- Field naming: **snake_case**
- Schema versioning: **version field inside** JSON (schema_version), same filename
- Media URLs: **relative paths** — app prepends CDN base URL

### Error & recovery
- If generation succeeds but CDN upload fails: **retry upload 3 times**
- **Atomic swap** — upload to temp location, swap only after all files succeed
- Malformed content data: **skip with warning** — exclude entity, log warning, continue build
- After 3 failed retries: **auto-requeue** with exponential backoff

### Build visibility
- Notifications: **both success and failure** via Frappe System Notification (bell icon)
- Build history: **logs only** — no Build Log DocType, use application logs
- Progress display: **simple status** — just "Building..." indicator, no granular steps

### Claude's Discretion
- Exact debounce implementation (Redis key TTL vs scheduled job)
- Temp directory structure for atomic swap
- Exponential backoff intervals
- Log format and detail level
- Pub/sub channel naming

</decisions>

<specifics>
## Specific Ideas

- JSON structure mirrors the hierarchy fetch pattern used in mobile app
- Relative media paths allow CDN migration without rebuilding content
- Atomic swap pattern prevents clients from fetching partial/inconsistent state

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 06-build-pipeline*
*Context gathered: 2026-02-02*
