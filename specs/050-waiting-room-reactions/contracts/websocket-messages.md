# WebSocket Message Contracts: Waiting Room Reactions

**Feature**: 050-waiting-room-reactions | **Date**: 2026-03-17
**Channel**: Existing Live Challenge WebSocket at `/api/v1/live-challenge/{event_id}/ws`

## Client → Server Messages

### `waiting_room_reaction_tap`

Sent by a participant to register a reaction tap.

```json
{
  "type": "waiting_room_reaction_tap",
  "reaction": "heart"
}
```

| Field | Type | Required | Values | Notes |
|-------|------|----------|--------|-------|
| `type` | string | yes | `"waiting_room_reaction_tap"` | Message discriminator |
| `reaction` | string | yes | `"heart"`, `"fire"`, `"clap"` | Invalid values silently dropped |

**Behavior**:
- Accepted only when room status is `"waiting"`. Silently dropped in all other states.
- Rate limited per user: 3 taps/sec sustained, 6 burst in 2s. Excess silently dropped.
- Room-level cap: 250 taps/sec aggregate. Excess silently dropped.
- Any additional fields (e.g., `count`, `intensity`, `metadata`) are ignored (FR-015).
- Invalid JSON is silently dropped (no error response).
- No acknowledgment is sent for individual taps.

**Error handling**: No errors are ever sent to the client for reaction taps. All rejection conditions (invalid reaction, wrong state, rate limited, room cap) result in silent drops. The client has no way to distinguish "accepted" from "dropped" — this is by design (reactions are cosmetic).

## Server → Client Messages

### `waiting_room_reaction_burst`

Broadcast to all room participants at each flush interval (default 300ms). Only emitted when at least one reaction was counted in the window.

```json
{
  "type": "waiting_room_reaction_burst",
  "room_id": "LC-EVENT-001",
  "reactions": {
    "heart": {
      "count": 42,
      "intensity": "medium"
    },
    "fire": {
      "count": 7,
      "intensity": "low"
    }
  },
  "degraded": false,
  "window_duration_ms": 300,
  "server_ts": "2026-03-17T14:23:05.123Z"
}
```

| Field | Type | Always Present | Notes |
|-------|------|----------------|-------|
| `type` | string | yes | `"waiting_room_reaction_burst"` |
| `room_id` | string | yes | The `event_id` of the Live Challenge |
| `reactions` | object | yes | Map of reaction type → detail. Only types with count > 0 are included. |
| `reactions.{type}.count` | integer | yes (per type) | Number of accepted taps in this window |
| `reactions.{type}.intensity` | string | yes (per type) | `"low"` (1-10), `"medium"` (11-50), `"high"` (51+) |
| `degraded` | boolean | yes | `true` if room cap was exceeded during this window |
| `window_duration_ms` | integer | yes | Configured flush interval in milliseconds |
| `server_ts` | string | yes | ISO 8601 UTC timestamp when burst was emitted |

**Suppression rules**:
- If all reaction counts are zero for a window, no burst message is emitted (empty window suppression).
- After room transitions out of `waiting`, no more burst messages are emitted.

**Anonymity guarantee** (FR-003): The burst message contains zero user-identifying information. No player IDs, names, avatars, join order, or sender metadata are included. The client has no way to determine who sent which reactions.

**Degradation behavior** (FR-009):
- When `degraded: true`, counts may be lower than actual tap volume (excess taps were dropped at the room cap).
- Intensity tiers reflect the capped counts, not the raw input volume.
- The burst cadence (flush interval) is maintained even under degradation.

## Message Flow Timing

```
Time  Client A      Client B      Server
─────────────────────────────────────────────
 0ms  tap(heart)                  accept, count heart=1
50ms                tap(fire)     accept, count fire=1
100ms tap(clap)                   accept, count clap=1
200ms tap(heart)    tap(heart)    accept, count heart=3
300ms                             ─── flush ───
                                  broadcast burst:
                                    heart: 3, low
                                    fire: 1, low
                                    clap: 1, low
      ◄─ burst ──  ◄─ burst ──
```

## Coexistence with Existing Messages

The reaction messages coexist with the existing Live Challenge WebSocket messages. The full message type set during `waiting` state:

| Type | Direction | Existing/New |
|------|-----------|-------------|
| `countdown` | server → client | existing (1s interval) |
| `waiting_room_reaction_tap` | client → server | **new** |
| `waiting_room_reaction_burst` | server → client | **new** |

On transition to `active`:
| Type | Direction | Existing/New |
|------|-----------|-------------|
| `exam_start` | server → client | existing |
| `event_ended` | server → client | existing |

Reaction messages stop entirely after `waiting` → `active` transition.
