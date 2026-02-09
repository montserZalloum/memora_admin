---
status: resolved
trigger: "After creating a new track for an existing subject, JSON files built successfully but players still see old version without new track"
created: 2026-02-09T00:00:00Z
updated: 2026-02-09T20:16:00Z
---

## Current Focus

hypothesis: CONFIRMED - CDN JSON files served without Cache-Control headers + no version cache-busting
test: Applied Nginx cache-control and version query params, verified via curl and FastAPI
expecting: Players will now get fresh content on every request
next_action: Archive session

## Symptoms

expected: Players should see the new track immediately in the subject's track list after JSON files are created
actual: Players see only old tracks, new track doesn't appear
errors: No errors - just missing track (caching issue)
reproduction: Create new track, build JSON files successfully, check with player account - old version shown
started: Just now (within last hour)

## Eliminated

- hypothesis: Redis hierarchy cache is stale after track creation
  evidence: Redis `memora:hierarchy:SUBJ-00448` contains both Track-00450 and Track-00460 (2 tracks)
  timestamp: 2026-02-09T20:02:00Z

- hypothesis: Build worker not running or failing
  evidence: BLD-00466/BLD-00467 both show Completed status with 10/11 files generated
  timestamp: 2026-02-09T20:03:00Z

- hypothesis: CDN files on disk are stale / not written by build
  evidence: _h.json contains both tracks. File mtime 19:46 UTC matches build time 22:46 Frappe (UTC+3)
  timestamp: 2026-02-09T20:04:00Z

- hypothesis: Track not published (is_published filter)
  evidence: Both tracks have is_published=1 in MariaDB
  timestamp: 2026-02-09T20:04:00Z

- hypothesis: Plan manifest cache is stale
  evidence: Redis plan manifest shows total_tracks=2 for SUBJ-00448
  timestamp: 2026-02-09T20:04:00Z

## Evidence

- timestamp: 2026-02-09T20:02:00Z
  checked: Redis hierarchy cache for SUBJ-00448
  found: 2 tracks (Track-00450, Track-00460) - up to date
  implication: Server-side Redis cache is correct

- timestamp: 2026-02-09T20:03:00Z
  checked: Build queue in MariaDB
  found: BLD-00466 (subject) and BLD-00467 (plan) both Completed with files generated
  implication: Build pipeline is working correctly

- timestamp: 2026-02-09T20:04:00Z
  checked: CDN _h.json file on disk
  found: File contains both tracks, correct content
  implication: Files were written successfully by publisher

- timestamp: 2026-02-09T20:05:00Z
  checked: Nginx response headers for CDN JSON files
  found: No Cache-Control header. Only ETag and Last-Modified present. hierarchy_url has no version/cache-buster.
  implication: HTTP clients will use heuristic caching (RFC 7234) - can cache for up to 10% of resource age. Mobile app sees stale cached response.

- timestamp: 2026-02-09T20:15:00Z
  checked: Nginx response after fix
  found: Cache-Control: no-cache header present, versioned URLs working (hierarchy_url: _h.json?v=1770667995)
  implication: Fix verified - HTTP clients will revalidate on every request

## Resolution

root_cause: Nginx serves CDN JSON files without Cache-Control headers. Mobile app (or any HTTP client) applies heuristic caching based on Last-Modified header per RFC 7234, serving stale cached responses even though the files on disk are correct. Additionally, hierarchy_url and content_url in the generated JSON had no version query parameter for cache busting.
fix: Two-pronged approach - (1) Added Nginx location block for /files/cdn/*.json with Cache-Control: no-cache (forces revalidation on every request, uses ETag/Last-Modified for conditional GET - efficient, not wasteful). (2) Added ?v={version} query param to all URLs in plan_generator.py (hierarchy_url, content_url for units, content_url for lessons) so even aggressive client-side caches see a new URL on every rebuild.
verification: Confirmed via curl that CDN JSON files now have Cache-Control: no-cache header. FastAPI plan manifest endpoint returns versioned URLs. Files regenerated and published to CDN.
files_changed:
  - /etc/nginx/sites-enabled/aurevia-bench.conf (added CDN JSON cache-control location block)
  - memora_admin/memora_admin/services/build/plan_generator.py (added ?v={version} to hierarchy_url, content_url)
