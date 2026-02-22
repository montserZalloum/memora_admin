# Contract: calculate_level Function

**Module**: `fastapi_app/core/level_config.py`
**Replaces**: `fastapi_app/core/constants.py:calculate_level()`

## Signature

```python
def calculate_level(total_xp: int, config: LevelConfig) -> tuple[int, str, int, int]:
    """Calculate player level from total XP using config parameters.

    Returns (level, title, xp_in_level, xp_to_next_level).
    """
```

## LevelConfig Dataclass

```python
@dataclass(frozen=True)
class LevelConfig:
    a: int = 50           # quadratic coefficient
    b: int = 50           # linear coefficient
    max_level: int = 15   # level cap
    titles: dict[int, str] = <15 default titles>
```

## Formulas

### Threshold (level → XP)
```
threshold(level) = round(a * (level - 1)^2 + b * (level - 1))
```

### Inverse (XP → level), O(1)
```
level = min(floor((-b + sqrt(b^2 + 4*a*xp)) / (2*a)) + 1, max_level)
```

## Return Values

| Field | Type | Description |
|-------|------|-------------|
| `level` | int | 1-based level number, clamped to [1, max_level] |
| `title` | str | `config.titles.get(level, f"Level {level}")` |
| `xp_in_level` | int | `total_xp - threshold(level)` |
| `xp_to_next` | int | `threshold(level+1) - total_xp` if level < max_level, else 0 |

## Helper: get_threshold

```python
def get_threshold(level: int, a: int, b: int) -> int:
    """Compute XP threshold for a given level. Pure, O(1)."""
    return round(a * (level - 1) ** 2 + b * (level - 1))
```

Used by `ProfilePageService.get_hero()` to compute `xp_level_start` and `xp_level_end`.

## Edge Cases

| Input | Expected Output |
|-------|----------------|
| `total_xp=0` | `(1, "Beginner", 0, 100)` |
| `total_xp=100` | `(2, "Learner", 0, 200)` |
| `total_xp=500` | `(3, "Explorer", 200, 100)` |
| `total_xp=11000` (max default) | `(15, "Transcendent", 500, 0)` |
| `total_xp=99999` (overflow) | `(15, "Transcendent", 89499, 0)` |
| `total_xp=-5` (negative) | `(1, "Beginner", 0, 100)` (clamped to 0) |

## Async Config Loader

```python
async def get_level_config(redis_client) -> LevelConfig:
    """Load level config from Redis, fallback to defaults on miss."""
```

- Reads `memora:config:levels` key
- Parses JSON payload
- Returns `LevelConfig` dataclass
- On miss/error: returns `DEFAULT_LEVEL_CONFIG`
