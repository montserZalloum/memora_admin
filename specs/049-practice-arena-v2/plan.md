# Implementation Plan: Practice Arena V2

**Branch**: `049-practice-arena-v2` | **Date**: 2026-03-14 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/049-practice-arena-v2/spec.md`

## Summary

Full backend redesign of the Practice Arena to eliminate real-time database queries during gameplay, introduce CDN-based content delivery via practice map files and content chunks, and decouple read/write paths using Redis Streams as the write queue. A new `tabPlayer Practice Summary` table stores one JSON row per (player, track) pair for instant session startup. The FastAPI sidecar handles all gameplay operations (start, submit, continue) using only Redis and in-memory computation. Background workers asynchronously persist results to both the Practice Log and Player Summary tables.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 + FastAPI)
**Primary Dependencies**: Frappe v15, FastAPI, Redis 7+ (Streams, Hashes, Sets), MariaDB 10.6+
**Storage**: MariaDB (cold source of truth), Redis (hot cache + sessions + write queue), Cloudflare R2/local CDN (content files)
**Testing**: pytest with httpx.AsyncClient (FastAPI endpoints), pytest with real Redis (no mocking), FrappeTestCase (Frappe-side)
**Target Platform**: Linux server (Ubuntu 22.04)
**Project Type**: Dual architecture — Frappe admin backend + FastAPI sidecar (port 8002)
**Performance Goals**: Session start < 200ms p95, submit < 100ms p95, continue < 150ms p95, 10K+ concurrent sessions
**Constraints**: Zero DB queries during gameplay (warm cache), `tabMemora Practice Log` schema unchanged, content hierarchy unchanged, existing V1 endpoints preserved during rollout
**Scale/Scope**: 10K+ concurrent players, ~500M Practice Log rows, ~10K Review Items per subject, ~500KB max Player Summary row

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Self-Healing Cache Architecture (NON-NEGOTIABLE) — PASS ✅

| Requirement | How V2 Satisfies |
|---|---|
| Cache-miss hydration | Player Summary uses `ensure_hydrated` pattern: on Redis cache miss, read single DB row per track, populate Redis, return |
| No Redis-only state | Player Summary is always backed by MariaDB table; sessions are ephemeral (acceptable loss of 1 session on Redis failure) |
| Dirty-set sync | Write queue (Redis Streams) replaces dirty sets for practice results; worker persists to DB asynchronously |
| Cross-cache invalidation | Content changes invalidate CDN cache + Redis map cache + pubsub to FastAPI workers |
| Two-pronged invalidation | Direct `r.delete()` for map/summary cache + Redis pubsub for FastAPI in-process caches |

### II. Sub-20ms Game API Performance — JUSTIFIED DEVIATION ⚠️

Practice Arena endpoints have relaxed latency targets (200ms/100ms/150ms) compared to the core game API (sub-20ms). This is justified because:
- Question selection involves sorting thousands of candidates in-memory (O(N log N) where N can be 10K)
- Cold-start cache miss requires a single DB read (but only once per 2-hour TTL window)
- The 200ms target is still dramatically faster than V1 (~800ms) and meets the product requirement
- Core game API performance is unaffected — Practice Arena uses separate endpoints and services

### III. Content Hierarchy Integrity — PASS ✅

Content hierarchy (Subject → Track → Unit → Topic → Lesson → Stage) is unchanged. Map files mirror this hierarchy exactly. No modifications to bit indexes, versions, or the build pipeline.

### IV. Double-Gate Access Control — N/A (DOCUMENTED)

Per spec assumption A-003: "The client application handles access control filtering." The server validates that requested scope exists in the map file but does not enforce access gates. This is consistent with the V1 implementation and is an acceptable trade-off for a training/practice feature.

### V. Cryptographic Voucher Security — N/A ✅

Practice Arena does not involve voucher operations.

### VI. Financial Precision — N/A ✅

Practice Arena does not involve financial calculations.

### VII. Auditable State Machines — PASS ✅

| State Machine | States | Transitions |
|---|---|---|
| Session | Created → Active → Submitted → Continued → Expired/Replaced | Forward-only; expiry is terminal |
| Write Queue Message | Pending → Processing → Completed / Failed → Dead-Letter | Retry with backoff; dead-letter after 5 failures |
| Content Generation | Pending → Processing → Completed / Failed | Same as existing Build Queue pattern |

### VIII. Test-First Coverage — PASS ✅

| Test Category | Approach |
|---|---|
| Pure logic | Question selection algorithm, scope filtering, priority scoring — `unittest.TestCase` |
| Integration | Full endpoint lifecycle (start → submit → continue) — pytest + httpx.AsyncClient + real Redis |
| Concurrency | Session replacement, rate limiting, duplicate submission — real Redis required |
| Background worker | Queue consumption, DB writes, idempotency — pytest with real DB connection |

### Gate Result: **PASS** (one justified deviation on Principle II)

## Project Structure

### Documentation (this feature)

```text
specs/049-practice-arena-v2/
├── plan.md              # This file
├── research.md          # Phase 0: technology decisions and research
├── data-model.md        # Phase 1: entity definitions and schemas
├── quickstart.md        # Phase 1: developer onboarding guide
├── contracts/           # Phase 1: API contracts
│   ├── practice-v2.yaml # OpenAPI spec for V2 endpoints
│   └── write-queue.md   # Redis Streams message schema
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
# FastAPI sidecar (game API)
fastapi_app/
├── api/v2/endpoints/
│   └── practice.py              # V2 HTTP entrypoints (start, submit, continue)
├── services/
│   ├── practice_v2.py           # V2 service: map-based selection, Redis summary, queue dispatch
│   ├── practice_map.py          # Map file loader + in-memory cache
│   └── practice_writer.py       # Background write worker (Redis Streams consumer)
├── models/
│   └── practice_v2.py           # Pydantic request/response models
└── tests/
    ├── test_practice_v2.py      # Endpoint integration tests
    ├── test_practice_selection.py # Unit tests for selection algorithm
    └── test_practice_writer.py  # Worker integration tests

# Frappe admin backend
memora_admin/memora_admin/
├── setup.py                     # Table creation: tabPlayer Practice Summary
├── services/build/
│   └── practice_content.py      # Map file + chunk generator
├── tasks/
│   └── practice_writer.py       # Background worker task (Frappe scheduler entry)
├── events/
│   └── practice_content_trigger.py  # Hooks for Review Item changes → content regeneration
└── api/
    └── practice_summary.py      # Backfill script + admin utilities

# Shared
fastapi_app/core/
└── redis_keys.py                # New key patterns for V2 (summary, rate limit, queue)
```

**Structure Decision**: Follows the existing dual-architecture pattern. V2 services are new files alongside V1 (no modification to V1 code). The FastAPI sidecar handles all gameplay; Frappe handles content generation, DB table creation, and the background worker scheduler. V1 and V2 coexist via API versioning (`/v1/practice/*` vs `/v2/practice/*`).

## Complexity Tracking

| Deviation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Redis Streams as write queue (vs dirty sets) | Need ordered, at-least-once delivery with consumer groups, visibility timeout, and dead-letter for practice results. Dirty sets only support unordered flush. | Dirty set pattern doesn't guarantee ordering or provide retry/dead-letter semantics |
| `tabPlayer Practice Summary` JSON column (vs normalized rows) | One row per (player, track) enables single-read cache population. With 5K questions/track, a normalized table would require 5K rows per player per track — cache hydration becomes a bulk query. | Normalized approach creates N+1 query pattern or requires complex aggregation on cache miss |
| Relaxed latency target (200ms vs 20ms) | Selection algorithm sorts up to 10K candidates in-memory. Sub-20ms is achievable only for O(1) Redis operations, not O(N log N) sorts. 200ms is still 4x faster than V1. | Sub-20ms would require pre-computed batch queues, adding significant complexity with minimal user benefit |
