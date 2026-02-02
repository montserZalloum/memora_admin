# Phase 02: Authentication - Research

**Researched:** 2026-02-02
**Domain:** JWT authentication with FastAPI, Frappe credential verification, Redis-based session management
**Confidence:** HIGH

## Summary

This research covers JWT-based authentication for a FastAPI sidecar that verifies credentials against Frappe, issues access/refresh tokens, and enforces single-session per player. The standard approach uses PyJWT for token encoding/decoding (not python-jose which is unmaintained), FastAPI's dependency injection with HTTPBearer for stateless token verification, and Redis for rate limiting and session invalidation.

The authentication pattern is well-established: PyJWT 2.11+ with HS256 algorithm, short-lived access tokens (15 min) and long-lived refresh tokens (30 days), Redis-backed rate limiting with atomic Lua scripts for dual-key limiting (IP + account), and a session invalidation mechanism using token family IDs stored in Redis. Frappe credential verification uses the internal `LoginManager.authenticate()` API via REST call to the Frappe server.

**Primary recommendation:** Use PyJWT (not python-jose) for JWT operations, implement HTTPBearer-based dependency for stateless token verification, use fastapi-limiter or custom Lua scripts for atomic dual-key rate limiting, and implement session invalidation via token family ID stored in Redis (new login generates new family ID, old tokens rejected on next use).

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyJWT | 2.11+ | JWT encode/decode | Actively maintained, FastAPI's official recommendation (replaced python-jose), full JWK support |
| fastapi | 0.115+ | Web framework | HTTPBearer, dependency injection, automatic OpenAPI docs |
| pwdlib | 0.2+ | Password hashing | Argon2 algorithm support, FastAPI's official recommendation (replaced passlib) |
| fastapi-limiter | 0.1+ | Rate limiting | Redis-backed, dependency-based, sliding window support |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| redis-py | 5.0+ | Session/rate limit storage | Rate limiting counters, token family ID storage |
| httpx | 0.27+ | Async HTTP client | Frappe API calls for credential verification |
| python-multipart | 0.0.9+ | Form data parsing | Required for OAuth2PasswordRequestForm (if using form login) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| PyJWT | python-jose | python-jose is unmaintained (3+ years), security risk |
| PyJWT | joserfc | joserfc is newer with JWE support, but PyJWT is more established |
| fastapi-limiter | slowapi | slowapi more features but fastapi-limiter simpler for Redis |
| httpx | requests | httpx is async-native, requests would block event loop |

**Installation:**
```bash
pip install pyjwt[crypto] pwdlib[argon2] fastapi-limiter httpx python-multipart
```

## Architecture Patterns

### Recommended Project Structure
```
fastapi_app/
├── main.py                      # FastAPI app with lifespan
├── core/
│   ├── __init__.py
│   ├── config.py                # Settings (existing)
│   ├── security.py              # JWT creation, password hashing
│   └── redis.py                 # Redis pool (existing)
├── api/
│   ├── __init__.py
│   ├── deps.py                  # Auth dependencies (get_current_user)
│   └── v1/
│       ├── router.py            # (existing)
│       └── endpoints/
│           ├── health.py        # (existing)
│           └── auth.py          # Login, refresh, logout endpoints
├── models/
│   ├── __init__.py
│   ├── auth.py                  # TokenResponse, LoginRequest, User models
│   └── player.py                # Player model
├── services/
│   ├── __init__.py
│   ├── frappe.py                # Frappe API client
│   └── session.py               # Session management (family ID)
└── middleware/
    ├── __init__.py
    ├── request_id.py            # (existing)
    └── rate_limit.py            # Rate limiting middleware
```

### Pattern 1: JWT Token Creation with Rich Payload
**What:** Create JWT tokens with user claims (sub, email, role, timezone, display_name)
**When to use:** Login endpoint after successful Frappe credential verification

```python
# Source: PyJWT docs + FastAPI JWT tutorial
from datetime import datetime, timezone, timedelta
import jwt
import uuid

def create_access_token(
    user_id: str,
    email: str,
    role: str,
    timezone_str: str,
    display_name: str,
    family_id: str,
    expires_delta: timedelta = timedelta(minutes=15),
) -> str:
    """Create access token with rich payload per CONTEXT.md decisions."""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,           # Subject (user ID)
        "email": email,
        "role": role,
        "tz": timezone_str,
        "name": display_name,
        "fid": family_id,         # Token family ID for session invalidation
        "type": "access",
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()), # Unique token ID
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

def create_refresh_token(
    user_id: str,
    family_id: str,
    expires_delta: timedelta = timedelta(days=30),
) -> str:
    """Create refresh token (minimal payload, longer lifetime)."""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "fid": family_id,
        "type": "refresh",
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
```

### Pattern 2: HTTPBearer Dependency for Stateless Verification
**What:** Use FastAPI's HTTPBearer with custom decoder for stateless JWT verification
**When to use:** All protected endpoints

```python
# Source: FastAPI security docs + PyJWT usage
from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from pydantic import BaseModel

security = HTTPBearer()

class TokenPayload(BaseModel):
    """Decoded JWT payload."""
    sub: str           # User ID
    email: str
    role: str
    tz: str
    name: str
    fid: str           # Family ID
    type: str          # "access" or "refresh"
    exp: int
    jti: str

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> TokenPayload:
    """
    Stateless JWT verification - no database lookup per CONTEXT.md.
    Only checks:
    1. Token signature is valid
    2. Token is not expired
    3. Token type is "access"
    """
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "type", "fid"]},
        )

        if payload.get("type") != "access":
            raise credentials_exception

        return TokenPayload(**payload)

    except jwt.ExpiredSignatureError:
        raise credentials_exception
    except jwt.InvalidTokenError:
        raise credentials_exception
```

### Pattern 3: Session Invalidation via Token Family ID
**What:** Store current valid family_id per user in Redis; reject tokens with old family_id
**When to use:** Single-session enforcement (new login invalidates old session)

```python
# Source: JWT session invalidation patterns + CONTEXT.md decisions
import uuid
from typing import Optional

class SessionService:
    """Manages single-session per player via token family ID."""

    def __init__(self, redis_client, key_prefix: str = "memora:session:"):
        self.redis = redis_client
        self.prefix = key_prefix

    async def create_session(self, user_id: str) -> str:
        """
        Create new session, invalidating any previous session.
        Returns new family_id to embed in tokens.
        """
        family_id = str(uuid.uuid4())
        key = f"{self.prefix}{user_id}"

        # Store new family_id (old one automatically invalidated)
        # TTL matches refresh token lifetime (30 days)
        await self.redis.set(key, family_id, ex=30 * 24 * 3600)
        return family_id

    async def validate_session(self, user_id: str, family_id: str) -> bool:
        """
        Check if family_id matches current session.
        Returns False if session was invalidated by new login.
        """
        key = f"{self.prefix}{user_id}"
        current_fid = await self.redis.get(key)
        return current_fid == family_id

    async def invalidate_session(self, user_id: str) -> None:
        """Explicitly invalidate session (logout)."""
        key = f"{self.prefix}{user_id}"
        await self.redis.delete(key)
```

### Pattern 4: Dual-Key Rate Limiting (IP + Account)
**What:** Rate limit both by IP address AND target account to prevent distributed attacks
**When to use:** Login endpoint per CONTEXT.md (10/min per IP, 5/min per account)

```python
# Source: Redis rate limiting patterns + CONTEXT.md decisions
from fastapi import Request, HTTPException, status
from datetime import datetime

# Lua script for atomic increment with TTL
RATE_LIMIT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
"""

class RateLimiter:
    """Dual-key rate limiter for login attempts."""

    def __init__(self, redis_client, key_prefix: str = "memora:ratelimit:"):
        self.redis = redis_client
        self.prefix = key_prefix
        self._script = None

    async def _get_script(self):
        """Register Lua script (cached)."""
        if self._script is None:
            self._script = self.redis.register_script(RATE_LIMIT_SCRIPT)
        return self._script

    async def check_rate_limit(
        self,
        ip_address: str,
        target_account: str | None,
        ip_limit: int = 10,
        account_limit: int = 5,
        window_seconds: int = 60,
    ) -> tuple[bool, int, str]:
        """
        Check dual rate limits.
        Returns: (allowed, retry_after_seconds, limit_type)
        """
        script = await self._get_script()

        # Check IP limit
        ip_key = f"{self.prefix}ip:{ip_address}"
        ip_count = await script(keys=[ip_key], args=[window_seconds])

        if ip_count > ip_limit:
            ttl = await self.redis.ttl(ip_key)
            return False, max(ttl, 1), "ip"

        # Check account limit (if account provided)
        if target_account:
            account_key = f"{self.prefix}account:{target_account.lower()}"
            account_count = await script(keys=[account_key], args=[window_seconds])

            if account_count > account_limit:
                ttl = await self.redis.ttl(account_key)
                return False, max(ttl, 1), "account"

        return True, 0, ""
```

### Pattern 5: Frappe Credential Verification via REST API
**What:** Verify credentials by calling Frappe's login endpoint
**When to use:** Login flow - verify user credentials before issuing JWT

```python
# Source: Frappe REST API docs + httpx async patterns
import httpx
from typing import Optional
from pydantic import BaseModel

class FrappeUser(BaseModel):
    """User data from Frappe after successful auth."""
    user_id: str
    email: str
    full_name: str
    user_type: str
    time_zone: str | None = None

class FrappeAuthService:
    """Authenticate against Frappe server."""

    def __init__(self, frappe_url: str, timeout: float = 10.0):
        self.frappe_url = frappe_url.rstrip("/")
        self.timeout = timeout

    async def verify_credentials(
        self, email: str, password: str
    ) -> Optional[FrappeUser]:
        """
        Verify credentials via Frappe login API.
        Returns user data on success, None on failure.

        NOTE: This respects Frappe's auth logic, hooks, and validations
        per CONTEXT.md decision.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                # Step 1: Login to get session
                login_response = await client.post(
                    f"{self.frappe_url}/api/method/login",
                    json={"usr": email, "pwd": password},
                )

                if login_response.status_code != 200:
                    return None

                # Step 2: Get user details
                user_response = await client.get(
                    f"{self.frappe_url}/api/method/frappe.auth.get_logged_user",
                    cookies=login_response.cookies,
                )

                if user_response.status_code != 200:
                    return None

                user_data = user_response.json().get("message", email)

                # Step 3: Get full user profile
                profile_response = await client.get(
                    f"{self.frappe_url}/api/resource/User/{user_data}",
                    cookies=login_response.cookies,
                )

                if profile_response.status_code == 200:
                    profile = profile_response.json().get("data", {})
                    return FrappeUser(
                        user_id=profile.get("name", email),
                        email=profile.get("email", email),
                        full_name=profile.get("full_name", ""),
                        user_type=profile.get("user_type", "System User"),
                        time_zone=profile.get("time_zone"),
                    )

                # Fallback: minimal user data
                return FrappeUser(
                    user_id=email,
                    email=email,
                    full_name=email,
                    user_type="System User",
                )

            except httpx.RequestError:
                return None

            finally:
                # Step 4: Logout from Frappe (clean up session)
                try:
                    await client.get(
                        f"{self.frappe_url}/api/method/logout",
                        cookies=login_response.cookies,
                    )
                except Exception:
                    pass  # Best effort cleanup
```

### Anti-Patterns to Avoid
- **Using python-jose:** Unmaintained for 3+ years, security risk. Use PyJWT instead.
- **Database lookup on every request:** JWT verification should be stateless; only check Redis for session invalidation
- **Non-atomic rate limiting:** INCR + EXPIRE as separate commands has race conditions; use Lua scripts
- **Revealing email existence:** Error messages should be generic ("Invalid credentials") regardless of whether email exists
- **Storing passwords:** Never store passwords; delegate to Frappe's authentication
- **Logging failed credentials:** Per CONTEXT.md, don't log failed login attempts (security/privacy)

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JWT encoding/decoding | Custom base64 + HMAC | PyJWT | Edge cases (padding, timing attacks, claim validation) |
| Rate limiting | Simple Redis counters | fastapi-limiter or Lua scripts | Race conditions, atomic operations, TTL management |
| Password hashing | MD5/SHA256 | pwdlib[argon2] | Timing attacks, salt handling, algorithm upgrades |
| HTTP Bearer extraction | Manual header parsing | FastAPI HTTPBearer | Consistent error responses, OpenAPI integration |
| Token expiration | Manual datetime comparison | PyJWT exp claim | Timezone handling, leeway support |

**Key insight:** Authentication code is security-critical. Any hand-rolled solution will miss edge cases discovered through years of security research and CVEs. Use battle-tested libraries and follow their documented patterns exactly.

## Common Pitfalls

### Pitfall 1: Token Not Invalidated on New Login
**What goes wrong:** User logs in on new device, but old device still works
**Why it happens:** JWT is stateless; old token remains valid until expiry
**How to avoid:** Use family_id in Redis; new login generates new family_id; validate on refresh endpoint
**Warning signs:** Users report being logged in on multiple devices simultaneously

### Pitfall 2: Race Condition in Rate Limiting
**What goes wrong:** Rate limit bypassed under high concurrency
**Why it happens:** INCR and EXPIRE as separate Redis commands; another request slips between them
**How to avoid:** Use Lua script for atomic INCR + conditional EXPIRE
**Warning signs:** Brute force attacks succeed despite rate limiting

### Pitfall 3: Information Leakage in Error Messages
**What goes wrong:** Attacker can enumerate valid email addresses
**Why it happens:** Different error messages for "user not found" vs "wrong password"
**How to avoid:** Always return "Invalid credentials" regardless of failure reason
**Warning signs:** Security audit finds email enumeration vulnerability

### Pitfall 4: Blocking Frappe API Calls
**What goes wrong:** Slow Frappe response blocks entire FastAPI event loop
**Why it happens:** Using sync `requests` library in async endpoint
**How to avoid:** Use `httpx.AsyncClient` for all HTTP calls
**Warning signs:** High latency on login endpoint under load

### Pitfall 5: Refresh Token Grants New Session
**What goes wrong:** Refresh creates new family_id, breaking single-session intent
**Why it happens:** Treating refresh like new login
**How to avoid:** Refresh endpoint validates existing family_id, issues new access token with SAME family_id
**Warning signs:** Refreshing token logs out other device

### Pitfall 6: Missing Algorithm Verification
**What goes wrong:** "alg: none" attack bypasses signature verification
**Why it happens:** Not specifying allowed algorithms in jwt.decode()
**How to avoid:** Always pass `algorithms=[settings.jwt_algorithm]` to jwt.decode()
**Warning signs:** Security audit finds algorithm confusion vulnerability

## Code Examples

Verified patterns from official sources:

### Login Endpoint (Complete Example)
```python
# Source: FastAPI JWT tutorial + CONTEXT.md decisions
from fastapi import APIRouter, HTTPException, status, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from datetime import timedelta

router = APIRouter(prefix="/auth", tags=["auth"])

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    credentials: LoginRequest,
    redis: RedisClient,
    settings: SettingsDep,
):
    """
    Login with Frappe credentials, receive JWT tokens.
    Rate limited: 10 attempts/min per IP, 5 attempts/min per account.
    """
    # Get client IP (respect X-Forwarded-For from nginx)
    client_ip = request.headers.get("X-Forwarded-For", request.client.host)
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()

    # Check rate limits
    rate_limiter = RateLimiter(redis)
    allowed, retry_after, limit_type = await rate_limiter.check_rate_limit(
        ip_address=client_ip,
        target_account=credentials.email,
    )

    if not allowed:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "Too many login attempts",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    # Verify credentials with Frappe
    frappe_service = FrappeAuthService(settings.frappe_url)
    user = await frappe_service.verify_credentials(
        credentials.email, credentials.password
    )

    if not user:
        # Generic error - don't reveal if email exists
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Create new session (invalidates any existing session)
    session_service = SessionService(redis)
    family_id = await session_service.create_session(user.user_id)

    # Determine role (map Frappe user_type to game role)
    role = "player"  # Default role
    if user.user_type == "System User":
        role = "admin"

    # Create tokens
    access_token = create_access_token(
        user_id=user.user_id,
        email=user.email,
        role=role,
        timezone_str=user.time_zone or "UTC",
        display_name=user.full_name,
        family_id=family_id,
        expires_delta=timedelta(minutes=15),
    )

    refresh_token = create_refresh_token(
        user_id=user.user_id,
        family_id=family_id,
        expires_delta=timedelta(days=30),
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )
```

### Refresh Endpoint
```python
# Source: JWT refresh patterns + CONTEXT.md decisions
class RefreshRequest(BaseModel):
    refresh_token: str

@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: RefreshRequest,
    redis: RedisClient,
    settings: SettingsDep,
):
    """
    Exchange refresh token for new access token.
    Does NOT rotate refresh token (per CONTEXT.md: reusable refresh tokens).
    Validates session is still active (not invalidated by new login).
    """
    try:
        payload = jwt.decode(
            request.refresh_token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "type", "fid"]},
        )

        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

        user_id = payload["sub"]
        family_id = payload["fid"]

        # Validate session is still active
        session_service = SessionService(redis)
        if not await session_service.validate_session(user_id, family_id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",  # Session invalidated
            )

        # Get user data from refresh token (or fetch from Frappe if needed)
        # For now, we need to fetch fresh data to get all claims
        # This is acceptable since refresh is infrequent

        # Issue new access token with SAME family_id
        access_token = create_access_token(
            user_id=user_id,
            email=payload.get("email", ""),  # May need to fetch
            role=payload.get("role", "player"),
            timezone_str=payload.get("tz", "UTC"),
            display_name=payload.get("name", ""),
            family_id=family_id,  # Keep same family_id
            expires_delta=timedelta(minutes=15),
        )

        # Return same refresh token (not rotated per CONTEXT.md)
        return TokenResponse(
            access_token=access_token,
            refresh_token=request.refresh_token,
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
```

### Protected Endpoint Example
```python
# Source: FastAPI dependency injection patterns
from typing import Annotated

CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]

@router.get("/me")
async def get_current_user_info(user: CurrentUser):
    """
    Get current user info from JWT claims.
    Stateless - no database lookup.
    """
    return {
        "user_id": user.sub,
        "email": user.email,
        "role": user.role,
        "timezone": user.tz,
        "display_name": user.name,
    }
```

### Settings Configuration (Additions)
```python
# Add to existing core/config.py
class Settings(BaseSettings):
    # ... existing settings ...

    # JWT Configuration (expand existing)
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 30

    # Frappe Integration
    frappe_url: str = "http://localhost:8000"

    # Rate Limiting
    login_rate_limit_ip: int = 10      # Per minute
    login_rate_limit_account: int = 5   # Per minute
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| python-jose | PyJWT | FastAPI docs updated 2024 | python-jose unmaintained, security risk |
| passlib[bcrypt] | pwdlib[argon2] | FastAPI docs updated 2024 | Argon2 is modern, recommended algorithm |
| OAuth2PasswordBearer | HTTPBearer | Preference | HTTPBearer simpler when not using OAuth2 form login |
| Token blacklist | Token family ID | Pattern evolution | Less storage, simpler invalidation |
| Separate rate limit checks | Atomic Lua script | Best practice | Prevents race conditions |

**Deprecated/outdated:**
- `python-jose`: Last release 2021, multiple unpatched security issues. Use PyJWT.
- `passlib`: FastAPI now recommends pwdlib with Argon2 (or bcrypt via pwdlib).
- `@app.on_event("startup")`: Use lifespan context manager for rate limiter initialization.

## Open Questions

Things that couldn't be fully resolved:

1. **Refresh Token Claims**
   - What we know: Refresh token needs sub and fid for session validation
   - What's unclear: Should refresh token include full user profile, or fetch on refresh?
   - Recommendation: Minimal refresh token (sub, fid, type, exp); fetch profile from Frappe on refresh if needed

2. **Frappe User Type to Role Mapping**
   - What we know: Frappe has user_type (System User, Website User, etc.)
   - What's unclear: Exact mapping to game roles (player, admin, etc.)
   - Recommendation: Start with simple mapping (System User = admin, others = player); extend as needed

3. **Logout Endpoint Necessity**
   - What we know: CONTEXT.md lists this as Claude's discretion
   - What's unclear: Whether explicit logout adds value given single-session design
   - Recommendation: Implement optional `/auth/logout` that invalidates session in Redis; not strictly required since new login auto-invalidates

4. **Rate Limit Storage Cleanup**
   - What we know: Rate limit keys use TTL for auto-expiration
   - What's unclear: Whether additional cleanup needed for Redis memory management
   - Recommendation: TTL-based cleanup is sufficient; Redis handles expiration automatically

## Sources

### Primary (HIGH confidence)
- [PyJWT 2.11 Usage Examples](https://pyjwt.readthedocs.io/en/latest/usage.html) - JWT encode/decode, claims, expiration
- [FastAPI OAuth2 with JWT Tutorial](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/) - Complete JWT auth pattern
- [FastAPI Security Tools Reference](https://fastapi.tiangolo.com/reference/security/) - HTTPBearer, OAuth2PasswordBearer
- [FastAPI Get Current User](https://fastapi.tiangolo.com/tutorial/security/get-current-user/) - Dependency injection for auth

### Secondary (MEDIUM confidence)
- [Frappe REST API Authentication](https://docs.frappe.io/framework/user/en/guides/integration/rest_api/simple_authentication) - Login endpoint pattern
- [Frappe auth.py source](https://github.com/frappe/frappe/blob/develop/frappe/auth.py) - LoginManager internals
- [fastapi-limiter PyPI](https://pypi.org/project/fastapi-limiter/) - Rate limiting with Redis
- [Redis INCR Pattern for Rate Limiting](https://redis.io/commands/incr) - Atomic counter with Lua

### Tertiary (LOW confidence)
- [JWT Session Invalidation Patterns](https://medium.com/@mmichaelb/5-different-approaches-to-invalidate-json-web-tokens-e4cc4e027343) - Token family ID pattern
- [FastAPI JWT Authentication (Medium)](https://medium.com/@ancilartech/bulletproof-jwt-authentication-in-fastapi-a-complete-guide-2c5602a38b4f) - Community patterns
- [FastAPI GitHub Discussion #9587](https://github.com/fastapi/fastapi/discussions/9587) - python-jose deprecation discussion

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH - PyJWT and FastAPI patterns verified via official docs
- Architecture: HIGH - Dependency injection and token patterns from FastAPI tutorials
- Rate Limiting: HIGH - Lua script pattern from Redis official docs
- Session Invalidation: MEDIUM - Token family ID pattern from community sources
- Frappe Integration: MEDIUM - REST API verified, internal auth.py inspected

**Research date:** 2026-02-02
**Valid until:** 2026-03-02 (30 days - stable domain, security-focused stack)
