# Memora Platform - Technical PRD
## Part 3: Operations & Deployment
### Version 1.0 | February 2026

---

# Table of Contents

1. [CDN & Caching Strategy](#1-cdn--caching-strategy)
2. [Security & Authentication](#2-security--authentication)
3. [Deployment Architecture](#3-deployment-architecture)
4. [File Structure](#4-file-structure)
5. [Monitoring & Health Checks](#5-monitoring--health-checks)
6. [Cost Estimation](#6-cost-estimation)
7. [Future Roadmap](#7-future-roadmap)
8. [Quick Reference](#8-quick-reference)

---

# 1. CDN & Caching Strategy

## 1.1 CDN Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           CDN ARCHITECTURE                                      │
│                                                                                 │
│  Student App                                                                    │
│      │                                                                          │
│      ▼                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                    CLOUDFLARE EDGE (Global)                              │   │
│  │                                                                          │   │
│  │  • 300+ data centers worldwide                                          │   │
│  │  • Automatic geographic routing                                         │   │
│  │  • DDoS protection                                                      │   │
│  │  • SSL/TLS termination                                                  │   │
│  │  • Edge caching                                                         │   │
│  │                                                                          │   │
│  │  Cache Rules:                                                            │   │
│  │  ┌────────────────────────────────┬───────────────┬─────────────────┐   │   │
│  │  │ Path Pattern                   │ Cache TTL     │ Notes           │   │   │
│  │  ├────────────────────────────────┼───────────────┼─────────────────┤   │   │
│  │  │ /manifest.json                 │ 5 minutes     │ Global manifest │   │   │
│  │  │ /plans/*/manifest.json         │ 5 minutes     │ Plan manifests  │   │   │
│  │  │ /subjects/*/_h.json            │ 5 minutes     │ Hierarchies     │   │   │
│  │  │ /subjects/*/units/*_c.json     │ 1 hour        │ Unit content    │   │   │
│  │  │ /lessons/*.json                │ 30 days       │ Lesson content  │   │   │
│  │  │ /assets/*                      │ 1 year        │ Static assets   │   │   │
│  │  └────────────────────────────────┴───────────────┴─────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                 │                                               │
│                                 │ Cache MISS                                    │
│                                 ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      CLOUDFLARE R2 (Origin)                              │   │
│  │                                                                          │   │
│  │  • S3-compatible object storage                                         │   │
│  │  • Zero egress fees                                                     │   │
│  │  • Automatic replication                                                │   │
│  │  • Direct Cloudflare integration                                        │   │
│  │                                                                          │   │
│  │  Buckets:                                                                │   │
│  │  • memora-content (public JSON files)                                   │   │
│  │  • memora-assets (images, media)                                        │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 1.2 Cache Busting Strategy

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        VERSION-BASED CACHE BUSTING                              │
│                                                                                 │
│  URL Pattern: /subjects/SUB-001/_h.json?v=1706275200                           │
│                                              └── Unix timestamp                 │
│                                                                                 │
│  Flow:                                                                          │
│  ──────                                                                         │
│  1. Admin saves content in Frappe                                              │
│  2. Build worker generates new JSON files                                      │
│  3. New version timestamp: 1706275200                                          │
│  4. Files uploaded to R2                                                       │
│  5. Version stored: HSET memora:versions {subject_id} {version}                │
│  6. Client fetches with ?v=1706275200                                          │
│  7. CDN treats as new URL → fetches from R2                                    │
│  8. New content cached at edge                                                 │
│                                                                                 │
│  Client-Side Logic:                                                             │
│  ───────────────────                                                            │
│  // On app launch                                                               │
│  const cachedVersion = localStorage.getItem('content_version');                │
│  const { version } = await fetch('/api/v1/version').then(r => r.json());       │
│                                                                                 │
│  if (version !== cachedVersion) {                                              │
│    await clearContentCache();                                                  │
│    localStorage.setItem('content_version', version);                           │
│  }                                                                              │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 1.3 CDN Upload Process

```python
# File: memora_admin/memora_admin/utils/cdn.py

import frappe
import boto3
import os

def upload_to_cdn(subject_id: str):
    """Upload public files to Cloudflare R2."""
    settings = frappe.get_single("Memora Settings")
    
    if not settings.cdn_enabled:
        return
    
    s3 = boto3.client('s3',
        endpoint_url=settings.cdn_base_url,
        aws_access_key_id=settings.get_password('access_key'),
        aws_secret_access_key=settings.get_password('secret_key')
    )
    
    bucket = "memora-content"
    base = frappe.get_site_path("public", "memora_content")
    
    # Upload hierarchy
    upload_file(s3, bucket,
        f"{base}/subjects/{subject_id}/_h.json",
        f"subjects/{subject_id}/_h.json",
        cache_control="public, max-age=300"  # 5 min
    )
    
    # Upload unit content files
    units_path = f"{base}/subjects/{subject_id}/units"
    if os.path.exists(units_path):
        for filename in os.listdir(units_path):
            upload_file(s3, bucket,
                f"{units_path}/{filename}",
                f"subjects/{subject_id}/units/{filename}",
                cache_control="public, max-age=3600"  # 1 hour
            )
    
    # Upload lesson files
    lessons = frappe.get_all("Memora Lesson",
        filters={"subject": subject_id},
        pluck="name"
    )
    for lesson_id in lessons:
        lesson_file = f"{base}/lessons/{lesson_id}.json"
        if os.path.exists(lesson_file):
            upload_file(s3, bucket,
                lesson_file,
                f"lessons/{lesson_id}.json",
                cache_control="public, max-age=2592000"  # 30 days
            )


def upload_file(s3, bucket: str, local_path: str, remote_path: str, cache_control: str):
    """Upload a single file to R2."""
    s3.upload_file(
        local_path,
        bucket,
        remote_path,
        ExtraArgs={
            'ContentType': 'application/json',
            'CacheControl': cache_control
        }
    )
```

## 1.4 Local Fallback Configuration

```nginx
# /etc/nginx/sites-available/memora

upstream frappe {
    server 127.0.0.1:8000;
}

upstream fastapi {
    server 127.0.0.1:8001;
}

server {
    listen 443 ssl http2;
    server_name api.memora.com;
    
    # SSL
    ssl_certificate /etc/letsencrypt/live/memora.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/memora.com/privkey.pem;
    
    # FastAPI routes (Game API)
    location /api/v1/ {
        proxy_pass http://fastapi;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 30s;
    }
    
    # Frappe routes (Admin, Auth)
    location /api/method/ {
        proxy_pass http://frappe;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    # Local content fallback (when CDN is unreachable)
    location /local_content/ {
        internal;
        alias /home/corex/aurevia-bench/sites/x.conanacademy.com/public/memora_content/;
        
        expires 5m;
        add_header Cache-Control "public, max-age=300";
        add_header X-Content-Source "local-fallback";
        
        gzip on;
        gzip_types application/json;
        gzip_min_length 1000;
    }
    
    # Health check
    location /health {
        proxy_pass http://fastapi/health;
    }
}
```

---

# 2. Security & Authentication

## 2.1 JWT Token Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            JWT TOKEN FLOW                                       │
│                                                                                 │
│  1. LOGIN                                                                       │
│  ─────────                                                                      │
│  POST /api/method/memora_admin.api.auth.login                                  │
│  Body: {email, password, device_id, device_name}                               │
│                                                                                 │
│         │                                                                       │
│         ▼                                                                       │
│  ┌─────────────────┐                                                           │
│  │ Frappe Backend  │                                                           │
│  │                 │                                                           │
│  │ 1. Verify creds │                                                           │
│  │ 2. Get player   │                                                           │
│  │ 3. Get season   │                                                           │
│  │ 4. Issue JWTs   │                                                           │
│  │ 5. Store session│                                                           │
│  └────────┬────────┘                                                           │
│           │                                                                     │
│           ▼                                                                     │
│  Response:                                                                      │
│  {                                                                              │
│    access_token: "eyJ...",     // 15 min TTL                                   │
│    refresh_token: "eyJ...",    // 7 day TTL                                    │
│    expires_in: 900                                                             │
│  }                                                                              │
│                                                                                 │
│  2. API REQUEST                                                                 │
│  ──────────────                                                                 │
│  GET /api/v1/progress/subjects/SUB-001                                         │
│  Header: Authorization: Bearer eyJ...                                          │
│                                                                                 │
│         │                                                                       │
│         ▼                                                                       │
│  ┌─────────────────┐                                                           │
│  │ FastAPI Backend │                                                           │
│  │                 │                                                           │
│  │ 1. Extract JWT  │  ← STATELESS (no DB lookup)                               │
│  │ 2. Verify sig   │                                                           │
│  │ 3. Check expiry │                                                           │
│  │ 4. Extract:     │                                                           │
│  │    - player_id  │                                                           │
│  │    - device_id  │                                                           │
│  │    - season_id  │                                                           │
│  └────────┬────────┘                                                           │
│           │                                                                     │
│           ▼                                                                     │
│  Process request with player context                                           │
│                                                                                 │
│  3. TOKEN REFRESH                                                               │
│  ─────────────────                                                              │
│  POST /api/method/memora_admin.api.auth.refresh                                │
│  Body: {refresh_token: "eyJ..."}                                               │
│                                                                                 │
│         │                                                                       │
│         ▼                                                                       │
│  ┌─────────────────┐                                                           │
│  │ Frappe Backend  │                                                           │
│  │                 │                                                           │
│  │ 1. Verify JWT   │                                                           │
│  │ 2. Check Redis  │  ← Session lookup (refresh only)                          │
│  │ 3. Issue new    │                                                           │
│  │    access token │                                                           │
│  └─────────────────┘                                                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 2.2 JWT Token Structure

```python
# Access Token (short-lived, stateless verification)
{
    "sub": "PLAYER-00001",      # Player ID
    "device": "DEV-UUID-123",   # Device ID
    "season": "SEASON-2026",    # Current season (for Gate 1)
    "type": "access",           # Token type
    "iat": 1706275200,          # Issued at
    "exp": 1706276100           # Expires (15 min)
}

# Refresh Token (long-lived, session-verified)
{
    "sub": "PLAYER-00001",
    "device": "DEV-UUID-123",
    "season": "SEASON-2026",
    "type": "refresh",
    "iat": 1706275200,
    "exp": 1706880000           # Expires (7 days)
}
```

## 2.3 Session Storage

```
Redis Key: session:{player_id}:{device_id}
TTL: 7 days (604800 seconds)

Value (JSON):
{
    "refresh_token_hash": "sha256:a1b2c3d4...",  # Hash only
    "device_name": "iPhone 15 Pro",
    "platform": "ios",
    "created_at": "2026-01-26T10:00:00Z"
}
```

## 2.4 Security Configuration

```json
// common_site_config.json (Frappe)
{
    "redis_cache": "redis://localhost:6379",
    "memora_jwt_secret": "your-super-secret-key-minimum-32-characters",
    "memora_jwt_algorithm": "HS256",
    "memora_jwt_access_expiry": 900,
    "memora_jwt_refresh_expiry": 604800
}
```

## 2.5 Rate Limiting

```python
# Rate limits by endpoint
RATE_LIMITS = {
    "complete-stage": 100,    # 100/minute (fast gameplay)
    "complete-lesson": 30,    # 30/minute
    "start-lesson": 30,       # 30/minute
    "progress": 60,           # 60/minute
    "leaderboard": 30,        # 30/minute
    "default": 120            # 120/minute
}

# Redis implementation
async def check_rate_limit(player_id: str, endpoint: str, redis):
    key = f"ratelimit:{player_id}:{endpoint}"
    current = await redis.incr(key)
    
    if current == 1:
        await redis.expire(key, 60)
    
    limit = RATE_LIMITS.get(endpoint, RATE_LIMITS["default"])
    if current > limit:
        raise HTTPException(429, "Rate limit exceeded")
```

---

# 3. Deployment Architecture

## 3.1 Single Server Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      PRODUCTION DEPLOYMENT                                      │
│                      (Single Server - Hetzner AX41)                             │
│                                                                                 │
│  Hardware:                                                                      │
│  • AMD Ryzen 5 3600 (6 cores / 12 threads)                                     │
│  • 64 GB RAM                                                                   │
│  • 2x 512 GB NVMe SSD (RAID 1)                                                 │
│  • 1 Gbit/s unmetered bandwidth                                                │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         SYSTEMD SERVICES                                │   │
│  │                                                                         │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐     │   │
│  │  │ nginx.service    │  │ redis.service    │  │ mariadb.service  │     │   │
│  │  │                  │  │                  │  │                  │     │   │
│  │  │ Reverse Proxy    │  │ Cache/Hot Data   │  │ Database         │     │   │
│  │  │ Port: 80, 443    │  │ Port: 6379       │  │ Port: 3306       │     │   │
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘     │   │
│  │                                                                         │   │
│  │  ┌─────────────────────────────────────────────────────────────────┐   │   │
│  │  │                    SUPERVISOR MANAGED                            │   │   │
│  │  │                                                                  │   │   │
│  │  │  ┌─────────────────────────────────────────────────────────┐    │   │   │
│  │  │  │ frappe-bench                                            │    │   │   │
│  │  │  │                                                         │    │   │   │
│  │  │  │  • frappe-bench-web (Gunicorn)      Port 8000          │    │   │   │
│  │  │  │  • frappe-bench-worker-short        Background Jobs    │    │   │   │
│  │  │  │  • frappe-bench-worker-long         Background Jobs    │    │   │   │
│  │  │  │  • frappe-bench-worker-default      Background Jobs    │    │   │   │
│  │  │  │  • frappe-bench-schedule            Scheduler          │    │   │   │
│  │  │  └─────────────────────────────────────────────────────────┘    │   │   │
│  │  │                                                                  │   │   │
│  │  │  ┌─────────────────────────────────────────────────────────┐    │   │   │
│  │  │  │ memora-fastapi                                         │    │   │   │
│  │  │  │                                                         │    │   │   │
│  │  │  │  • uvicorn (4 workers)              Port 8001          │    │   │   │
│  │  │  └─────────────────────────────────────────────────────────┘    │   │   │
│  │  └──────────────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 3.2 Supervisor Configuration

```ini
# /etc/supervisor/conf.d/memora-fastapi.conf

[program:memora-fastapi]
command=/home/frappe/fastapi_app/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001 --workers 4
directory=/home/frappe/fastapi_app
user=frappe
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stderr_logfile=/var/log/supervisor/memora-fastapi.err.log
stdout_logfile=/var/log/supervisor/memora-fastapi.out.log
environment=PYTHONPATH="/home/frappe/fastapi_app"
```

## 3.3 Systemd Service (Alternative)

```ini
# /etc/systemd/system/memora-fastapi.service

[Unit]
Description=Memora FastAPI Game Server
After=network.target redis.service

[Service]
Type=simple
User=frappe
Group=frappe
WorkingDirectory=/home/frappe/fastapi_app
Environment="PATH=/home/frappe/fastapi_app/venv/bin"
ExecStart=/home/frappe/fastapi_app/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001 --workers 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## 3.4 Memory Allocation

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    MEMORY ALLOCATION (64 GB Total)                              │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Redis                                                    16 GB          │   │
│  │ ─────                                                                   │   │
│  │ • Progress bitmaps: ~10 GB (100K players × 10 subjects × 12.5 KB)      │   │
│  │ • Wallets: ~1 GB (100K players × 100 bytes)                            │   │
│  │ • Sessions: ~500 MB                                                    │   │
│  │ • Leaderboards: ~500 MB                                                │   │
│  │ • Buffer: ~4 GB                                                        │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ MariaDB                                                  8 GB           │   │
│  │ ────────                                                                │   │
│  │ • InnoDB buffer pool: 6 GB                                             │   │
│  │ • Query cache: 1 GB                                                    │   │
│  │ • Connections: 1 GB                                                    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ FastAPI (4 workers)                                      4 GB           │   │
│  │ ──────────────────                                                      │   │
│  │ • Bitmap cache: ~2 GB (all _b.json files in memory)                    │   │
│  │ • Request handling: ~2 GB                                              │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Frappe/Gunicorn                                          4 GB           │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ OS & Other                                               4 GB           │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Reserved Buffer                                          28 GB          │   │
│  │ ───────────────                                                         │   │
│  │ • Burst handling                                                       │   │
│  │ • Build processes                                                      │   │
│  │ • Future growth                                                        │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 3.5 Redis Configuration

```conf
# /etc/redis/redis.conf

# Memory
maxmemory 16gb
maxmemory-policy noeviction

# Persistence
save 900 1
save 300 10
save 60 10000
appendonly yes
appendfsync everysec

# Performance
tcp-keepalive 300
timeout 0

# Connections
maxclients 10000
```

## 3.6 MariaDB Configuration

```conf
# /etc/mysql/mariadb.conf.d/50-server.cnf

[mysqld]
# InnoDB
innodb_buffer_pool_size = 6G
innodb_log_file_size = 512M
innodb_flush_log_at_trx_commit = 2
innodb_flush_method = O_DIRECT

# Connections
max_connections = 500
wait_timeout = 600

# Query Cache
query_cache_type = 1
query_cache_size = 256M

# Character Set
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
```

---

# 4. File Structure

## 4.1 Complete Directory Structure

```
/home/frappe/
├── frappe-bench/
│   ├── apps/
│   │   ├── frappe/
│   │   ├── erpnext/
│   │   └── memora_admin/
│   │       ├── memora_admin/
│   │       │   ├── __init__.py
│   │       │   ├── hooks.py
│   │       │   │
│   │       │   ├── memora_admin/                    # Module
│   │       │   │   ├── doctype/
│   │       │   │   │   ├── memora_academic_plan/
│   │       │   │   │   ├── memora_subject/
│   │       │   │   │   ├── memora_track/
│   │       │   │   │   ├── memora_unit/
│   │       │   │   │   ├── memora_topic/
│   │       │   │   │   ├── memora_lesson/
│   │       │   │   │   ├── memora_lesson_stage_settings/
│   │       │   │   │   ├── memora_grade/
│   │       │   │   │   ├── memora_major/
│   │       │   │   │   ├── memora_season/
│   │       │   │   │   ├── memora_player_profile/
│   │       │   │   │   ├── memora_player_wallet/
│   │       │   │   │   ├── memora_player_subscription/
│   │       │   │   │   ├── memora_structure_progress/
│   │       │   │   │   ├── memora_memory_state/
│   │       │   │   │   ├── memora_interaction_log/
│   │       │   │   │   ├── memora_analytics_aggregate/
│   │       │   │   │   ├── memora_product_grant/
│   │       │   │   │   ├── memora_plan_overrider/
│   │       │   │   │   ├── memora_subscription_transaction/
│   │       │   │   │   ├── memora_build_queue/
│   │       │   │   │   ├── memora_sync_log/
│   │       │   │   │   └── memora_settings/
│   │       │   │   │
│   │       │   │   └── report/
│   │       │   │       ├── player_progress/
│   │       │   │       └── content_analytics/
│   │       │   │
│   │       │   ├── api/
│   │       │   │   ├── __init__.py
│   │       │   │   ├── auth.py
│   │       │   │   └── payment.py
│   │       │   │
│   │       │   ├── tasks/
│   │       │   │   ├── __init__.py
│   │       │   │   ├── build.py
│   │       │   │   ├── sync.py
│   │       │   │   ├── aggregate.py
│   │       │   │   └── maintenance.py
│   │       │   │
│   │       │   └── utils/
│   │       │       ├── __init__.py
│   │       │       ├── build.py
│   │       │       └── cdn.py
│   │       │
│   │       ├── setup.py
│   │       └── requirements.txt
│   │
│   ├── sites/
│   │   ├── common_site_config.json           # JWT secrets, Redis URL
│   │   └── x.conanacademy.com/
│   │       ├── public/
│   │       │   └── memora_content/           # Public JSON (CDN source)
│   │       │       ├── manifest.json
│   │       │       ├── plans/
│   │       │       │   └── {plan_id}/
│   │       │       │       └── manifest.json
│   │       │       ├── subjects/
│   │       │       │   └── {subject_id}/
│   │       │       │       ├── _h.json
│   │       │       │       └── units/
│   │       │       │           └── {unit_id}_c.json
│   │       │       └── lessons/
│   │       │           └── {lesson_id}.json
│   │       │
│   │       └── private/
│   │           └── memora_bitmaps/           # Private JSON (FastAPI)
│   │               └── {subject_id}_b.json
│   │
│   └── logs/
│
└── fastapi_app/                              # FastAPI Sidecar
    ├── venv/
    ├── main.py
    ├── config.py
    ├── dependencies.py
    ├── models/
    │   ├── __init__.py
    │   ├── requests.py
    │   └── responses.py
    ├── routers/
    │   ├── __init__.py
    │   ├── progress.py
    │   ├── game.py
    │   ├── wallet.py
    │   └── leaderboard.py
    ├── services/
    │   ├── __init__.py
    │   ├── unlocker.py
    │   ├── access.py
    │   └── bitmap.py
    └── utils/
        ├── __init__.py
        └── redis_client.py
```

---

# 5. Monitoring & Health Checks

## 5.1 Health Check Endpoints

```python
# FastAPI health endpoints

@app.get("/health")
async def health_check():
    """Basic health check for load balancer."""
    return {"status": "healthy", "version": "1.0.0"}

@app.get("/health/detailed")
async def detailed_health():
    """Detailed health with dependencies."""
    redis_ok = False
    bitmap_cache_size = 0
    
    try:
        r = await get_redis()
        redis_ok = await r.ping()
        bitmap_cache_size = len(unlocker_engine._structure_cache)
    except:
        pass
    
    return {
        "status": "healthy" if redis_ok else "degraded",
        "version": "1.0.0",
        "dependencies": {
            "redis": "ok" if redis_ok else "error"
        },
        "cache": {
            "bitmap_subjects_loaded": bitmap_cache_size
        }
    }
```

## 5.2 Key Metrics to Monitor

| Metric | Warning Threshold | Critical Threshold |
|--------|-------------------|-------------------|
| API Latency (p99) | > 100ms | > 500ms |
| API Error Rate | > 1% | > 5% |
| Redis Memory | > 80% | > 95% |
| Redis Connections | > 8000 | > 9500 |
| MariaDB Connections | > 400 | > 480 |
| Disk Usage | > 80% | > 95% |
| CPU Usage | > 70% | > 90% |
| Build Queue Size | > 50 | > 100 |
| Dirty Sync Queue | > 10000 | > 50000 |

## 5.3 Logging Strategy

```python
# Log levels by component

# FastAPI
- INFO: Request received, response sent
- WARNING: Rate limit approaching, slow response
- ERROR: Request failed, Redis connection error

# Frappe Tasks
- INFO: Sync completed (X records)
- INFO: Build completed (X files)
- WARNING: Build taking > 5 minutes
- ERROR: Build failed, Sync failed

# Example log format
{
    "timestamp": "2026-02-01T10:30:00Z",
    "level": "INFO",
    "component": "fastapi",
    "endpoint": "/api/v1/progress/subjects/SUB-001",
    "player_id": "PLAYER-00001",
    "duration_ms": 15,
    "status": 200
}
```

---

# 6. Cost Estimation

## 6.1 Monthly Cost Breakdown

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        MONTHLY COST BREAKDOWN                                   │
│                        (Target: 100,000 Concurrent Users)                       │
│                                                                                 │
│  Infrastructure:                                                                │
│  ┌────────────────────────────────────────────────────────────────────────┐    │
│  │ Hetzner AX41-NVMe Dedicated Server                                     │    │
│  │ • AMD Ryzen 5 3600 (6c/12t)                                           │    │
│  │ • 64 GB RAM                                                            │    │
│  │ • 2x 512 GB NVMe (RAID 1)                                             │    │
│  │ • 1 Gbit/s unmetered                                                   │    │
│  │                                                      €44.51 (~$48)     │    │
│  └────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  CDN & Storage:                                                                 │
│  ┌────────────────────────────────────────────────────────────────────────┐    │
│  │ Cloudflare Free Plan                                                   │    │
│  │ • Unlimited bandwidth                                                  │    │
│  │ • DDoS protection                                                      │    │
│  │ • SSL/TLS                                                              │    │
│  │                                                              $0        │    │
│  ├────────────────────────────────────────────────────────────────────────┤    │
│  │ Cloudflare R2 Storage                                                  │    │
│  │ • 10 GB free tier                                                      │    │
│  │ • Estimated usage: ~500 MB JSON + assets                              │    │
│  │ • Zero egress fees                                                     │    │
│  │                                                              $0        │    │
│  └────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  Domain & SSL:                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────┐    │
│  │ Domain                                                 ~$12/year       │    │
│  │ SSL (Let's Encrypt)                                           $0       │    │
│  │                                                         ~$1/month      │    │
│  └────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  Backup:                                                                        │
│  ┌────────────────────────────────────────────────────────────────────────┐    │
│  │ Hetzner Storage Box (100 GB)                            €3.81 (~$4)    │    │
│  └────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  ═══════════════════════════════════════════════════════════════════════════   │
│                                                                                 │
│  TOTAL MONTHLY COST:                                           ~$53            │
│                                                                                 │
│  Cost per 1,000 users:                                         $0.53           │
│  Cost per user (100K):                                         $0.00053        │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 6.2 Scaling Cost Projection

| Phase | Users | Infrastructure | Monthly Cost |
|-------|-------|----------------|--------------|
| Phase 1 | 0 - 10K | Single AX41 | ~$53 |
| Phase 2 | 10K - 50K | Single AX41 (optimized) | ~$53 |
| Phase 3 | 50K - 100K | Upgrade to AX51 (128GB RAM) | ~$80 |
| Phase 4 | 100K - 250K | AX101 (256GB RAM) | ~$130 |
| Phase 5 | 250K - 500K | 2 servers (load balanced) | ~$260 |
| Phase 6 | 500K+ | Multi-server cluster | ~$500+ |

## 6.3 Cloud Provider Comparison

| Provider | Equivalent Specs | Monthly Cost | vs Hetzner |
|----------|------------------|--------------|------------|
| Hetzner AX41 | 6c/64GB/1TB NVMe | ~$53 | Baseline |
| AWS EC2 m5.2xlarge | 8c/32GB | ~$280 | 5x more |
| Google Cloud n2-standard-8 | 8c/32GB | ~$250 | 5x more |
| DigitalOcean Premium | 8c/64GB | ~$380 | 7x more |
| Azure D8s v3 | 8c/32GB | ~$300 | 6x more |

---

# 7. Future Roadmap

## 7.1 Phase 1: Core Platform (Current)
- ✅ Content management (DocTypes)
- ✅ Access control (Double-Gate)
- ✅ Progress tracking (Bitmaps)
- ✅ Gamification (XP, Streaks)
- ✅ Build pipeline (JSON generation)
- ✅ CDN delivery

## 7.2 Phase 2: Enhanced Features (Q2 2026)

### Offline Support
```
• Download lessons for offline use
• Store progress locally (IndexedDB)
• Sync when back online
• Conflict resolution (last-write-wins)
```

### Push Notifications (Firebase)
```
• Streak reminders
• New content alerts
• Achievement notifications
• Daily lesson reminders
```

## 7.3 Phase 3: Analytics & Insights (Q3 2026)

### Analytics Pipeline
```
Interaction Logs → ETL (daily) → Analytics DB → Dashboards

Metrics:
• Learning patterns
• Content difficulty analysis
• Retention rates
• Engagement metrics
```

### Admin Dashboards
```
• Real-time active users
• Content performance
• Revenue analytics
• Player progression
```

## 7.4 Phase 4: Advanced Features (Q4 2026)

### Anti-Cheat System
```
• Timing validation
• Answer verification
• Device fingerprinting
• Behavioral analysis
```

### Monitoring (Grafana + Prometheus)
```
• API metrics
• Redis metrics
• Database metrics
• Custom business metrics
```

### FSRS Integration (Spaced Repetition)
```
• Memory state tracking
• Review scheduling
• Difficulty adjustment
• Personalized learning paths
```

---

# 8. Quick Reference

## 8.1 API Endpoints Summary

### FastAPI (Game API)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| GET | `/api/v1/progress/subjects/{id}` | Get progress & states |
| POST | `/api/v1/game/start-lesson` | Start lesson |
| POST | `/api/v1/game/complete-stage` | Complete stage |
| POST | `/api/v1/game/end-lesson` | End lesson |
| GET | `/api/v1/wallet` | Get wallet |
| GET | `/api/v1/leaderboard/xp/{period}` | XP leaderboard |
| GET | `/api/v1/leaderboard/streak` | Streak leaderboard |

### Frappe (Admin/Auth)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/method/memora_admin.api.auth.login` | Login |
| POST | `/api/method/memora_admin.api.auth.refresh` | Refresh token |
| POST | `/api/method/memora_admin.api.payment.payment_webhook` | Payment webhook |

## 8.2 Redis Keys Summary

| Pattern | Type | Purpose |
|---------|------|---------|
| `memora:season:{id}:meta` | Hash | Season status & end_ts |
| `memora:access:{player}` | Set | Player's access grants |
| `memora:plan_subjects:{plan}` | Set | Subjects in plan |
| `progress:{player}:{subject}` | String | Progress bitmap |
| `wallet:{player}` | Hash | XP, streak |
| `session:{player}:{device}` | String | Auth session |
| `leaderboard:xp:{period}` | Sorted Set | Rankings |
| `dirty:progress` | Set | Pending sync |
| `dirty:wallet` | Set | Pending sync |
| `memora:pending_builds` | Set | Build queue |
| `memora:lesson_info` | Hash | Lesson cache |
| `memora:versions` | Hash | Content versions |

## 8.3 Response Time Targets

| Operation | Target | Max |
|-----------|--------|-----|
| Access check | < 2ms | 10ms |
| Progress fetch | < 20ms | 100ms |
| Stage complete | < 10ms | 50ms |
| Lesson complete | < 30ms | 150ms |
| CDN content | < 50ms | 200ms |
| Build (per subject) | < 60s | 300s |

## 8.4 Scheduled Tasks

| Schedule | Task | Purpose |
|----------|------|---------|
| Every 1 min | sync_dirty_progress | Redis → MariaDB |
| Every 1 min | sync_dirty_wallets | Redis → MariaDB |
| Every 1 min | flush_interaction_buffer | Buffer → MariaDB |
| Every 2 min | process_pending_builds | Generate JSON |
| Daily 2 AM | aggregate_daily_stats | Analytics |
| Daily 2 AM | reset_broken_streaks | Streak maintenance |
| Weekly | cleanup_old_logs | Log retention |

---

# End of PRD

## Document Summary

| Part | Content | Pages |
|------|---------|-------|
| Part 1 | Infrastructure & Data Layer | DocTypes, Redis, JSON |
| Part 2 | Business Logic & APIs | Access Control, FastAPI, Build Pipeline |
| Part 3 | Operations & Deployment | CDN, Security, Deployment, Costs |

**Total DocTypes**: 23 + 4 Child Tables = 27

**Version**: 1.0  
**Last Updated**: February 2026