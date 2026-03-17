"""Nginx WebSocket proxy setup for Memora FastAPI endpoints.

Patches the bench nginx config to add WebSocket upgrade headers for
FastAPI WebSocket endpoints (notifications, live challenge). Without
these, nginx strips the Upgrade header and FastAPI returns 404.

Called from after_install and after_migrate hooks in setup.py.
Idempotent: skips if the blocks already exist.
"""

from __future__ import annotations

import os
import re
import subprocess

# The upstream block name used for FastAPI in the nginx config
_UPSTREAM_NAME = "memora-fastapi"

# WebSocket upgrade map (must be defined before server blocks)
_WS_MAP_BLOCK = """\
# WebSocket upgrade support for FastAPI
map $http_upgrade $connection_upgrade {
\tdefault upgrade;
\t'' close;
}"""

# Marker used to detect if map block already exists
_WS_MAP_MARKER = "connection_upgrade"

# WebSocket location blocks to inject before the generic /api/v1/ block.
# Order matters: nginx matches the first matching location, so these
# must come before the generic /api/v1/ prefix block.
_WS_LOCATIONS = [
    {
        "name": "notifications",
        "marker": "location /api/v1/notifications/ws",
        "block": """\
\t# Memora FastAPI WebSocket endpoint
\tlocation /api/v1/notifications/ws {
\t\tproxy_http_version 1.1;
\t\tproxy_set_header Host $http_host;
\t\tproxy_set_header X-Real-IP $remote_addr;
\t\tproxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
\t\tproxy_set_header X-Forwarded-Proto $scheme;
\t\tproxy_set_header X-Request-ID $request_id;
\t\tproxy_set_header Upgrade $http_upgrade;
\t\tproxy_set_header Connection $connection_upgrade;

\t\tproxy_connect_timeout 7d;
\t\tproxy_read_timeout 7d;
\t\tproxy_send_timeout 7d;

\t\tproxy_pass http://memora-fastapi;
\t}""",
    },
    {
        "name": "live-challenge",
        "marker": "live-challenge/.+/ws",
        "block": """\
\t# Memora Live Challenge WebSocket endpoint
\tlocation ~ ^/api/v1/live-challenge/.+/ws$ {
\t\tproxy_http_version 1.1;
\t\tproxy_set_header Host $http_host;
\t\tproxy_set_header X-Real-IP $remote_addr;
\t\tproxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
\t\tproxy_set_header X-Forwarded-Proto $scheme;
\t\tproxy_set_header X-Request-ID $request_id;
\t\tproxy_set_header Upgrade $http_upgrade;
\t\tproxy_set_header Connection $connection_upgrade;

\t\tproxy_connect_timeout 7d;
\t\tproxy_read_timeout 7d;
\t\tproxy_send_timeout 7d;

\t\tproxy_pass http://memora-fastapi;
\t}""",
    },
]

# Pattern that matches the generic /api/v1/ location block opening line
_API_V1_PATTERN = re.compile(r"(\t# Memora FastAPI Game API\n\tlocation /api/v1/ \{)")


def get_nginx_config_path() -> str | None:
    """Find the active nginx config for this bench.

    Checks /etc/nginx/sites-enabled/ for a config that references
    the memora-fastapi upstream. Falls back to bench config/nginx.conf.
    """
    sites_enabled = "/etc/nginx/sites-enabled"
    if os.path.isdir(sites_enabled):
        for fname in os.listdir(sites_enabled):
            fpath = os.path.join(sites_enabled, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath) as f:
                    content = f.read(4096)
                if _UPSTREAM_NAME in content:
                    return fpath
            except OSError:
                continue

    # Fallback: bench-level config
    bench_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    bench_conf = os.path.join(bench_root, "config", "nginx.conf")
    if os.path.isfile(bench_conf):
        return bench_conf

    return None


def ensure_nginx_websocket_proxies() -> None:
    """Patch the nginx config to include WebSocket proxy locations.

    Idempotent: each block is only added if its marker string is not
    already present in the config file. Reloads nginx after changes.
    """
    config_path = get_nginx_config_path()
    if not config_path:
        print("[setup_nginx] WARNING: Could not find nginx config with memora-fastapi upstream. "
              "WebSocket proxy locations must be added manually.")
        return

    try:
        with open(config_path) as f:
            content = f.read()
    except OSError as e:
        print(f"[setup_nginx] WARNING: Cannot read {config_path}: {e}")
        return

    original = content
    changes = []

    # 1. Ensure the WebSocket upgrade map block exists
    if _WS_MAP_MARKER not in content:
        # Insert after the last upstream block, before "# server blocks" or first "server {"
        server_match = re.search(r"\n(# server blocks|server\s*\{)", content)
        if server_match:
            insert_pos = server_match.start()
            content = content[:insert_pos] + "\n" + _WS_MAP_BLOCK + "\n\n" + content[insert_pos:]
            changes.append("WebSocket upgrade map")
        else:
            print("[setup_nginx] WARNING: Could not find server block insertion point for map directive")

    # 2. Ensure each WebSocket location block exists
    for loc in _WS_LOCATIONS:
        if loc["marker"] in content:
            continue

        # Insert before the generic /api/v1/ location block
        match = _API_V1_PATTERN.search(content)
        if match:
            content = content[:match.start()] + loc["block"] + "\n\n\t" + content[match.start():]
            changes.append(f"WebSocket location: {loc['name']}")
        else:
            print(f"[setup_nginx] WARNING: Could not find /api/v1/ block to insert {loc['name']} WS location. "
                  "Add it manually before the generic /api/v1/ location.")

    if content == original:
        print("[setup_nginx] Nginx WebSocket proxy locations already configured")
        return

    # Write the patched config
    try:
        with open(config_path, "w") as f:
            f.write(content)
        print(f"[setup_nginx] Patched {config_path}: {', '.join(changes)}")
    except OSError as e:
        print(f"[setup_nginx] WARNING: Cannot write {config_path}: {e}. "
              "Run with sudo or add the WebSocket locations manually.")
        return

    # Test and reload nginx
    _reload_nginx()


def _reload_nginx() -> None:
    """Test nginx config and reload if valid.

    Requires passwordless sudo for nginx, otherwise prints a manual reload hint.
    """
    try:
        # Pre-check: verify sudo is available without a password prompt
        sudo_check = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True, text=True, timeout=5,
        )
        if sudo_check.returncode != 0:
            print("[setup_nginx] WARNING: passwordless sudo not available. "
                  "Run 'sudo nginx -t && sudo nginx -s reload' manually.")
            return

        result = subprocess.run(
            ["sudo", "nginx", "-t"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print(f"[setup_nginx] ERROR: nginx config test failed:\n{result.stderr}")
            print("[setup_nginx] The config was written but nginx was NOT reloaded. Fix manually.")
            return

        subprocess.run(
            ["sudo", "nginx", "-s", "reload"],
            capture_output=True, text=True, timeout=10,
        )
        print("[setup_nginx] Nginx reloaded successfully")
    except FileNotFoundError:
        print("[setup_nginx] WARNING: nginx not found. Reload manually after fixing the config.")
    except subprocess.TimeoutExpired:
        print("[setup_nginx] WARNING: nginx command timed out. Reload manually.")
    except OSError as e:
        print(f"[setup_nginx] WARNING: Cannot run nginx: {e}. Reload manually.")
