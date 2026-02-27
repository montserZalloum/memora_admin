# API Contracts: Large-Data Performance Fixes

## No New or Changed API Contracts

This feature is a **pure internal optimization**. No API endpoints are added, removed, or have their request/response schemas changed.

### Endpoints Affected (behavior unchanged, performance improved)

| Endpoint | Method | Change |
|---|---|---|
| `/api/v1/progress/` | GET | Faster hierarchy lookup via local cache |
| `/api/v1/progress/{subject}` | GET | Faster hierarchy + coalesced stats recompute |
| `/api/v1/progress/{subject}/tracks` | GET | Faster hierarchy + coalesced stats recompute |
| `/api/v1/progress/{subject}/tracks/{track_id}` | GET | Faster hierarchy + coalesced stats recompute |
| `/api/v1/progress/{subject}/tracks/{track_id}/units/{unit_id}` | GET | Faster hierarchy + coalesced stats recompute |
| `/api/v1/progress/{subject}/topics/{topic_id}/lessons` | GET | Faster hierarchy lookup via local cache |

All request/response schemas remain identical. The only observable change is reduced latency.

### Internal Service Contracts (Changed)

**HierarchyService.get_hierarchy(subject_id: str) -> SubjectHierarchy | None**
- Unchanged signature
- New behavior: Checks module-level local cache before Redis
- Return type unchanged

**HierarchyService.invalidate(subject_id: str) -> None**
- Unchanged signature
- New behavior: Also clears module-level local cache entry

**HierarchyService.invalidate_all() -> None**
- Unchanged signature
- New behavior: Also clears all module-level local cache entries

**StatsService.get_or_recompute(...) -> dict[str, str]**
- Unchanged signature
- New behavior: Acquires per-key lock before recomputation to prevent duplicate work
