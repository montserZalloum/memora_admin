# API Contract: Authentication

## POST /api/v1/auth/player/login

**Used by**: All 4 user profiles (in `on_start()`)

### Request
```http
POST /api/v1/auth/player/login HTTP/1.1
Content-Type: application/json
X-Device-ID: locust-abc123def456

{
  "mobile": "+201000000001",
  "password": "test_password"
}
```

### Response (200 OK)
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "profile": {
    "display_name": "TestPlayer1",
    "avatar": "avatar_1",
    "xp": 5200
  }
}
```

### Response (429 Rate Limited)
```json
{
  "detail": "Too many login attempts",
  "retry_after": 60
}
```
**Headers**: `Retry-After: 60`
**Load test handling**: Mark as success (FR-007)

### Response (401 Invalid Credentials)
```json
{
  "detail": "Invalid credentials"
}
```
**Load test handling**: Mark as failure, set `self.token = None`

---

## POST /api/v1/auth/refresh

**Used by**: Long-running tests (>60 min) — optional refresh mechanism

### Request
```http
POST /api/v1/auth/refresh HTTP/1.1
Content-Type: application/json

{
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

### Response (200 OK)
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

### Response (401 Session Superseded)
```json
{
  "code": "SESSION_SUPERSEDED",
  "message": "Session invalidated by new login"
}
```
**Load test handling**: Mark as success (expected when multiple virtual users share same player account)
