# Research: Live Challenge Waiting Room Reactions

**Feature**: 050-waiting-room-reactions | **Date**: 2026-03-17

## R1: In-Memory vs Redis for Tap Aggregation

**Decision**: In-memory counters (process-local dicts)

**Rationale**: WebSocket connections are pinned to the single FastAPI process. `LiveChallengeService` is a singleton that manages all connections for all events via `_ws_connections: dict[str, set[WebSocket]]`. The countdown loop, broadcast, and connection tracking are all in-process. Aggregation counters don't need cross-process visibility because all taps for a room arrive at the same process.

**Alternatives considered**:
- **Redis INCRBY per tap**: Adds 1 Redis round-trip per tap. At 250 taps/sec peak, that's 250 extra Redis ops/sec. Unnecessary since a single process handles all connections. Would also complicate flush (need GETSET or Lua to atomically read-and-reset).
- **Redis HASH per window**: Same overhead as INCRBY, plus window management complexity. The TTL-based cleanup is nice but in-memory cleanup via flush loop is simpler and faster.

**Risk**: Process crash loses buffered counters. Acceptable — reactions are cosmetic and ephemeral. A 300ms window of lost taps is invisible to users.

## R2: Rate Limiting Algorithm

**Decision**: Token bucket via Redis Lua script (HASH with tokens + last_ms fields)

**Rationale**: Token bucket naturally handles both sustained rate (3/sec) and burst allowance (6 in 2s). The spec's dual requirement (sustained 3/sec, burst 6 in 2s) maps directly to a bucket with capacity=6 and refill_rate=3/sec. Redis Lua provides atomicity without distributed locking. HASH with two fields (tokens, last_ms) is the minimal state needed.

**Alternatives considered**:
- **Sliding window counter (Redis INCR + EXPIRE)**: Simpler but doesn't distinguish burst vs sustained. A user could send 6 taps in 1ms and be blocked for 2 seconds — poor UX. Token bucket drains smoothly.
- **In-memory rate limiting**: Resets on WebSocket reconnect. A user could disconnect/reconnect to bypass the limit. Redis keys survive reconnects within the TTL window.
- **Leaky bucket**: Similar to token bucket but queues excess taps instead of dropping. Not wanted — spec says "silently dropped."

## R3: Flush Loop Lifecycle Management

**Decision**: One async task per active room, managed by ReactionEngine, started/stopped by LiveChallengeService

**Rationale**: Follows the existing pattern of `_countdown_tasks: dict[str, asyncio.Task]` in LiveChallengeService. The flush loop is started when the first tap arrives for a room in `waiting` state, and stopped when the room transitions out of `waiting`. The flush loop is independent of the countdown loop — both can run concurrently during the waiting phase.

**Alternatives considered**:
- **Single global flush loop**: Iterates all rooms every 300ms. Simpler code but wastes CPU when most rooms have zero taps. Per-room loops only run when a room has activity.
- **Countdown loop integration**: Add reaction flushing to the existing 1s countdown tick. But 1s is too slow for 300ms windows, and coupling reaction logic into the countdown loop violates separation of concerns.

**Risk**: Many simultaneous events with active taps create many flush tasks. Mitigated by: each task sleeps 300ms and does minimal work (dict snapshot + broadcast call). At 100 concurrent events, that's 100 lightweight tasks — well within asyncio capacity.

## R4: WebSocket Message Handling Pattern

**Decision**: Parse incoming messages in the existing WS endpoint message loop; route `waiting_room_reaction_tap` to the reaction engine

**Rationale**: The current WS endpoint (live_challenge.py:282-283) has `while True: await websocket.receive_text()` — it receives messages but discards them (only used for disconnect detection). Adding message routing here is the natural extension point. The endpoint already has `event_id`, `user_id`, and `service` in scope.

**Alternatives considered**:
- **Separate WebSocket endpoint for reactions**: Creates a second connection per client. Wastes resources, complicates client implementation, and the spec says "use the existing channel" (FR-014).
- **Middleware-level message routing**: Overengineered for a single additional message type. Direct if/elif in the message loop is clearer and sufficient.

**Implementation detail**: The message loop becomes:
```python
while True:
    raw = await websocket.receive_text()
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        continue  # silently drop malformed
    if msg.get("type") == "waiting_room_reaction_tap":
        await service.handle_reaction_tap(event_id, user_id, msg)
```

All other message types are silently ignored (forward-compatible).

## R5: Room-Level Cap Enforcement

**Decision**: In-memory per-second counter with atomic reset

**Rationale**: The room cap (250/sec) is a coarse safety valve, not a precise rate limiter. An in-memory counter that increments per accepted tap and resets every second (or is checked against a 1-second sliding window) is sufficient. No Redis needed — same rationale as R1 (single-process).

**Alternatives considered**:
- **Redis counter with 1s EXPIRE**: Adds Redis round-trip per tap. Unnecessary for a single-process safety valve.
- **Semaphore/lock**: Wrong abstraction — we're counting events per window, not limiting concurrency.

**Implementation**: Track `(room_total_this_second, second_timestamp)` per event. On each tap, if `current_second != stored_second`, reset counter. If counter >= cap, drop tap. This is O(1) per tap with no I/O.

## R6: Intensity Tier Computation

**Decision**: Static threshold lookup per reaction type, applied at flush time

**Rationale**: The spec defines tiers as low/medium/high based on tap count per reaction per window. Thresholds are configurable but ship with defaults (1-10, 11-50, 51+). Computing at flush time (not per-tap) keeps the hot path free of tier logic.

**Alternatives considered**:
- **Dynamic thresholds based on room size**: More accurate but adds complexity. Static thresholds with configurable breakpoints are sufficient for v1.
- **Single tier for entire burst**: Spec says "intensity tiers for each reaction type" — per-reaction tiers required.

## R7: Feature Flag and Configuration

**Decision**: Settings in `config.py` (Pydantic BaseSettings), with a `reaction_enabled` boolean for global kill-switch

**Rationale**: Follows existing pattern — all tunable parameters are in `config.py` loaded from `.env`. Feature flag via boolean setting (not a separate feature flag service) keeps it simple. Can be toggled via environment variable without code deploy.

**Alternatives considered**:
- **Redis-based feature flag**: Would allow runtime toggling without restart. Overkill for a single boolean. If needed later, can be added.
- **Frappe-based feature flag**: Would require Frappe ORM call on hot path. Violates Principle II.

## R8: Multi-Worker Considerations

**Decision**: No multi-worker changes needed for the current deployment

**Rationale**: The FastAPI sidecar runs as a single uvicorn process (not multiple workers) because WebSocket connection state is process-local. The `LiveChallengeService._ws_connections` dict and countdown tasks are already single-process. Reactions follow the same model. If multi-worker deployment is added in the future, both the existing countdown system and the reaction system would need a shared-state layer (Redis pub/sub or similar).

**Risk**: If multi-worker is ever enabled without updating reactions, each worker would have partial counters. Mitigated by: the same issue affects the existing countdown loop, so any multi-worker migration will necessarily address process-local state.
