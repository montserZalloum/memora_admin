# Phase 15: JWT Simplification - Research

**Researched:** 2026-02-05
**Domain:** JWT Token Management, FastAPI Authentication, Frappe Integration
**Confidence:** HIGH

## Summary

This research covers implementing JWT payload simplification, mobile number login, and login response enrichment for the Memora platform. The phase modifies the existing authentication flow to add `plan_id` to tokens, remove unused fields (`timezone`, `role`), enable identifier-based login (email or mobile), and return player profile data in login response.

The existing codebase has solid JWT infrastructure using PyJWT 2.3.0, session management via Redis family_id pattern, and rate limiting with dual-key Lua scripts. The main work involves extending the FrappeAuthService for mobile lookup, modifying token creation functions, and enriching the login response model.

**Primary recommendation:** Extend existing patterns - modify `create_access_token` signature, add mobile lookup to FrappeAuthService, extend TokenResponse with profile data.

## Standard Stack

The project already uses the correct stack. No new dependencies needed.

### Core (Already in Project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyJWT | 2.3.0 | JWT encode/decode | Project standard, well-maintained |
| Pydantic | 2.x | Request/response validation | FastAPI native, EmailStr for email detection |
| redis.asyncio | 5.x | Session storage | Project standard for Redis ops |
| httpx | 0.27+ | Frappe API calls | Async HTTP client for FrappeAuthService |

### Supporting (Already in Project)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic-settings | 2.x | Config management | Environment variables |
| structlog | 24.x | Structured logging | All service logging |

### No New Dependencies
This phase requires no new packages. All functionality can be implemented with existing stack.

**Installation:** No changes needed to requirements.txt

## Architecture Patterns

### Current Auth Architecture (Existing)
```
fastapi_app/
├── api/v1/endpoints/auth.py     # Login/refresh endpoints
├── core/security.py             # JWT creation/decoding
├── models/auth.py               # LoginRequest, TokenResponse, FrappeUser
├── services/
│   ├── frappe.py               # FrappeAuthService (credential verification)
│   ├── session.py              # SessionService (family_id management)
│   └── rate_limit.py           # RateLimiter (dual-key limiting)
```

### Token Payload Changes

**Current Access Token Payload:**
```json
{
  "sub": "user_id",
  "email": "user@example.com",
  "role": "player",           // REMOVE
  "tz": "Asia/Amman",         // REMOVE
  "name": "Display Name",
  "fid": "family_uuid",
  "type": "access",
  "iat": 1234567890,
  "exp": 1234567890,
  "jti": "unique_id"
}
```

**New Access Token Payload:**
```json
{
  "sub": "user_id",
  "email": "user@example.com",
  "plan": "PLAN-00001",       // ADD - Frappe document name
  "name": "Display Name",
  "fid": "family_uuid",
  "type": "access",
  "iat": 1234567890,
  "exp": 1234567890,
  "jti": "unique_id"
}
```

### Pattern 1: Email vs Mobile Detection

**What:** Detect identifier type using Pydantic's EmailStr validation
**When to use:** Login request processing
**Example:**
```python
# Source: Pydantic docs + project pattern
from pydantic import BaseModel, EmailStr, field_validator

class LoginRequest(BaseModel):
    identifier: str  # Email or mobile number
    password: str

def is_email(identifier: str) -> bool:
    """Check if identifier is email format using @ presence.

    Per CONTEXT.md: Simple detection - email has @, mobile doesn't.
    No complex regex needed since exact match required for mobile.
    """
    return "@" in identifier
```

**Rationale:** The simplest reliable detection. All valid emails contain `@`. Mobile numbers don't. Per CONTEXT.md, mobile match is exact (no normalization), so no phone parsing library needed.

### Pattern 2: Token Invalidation on Plan Change

**What:** Invalidate all tokens when admin changes player's plan
**When to use:** Frappe doc_event hook on Memora Player Profile
**Example:**
```python
# Source: Existing profile_sync.py pattern
# In memora_admin/events/plan_change_sync.py

def on_player_profile_plan_changed(doc, method):
    """Invalidate player session when plan changes.

    Per CONTEXT.md: Immediate invalidation, player must re-login.
    Uses existing SessionService.invalidate_session pattern.
    """
    if doc.has_value_changed("plan"):
        cache = frappe.cache()
        # Delete session key to invalidate all tokens
        session_key = f"memora:session:{doc.user}"
        cache.delete_value(session_key)

        # Publish invalidation message for FastAPI
        invalidation_msg = json.dumps({
            "type": "session",
            "player_id": doc.user,
            "reason": "plan_changed",
            "timestamp": time.time(),
        })
        cache.publish("memora:cache:invalidate", invalidation_msg)
```

### Pattern 3: Enriched Login Response

**What:** Return profile data with tokens
**When to use:** Login success response
**Example:**
```python
# Source: Project pattern from ProfileService + WalletService
class LoginProfile(BaseModel):
    display_name: str
    avatar: str
    gender: str  # Requires schema update
    xp: int

class EnrichedTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    profile: LoginProfile
```

### Anti-Patterns to Avoid
- **Regex for email detection:** Overkill when simple `@` check suffices
- **Phone number normalization library:** Not needed per CONTEXT.md (exact match)
- **Storing plan_id in refresh token:** Refresh token should stay minimal
- **Graceful plan transition:** Per CONTEXT.md, immediate invalidation is acceptable

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Email validation | Custom regex | `"@" in identifier` | Simple heuristic sufficient for detection |
| Session invalidation | Custom Redis DEL | `SessionService.invalidate_session()` | Already exists, tested pattern |
| Profile fetch | Direct Frappe query | `ProfileService.get_profiles_batch()` | Handles caching, fallbacks |
| Wallet XP fetch | Direct Redis HGET | `WalletService.get_wallet()` | Handles bytes/str, defaults |

**Key insight:** This phase extends existing services rather than building new ones. The SessionService, ProfileService, and WalletService already have the primitives needed.

## Common Pitfalls

### Pitfall 1: Schema Changes for Missing Fields
**What goes wrong:** CONTEXT.md mentions `gender` in login response, but Memora Player Profile schema doesn't have a `gender` field
**Why it happens:** Requirements outpace schema during planning
**How to avoid:** Add `gender` field to Memora Player Profile DocType first, or remove from response requirements
**Warning signs:** Pydantic validation error on missing field

### Pitfall 2: Mobile Number Field Missing
**What goes wrong:** CONTEXT.md mentions mobile_number lookup, but Memora Player Profile doesn't have `mobile_number` field
**Why it happens:** Frappe User has `mobile_no`, but Player Profile doesn't
**How to avoid:** Either add field to Player Profile, or query Frappe User doctype directly
**Warning signs:** Frappe query returns empty for mobile lookup

### Pitfall 3: Refresh Token Missing Plan Data
**What goes wrong:** Refresh flow creates new access token but doesn't have plan_id
**Why it happens:** Refresh token has minimal payload (no email, role, etc.)
**How to avoid:** Fetch plan_id from Frappe/cache during refresh, or store in Redis session
**Warning signs:** Access token from refresh has null/empty plan_id

### Pitfall 4: Rate Limit Key Change
**What goes wrong:** Rate limiter uses `credentials.email` but now receives `identifier`
**Why it happens:** RateLimiter.check_rate_limit expects account (email)
**How to avoid:** Pass identifier to rate limiter (works for both email and mobile)
**Warning signs:** Rate limiting bypassed for mobile logins

### Pitfall 5: Plan Required but Not Assigned
**What goes wrong:** Login fails for players without plan
**Why it happens:** New player registration might not auto-assign plan
**How to avoid:** Clear error message, admin workflow to assign plans
**Warning signs:** HTTP 401 with unclear error

## Code Examples

Verified patterns from official sources and existing codebase:

### Modified create_access_token
```python
# Source: Existing fastapi_app/core/security.py pattern
def create_access_token(
    user_id: str,
    email: str,
    plan_id: str,  # CHANGED: was role, timezone_str
    display_name: str,
    family_id: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create JWT access token with plan_id."""
    settings = get_settings()

    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)

    now = datetime.now(tz=timezone.utc)
    expire = now + expires_delta

    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "plan": plan_id,  # NEW: plan_id in token
        "name": display_name,
        "fid": family_id,
        "type": "access",
        "iat": now,
        "exp": expire,
        "jti": str(uuid4()),
    }

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
```

### Mobile Lookup in FrappeAuthService
```python
# Source: Existing fastapi_app/services/frappe.py pattern
async def lookup_user_by_mobile(self, mobile: str) -> str | None:
    """Find Frappe User by mobile_no field.

    Returns user email if found, None otherwise.
    Per CONTEXT.md: Exact match required.
    """
    try:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # API key auth for internal lookup
            response = await client.get(
                f"{self.frappe_url}/api/resource/User",
                params={
                    "filters": json.dumps([["mobile_no", "=", mobile]]),
                    "fields": '["email"]',
                    "limit_page_length": 1,
                },
                headers={"Authorization": f"token {api_key}:{api_secret}"},
            )

            if response.status_code == 200:
                data = response.json().get("data", [])
                if data:
                    return data[0].get("email")
            return None
    except httpx.RequestError:
        return None
```

### Fetching Player Profile Plan
```python
# Source: Existing Frappe API pattern
async def get_player_plan(self, user_id: str) -> str | None:
    """Get player's plan from Memora Player Profile.

    Returns plan document name (e.g., "PLAN-00001") or None.
    """
    try:
        response = await client.get(
            f"{self.frappe_url}/api/resource/Memora Player Profile/{user_id}",
            params={"fields": '["plan"]'},
            headers={"Authorization": f"token {api_key}:{api_secret}"},
        )

        if response.status_code == 200:
            data = response.json().get("data", {})
            return data.get("plan")
        return None
    except httpx.RequestError:
        return None
```

### Hardcoded Timezone Pattern
```python
# Source: Existing fastapi_app/services/wallet.py
from zoneinfo import ZoneInfo

# Hardcoded per CONTEXT.md - all players use Asia/Amman
AMMAN_TZ = ZoneInfo("Asia/Amman")

def get_local_now() -> datetime:
    """Get current time in Asia/Amman timezone."""
    return datetime.now(AMMAN_TZ)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Timezone in token | Hardcoded Asia/Amman | This phase | Smaller token, consistent timezone |
| Role in token | Remove (all players) | This phase | Smaller token, simpler auth |
| Email-only login | Identifier (email/mobile) | This phase | Better UX for mobile users |
| Tokens-only response | Tokens + profile | This phase | Fewer API calls on login |

**Deprecated/outdated:**
- Per-user timezone: Removed per CONTEXT.md, hardcode Asia/Amman
- Role field: All FastAPI users are players (admins use Frappe Desk)

## Open Questions

Things that need resolution before planning:

1. **Gender field in Player Profile**
   - What we know: CONTEXT.md specifies gender in login response
   - What's unclear: Field doesn't exist in Memora Player Profile schema
   - Recommendation: Add gender field to DocType or remove from response requirements

2. **Mobile number storage location**
   - What we know: Frappe User has `mobile_no`, Player Profile doesn't
   - What's unclear: Should we add to Player Profile or query User directly?
   - Recommendation: Query Frappe User doctype for mobile lookup (avoids schema change)

3. **Refresh token plan fetch strategy**
   - What we know: Refresh token doesn't contain plan_id
   - What's unclear: Should we cache plan_id in Redis session or fetch from Frappe?
   - Recommendation: Store plan_id in Redis session alongside family_id (single key)

## Sources

### Primary (HIGH confidence)
- Existing codebase: `fastapi_app/core/security.py` - JWT creation patterns
- Existing codebase: `fastapi_app/api/v1/endpoints/auth.py` - Login flow
- Existing codebase: `fastapi_app/services/session.py` - Session invalidation
- Existing codebase: `memora_admin/events/profile_sync.py` - Pub/sub pattern
- [PyJWT Documentation](https://pyjwt.readthedocs.io/en/latest/usage.html) - JWT encoding
- [FastAPI JWT Tutorial](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/) - OAuth2 patterns

### Secondary (MEDIUM confidence)
- [Pydantic String Types](https://docs.pydantic.dev/2.0/usage/types/string_types/) - EmailStr validation
- [Python Email Validation Tutorial](https://mailtrap.io/blog/python-validate-email/) - Email detection patterns
- [FastAPI JWT Revocation](https://github.com/fastapi/fastapi/discussions/3580) - Token invalidation patterns

### Tertiary (LOW confidence)
- WebSearch for Frappe User mobile_no field - needs verification in actual Frappe instance

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - No changes to existing stack
- Architecture: HIGH - Extends existing patterns
- Token payload changes: HIGH - Clear from CONTEXT.md
- Mobile lookup: MEDIUM - Frappe User mobile_no needs verification
- Gender field: LOW - Schema gap needs resolution
- Pitfalls: HIGH - Based on codebase analysis

**Research date:** 2026-02-05
**Valid until:** 30 days (stable domain, minimal external dependencies)
