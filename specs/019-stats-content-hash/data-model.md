# Data Model: Stats Cache Staleness Detection

**Feature**: 019-stats-content-hash
**Date**: 2026-02-18

## Entities

### 1. Subject Hierarchy (Modified)

**Source**: `memora_admin/api/hierarchy.py` → `get_subject_hierarchy()`
**Storage**: Redis key `memora:hierarchy:{subject_id}` (JSON string, 1h TTL)

| Field | Type | New? | Description |
|-------|------|------|-------------|
| `subject_id` | `str` | No | Subject identifier |
| `version` | `int` | No | Hierarchy version (default 1) |
| `bit_range` | `int` | No | Total bits allocated for lessons |
| `excluded_bits` | `list[int]` | No | Deleted lesson bit indices |
| `is_linear` | `bool` | No | Linear progression flag |
| `free_units` | `list[str]` | No | Unit IDs with free access |
| `free_topics` | `list[str]` | No | Topic IDs with free access |
| **`content_hash`** | **`str`** | **Yes** | **Structural fingerprint (8 hex chars, MD5 truncated). Computed once during hierarchy build. Default `""` for backward compatibility.** |
| `tracks` | `list[TrackInfo]` | No | Track tree (units → topics → lessons) |

**Pydantic model**: `fastapi_app/models/progress.py:SubjectHierarchy`

```python
class SubjectHierarchy(BaseModel):
    subject_id: str
    version: int = 1
    bit_range: int
    excluded_bits: list[int] = []
    is_linear: bool = True
    free_units: list[str] = []
    free_topics: list[str] = []
    content_hash: str = ""    # NEW
    tracks: list[TrackInfo]
```

### 2. Stats Cache (Modified)

**Storage**: Redis key `memora:stats:{user_id}:{subject_id}:v{version}` (Hash, 1h TTL)

| Field | Type | New? | Description |
|-------|------|------|-------------|
| `completed` | `str` (int-as-string) | No | Total completed lessons (subject level) |
| `total` | `str` (int-as-string) | No | Total lessons in subject |
| `{track_id}:completed` | `str` | No | Completed lessons in track |
| `{track_id}:total` | `str` | No | Total lessons in track |
| `{unit_id}:completed` | `str` | No | Completed lessons in unit |
| `{unit_id}:total` | `str` | No | Total lessons in unit |
| `{topic_id}:completed` | `str` | No | Completed lessons in topic |
| `{topic_id}:total` | `str` | No | Total lessons in topic |
| **`_content_hash`** | **`str`** | **Yes** | **Structural fingerprint from hierarchy at time of stats computation. Prefixed with `_` to avoid collision with entity ID patterns.** |

**No Pydantic model** — the stats hash is read as a raw `dict[str, str]` via `HGETALL`.

### 3. Content Hash (New Concept — Not a Separate Entity)

**Computation**: `_compute_content_hash(hierarchy: dict) -> str`
**Location**: `memora_admin/api/hierarchy.py` (Frappe side)
**Algorithm**: Incremental `hashlib.md5()`, truncated to 8 hex chars

**Fields hashed** (order matters — natural Frappe `ORDER BY idx asc`):
1. `hierarchy["bit_range"]` → `str(bit_range).encode()`
2. `len(excluded_bits)` → `str(count).encode()`
3. Each `excluded_bit` (sorted) → `str(bit).encode()`
4. For each track: `track_id.encode()`
5. For each unit: `unit_id.encode()`
6. For each topic: `topic_id.encode()`
7. For each topic: `str(len(lessons)).encode()`
8. For each lesson: `lesson_id.encode()`
9. For each lesson: `str(bit_index).encode()`

**Fields NOT hashed**: `is_linear`, `is_free`, `xp`, `max_hearts`, `free_units`, `free_topics`, `is_sold_separately`

## Relationships

```
SubjectHierarchy (Redis JSON, 1h TTL)
  ├── content_hash: computed at build time from structural fields
  │
  └── used by ──→ Stats Cache (Redis Hash, 1h TTL)
                    └── _content_hash: copied from hierarchy.content_hash at stats computation time

On progress read:
  hierarchy.content_hash != stats._content_hash → recompute stats from bitmap
  hierarchy.content_hash == stats._content_hash → serve cached stats
```

## Validation Rules

| Rule | Enforcement |
|------|-------------|
| `content_hash` is deterministic | Same hierarchy structure always produces same hash (unit tested) |
| `content_hash` changes on structural change | Adding/removing/reordering lessons changes hash (unit tested) |
| `content_hash` stable on non-structural change | Changing XP, linearity, free flags does NOT change hash (unit tested) |
| `_content_hash` survives HINCRBY | Redis HINCRBY on `:completed` fields does not affect `_content_hash` field |
| Missing `_content_hash` = stale | `dict.get("_content_hash")` returns `None`, triggering recompute |
| Empty `content_hash` = pre-migration | Default `""` on SubjectHierarchy; mismatches any real hash; heals on next hierarchy rebuild |

## State Transitions

### Stats Cache Lifecycle

```
[Empty/Missing] ──cold start──→ [Fresh: has _content_hash matching hierarchy]
                                    │
                             lesson completion (HINCRBY)
                                    │
                                    ▼
                               [Warm: _content_hash unchanged, :completed incremented]
                                    │
                              content change (hierarchy rebuilt with new hash)
                                    │
                                    ▼
                               [Stale: _content_hash != hierarchy.content_hash]
                                    │
                              next progress read
                                    │
                                    ▼
                               [Fresh: recomputed with new _content_hash]
```

### Pre-Migration Path

```
[Legacy: no _content_hash field] ──next read──→ dict.get() returns None
                                                     │
                                                None != hierarchy.content_hash
                                                     │
                                                     ▼
                                               [Recompute + store _content_hash]
                                                     │
                                                     ▼
                                               [Fresh: self-healed]
```
