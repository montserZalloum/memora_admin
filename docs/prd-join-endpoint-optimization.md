# PRD: Live Challenge Join Endpoint Optimization

## Problem

The `POST /api/v1/live-challenge/{event_id}/join` endpoint makes **3 synchronous HTTP calls to Frappe** on the hot path, adding 200-500ms of latency per join request:

1. `frappe.client.get_value` — look up the player's plan for eligibility check (~100ms)
2. `frappe.client.insert` — create a `Memora Live Challenge Participation` record (~150ms)
3. `frappe.client.set_value` — sync `participant_count` back to the event doc (~100ms)

This is unacceptable for a burst-traffic endpoint where 100+ players join within seconds.

## Goal

Make the join endpoint **pure-Redis** with **zero Frappe HTTP calls** on the hot path. Target latency: **< 5ms** (down from 200-500ms).

All Frappe persistence moves to a background reconciliation step triggered when the event ends.

---

## Current Architecture (before)

```
Client → POST /join
           ├── Redis GET  lc:{id}:status
           ├── Redis HGETALL lc:{id}:meta
           ├── Frappe HTTP → get_value (player plan)       ← SLOW
           ├── Redis Lua (atomic join: SISMEMBER + INCR + SADD)
           ├── Redis EXPIRE
           ├── Frappe HTTP → insert (participation doc)    ← SLOW
           ├── Frappe HTTP → set_value (participant_count) ← SLOW
           └── return JoinResponse
```

## Target Architecture (after)

```
Client → POST /join
           ├── Read user.plan from JWT (zero cost, already parsed)
           ├── Redis Lua (all-in-one script, see below)
           └── return JoinResponse

Event ends → background reconciliation
           ├── Read lc:{id}:participants hash from Redis
           ├── Bulk-insert Participation docs to Frappe
           ├── Sync final participant_count to Frappe
           ├── DEL all lc:{id}:* keys from Redis
           └── Done
```

---

## Detailed Requirements

### R1: Plan eligibility from JWT (replaces Frappe HTTP call #1)

**Current:** Calls `frappe.client.get_value` on `Memora Player Profile` to get the player's plan.
**New:** Read `user.plan` directly from the `TokenPayload` (already available on `CurrentUser`).

- The endpoint handler already receives `user: CurrentUser` which has `user.plan: str | None`.
- Pass `user.plan` into `service.join()` as a new parameter.
- Inside `join()`, check `user.plan in eligible_plans` instead of calling Frappe.
- If `eligible_plans` is empty (no restriction), skip the check entirely (current behavior).
- Remove the `frappe.client.get_value` call and its try/except block.

**File:** `fastapi_app/api/v1/endpoints/live_challenge.py` (pass `user.plan`), `fastapi_app/services/live_challenge.py` (accept and use it).

### R2: Store participation in Redis hash (replaces Frappe HTTP call #2)

**Current:** Calls `frappe.client.insert` to create a `Memora Live Challenge Participation` doc immediately during join.
**New:** Write participation to a Redis hash; reconcile to Frappe later.

- Define a new key builder in `redis_keys.py`:
  ```python
  def lc_participants_key(event_id: str) -> str:
      """Hash: player_id → joined_at_iso. All players who joined this event."""
      return f"memora:lc:{event_id}:participants"
  ```
- In `join()`, after the Lua script succeeds:
  ```python
  await self.redis.hset(lc_participants_key(event_id), player_id, joined_at)
  await self.redis.expire(lc_participants_key(event_id), LC_KEY_TTL)
  ```
- Remove the `frappe.client.insert` call and its rollback block entirely.
- **Rollback on HSET failure:** If HSET fails (unlikely), decrement count and SREM from joined set (same rollback pattern as today, just without the Frappe call).

### R3: Drop synchronous participant_count sync (replaces Frappe HTTP call #3)

**Current:** Calls `frappe.client.set_value` to update `participant_count` on the event doc after every join.
**New:** Remove entirely from the join path. The Redis counter `lc:{id}:count` is the live source of truth during the event. The final count is synced to Frappe during reconciliation (R4).

- Remove the `frappe.client.set_value` call and its try/except block from `join()`.

### R4: Background reconciliation on event end

When an event transitions to `ended`, run a reconciliation step that persists all Redis-only data to Frappe and then cleans up.

**Trigger point:** In `_countdown_loop()`, right after `drain_queue()` and before/after `_broadcast_event_ended()` — the exact spot where the event transitions to ended (around line 1102-1104).

**Reconciliation steps (in order):**

1. **Read participants:** `HGETALL lc:{event_id}:participants` → dict of `{player_id: joined_at}`.
2. **Bulk-insert Participation docs:** For each participant, call `frappe.client.insert` to create `Memora Live Challenge Participation` with `event`, `player`, `joined_at`. Use batched calls if Frappe supports it, otherwise sequential. This runs in background — latency doesn't matter.
3. **Sync final participant_count:** Read `lc:{event_id}:count`, call `frappe.client.set_value` once to update the event doc.
4. **Cleanup Redis keys:** Delete ALL ephemeral keys for this event in a single pipeline:
   ```python
   pipe = self.redis.pipeline()
   pipe.delete(lc_status_key(event_id))
   pipe.delete(lc_meta_key(event_id))
   pipe.delete(lc_questions_key(event_id))
   pipe.delete(lc_count_key(event_id))
   pipe.delete(lc_joined_key(event_id))
   pipe.delete(lc_submitted_key(event_id))
   pipe.delete(lc_participants_key(event_id))
   pipe.delete(lc_transition_lock_key(event_id))
   await pipe.execute()
   ```
5. **Log:** `lc_reconciliation_complete` with `event_id`, `participant_count`, `keys_deleted`.

**Error handling:**
- Wrap reconciliation in try/except. If Frappe calls fail, log `lc_reconciliation_failed` with error details but do NOT skip the key cleanup — the TTL is the safety net, but explicit cleanup is preferred.
- If reconciliation partially fails (e.g., some inserts succeed, some don't), log which player_ids failed. Do NOT retry in-line — the data is still in Redis (24h TTL) for manual recovery.

**Important:** Extract reconciliation into its own method `async def _reconcile_event(self, event_id: str)` so it can be called from `_countdown_loop` and also invoked manually if needed.

### R5: Redis key cleanup policy

**Current:** All `lc:*` keys have a 24h TTL (`LC_KEY_TTL = 86400`) and just expire on their own.
**New:** Explicit deletion on event end (R4) + TTL as safety net.

- Keep `LC_KEY_TTL = 86400` on all keys as a fallback (no change to existing SET/EXPIRE calls).
- The new `lc_participants_key` must also have `LC_KEY_TTL`.
- After reconciliation completes, explicitly DEL all keys (R4 step 4). This frees memory immediately instead of waiting up to 24h.
- The TTL remains as a safety net for cases where reconciliation doesn't run (crash, no WS clients connected, etc.).

---

## Files to modify

| File | Changes |
|------|---------|
| `fastapi_app/core/redis_keys.py` | Add `lc_participants_key()` builder |
| `fastapi_app/services/live_challenge.py` | Rewrite `join()` per R1-R3; add `_reconcile_event()` per R4; call reconciliation from `_countdown_loop` on event end; cleanup keys |
| `fastapi_app/api/v1/endpoints/live_challenge.py` | Pass `user.plan` to `service.join()` |
| `fastapi_app/tests/test_live_challenge_integration.py` | Update join tests for new signature and Redis-only behavior |
| `fastapi_app/tests/test_live_challenge_ws.py` | Update if WS tests assert on Frappe calls during join |

## Files NOT to modify

- `fastapi_app/models/auth.py` — `TokenPayload.plan` already exists.
- `fastapi_app/models/live_challenge.py` — Response models unchanged.
- Lua script `_ATOMIC_JOIN_LUA` — keep as-is, it already does the atomic join correctly.

---

## Acceptance criteria

1. `POST /join` makes **zero** Frappe HTTP calls.
2. `POST /join` makes at most **4 Redis calls** (Lua script + HSET participants + EXPIRE + status/meta reads if not folded into Lua).
3. Plan eligibility is checked using `user.plan` from JWT.
4. Participation records are persisted to Frappe when the event ends.
5. `participant_count` is synced to Frappe when the event ends.
6. All `lc:{event_id}:*` keys are explicitly DELeted after reconciliation.
7. If reconciliation fails, keys still expire via 24h TTL safety net.
8. Existing tests pass (updated for new behavior).
9. No data loss: every player who joined (per Redis joined set) gets a Participation doc in Frappe.
