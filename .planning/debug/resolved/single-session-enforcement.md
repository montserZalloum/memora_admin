---
status: resolved
trigger: "Single active session enforcement - invalidate all other sessions when player logs in from new device"
created: 2026-02-13T00:00:00Z
updated: 2026-02-13T00:05:00Z
---

## Current Focus

hypothesis: RESOLVED
test: End-to-end test confirmed session enforcement works
expecting: N/A
next_action: Archive session

## Symptoms

expected: Only one active session per player. When player logs in on device B, the session on device A should be immediately invalidated.
actual: Player can have multiple active sessions on multiple devices simultaneously.
errors: No errors - missing enforcement feature
reproduction: Login from one device, then login from another device/browser with same account. Both sessions remain active.
started: Feature needs to be implemented in current session system.

## Eliminated

## Evidence

- timestamp: 2026-02-13T00:00:30Z
  checked: SessionService.create_session() in services/session.py
  found: create_session() generates new family_id and OVERWRITES the Redis key (memora:session:{user_id}), which means old family_id is automatically invalidated. This mechanism is correct.
  implication: The session invalidation on login IS implemented at the Redis level.

- timestamp: 2026-02-13T00:00:40Z
  checked: deps.py:get_current_user() - the dependency that validates access tokens on every API call
  found: get_current_user() ONLY does stateless JWT validation (signature + expiry + type). It does NOT check if the family_id in the token matches the current session in Redis.
  implication: THIS IS THE ROOT CAUSE. Even though create_session() invalidates old sessions, old access tokens remain valid until they expire (60 min default) because nothing checks family_id against Redis on API calls.

- timestamp: 2026-02-13T00:00:50Z
  checked: auth.py:refresh() endpoint
  found: The refresh endpoint DOES validate session (calls session_service.validate_session which checks family_id). So old device gets 401 on refresh, but NOT on regular API calls while access token is still valid.
  implication: There's a gap: access tokens are trusted for their full lifetime without session check. Old device can use API for up to 60 minutes after being "kicked".

- timestamp: 2026-02-13T00:00:55Z
  checked: WebSocket notifications endpoint (notifications.py)
  found: WebSocket validates JWT on connect but never re-validates. No mechanism to force-disconnect old device's WebSocket when new login occurs.
  implication: Need to also close old device WebSocket connections when new session is created.

- timestamp: 2026-02-13T00:01:00Z
  checked: ConnectionManager (ws_manager.py) capabilities
  found: ws_manager has send_to_user() which sends to ALL connections for a user. It can be used to send a "session_invalidated" event before disconnecting.
  implication: Can leverage existing WS infrastructure to force-kick old devices immediately.

- timestamp: 2026-02-13T00:04:00Z
  checked: End-to-end test with real JWT tokens and Redis
  found: Device A token returns HTTP 200 before device B login, then returns HTTP 401 with SESSION_SUPERSEDED code after device B login. Works correctly.
  implication: Fix verified working correctly.

## Resolution

root_cause: Access tokens were validated statelessly (JWT-only, no Redis session check). When player logs in on device B, create_session() overwrites the family_id in Redis, but device A's access token remained valid for up to 60 minutes because get_current_user() never checked Redis. WebSocket connections also persisted.
fix: 1) Added Redis session validation to get_current_user() dependency - checks family_id in token matches current session in Redis on every API call. Returns 401 with SESSION_SUPERSEDED code when session has been superseded. Gracefully degrades if Redis is unavailable. 2) Added _force_kick_old_sessions() helper to auth.py that sends session_invalidated WebSocket event and closes old connections before creating new session. Called in player_login and player_register_verify.
verification: End-to-end test confirmed: Device A token returns 200 before device B login, 401 with SESSION_SUPERSEDED after. Integration tests pass. All routes build correctly. Pre-commit checks pass.
files_changed:
  - fastapi_app/api/deps.py
  - fastapi_app/api/v1/endpoints/auth.py
