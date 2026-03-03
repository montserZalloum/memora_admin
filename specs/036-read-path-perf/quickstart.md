# Quickstart: Progress & Practice Read-Path Performance

**Feature Branch**: `036-read-path-perf`
**Date**: 2026-03-03

## Prerequisites

- FastAPI sidecar running on port 8002
- Redis on port 13001 with existing data
- Valid JWT token for a player with progress data

## Testing the Optimizations

### 1. Stats-First Read Path (Warm Cache)

```bash
# Ensure stats cache exists (first call populates it)
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/progress/SUBJ-001/tracks

# Second call should use stats directly (no bitmap decode)
# Verify response is identical
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/progress/SUBJ-001/tracks
```

### 2. Partial Stats Reads

```bash
# Track list — should use HMGET with ~21 fields instead of HGETALL ~500
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/progress/SUBJ-001/tracks

# Track detail — should use HMGET with ~17 fields
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/progress/SUBJ-001/tracks/TRK-001

# Unit detail — should use HMGET with ~19 fields
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/progress/SUBJ-001/tracks/TRK-001/units/UNIT-001
```

### 3. Practice Hierarchy (Subject Access Hoisting)

```bash
# Should evaluate subject-level access once, not per-track
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8002/api/v1/practice/hierarchy?subject_id=SUBJ-001"
```

### 4. Progress Summary (Bounded Concurrency)

```bash
# For a player with many subjects, should process in bounded batches
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/progress/
```

## Running Existing Tests

```bash
# All tests must pass without modification
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_progress_endpoints.py -v
python -m pytest fastapi_app/tests/test_stats_service.py -v
python -m pytest fastapi_app/tests/test_progress_service.py -v
python -m pytest fastapi_app/tests/test_content_hash.py -v
```

## Production Environment Tuning

Add to `.env` for production deployment:

```env
# Redis pool — increase for 100k concurrent users
# Default: 20. With 4 workers = 80 total connections.
# Recommended: 50. With 4 workers = 200 total connections.
REDIS_MAX_CONNECTIONS=50

# Frappe upstream — reduce timeout for faster failure detection
# Default: 30.0s. Too long for game API paths.
# Recommended: 10.0s.
FRAPPE_TIMEOUT=10.0

# Frappe keepalive — increase to reduce TCP handshake overhead
# Default: 20. Recommended: 50.
FRAPPE_MAX_KEEPALIVE=50
```

## Verifying Optimizations Work

### Check Stats-First Path (structlog output)

With `LOG_LEVEL=DEBUG`, look for:
- `stats_cache_hit` — stats used directly without bitmap decode
- `stats_cache_miss` — fallback to bitmap decode + recompute
- `stats_partial_read` — HMGET used instead of HGETALL

### Check Cache-Fill Coalescing

With `LOG_LEVEL=DEBUG`, look for:
- `hierarchy_fill_coalesced` — waited for another request's fill
- `meta_fill_coalesced` — waited for another request's fill
- `hierarchy_fill_timeout` — timeout hit, proceeding independently

### Check Bounded Concurrency

With `LOG_LEVEL=DEBUG`, look for:
- `progress_summary_bounded` — semaphore used for subject fan-out

## Restart FastAPI After Changes

```bash
pkill -f "uvicorn fastapi_app.main:app"
# Wait 2-3 seconds for supervisor restart
curl http://127.0.0.1:8002/api/v1/health/live
```
