# Contract: Level Config Redis Cache

**Key**: `memora:config:levels`
**Type**: JSON string
**TTL**: 3600s (1 hour)
**Producer**: Frappe `on_update` hook for `Memora Level Settings`
**Consumer**: FastAPI `level_config.get_level_config(redis)`

## Write (Frappe → Redis)

Triggered by: `Memora Level Settings` save event

```
SET memora:config:levels <json_payload> EX 3600
PUBLISH memora:cache:invalidate {"type": "level_config", "timestamp": "<now>"}
```

### Payload Schema

```json
{
  "a": <int>,           // quadratic_coefficient, >= 1
  "b": <int>,           // linear_coefficient, >= 0
  "max_level": <int>,   // max level cap, >= 1
  "titles": {           // level_number (str) → title_en (str)
    "1": "Beginner",
    "2": "Learner",
    ...
  }
}
```

## Read (FastAPI ← Redis)

```
GET memora:config:levels
```

- **Cache hit**: Parse JSON, construct `LevelConfig` dataclass
- **Cache miss**: Return hardcoded `DEFAULT_LEVEL_CONFIG` (a=50, b=50, max=15, 15 titles)

## Invalidation

| Trigger | Action |
|---------|--------|
| Admin saves Level Settings | Direct `SET` with new data + pubsub |
| TTL expires (1h) | Next read falls back to defaults |
| Redis FLUSHDB | Next read falls back to defaults |

## Pubsub Message

**Channel**: `memora:cache:invalidate`

```json
{
  "type": "level_config",
  "timestamp": "2026-02-22 12:00:00"
}
```

**Handler**: `pubsub.py:_handle_invalidation()` — logs the event. No in-memory cache to invalidate (config is read from Redis on each request).
