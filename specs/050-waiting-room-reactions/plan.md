# Implementation Plan: Live Challenge Waiting Room Reactions (Backend Only)

**Branch**: `050-waiting-room-reactions` | **Date**: 2026-03-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/050-waiting-room-reactions/spec.md`

## Summary

Add lightweight, anonymous, real-time reaction support (heart, fire, clap) to the Live Challenge waiting room backend. Taps are aggregated in-memory on the FastAPI process and broadcast as windowed bursts over the existing WebSocket channel. Per-user rate limiting uses a Redis Lua script for atomicity. No persistent storage — all reaction data is ephemeral and process-scoped.

## Technical Context

**Language/Version**: Python 3.11+ (async/await)
**Primary Dependencies**: FastAPI (WebSocket), redis.asyncio (rate limiting Lua), structlog (logging)
**Storage**: In-memory dicts for aggregation counters; Redis for per-user rate limit tokens only (short TTL)
**Testing**: pytest with real Redis (existing conftest pattern), Starlette TestClient for WebSocket
**Target Platform**: Linux server (same FastAPI sidecar on port 8002)
**Project Type**: Single — extends existing FastAPI sidecar
**Performance Goals**: < 1ms per tap acceptance (in-memory increment); < 500ms end-to-end perceived delay (300ms aggregation window + broadcast)
**Constraints**: Zero database writes (FR-004); zero user-identifying fields in broadcasts (FR-003); reactions must never block countdown/exam transitions
**Scale/Scope**: Up to 10k concurrent WebSocket connections per event; 250 reactions/sec room-level cap (configurable)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Self-Healing Cache (NON-NEGOTIABLE) | **JUSTIFIED DEVIATION** | Reactions are intentionally ephemeral (FR-004). No MariaDB source of truth exists because data MUST NOT persist. Redis loss = reactions silently degrade to no-op (FR-013). The "No Redis-only state" rule targets data that must survive restarts; reactions are explicitly disposable. See Complexity Tracking. |
| II. Sub-20ms Game API Performance | **COMPLIANT** | Tap processing is in-memory (zero I/O). Rate limiting uses Redis Lua (sub-1ms). No Frappe ORM in hot path. Broadcast reuses existing `_broadcast_json` with pre-serialization. |
| III. Content Hierarchy Integrity | **N/A** | Reactions do not touch content structure, bitmaps, or hierarchy. |
| IV. Double-Gate Access Control | **COMPLIANT** | FR-011 participant validation uses existing `lc_joined:{event_id}` SISMEMBER check (already performed at WebSocket handshake). Gate 1 (season) is implicitly satisfied because the event must be in `waiting` state. |
| V. Cryptographic Voucher Security | **N/A** | No voucher, PIN, or HMAC operations. |
| VI. Financial Precision | **N/A** | No monetary calculations. |
| VII. Auditable State Machines | **COMPLIANT** | Reactions follow existing room state machine (Draft → Waiting → Active → Ended). No new states introduced. Reactions only active during `waiting`; cutoff on any transition. |
| VIII. Test-First Coverage | **COMPLIANT** | Unit tests for engine logic (pure, no I/O). Integration tests for WS tap-to-burst flow with real Redis. Concurrency test for rate limiting. |

**Gate evaluation**: PASS — one justified deviation (Principle I), all others compliant or N/A.

## Project Structure

### Documentation (this feature)

```text
specs/050-waiting-room-reactions/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── websocket-messages.md
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
fastapi_app/
├── services/
│   ├── live_challenge.py            # MODIFIED — wire reaction engine lifecycle
│   └── waiting_room_reactions.py    # NEW — ReactionEngine class
├── api/v1/endpoints/
│   └── live_challenge.py            # MODIFIED — handle tap messages in WS loop
├── core/
│   ├── redis_keys.py                # MODIFIED — add lc_reaction_rl_key
│   └── config.py                    # MODIFIED — add reaction config settings
└── tests/
    ├── test_waiting_room_reactions.py       # NEW — unit tests for engine
    └── test_waiting_room_reactions_ws.py    # NEW — integration tests for WS flow
```

**Structure Decision**: Reaction logic lives in a dedicated service module (`waiting_room_reactions.py`) to keep `live_challenge.py` focused on its existing responsibilities. The engine is instantiated by `LiveChallengeService` and receives a broadcast callback. This mirrors the existing pattern where service classes are composed in `main.py` lifespan.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Principle I: Ephemeral Redis-only rate limit keys | Rate limit tokens (3s TTL) must survive WebSocket reconnects within the same burst window. MariaDB round-trip would exceed performance budget. | In-memory-only rate limiting resets on reconnect, allowing burst-after-reconnect abuse. Redis Lua gives atomicity + cross-connection persistence at sub-1ms cost. |
| Principle I: No MariaDB source of truth for reaction data | FR-004 explicitly forbids persistent storage. Reactions are cosmetic, anonymous, and disposable by design. | Adding a MariaDB table would violate the spec, add write latency, and create compliance surface (student data retention). |

## Architecture: Reaction Engine

### Design Decision: In-Memory Aggregation + Redis Rate Limiting

The `ReactionEngine` uses a **hybrid approach**:

1. **In-memory counters** for tap aggregation — zero I/O per tap, process-local
2. **Redis Lua script** for per-user rate limiting — atomic, survives reconnects
3. **In-memory sliding window** for room-level cap tracking — process-local

**Rationale**: WebSocket connections are pinned to a single FastAPI process. The `LiveChallengeService` is a singleton managing all connections for all events. Aggregation counters don't need cross-process visibility since all taps for a room arrive on the same process. Redis is only needed where atomicity or cross-connection state matters (rate limiting).

### Component Responsibilities

```
┌─────────────────────────────────────────────────────────┐
│              LiveChallengeService (existing)             │
│  - Owns WebSocket connections + broadcast               │
│  - Manages countdown loop lifecycle                     │
│  - Delegates tap handling to ReactionEngine              │
│  - Starts/stops reaction flush loop alongside countdown  │
│  - On transition to Active/Ended: calls engine.stop()   │
├─────────────────────────────────────────────────────────┤
│              ReactionEngine (new service)                │
│  - Per-room in-memory state: counters, window tracker   │
│  - Per-user rate limiting (Redis Lua)                   │
│  - Room-level cap enforcement (in-memory)               │
│  - Flush loop (async task, 300ms interval)              │
│  - Produces burst message dicts; caller broadcasts      │
└─────────────────────────────────────────────────────────┘
```

### Tap Processing Flow

```
Client sends: {"type": "waiting_room_reaction_tap", "reaction": "heart"}
                          │
                          ▼
            WS endpoint receives text
                          │
                          ▼
            Parse JSON, extract "reaction"
                          │
                          ▼
            Validate reaction ∈ {heart, fire, clap}
            (invalid → silently drop)
                          │
                          ▼
            Check room status == "waiting"
            (not waiting → silently drop)
                          │
                          ▼
            engine.accept_tap(event_id, player_id, reaction)
                          │
                ┌─────────┴──────────┐
                │                    │
         Rate limit check      Room cap check
         (Redis Lua)           (in-memory)
                │                    │
                └─────────┬──────────┘
                          │
                 Both pass → increment counter
                 Either fails → silently drop
```

### Flush Loop Flow

```
Every 300ms (configurable):
    │
    ▼
Snapshot + reset counters for all active rooms
    │
    ▼
For each room with non-zero counts:
    │
    ├── Compute intensity tier per reaction type
    ├── Compute degradation status (room cap exceeded?)
    ├── Build burst message dict
    └── Call broadcast callback (LiveChallengeService._broadcast_json)
```

### Rate Limiting: Token Bucket via Redis Lua

```lua
-- KEYS[1] = lc_reaction_rl:{event_id}:{player_id}
-- ARGV[1] = max_tokens (burst allowance = 6)
-- ARGV[2] = refill_rate_per_sec (sustained = 3)
-- ARGV[3] = now_ms (server timestamp in milliseconds)
-- ARGV[4] = ttl_sec (key expiry = 5)
-- Returns: 1 if allowed, 0 if rejected

local key = KEYS[1]
local max_tokens = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_ms')
local tokens = tonumber(data[1])
local last_ms = tonumber(data[2])

if tokens == nil then
    -- First tap: initialize bucket
    tokens = max_tokens - 1
    redis.call('HMSET', key, 'tokens', tokens, 'last_ms', now_ms)
    redis.call('EXPIRE', key, ttl)
    return 1
end

-- Refill tokens based on elapsed time
local elapsed_ms = now_ms - last_ms
local refill = elapsed_ms * refill_rate / 1000
tokens = math.min(max_tokens, tokens + refill)

if tokens < 1 then
    -- Rate limited — update timestamp but don't consume
    redis.call('HSET', key, 'last_ms', now_ms)
    redis.call('EXPIRE', key, ttl)
    return 0
end

-- Consume one token
tokens = tokens - 1
redis.call('HMSET', key, 'tokens', tokens, 'last_ms', now_ms)
redis.call('EXPIRE', key, ttl)
return 1
```

### Intensity Tier Thresholds

| Tier | Tap Count Range (per reaction per window) |
|------|-------------------------------------------|
| `low` | 1 – 10 |
| `medium` | 11 – 50 |
| `high` | 51+ |

### Room-Level Degradation

When total taps across all reactions in a 1-second sliding window exceed the room cap (default 250/sec):
- `degraded` flag set to `true` in burst messages
- Excess taps beyond the cap are silently dropped
- Cap is enforced per-second using an in-memory counter that resets each second

### Configuration Settings (added to `config.py`)

| Setting | Default | Description |
|---------|---------|-------------|
| `reaction_flush_interval_ms` | 300 | Burst aggregation window in milliseconds |
| `reaction_sustained_rate` | 3 | Max taps/sec per user (sustained) |
| `reaction_burst_allowance` | 6 | Max tokens in rate limit bucket |
| `reaction_room_cap_per_sec` | 250 | Room-level reaction cap per second |
| `reaction_rl_ttl_sec` | 5 | TTL for rate limit Redis keys |
| `reaction_counter_ttl_sec` | 15 | TTL for ephemeral Redis keys (post-room-transition cleanup) |
| `reaction_enabled` | True | Feature flag to disable reactions globally |

### Redis Key

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `lc_reaction_rl:{event_id}:{player_id}` | HASH (tokens, last_ms) | 5s | Per-user rate limit token bucket |

All other state (aggregation counters, room caps, flush tasks) is in-memory only.

### Room Transition Cutoff

When `LiveChallengeService` transitions a room from `waiting` to `active` (line 1273-1278 in live_challenge.py):
1. Call `engine.stop_room(event_id)` — cancels flush task, clears counters
2. Incoming taps are rejected by status check in WS handler (status != "waiting")
3. Redis rate limit keys auto-expire via 5s TTL

### Error Handling / Resilience (FR-013)

All reaction processing is wrapped in try/except at every boundary:
- **Redis unavailable** (rate limit check fails): fail-open, accept the tap (skip rate limiting)
- **Engine error** (counter increment fails): silently drop tap, log warning
- **Broadcast error**: handled by existing `_broadcast_json` dead-connection cleanup
- **Flush loop crash**: log error, loop restarts on next tap (or next countdown tick recreates it)

No reaction failure can propagate to countdown, room transitions, or exam flow.
