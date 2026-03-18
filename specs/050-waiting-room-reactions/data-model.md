# Data Model: Live Challenge Waiting Room Reactions

**Feature**: 050-waiting-room-reactions | **Date**: 2026-03-17

## Overview

All reaction data is ephemeral. No MariaDB tables are created. Data exists only in two places:
1. **In-memory** (Python dicts on the FastAPI process) — aggregation counters, room caps
2. **Redis** (short-TTL keys) — per-user rate limit tokens only

## Entities

### ReactionTap (transient event — never stored)

An individual user action received over WebSocket. Validated, rate-checked, and counted in-memory. Never persisted.

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `event_id` | str | URL path param | From WebSocket endpoint `/{event_id}/ws` |
| `player_id` | str | JWT `sub` claim | Authenticated at WS handshake; not included in broadcasts |
| `reaction` | str | Client message | One of: `heart`, `fire`, `clap`. All others silently dropped. |
| `timestamp` | float | Server | `time.monotonic()` at receipt. Used for rate limit and room cap tracking. |

**Validation rules**:
- `reaction` must be in `{"heart", "fire", "clap"}` (FR-002)
- Room status must be `"waiting"` (FR-001)
- Client fields `count`, `intensity`, `metadata` are ignored if present (FR-015)

### RoomReactionState (in-memory, per event)

Process-local state tracking reaction counters for a single waiting room.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `counters` | `dict[str, int]` | `{}` | Maps reaction type → tap count for current window. Reset on flush. |
| `room_tap_count` | `int` | `0` | Total taps this second (all reaction types). Resets each second. |
| `room_tap_second` | `int` | `0` | Unix second of `room_tap_count`. Used for per-second reset. |
| `flush_task` | `asyncio.Task \| None` | `None` | Handle to the flush loop background task. |
| `degraded` | `bool` | `False` | Set when room cap exceeded. Reset when volume drops below cap. |

**Lifecycle**:
- Created on first tap for an event (lazy initialization)
- Destroyed when `stop_room(event_id)` is called (room transition out of `waiting`)
- Flush loop reads + resets `counters` every 300ms

### RateLimitToken (Redis HASH, per user per event)

Tracks token bucket state for per-user rate limiting. Stored in Redis to survive WebSocket reconnects.

| Field | Type | Notes |
|-------|------|-------|
| `tokens` | float (stored as string) | Current token count. Starts at `max_tokens` (6). Decremented by 1 per tap. Refilled at `refill_rate` (3/sec). |
| `last_ms` | int (stored as string) | Server timestamp in milliseconds of last bucket update. Used to compute refill amount. |

**Redis key**: `lc_reaction_rl:{event_id}:{player_id}`
**Redis type**: HASH
**TTL**: 5 seconds (auto-cleanup after inactivity)

**State transitions**:
- **New user taps**: Key created with `tokens = max_tokens - 1`, `last_ms = now`
- **Subsequent taps**: Refill tokens based on elapsed time, then attempt to consume 1 token
- **Token exhaustion**: `tokens < 1` → tap rejected (return 0 from Lua)
- **Inactivity**: Key expires after 5 seconds (TTL reset on every access)
- **Room transition**: Keys auto-expire; no explicit cleanup needed

### BurstMessage (outgoing broadcast — never stored)

The aggregated payload broadcast to all room participants at each flush interval.

| Field | Type | Notes |
|-------|------|-------|
| `type` | str | Always `"waiting_room_reaction_burst"` |
| `room_id` | str | The `event_id` of the Live Challenge |
| `reactions` | `dict[str, ReactionDetail]` | Only includes reaction types with count > 0 |
| `degraded` | bool | `true` if room cap was exceeded during this window |
| `window_duration_ms` | int | Configured flush interval (default 300) |
| `server_ts` | str | ISO 8601 timestamp (UTC) |

**ReactionDetail** (nested object per reaction type):

| Field | Type | Notes |
|-------|------|-------|
| `count` | int | Number of accepted taps in this window |
| `intensity` | str | One of: `low` (1-10), `medium` (11-50), `high` (51+) |

**Validation rules**:
- Empty windows (all counts = 0) are suppressed — no message emitted (edge case)
- Zero user-identifying information (FR-003) — no player_id, name, avatar, sender metadata
- `count` and `intensity` are server-computed; client-provided values are ignored (FR-015)

## Entity Relationships

```
ReactionTap (transient)
    │
    ├── validated against → RoomReactionState.counters (in-memory)
    ├── rate-checked via → RateLimitToken (Redis)
    └── cap-checked via → RoomReactionState.room_tap_count (in-memory)

RoomReactionState (in-memory)
    │
    └── flush loop produces → BurstMessage (transient broadcast)
```

## State Diagram: Room Reaction Lifecycle

```
                ┌────────────────┐
                │   Room Created  │
                │   (no reactions)│
                └───────┬────────┘
                        │ First tap while status == "waiting"
                        ▼
                ┌────────────────┐
                │  Room Active    │
                │  (flush loop    │◄──── taps accepted
                │   running)      │       counters incremented
                └───────┬────────┘       burst messages emitted
                        │ Room transitions to "active" or "ended"
                        ▼
                ┌────────────────┐
                │  Room Stopped   │
                │  (flush loop    │      taps silently rejected
                │   cancelled,    │      counters cleared
                │   counters      │      Redis keys auto-expire (5s TTL)
                │   discarded)    │
                └────────────────┘
```

## No MariaDB Schema Changes

This feature creates zero database tables, zero DocTypes, and zero schema migrations. All state is either:
- In-memory Python dicts (aggregation, room cap)
- Short-TTL Redis keys (rate limiting only)

This is consistent with FR-004: "System MUST NOT write reaction data to any persistent store."
