# Phase 9: Game Sessions - Research

**Researched:** 2026-02-03
**Domain:** Redis session lifecycle management with TTL, atomic operations, and lesson flow tracking
**Confidence:** HIGH

## Summary

Phase 9 implements game sessions for lesson flow tracking. Based on CONTEXT.md decisions, the architecture is intentionally simple: sessions track that a lesson is in progress (not stage-by-stage), auto-expire after 1 hour via Redis TTL, and are force-closed when starting a new lesson (one active session per user). No recovery logic is needed since crashes mean restart.

The codebase already has established patterns for Redis services (DeviceService, SessionService, WalletService, ProgressService), Lua scripts for atomic operations, and interaction logging via Redis buffers. The game session implementation follows these patterns with a new GameSessionService and lesson endpoints (start, end).

**Primary recommendation:** Use a Redis hash per user for session data (`memora:gamesession:{user_id}`) with EXPIRE for 1-hour TTL. Use a Lua script for atomic session start that force-closes any existing session and creates a new one. On lesson end, collect stage analytics and push to the existing interaction buffer.

## Standard Stack

This phase uses the existing codebase stack. No new libraries required.

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py (async) | 5.x | Redis operations with TTL and Lua scripts | Already used in codebase for all services |
| Pydantic | 2.x | Request/response models | Already used for all FastAPI endpoints |
| structlog | Already installed | Structured logging | Already used throughout FastAPI app |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| FastAPI | Already installed | Endpoint definitions | Session endpoints |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Redis hash | Redis string with JSON | Hash allows field-level updates, better for session metadata |
| Separate TTL key | Single hash with EXPIRE | Single key simpler, matches existing patterns |

**Installation:**
```bash
# No new dependencies - all already in requirements.txt
```

## Architecture Patterns

### Recommended Project Structure
```
fastapi_app/
├── models/
│   └── game_session.py       # GameSession, StartSessionRequest, EndSessionRequest, StageResult
├── services/
│   └── game_session.py       # GameSessionService with Lua script
└── api/v1/endpoints/
    └── sessions.py           # POST /start, POST /end
```

### Pattern 1: Single User Session via Hash + EXPIRE
**What:** Store session data in a Redis hash keyed by user_id, set TTL on the hash itself
**When to use:** Single active session per user with auto-expiry
**Example:**
```python
# Key pattern: memora:gamesession:{user_id}
# Fields: session_id, lesson_id, subject_id, started_at, device_id
# TTL: 1 hour (3600 seconds)

# Source: Codebase pattern from DeviceService
async def create_session(self, user_id: str, lesson_id: str, subject_id: str, device_id: str) -> str:
    """Create game session, force-closing any existing one."""
    script = await self._get_start_script()
    session_id = str(uuid.uuid4())

    result = await script(
        keys=[self._session_key(user_id)],
        args=[session_id, lesson_id, subject_id, device_id, iso_timestamp, TTL_SECONDS],
    )
    return session_id
```

### Pattern 2: Lua Script for Atomic Session Start
**What:** Use Lua script to atomically check/close existing session and create new one
**When to use:** Prevent race conditions when same user starts multiple sessions simultaneously
**Example:**
```lua
-- Source: Codebase pattern from device.py REGISTER_DEVICE_SCRIPT
-- KEYS[1] = memora:gamesession:{user_id}
-- ARGV[1..6] = session_id, lesson_id, subject_id, device_id, timestamp, ttl

-- Delete any existing session (force-close, no notification per CONTEXT.md)
redis.call('DEL', KEYS[1])

-- Create new session hash with all fields
redis.call('HSET', KEYS[1],
    'session_id', ARGV[1],
    'lesson_id', ARGV[2],
    'subject_id', ARGV[3],
    'device_id', ARGV[4],
    'started_at', ARGV[5])

-- Set TTL (1 hour)
redis.call('EXPIRE', KEYS[1], ARGV[6])

return {1, ARGV[1]}
```

### Pattern 3: Session Validation via HGET
**What:** Check session exists and matches expected lesson before allowing operations
**When to use:** Endpoint that requires active session (though per CONTEXT.md, stage completion happens client-side)
**Example:**
```python
# Source: Codebase pattern from SessionService.validate_session
async def get_active_session(self, user_id: str) -> GameSession | None:
    """Get current active session if exists."""
    key = self._session_key(user_id)
    data = await self.redis.hgetall(key)
    if not data:
        return None
    return GameSession.from_redis_hash(data)
```

### Pattern 4: Lesson End with Analytics Push
**What:** On lesson end, close session and push stage analytics to interaction buffer
**When to use:** Single API call at lesson complete per CONTEXT.md
**Example:**
```python
# Source: Codebase pattern from sync.py INTERACTION_BUFFER_KEY
async def end_session(
    self,
    user_id: str,
    stages: list[StageResult],
    progress_service: ProgressService,
    wallet_service: WalletService,
) -> EndSessionResponse:
    """End session: validate, delete session, log analytics, trigger completion."""
    # 1. Get and validate session exists
    session = await self.get_active_session(user_id)
    if not session:
        raise NoActiveSessionError()

    # 2. Push stage analytics to interaction buffer
    for stage in stages:
        interaction = {
            "player": user_id,
            "lesson": session.lesson_id,
            "stage_id": stage.stage_id,
            "event_type": "Completed",
            "time_spent": stage.time_spent,
            "errors_count": stage.fail_count,
            "timestamp": stage.completed_at,
            "metadata": stage.metadata,
        }
        await self.redis.rpush(INTERACTION_BUFFER_KEY, json.dumps(interaction))

    # 3. Delete session key
    await self.redis.delete(self._session_key(user_id))

    # 4. Trigger existing completion flow (progress, XP, streak)
    # ... delegate to existing progress endpoint logic
```

### Anti-Patterns to Avoid
- **Per-stage API calls:** CONTEXT.md specifies client handles stages offline, single API call at end
- **Session recovery logic:** Crash means restart per CONTEXT.md - simpler is better
- **TTL refresh on activity:** Not needed - session is for one lesson, 1 hour is generous
- **Concurrent session notification:** Old session just closes silently per CONTEXT.md

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic session start | GET + conditional SET | Lua script | Race condition between check and set |
| Session TTL | Manual expiry tracking | Redis EXPIRE | Built-in, reliable, no cleanup needed |
| Interaction logging | Direct DB insert | Existing INTERACTION_BUFFER_KEY | Already has batch flush, schema matches |
| Lesson completion flow | New XP/streak logic | Existing complete_lesson logic | Already handles replay detection, XP calc, streak |

**Key insight:** The codebase already has all building blocks. Session service is a thin orchestration layer that uses existing patterns (Lua scripts, Redis hash, interaction buffer, progress/wallet services).

## Common Pitfalls

### Pitfall 1: Using Separate Keys for Session and TTL
**What goes wrong:** Using one key for session data and another for TTL tracking leads to orphaned data
**Why it happens:** Trying to track "session created" separately from "session expires"
**How to avoid:** Use single hash with EXPIRE - Redis handles cleanup automatically
**Warning signs:** Multiple Redis keys per session, manual cleanup logic

### Pitfall 2: Stage Validation on Server
**What goes wrong:** Building server-side stage sequence validation when client handles ordering
**Why it happens:** Assuming server needs to validate stage order
**How to avoid:** Per CONTEXT.md - "Client handles stage ordering - backend trusts client"
**Warning signs:** Stage sequence tracking in session, validation errors for "wrong order"

### Pitfall 3: Returning to Old /progress/complete Endpoint
**What goes wrong:** Using existing complete endpoint without session context
**Why it happens:** Forgetting sessions are required for stage completion
**How to avoid:** New /sessions/end endpoint that validates session then calls completion logic
**Warning signs:** Completions without active sessions, bypassing session requirement

### Pitfall 4: Complex Session Recovery
**What goes wrong:** Building checkpoint/resume logic when crashes mean restart
**Why it happens:** Over-engineering for edge cases
**How to avoid:** Per CONTEXT.md - "Crash means restart - no recovery logic"
**Warning signs:** Checkpoint fields in session, resume endpoint, client state sync

### Pitfall 5: Race Condition on Concurrent Session Check
**What goes wrong:** User starts two lessons simultaneously, both pass "no existing session" check
**Why it happens:** Non-atomic read-then-write pattern
**How to avoid:** Lua script that atomically deletes existing and creates new
**Warning signs:** User ends up in "wrong" lesson, duplicate sessions briefly exist

## Code Examples

Verified patterns from codebase (Context7 confirmed redis-py API):

### GameSession Pydantic Model
```python
# Source: Follows codebase pattern from models/device.py
from pydantic import BaseModel

class GameSession(BaseModel):
    """Active game session stored in Redis hash."""
    session_id: str
    lesson_id: str
    subject_id: str
    device_id: str | None = None
    started_at: str  # ISO timestamp

    @classmethod
    def from_redis_hash(cls, data: dict) -> "GameSession":
        """Parse Redis hash (handles bytes/str)."""
        def decode(v):
            return v.decode("utf-8") if isinstance(v, bytes) else v
        return cls(
            session_id=decode(data.get(b"session_id") or data.get("session_id", "")),
            lesson_id=decode(data.get(b"lesson_id") or data.get("lesson_id", "")),
            subject_id=decode(data.get(b"subject_id") or data.get("subject_id", "")),
            device_id=decode(data.get(b"device_id") or data.get("device_id")),
            started_at=decode(data.get(b"started_at") or data.get("started_at", "")),
        )


class StageResult(BaseModel):
    """Stage completion data submitted by client at lesson end."""
    stage_id: str
    time_spent: int  # seconds
    fail_count: int = 0
    completed_at: str  # ISO timestamp
    metadata: dict = {}  # Client-provided extra data


class StartSessionRequest(BaseModel):
    """Request to start a lesson session."""
    lesson_id: str
    subject_id: str


class StartSessionResponse(BaseModel):
    """Response from session start."""
    session_id: str
    lesson_id: str


class EndSessionRequest(BaseModel):
    """Request to end lesson session with stage results."""
    stages: list[StageResult]


class EndSessionResponse(BaseModel):
    """Response from session end - matches existing CompleteResponse."""
    success: bool
    xp_awarded: int
    is_replay: bool
    streak: int
```

### GameSessionService Lua Script
```python
# Source: Follows codebase pattern from services/device.py
START_SESSION_SCRIPT = """
local key = KEYS[1]
local session_id = ARGV[1]
local lesson_id = ARGV[2]
local subject_id = ARGV[3]
local device_id = ARGV[4]
local timestamp = ARGV[5]
local ttl = tonumber(ARGV[6])

-- Force-close any existing session (silent per CONTEXT.md)
redis.call('DEL', key)

-- Create new session
redis.call('HSET', key,
    'session_id', session_id,
    'lesson_id', lesson_id,
    'subject_id', subject_id,
    'device_id', device_id,
    'started_at', timestamp)

-- Set 1-hour TTL
redis.call('EXPIRE', key, ttl)

return {1, session_id}
"""
```

### Dependency Injection Pattern
```python
# Source: Follows codebase pattern from api/deps.py
async def get_game_session_service(request: Request) -> GameSessionService:
    """Get GameSessionService with Redis from app state."""
    redis_client = redis.Redis(connection_pool=request.app.state.redis_pool)
    return GameSessionService(redis_client)


GameSessionServiceDep = Annotated[GameSessionService, Depends(get_game_session_service)]
```

### Session Validation Error
```python
# Source: Follows codebase pattern from endpoints
class NoActiveSessionError(Exception):
    """Raised when operation requires active session but none exists."""
    pass

# In endpoint:
if not session:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "NO_ACTIVE_SESSION", "message": "No active lesson session"},
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Per-stage API calls | Single call at lesson end | CONTEXT.md decision | Simpler architecture, less network traffic |
| Session recovery/checkpoints | Crash = restart | CONTEXT.md decision | No checkpoint logic needed |
| Multiple concurrent lessons | One session per user | CONTEXT.md decision | Force-close on new start |

**Deprecated/outdated:**
- None - this is new functionality

## Open Questions

Things that couldn't be fully resolved:

1. **Session timeout behavior on client**
   - What we know: TTL is 1 hour, session auto-expires in Redis
   - What's unclear: Should client receive specific error code when session expired vs never existed?
   - Recommendation: Return same "NO_ACTIVE_SESSION" for both - client can prompt to restart lesson

2. **Device ID in session**
   - What we know: CONTEXT.md says device metadata is optional for Phase 9
   - What's unclear: Whether to track which device started the session
   - Recommendation: Include device_id field (nullable) for future analytics, populate from X-Device-ID header

3. **Subject validation on session start**
   - What we know: Need to verify lesson belongs to subject
   - What's unclear: Use hierarchy service or trust client?
   - Recommendation: Validate via hierarchy service (already used in progress endpoints) - minimal overhead, prevents invalid data

## Sources

### Primary (HIGH confidence)
- `/redis/redis-py` Context7 - Hash operations, TTL/EXPIRE, Lua script registration
- Codebase: `services/device.py` - Lua script pattern, atomic registration
- Codebase: `services/session.py` - Session key pattern, validation
- Codebase: `services/wallet.py` - Lua script with date logic, dirty tracking
- Codebase: `tasks/sync.py` - INTERACTION_BUFFER_KEY pattern, batch insert

### Secondary (MEDIUM confidence)
- [Redis Best Practices - DragonflyDB](https://www.dragonflydb.io/guides/redis-best-practices) - TTL management, atomic operations
- [Redis Hashes and Related Commands](https://www.dragonflydb.io/guides/redis-hashes-examples-and-pro-tips) - Hash with EXPIRE pattern
- [Prevent Multiple Logins with Redis](https://blog.bytescrum.com/how-to-prevent-multiple-logins-in-nestjs-using-redis-cache) - Single session enforcement pattern

### Tertiary (LOW confidence)
- None - all patterns verified against codebase and Context7

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Uses existing codebase libraries only
- Architecture: HIGH - All patterns match existing codebase services
- Pitfalls: HIGH - Derived from CONTEXT.md decisions and codebase analysis

**Research date:** 2026-02-03
**Valid until:** 30 days (stable domain, no external dependencies)
