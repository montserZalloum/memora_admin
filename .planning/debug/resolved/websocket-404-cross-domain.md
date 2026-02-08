---
status: resolved
trigger: "Player App cannot connect to WebSocket endpoint on FastAPI server. Direct access returns 404."
created: 2026-02-08T00:00:00Z
updated: 2026-02-08T16:45:00Z
---

## Current Focus

hypothesis: CONFIRMED AND FIXED
test: WebSocket handshake through nginx now returns 403 (auth reject) instead of 404
expecting: N/A - resolved
next_action: Archive session

## Symptoms

expected: WebSocket should connect successfully through Vite proxy to wss://x.conanacademy.com/api/v1/notifications/ws
actual: WebSocket connection fails. Direct access to https://x.conanacademy.com/api/v1/notifications/ws returns 404.
errors: Browser console WS connection failed; server returns {"detail": "Not Found"}
reproduction: Access /api/v1/notifications/ws on FastAPI server
started: First time testing after implementation

## Eliminated

- hypothesis: WebSocket endpoint not registered in FastAPI
  evidence: Route exists as APIWebSocketRoute at /api/v1/notifications/ws. Direct curl to 127.0.0.1:8002 with WS headers returns 403 (auth fail, not 404). Router is imported and included in v1 router.
  timestamp: 2026-02-08T16:40:00Z

## Evidence

- timestamp: 2026-02-08T16:40:00Z
  checked: FastAPI route registration (python3 introspection of app.routes)
  found: /api/v1/notifications/ws registered as APIWebSocketRoute type
  implication: Endpoint exists in code and module

- timestamp: 2026-02-08T16:40:30Z
  checked: Direct WebSocket handshake to 127.0.0.1:8002/api/v1/notifications/ws?token=test
  found: Returns HTTP 403 Forbidden (correct - invalid JWT token)
  implication: FastAPI endpoint works correctly, rejects invalid auth

- timestamp: 2026-02-08T16:40:45Z
  checked: WebSocket handshake via nginx at x.conanacademy.com/api/v1/notifications/ws?token=test
  found: Returns HTTP 404 Not Found
  implication: nginx is not properly forwarding WebSocket upgrade to FastAPI

- timestamp: 2026-02-08T16:41:00Z
  checked: nginx config for x.conanacademy.com location /api/v1/
  found: proxy_set_header Connection "" explicitly clears Connection header, no Upgrade header forwarding
  implication: ROOT CAUSE - WebSocket upgrade request stripped by nginx, FastAPI sees regular HTTP GET and returns 404

- timestamp: 2026-02-08T16:43:00Z
  checked: After fix - WebSocket handshake via nginx (HTTP/1.1)
  found: Returns HTTP 403 Forbidden (same as direct FastAPI - upgrade reaches backend)
  implication: Fix works - nginx now properly forwards WebSocket upgrade

- timestamp: 2026-02-08T16:44:00Z
  checked: After fix - Python websockets library connection test
  found: "server rejected WebSocket connection: HTTP 403" (proper WS rejection, not 404)
  implication: Full WebSocket protocol handshake reaches FastAPI through nginx

- timestamp: 2026-02-08T16:44:30Z
  checked: Regression - regular HTTP endpoints still work
  found: GET /api/v1/health/live returns {"status":"alive","api_version":"v1"}
  implication: No regression on existing HTTP API

## Resolution

root_cause: nginx config for x.conanacademy.com had `proxy_set_header Connection ""` in the `/api/v1/` location block, which strips the WebSocket Connection:Upgrade header. It also did not forward the Upgrade header. Without these headers, FastAPI received a plain HTTP GET request to a WebSocket-only endpoint and returned 404.

fix: Two changes to /etc/nginx/sites-enabled/aurevia-bench.conf:
1. Added `map $http_upgrade $connection_upgrade` directive at top level to conditionally set Connection header
2. Added dedicated `location /api/v1/notifications/ws` block BEFORE the general `/api/v1/` block with:
   - proxy_set_header Upgrade $http_upgrade
   - proxy_set_header Connection $connection_upgrade
   - Extended timeouts (7d) for long-lived WebSocket connections
   - Same proxy headers as the general /api/v1/ block

verification:
- curl --http1.1 with WS headers to x.conanacademy.com returns 403 (was 404)
- Python websockets library gets proper WS rejection (403, not 404)
- Regular HTTP /api/v1/health/live still returns 200

files_changed:
- /etc/nginx/sites-enabled/aurevia-bench.conf
