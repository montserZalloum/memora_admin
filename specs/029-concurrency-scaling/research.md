# Research: 100k Concurrency Scaling Optimizations

**Feature Branch**: `029-concurrency-scaling` | **Date**: 2026-02-27

## Research Summary

All NEEDS CLARIFICATION items resolved through codebase analysis.

---

## R-001: Redis Bitmap Single-Fetch with `decode_responses=True`

**Decision**: Use `GET` command on bitmap key, then re-encode the text-decoded string back to bytes using `latin-1` (ISO 8859-1) encoding for lossless byte round-tripping.

**Rationale**: The Redis pool uses `decode_responses=True` (`core/redis.py:17`), which means `GET` on a bitmap key returns a **string** (text-decoded bytes). Redis uses `latin-1` internally for decode_responses — every byte 0-255 maps to exactly one Unicode codepoint in latin-1, making it a lossless round-trip codec. `result.encode("latin-1")` recovers the original bytes exactly.

**Alternatives considered**:
1. **Separate non-decoding pool**: Would require a second connection pool solely for bitmap reads. Adds connection overhead and complexity. Rejected — not worth it for a single operation.
2. **BITFIELD GET u8 across range**: Multiple u8 reads in pipeline. More complex than GET and still requires byte assembly. Rejected.
3. **Base64 encode in Lua script**: Adds Lua complexity and CPU overhead. Rejected.

**Implementation**:
```python
async def get_completed_bits(self, user_id, subject_id, bit_range, version=1):
    key = self._progress_key(user_id, subject_id, version)
    raw = await self.redis.get(key)
    if not raw:
        return set()
    # Lossless: latin-1 maps every byte 0-255 to one codepoint
    bitmap_bytes = raw.encode("latin-1")
    completed = set()
    for i in range(bit_range):
        byte_idx, bit_offset = divmod(i, 8)
        if byte_idx < len(bitmap_bytes):
            # Redis bitmaps use MSB-first bit ordering
            if bitmap_bytes[byte_idx] & (0x80 >> bit_offset):
                completed.add(i)
    return completed
```

**Validation**: Redis GETBIT uses MSB-first ordering within each byte. Bit 0 = MSB of byte 0, bit 7 = LSB of byte 0, bit 8 = MSB of byte 1, etc. The bitwise check `(byte & (0x80 >> bit_offset))` mirrors this exactly.

**Edge cases**:
- `bit_range=0` → returns empty set (range(0) produces no iterations)
- Empty/missing key (`raw is None`) → returns empty set
- Sparse bitmap (only a few bits set in large key) → correct, iterates only up to `bit_range`

---

## R-002: Redis Connection Pool Sizing for 100k Users

**Decision**: Add `redis_max_connections` setting with default `20` (dev) and recommend `200` for production.

**Rationale**: Per uvicorn worker, connections are consumed by:
- API request handlers (concurrent endpoint processing)
- Rate limiting middleware (1 per request)
- WebSocket connections (pub/sub listeners)
- Background tasks (pub/sub cache invalidation, notification listener)

With 4 uvicorn workers in production and a shared Redis, 200 connections per worker = 800 total. Redis default max clients is 10,000, so 800 is well within limits.

**Alternatives considered**:
1. **Auto-calculate from worker count**: Too complex, depends on deployment configuration. Rejected.
2. **Single large pool shared across workers**: Not possible with uvicorn — each worker is a separate process. Rejected.

---

## R-003: Per-User WebSocket Lock vs Global Lock

**Decision**: Replace global `asyncio.Lock()` with `dict[str, asyncio.Lock]` (per-user locks) with cleanup on last disconnect.

**Rationale**: Current global lock (`ws_manager.py:33`) serializes ALL connect/disconnect/send operations across ALL users. With 100k concurrent users and connection churn, this becomes a bottleneck. Per-user locks allow operations on different users to proceed independently.

**Memory impact**: `asyncio.Lock()` is ~100 bytes. With 100k users, per-user locks add ~10MB — negligible compared to WebSocket connection objects (which are kilobytes each).

**Cleanup**: When `disconnect()` detects `is_last=True` for a user, delete the user's lock from the dict. This prevents unbounded memory growth.

**Thread safety**: `asyncio.Lock` is single-threaded (event loop). No `defaultdict` race because all operations run on the same event loop. However, we need to be careful with lock creation — use `setdefault` pattern or check-and-create within a lightweight creation lock.

**Implementation approach**: Use a thin creation lock (only held for dict lookup + lock creation) and per-user locks for actual operations:
```python
self._user_locks: dict[str, asyncio.Lock] = {}
self._lock_guard = asyncio.Lock()  # Only for lock creation/deletion

async def _get_user_lock(self, user_id: str) -> asyncio.Lock:
    lock = self._user_locks.get(user_id)
    if lock is None:
        async with self._lock_guard:
            lock = self._user_locks.setdefault(user_id, asyncio.Lock())
    return lock
```

---

## R-004: Parallel WebSocket Broadcast

**Decision**: Use `asyncio.gather()` with configurable concurrency via `ws_broadcast_concurrency` setting. Default `0` (sequential) for development, `>0` for production.

**Rationale**: `send_to_plan()` currently iterates users sequentially. With many users per plan, one slow connection delays all subsequent sends. `asyncio.gather()` dispatches all sends concurrently.

**Backpressure**: When `ws_broadcast_concurrency > 0`, use `asyncio.Semaphore(ws_broadcast_concurrency)` to limit concurrent sends. This prevents overwhelming the event loop with thousands of concurrent WebSocket writes.

**Alternatives considered**:
1. **Always parallel**: Risky in development where debugging sequential behavior is easier. Rejected — configurable is better.
2. **Fire-and-forget tasks**: Loses error tracking and completion guarantees. Rejected.

---

## R-005: Parallel Progress Summary via `asyncio.gather()`

**Decision**: Use `asyncio.gather(*tasks, return_exceptions=True)` to parallelize per-subject lookups.

**Rationale**: Current code (`progress.py:208-235`) awaits each subject sequentially. For 8 subjects at ~5-10ms each = 40-80ms. With gather, all 8 run concurrently = ~10ms total (single slowest subject).

**Error handling**: `return_exceptions=True` means individual failures return as exception objects. Filter them out and log, returning results for successful subjects.

---

## R-006: Rate Limiter Fail-Open/Fail-Closed Configuration

**Decision**: Add `rate_limit_fail_open` boolean setting. Default `True` (current behavior — fail-open). Production can set to `False` for fail-closed.

**Rationale**: Current middleware (`rate_limit.py:76-78`) always fails open on Redis errors. For production under attack, this means rate limiting is silently disabled during Redis issues. Fail-closed returns 503 Service Unavailable with Retry-After header.

**Implementation**: Pass `fail_open` to middleware constructor. In the except block, check the flag:
- `fail_open=True`: log warning, pass request through (current behavior)
- `fail_open=False`: return 503 with `Retry-After: 5` header

---

## R-007: Configurable FrappeClient Timeout and Connection Limits

**Decision**: Add `frappe_timeout`, `frappe_max_connections`, and `frappe_max_keepalive` settings.

**Rationale**: Current hardcoded values (`frappe_client.py:38-39`): timeout=30.0, max_connections=100, max_keepalive=20. During cache recovery events (post-Redis flush), many concurrent hydration calls can overwhelm these limits. Making them configurable allows production tuning.

**Defaults** (match current behavior):
- `frappe_timeout: float = 30.0`
- `frappe_max_connections: int = 100`
- `frappe_max_keepalive: int = 20`

---

## R-008: Settings Loading Pattern

**Decision**: Continue using pydantic-settings `BaseSettings` with `@lru_cache`. New fields have development-safe defaults.

**Rationale**: Current pattern (`config.py:69-72`) uses `@lru_cache` for singleton. All new settings MUST have defaults that preserve current development behavior. This means:
- `redis_max_connections = 20` (current hardcoded value)
- `ws_broadcast_concurrency = 0` (sequential, matching current behavior)
- `rate_limit_fail_open = True` (current behavior)
- `frappe_timeout = 30.0` (current hardcoded)
- `frappe_max_connections = 100` (current hardcoded)
- `frappe_max_keepalive = 20` (current hardcoded)

All changes are backward-compatible: zero-config development still works identically.
