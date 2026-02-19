# Feature Specification: Cloudflare CDN Cache Purge Integration

**Feature Branch**: `021-cdn-cache-purge`
**Created**: 2026-02-19
**Status**: Draft
**Input**: User description: "Cloudflare CDN Cache Purge Integration"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automatic Cache Purge on Content Publish (Priority: P1)

When a content editor publishes updated lesson data, subject hierarchies, or plan manifests through the build pipeline, the system automatically purges the corresponding files from Cloudflare's edge cache so that mobile app users receive fresh content immediately rather than waiting for cache TTL expiration.

**Why this priority**: This is the core value of the feature. Without automatic purge, content updates are invisible to end users until Cloudflare's cache expires, leading to stale content delivery and confusion. With 100k concurrent users, stale content at scale is a critical issue.

**Independent Test**: Can be fully tested by triggering a content build and verifying that the published files are purged from Cloudflare's cache. Delivers immediate value by ensuring content freshness for all mobile app users.

**Acceptance Scenarios**:

1. **Given** a content build completes successfully and CDN purge is enabled, **When** the build worker finishes publishing files, **Then** the system sends purge requests to Cloudflare for exactly the files that were published.
2. **Given** a build publishes more than 30 files, **When** the purge step executes, **Then** the system batches purge requests to stay within Cloudflare's per-request limit without losing any files.
3. **Given** the Cloudflare purge request fails (network error, invalid credentials, rate limit), **When** the purge step encounters the failure, **Then** the build is still marked as successful and the failure is logged for investigation. The build must never fail due to a purge error.
4. **Given** CDN purge is not enabled in settings, **When** a build completes, **Then** no purge requests are sent and no errors are logged.

---

### User Story 2 - CDN Configuration in Admin Settings (Priority: P2)

A system administrator configures the Cloudflare CDN integration (zone ID, API token, base URL) through the existing Memora Settings page. The configuration fields are conditionally visible based on whether CDN is enabled and the selected provider.

**Why this priority**: Configuration must exist before automatic or manual purge can work. This is the foundation that enables all other stories.

**Independent Test**: Can be tested by navigating to Memora Settings, enabling CDN, selecting "Cloudflare CDN" as provider, and verifying that the relevant credential fields appear and can be saved.

**Acceptance Scenarios**:

1. **Given** CDN is disabled in settings, **When** an administrator views the settings page, **Then** Cloudflare-specific fields (zone ID, API token) are hidden.
2. **Given** CDN is enabled and "Cloudflare CDN" is selected as the provider, **When** an administrator views the settings page, **Then** the zone ID and API token fields are visible and editable.
3. **Given** CDN is enabled, **When** an administrator views the settings page, **Then** the CDN base URL field is visible regardless of the selected provider.
4. **Given** an administrator saves settings with CDN enabled but missing required Cloudflare fields, **When** the purge service is invoked, **Then** it gracefully returns without action and logs a warning about missing configuration.

---

### User Story 3 - Manual Full Cache Purge (Priority: P3)

A system administrator can trigger a full Cloudflare cache purge from the Memora Settings page when needed (e.g., after a bulk content migration, DNS change, or to resolve suspected cache corruption).

**Why this priority**: Manual purge is a safety net for edge cases that automatic per-file purge doesn't cover. It's less frequently needed but important for operational confidence.

**Independent Test**: Can be tested by clicking the "Purge CDN Cache" button on the settings page and verifying that Cloudflare's entire cache for the zone is purged. Delivers value as an emergency operations tool.

**Acceptance Scenarios**:

1. **Given** CDN is enabled and properly configured, **When** an administrator clicks "Purge CDN Cache", **Then** a full cache purge request is sent to Cloudflare and a success message is displayed.
2. **Given** CDN is not enabled or not configured, **When** an administrator attempts to purge, **Then** the system displays an error message explaining that CDN is not configured.
3. **Given** the full purge request fails, **When** the administrator clicks the button, **Then** a failure message is displayed directing them to check the error log.
4. **Given** CDN is disabled, **When** an administrator views the settings page, **Then** the "Purge CDN Cache" button is not visible.

---

### Edge Cases

- What happens when a single purge batch partially fails (e.g., 2 of 3 batches succeed)? The system logs specific failures but reports partial success.
- What happens when the Cloudflare API rate-limits the request? The system retries once after a delay, then logs the failure without blocking the build.
- What happens when the CDN base URL has a trailing slash and filenames have a leading slash? URL construction normalizes to avoid double slashes.
- What happens when a build produces zero files (empty build)? The purge step is skipped silently.
- What happens when Cloudflare credentials are rotated while a build is in progress? The purge reads credentials at purge time, so credential rotation is safe between builds.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST automatically purge specific files from the Cloudflare CDN cache after a successful content build publish.
- **FR-002**: System MUST batch purge requests when the number of files exceeds 30 per request (Cloudflare's per-request limit).
- **FR-003**: System MUST treat cache purge as best-effort — a purge failure MUST NOT cause the build to be marked as failed.
- **FR-004**: System MUST retry a failed purge request once with a short delay before giving up.
- **FR-005**: System MUST log all purge results (success and failure) for operational visibility.
- **FR-006**: System MUST allow administrators to configure Cloudflare CDN settings (zone ID, API token, base URL) in the Memora Settings page.
- **FR-007**: System MUST conditionally show Cloudflare configuration fields only when CDN is enabled and "Cloudflare CDN" is selected as the provider.
- **FR-008**: System MUST allow administrators to trigger a manual full cache purge from the Memora Settings page.
- **FR-009**: System MUST gracefully skip the purge step when CDN is not enabled or required configuration fields are missing.
- **FR-010**: System MUST construct full CDN URLs by combining the base URL with the relative file paths from the build pipeline.

### Key Entities

- **Memora Settings**: Singleton configuration document containing CDN credentials and preferences. Extended with a Cloudflare zone ID field and updated provider options.
- **Purge Service**: A service that communicates with the Cloudflare cache purge API. Reads credentials from Memora Settings. Supports per-file and full-zone purge operations.
- **Build Worker**: Existing background job orchestrator. Extended to call the purge service after successful file publication.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Mobile app users see updated content within 60 seconds of a content build completing (previously limited by Cloudflare's cache TTL, which can be hours).
- **SC-002**: Content build success rate remains unchanged — zero builds fail due to cache purge issues.
- **SC-003**: Administrators can configure CDN settings and trigger manual purge in under 2 minutes without documentation.
- **SC-004**: Cache purge for a typical build (under 30 files) completes within 5 seconds of build completion.
- **SC-005**: System handles builds with up to 500 files by batching purge requests without timeout or failure.

## Assumptions

- Cloudflare API token has `Zone.Cache Purge` permission for the target zone.
- The `requests` library is available in the runtime environment (standard dependency).
- Cloudflare's purge API limit of 30 URLs per request is current and stable.
- The CDN base URL provided in settings directly maps to the Cloudflare-proxied origin (i.e., `{cdn_base_url}/files/cdn/{filename}` resolves to the correct cached resource).
- The existing `access_key` Password field in Memora Settings can be repurposed for storing the Cloudflare API token.
- Content files always remain local on the origin server — Cloudflare is a reverse proxy cache, not object storage.

## Scope Boundaries

### In Scope

- Cloudflare cache purge integration (per-file and full-zone purge)
- Memora Settings updates (new field, label changes, conditional visibility)
- Build worker integration (automatic purge after publish)
- Manual purge button on the Settings admin page
- Error handling, logging, and retry logic for purge operations

### Out of Scope

- No changes to the local storage backend, publisher, or file generators
- No R2/S3 object storage integration — files remain local
- No changes to the mobile app API layer or internal cache layer
- No changes to the storage backend factory function
- No automated credential validation or connectivity test on save
- No purge analytics or dashboard
