# Quickstart: Waiting Room Reactions

**Feature**: 050-waiting-room-reactions | **Date**: 2026-03-17

## What This Feature Does

Adds anonymous, ephemeral reaction support (heart/fire/clap) to the Live Challenge waiting room. Participants tap reactions via the existing WebSocket; the server aggregates taps into 300ms burst broadcasts. No database writes, no user attribution, no gameplay impact.

## Files to Create

| File | Purpose |
|------|---------|
| `fastapi_app/services/waiting_room_reactions.py` | ReactionEngine: in-memory aggregation, Redis rate limiting, flush loop |
| `fastapi_app/tests/test_waiting_room_reactions.py` | Unit tests for ReactionEngine (pure logic, mocked Redis) |
| `fastapi_app/tests/test_waiting_room_reactions_ws.py` | Integration tests for full WS tap → burst flow (real Redis) |

## Files to Modify

| File | Change |
|------|--------|
| `fastapi_app/core/redis_keys.py` | Add `lc_reaction_rl_key(event_id, player_id)` builder + `REACTION_RL_TTL` constant |
| `fastapi_app/core/config.py` | Add 7 reaction settings to `Settings` class |
| `fastapi_app/services/live_challenge.py` | Create ReactionEngine in `__init__`; wire tap handling; start/stop reaction flush loop; stop reactions on room transition |
| `fastapi_app/api/v1/endpoints/live_challenge.py` | Parse incoming WS messages; route `waiting_room_reaction_tap` to service |

## Key Architecture Decisions

1. **In-memory aggregation** — Tap counters are Python dicts on the process. No Redis I/O per tap. Flush loop reads+resets every 300ms.
2. **Redis Lua for rate limiting** — Token bucket (capacity=6, refill=3/sec) via atomic Lua script. Survives WebSocket reconnects. 5s TTL auto-cleanup.
3. **Broadcast via existing `_broadcast_json`** — Reuses the concurrency-controlled fan-out (2000-connection semaphore, 2s timeout, dead connection cleanup).
4. **Fail-open on Redis errors** — If rate limit check fails, tap is accepted (rate limiting degrades, aggregation continues).
5. **Feature flag** — `reaction_enabled` setting in config. `False` = all taps silently dropped, no flush loops started.

## How to Test

### Unit tests (no Redis needed)
```bash
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_waiting_room_reactions.py -v
```

### Integration tests (requires Redis)
```bash
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_waiting_room_reactions_ws.py -v
```

### Manual WebSocket test
```bash
# Terminal 1: Start FastAPI
uvicorn fastapi_app.main:app --host 0.0.0.0 --port 8002

# Terminal 2: Connect via wscat (or any WS client)
# First join the event via REST, then connect:
wscat -c "ws://localhost:8002/api/v1/live-challenge/LC-TEST/ws?token=<jwt>"

# Send a tap:
{"type": "waiting_room_reaction_tap", "reaction": "heart"}

# Expect burst within 300ms:
{"type": "waiting_room_reaction_burst", "room_id": "LC-TEST", "reactions": {"heart": {"count": 1, "intensity": "low"}}, "degraded": false, "window_duration_ms": 300, "server_ts": "2026-03-17T..."}
```

## Configuration (.env)

All settings have defaults — no .env changes required for development:

```env
# Optional: tune reaction behavior
REACTION_FLUSH_INTERVAL_MS=300
REACTION_SUSTAINED_RATE=3
REACTION_BURST_ALLOWANCE=6
REACTION_ROOM_CAP_PER_SEC=250
REACTION_RL_TTL_SEC=5
REACTION_COUNTER_TTL_SEC=15
REACTION_ENABLED=true
```

## Dependency Map

```
config.py (settings)
    │
    ├──► redis_keys.py (lc_reaction_rl_key)
    │
    └──► waiting_room_reactions.py (ReactionEngine)
              │
              ├── uses redis_keys.py for rate limit keys
              ├── uses config.py for thresholds
              └── receives broadcast callback from LiveChallengeService
                      │
                      ├── live_challenge.py (service) — creates engine, wires lifecycle
                      └── live_challenge.py (endpoint) — routes WS taps to service
```
