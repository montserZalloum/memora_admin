# Data Model: Dynamic Level System

**Feature**: 023-dynamic-level-system
**Date**: 2026-02-22

## Entities

### Memora Level Settings (Single DocType)

A singleton configuration document. Only one instance exists in the system.

| Field | Type | Default | Required | Validation |
|-------|------|---------|----------|------------|
| `quadratic_coefficient` | Int | 50 | Yes | min 1 |
| `linear_coefficient` | Int | 50 | Yes | min 0 |
| `max_level` | Int | 15 | Yes | min 1, max 200 |
| `level_titles` | Table (Memora Level Title) | 15 rows | Yes | At least 1 row |

**Permissions**: System Manager (create, read, write, delete)

**Lifecycle**: Created automatically on first access. Pre-populated with default values (a=50, b=50, max=15, 15 title rows).

### Memora Level Title (Child Table)

Embedded in `Memora Level Settings`. Each row defines the display title for a specific level number.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `level_number` | Int | Yes | 1-based, unique within parent |
| `title_en` | Data | Yes | English title, max 140 chars |
| `title_ar` | Data | No | Arabic title, max 140 chars |
| `icon` | Attach Image | No | Optional badge/icon image |

**Constraints**:
- No duplicate `level_number` values within the same parent
- `title_en` must be non-empty for every row
- `level_number` must be >= 1

### Level Config (Redis Cached)

Read-only snapshot pushed to Redis on admin save.

**Redis Key**: `memora:config:levels`
**TTL**: 3600 seconds (1 hour)
**Fallback**: Hardcoded defaults in FastAPI code

**Payload Schema** (JSON string):
```json
{
  "a": 50,
  "b": 50,
  "max_level": 15,
  "titles": {
    "1": "Beginner",
    "2": "Learner",
    "3": "Explorer",
    "4": "Scholar",
    "5": "Achiever",
    "6": "Expert",
    "7": "Master",
    "8": "Champion",
    "9": "Legend",
    "10": "Grandmaster",
    "11": "Sage",
    "12": "Titan",
    "13": "Mythic",
    "14": "Immortal",
    "15": "Transcendent"
  }
}
```

## Relationships

```
Memora Level Settings (1) ──has-many──> Memora Level Title (N)
         │
         │ on_update hook
         ▼
  Redis: memora:config:levels (JSON string, 1h TTL)
         │
         │ read by
         ▼
  FastAPI: level_config.get_level_config(redis) → LevelConfig dataclass
         │
         │ used by
         ▼
  FastAPI: level_config.calculate_level(xp, config) → (level, title, xp_in, xp_to_next)
```

## State Transitions

None. Level Settings is a static configuration document with no lifecycle states. It is created once and updated in-place.

## Default Data (Pre-populated on First Creation)

| Level | title_en |
|-------|----------|
| 1 | Beginner |
| 2 | Learner |
| 3 | Explorer |
| 4 | Scholar |
| 5 | Achiever |
| 6 | Expert |
| 7 | Master |
| 8 | Champion |
| 9 | Legend |
| 10 | Grandmaster |
| 11 | Sage |
| 12 | Titan |
| 13 | Mythic |
| 14 | Immortal |
| 15 | Transcendent |
