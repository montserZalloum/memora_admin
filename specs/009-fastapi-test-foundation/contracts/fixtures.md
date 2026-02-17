# Fixture Contracts: conftest.py

**Date**: 2026-02-17

These are the public fixture contracts that all subsequent test phases (2-7) depend on. Changes to these signatures require updating all downstream test files.

---

## Settings Override (Module-Level, Not a Fixture)

```python
# Executed at conftest.py import time, before any test collection
_test_settings = Settings(
    redis_url="redis://127.0.0.1:13000",
    jwt_secret="test-secret-key-for-unit-tests",
    jwt_algorithm="HS256",
    bitmap_json_path="/tmp/test-bitmaps",
    frappe_url="http://localhost:8000",
    frappe_site="test.local",
    frappe_api_key="test-key",
    frappe_api_secret="test-secret",
    voucher_hmac_secret="test-hmac-secret",
)
```

**Contract**: After conftest.py loads, `get_settings()` anywhere in `fastapi_app.*` returns `_test_settings`.

---

## Fixture: `test_prefix`

**Scope**: function
**Returns**: `str` — format `"test:{8-char-hex}:"`
**Purpose**: Unique key namespace per test for Redis isolation.

```python
@pytest.fixture
def test_prefix() -> str
```

---

## Fixture: `redis_client`

**Scope**: function
**Returns**: `redis.asyncio.Redis` — connected to `redis://127.0.0.1:13000`, `decode_responses=True`
**Teardown**: `await client.aclose()`

```python
@pytest.fixture
async def redis_client() -> AsyncGenerator[redis.asyncio.Redis, None]
```

---

## Fixture: `cleanup_keys` (autouse)

**Scope**: function
**Autouse**: yes — runs for every test automatically
**Depends on**: `redis_client`, `test_prefix`
**Purpose**: After test completes, SCAN+DELETE all keys matching `{test_prefix}*`

```python
@pytest.fixture(autouse=True)
async def cleanup_keys(redis_client, test_prefix) -> AsyncGenerator[None, None]
```

---

## Fixture: `mock_frappe`

**Scope**: function
**Returns**: `AsyncMock` with spec-like interface:
- `.call` → `AsyncMock(return_value=None)`
- `.get_grant_keys` → `AsyncMock(return_value=[])`
- `.create_subscription` → `AsyncMock(return_value={})`
- `.close` → `AsyncMock()`

```python
@pytest.fixture
def mock_frappe() -> AsyncMock
```

**Usage**: Override `.call.return_value` per-test to simulate Frappe API responses.

---

## Fixture: `make_player_token`

**Scope**: function
**Returns**: Callable factory `(player_id?, plan_id?, display_name?) -> (token_str, family_id)`
**Purpose**: Generate valid JWT access tokens for player authentication in endpoint tests.

```python
@pytest.fixture
def make_player_token() -> Callable[..., tuple[str, str]]
```

**Default values**: `player_id="PLAYER-TEST-001"`, `plan_id="PLAN-TEST-001"`, `display_name="Test Player"`

---

## Fixture: `make_admin_token`

**Scope**: function
**Returns**: Callable factory `(email?) -> (token_str, family_id)`
**Purpose**: Generate valid JWT access tokens with admin role for admin endpoint tests.

```python
@pytest.fixture
def make_admin_token() -> Callable[..., tuple[str, str]]
```

**Default values**: `email="admin@test.local"`, `role="System Manager"`

---

## Fixture: `app_client`

**Scope**: function
**Depends on**: `redis_client`, `mock_frappe`
**Returns**: `httpx.AsyncClient` — ASGI transport bound to FastAPI app with dependency overrides
**Overrides**:
- `get_redis` → returns `redis_client`
- `get_frappe_client` → returns `mock_frappe`
**Teardown**: `await client.aclose()`; clears `app.dependency_overrides`

```python
@pytest.fixture
async def app_client(redis_client, mock_frappe) -> AsyncGenerator[httpx.AsyncClient, None]
```

---

## Fixture: `authed_client`

**Scope**: function
**Depends on**: `app_client`, `redis_client`, `make_player_token`
**Returns**: `tuple[httpx.AsyncClient, str, str, str]` — `(client, token, player_id, family_id)`
**Setup**: Seeds `memora:session:{player_id}` in Redis with `{"fid": family_id}`
**Headers**: Sets `Authorization: Bearer {token}` on the client

```python
@pytest.fixture
async def authed_client(app_client, redis_client, make_player_token) -> tuple
```

---

## Fixture: `admin_client`

**Scope**: function
**Depends on**: `app_client`, `redis_client`, `make_admin_token`
**Returns**: `tuple[httpx.AsyncClient, str, str, str]` — `(client, token, email, family_id)`
**Setup**: Seeds `memora:session:{email}` in Redis with `{"fid": family_id}`
**Headers**: Sets `Authorization: Bearer {token}` on the client

```python
@pytest.fixture
async def admin_client(app_client, redis_client, make_admin_token) -> tuple
```
