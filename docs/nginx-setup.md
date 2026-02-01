# Nginx Setup for Memora FastAPI

This guide explains how to configure Nginx to route requests between Frappe and the FastAPI sidecar.

## Overview

- `/api/v1/*` routes to FastAPI (port 8001) - game API endpoints
- `/api/method/*` routes to Frappe (port 8000) - admin/content API
- All other routes go to Frappe (default behavior)

## Prerequisites

1. FastAPI server running on port 8001
2. Frappe site with nginx configured
3. Access to modify nginx configuration

## Installation Steps

### Step 1: Locate Frappe Nginx Config

Frappe generates nginx config at:
```
/etc/nginx/conf.d/{site_name}.conf
```

Or check your bench config:
```bash
bench config nginx
```

### Step 2: Add FastAPI Upstream

Add the upstream definition before the `server` block:

```nginx
# Add this BEFORE the server block
upstream memora-fastapi {
    server 127.0.0.1:8001 fail_timeout=0;
    keepalive 32;
}
```

Alternatively, include the provided config file:
```nginx
include /home/corex/aurevia-bench/apps/memora_admin/nginx/memora-fastapi.conf;
```

### Step 3: Add Location Block

Inside the `server { ... }` block, add the FastAPI location BEFORE any catch-all locations:

```nginx
server {
    # ... existing config ...

    # FastAPI game API routes (add this block)
    location /api/v1/ {
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
        proxy_set_header Connection "";

        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;

        proxy_pass http://memora-fastapi;
    }

    # Existing Frappe locations remain unchanged
    # location /api/method/ { ... }
}
```

### Step 4: Test and Reload

```bash
# Test nginx configuration
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

### Step 5: Verify Routing

```bash
# Test FastAPI route
curl http://your-site.com/api/v1/health/live
# Expected: {"status":"alive","api_version":"v1"}

# Test Frappe route still works
curl http://your-site.com/api/method/frappe.ping
# Expected: {"message":"pong"}
```

## Request ID Correlation

The configuration passes `$request_id` (nginx-generated) to FastAPI via `X-Request-ID` header. FastAPI middleware also generates its own 8-char ID.

For complete tracing:
- Nginx log includes `$request_id`
- FastAPI log includes its generated `request_id`
- Response header `X-Request-ID` comes from FastAPI

## Troubleshooting

### 502 Bad Gateway
- Check if FastAPI is running: `curl http://127.0.0.1:8001/api/v1/health/live`
- Check FastAPI logs for errors

### 404 Not Found on /api/v1/*
- Verify location block is inside server block
- Check location block ordering (specific before general)

### Frappe routes broken
- Ensure existing Frappe location blocks are preserved
- Check for conflicting location patterns

## Production Considerations

1. **SSL Termination**: Handle at nginx level, FastAPI runs HTTP internally
2. **Rate Limiting**: Add `limit_req` for game API if needed
3. **Monitoring**: Enable nginx access/error logs for /api/v1/*
