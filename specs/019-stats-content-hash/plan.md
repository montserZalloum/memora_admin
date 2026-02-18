# Implementation Plan: Stats Cache Staleness Detection

**Branch**: `019-stats-content-hash` | **Date**: 2026-02-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/019-stats-content-hash/spec.md`

## Summary

Embed a deterministic structural fingerprint (`content_hash`) into the subject hierarchy and per-user stats cache. On every progress read, compare the two hashes — mismatch triggers a lazy recompute (~4ms). This eliminates the up-to-1-hour stale window after content changes with zero write amplification at 100k+ users. The feature is fully backward-compatible: pre-existing stats without the hash self-heal on next read.

## Technical Context

**Language/Version**: Python 3.11+ (Frappe v15 bench environment)
**Primary Dependencies**: Frappe Framework (ORM, whitelist API), FastAPI, Pydantic v2, `redis.asyncio`, `hashlib` (stdlib)
**Storage**: Redis at `redis://127.0.0.1:13000` (stats hash, hierarchy JSON cache), MariaDB via Frappe ORM (hierarchy source data)
**Testing**: `pytest` 8.4.2 + `pytest-asyncio` 0.26.0, `httpx.AsyncClient`, real Redis (prefix-isolated)
**Target Platform**: Linux server (Frappe bench)
**Project Type**: Dual architecture (Frappe backend + FastAPI sidecar)
**Performance Goals**: Progress read <20ms, lesson completion <30ms, stats recompute <5ms, zero per-request hash computation (precomputed on Frappe side)
**Constraints**: <20ms p95 for progress reads, zero writes to stats keys on content change, HINCRBY warm path must NOT be modified
**Scale/Scope**: 100k+ concurrent users, 5 files modified (all additive), no schema migrations

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Source-of-Truth Awareness (NON-NEGOTIABLE) — PASS

| Check | Status | Detail |
|-------|--------|--------|
| Write path verifies both layers? | PASS | Content hash is computed from Frappe (MariaDB source of truth) during hierarchy build; stored in Redis hierarchy cache |
| Read path verifies hydration? | PASS | Missing `_content_hash` in stats → treated as stale → full recompute from bitmap (which self-heals from MariaDB via existing `ensure_hydrated()`) |
| Dirty set membership? | N/A | Feature does not modify wallet/progress dirty sets |
| Cache invalidation tested? | PASS | Existing hierarchy invalidation flow remains unchanged; content hash changes naturally when hierarchy is rebuilt |

### II. Atomic Operation Integrity (NON-NEGOTIABLE) — PASS

| Check | Status | Detail |
|-------|--------|--------|
| Lua scripts unchanged? | PASS | FR-008: lesson completion Lua script NOT modified |
| HINCRBY pipeline unchanged? | PASS | Warm path (4x HINCRBY + EXPIRE) remains identical |
| Atomic recompute? | PASS | Cold-start recompute uses single `HSET mapping` (atomic) to write all stats + `_content_hash` |

### III. Edge-Case-First Design — PASS

| Edge Case | Covered |
|-----------|---------|
| Pre-migration stats (no `_content_hash`) | Self-heals: `None != hash` → recompute |
| Pre-migration hierarchy (`content_hash=""`) | `"" != any_real_hash` → recompute; heals on next hierarchy rebuild |
| Redis FLUSHDB | Both caches rebuilt from scratch via existing self-healing |
| Mid-session content change | HINCRBY updates old stats; next read detects mismatch → recompute |
| Two rapid content changes | Hash changes twice; user recomputes once (gets latest) |
| Track reorder without lesson change | False positive (~4ms recompute) — acceptable, rare operation |
| Hierarchy TTL refresh with same structure | Same hash → no recompute — optimal |

### IV. Test Isolation (NON-NEGOTIABLE) — PASS

| Check | Status | Detail |
|-------|--------|--------|
| Hash computation tests | PASS | Pure function — no state, no Redis, no cleanup needed |
| Stats staleness tests | PASS | Use unique player IDs + prefix-isolated Redis keys |
| Integration tests | PASS | Factory functions for test data, teardown cleans Redis keys |

### V. Business Flow Completeness — PASS

| Flow | Tested |
|------|--------|
| Content change → hierarchy rebuild → hash mismatch → recompute → correct stats | Yes (integration test) |
| Lesson completion → warm HINCRBY → hash preserved → next read validates | Yes (integration test) |
| Cold start → full recompute with hash → subsequent reads skip recompute | Yes (unit test) |

### Quality Gates

- **Gate 1 (Pre-Merge)**: All tests pass, no `time.sleep()`, no excluded scope imports
- **Gate 2 (Coverage)**: `_compute_content_hash` 100% covered, all 4 stats-reading endpoints tested, `compute_stats_from_hierarchy` tested with hash
- **Gate 3 (Risk)**: RISK-07 (stats cold start double-counts) directly addressed by hash-based staleness detection

## Project Structure

### Documentation (this feature)

```text
specs/019-stats-content-hash/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── progress-api.md  # Updated endpoint contracts
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
memora_admin/
├── api/
│   └── hierarchy.py                 # ADD: _compute_content_hash(), set hierarchy["content_hash"]

fastapi_app/
├── models/
│   └── progress.py                  # ADD: content_hash field to SubjectHierarchy
├── services/
│   └── stats.py                     # ADD: _content_hash in compute_stats_from_hierarchy() output
└── api/v1/endpoints/
    ├── progress.py                  # MODIFY: staleness check in 4 endpoints
    └── sessions.py                  # MODIFY: cold-start path includes _content_hash (via stats function)

fastapi_app/tests/                   # Test files
├── test_content_hash.py             # Unit tests for hash computation
└── test_stats_staleness.py          # Integration tests for staleness detection
```

**Structure Decision**: Existing dual-architecture layout. Frappe-side change in `memora_admin/api/hierarchy.py` (hash computation). FastAPI-side changes across models, services, and endpoints (hash propagation and validation). Tests in existing `fastapi_app/tests/` directory.

## Complexity Tracking

> No constitution violations. All changes are additive and align with existing patterns.

*No entries needed.*
