# Quickstart: Redis Hardening

**Feature**: 027-redis-hardening | **Date**: 2026-02-25

## Prerequisites

- Redis server binary installed (`/usr/bin/redis-server`)
- `redis` user/group exists on the system
- Port 13001 available
- Sufficient disk space for AOF files (~2x Redis memory usage)

## Setup Steps

### 1. Create Redis Configuration

```bash
sudo tee /etc/redis/redis-memora.conf << 'EOF'
# Memora Dedicated Redis Instance
port 13001
bind 127.0.0.1
daemonize no
supervised systemd

# Memory
maxmemory 128mb
maxmemory-policy volatile-ttl

# AOF Persistence
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
aof-use-rdb-preamble yes

# Storage
dir /var/lib/redis-memora
logfile /var/log/redis/redis-memora.log
dbfilename dump-memora.rdb

# Performance
databases 1
tcp-keepalive 300
timeout 0
EOF
```

### 2. Create Data Directory

```bash
sudo mkdir -p /var/lib/redis-memora
sudo chown redis:redis /var/lib/redis-memora
sudo chmod 750 /var/lib/redis-memora
```

### 3. Create Systemd Service

```bash
sudo tee /etc/systemd/system/redis-memora.service << 'EOF'
[Unit]
Description=Memora Redis (Dedicated Game Data)
After=network.target
Documentation=https://redis.io/documentation

[Service]
Type=notify
ExecStart=/usr/bin/redis-server /etc/redis/redis-memora.conf --supervised systemd
User=redis
Group=redis
RuntimeDirectory=redis-memora
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
```

### 4. Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable redis-memora
sudo systemctl start redis-memora
```

### 5. Verify

```bash
# Check service status
sudo systemctl status redis-memora

# Test connection
redis-cli -p 13001 ping
# Expected: PONG

# Verify AOF
redis-cli -p 13001 INFO persistence | grep aof_enabled
# Expected: aof_enabled:1

# Verify memory policy
redis-cli -p 13001 CONFIG GET maxmemory-policy
# Expected: volatile-ttl
```

### 6. Update Application Config

```bash
# Update FastAPI .env
cd /home/corex/aurevia-bench/apps/memora_admin
sed -i 's|REDIS_URL=redis://127.0.0.1:13000|REDIS_URL=redis://127.0.0.1:13001|' .env

# Update Frappe site config
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com set-config redis_memora "redis://127.0.0.1:13001"
```

### 7. Restart Services

```bash
# Flush dirty sets to MariaDB first (manual trigger)
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com execute memora_admin.tasks.sync.sync_dirty_progress
bench --site x.conanacademy.com execute memora_admin.tasks.sync.sync_dirty_wallets
bench --site x.conanacademy.com execute memora_admin.tasks.sync.flush_interaction_buffer

# Restart FastAPI
pkill -f "uvicorn fastapi_app.main:app"
# Wait for auto-restart, then verify:
sleep 3
curl http://127.0.0.1:8002/api/v1/health/live

# Restart Frappe workers
bench restart
```

### 8. Verify End-to-End

```bash
# Check Redis health endpoint
curl http://127.0.0.1:8002/api/v1/health/redis | python3 -m json.tool

# Verify Frappe can reach Memora Redis
bench --site x.conanacademy.com console
>>> import redis
>>> r = redis.from_url(frappe.conf.get("redis_memora", frappe.conf.redis_cache))
>>> r.ping()
True

# Verify bench clear-cache doesn't affect Memora data
redis-cli -p 13001 SET memora:test:canary "alive"
bench --site x.conanacademy.com clear-cache
redis-cli -p 13001 GET memora:test:canary
# Expected: "alive"
redis-cli -p 13001 DEL memora:test:canary
```

## Development Workflow

After code changes to `fastapi_app/*`:
```bash
pkill -f "uvicorn fastapi_app.main:app"
sleep 3
curl http://127.0.0.1:8002/api/v1/health/live
```

After code changes to `memora_admin/events/*` or `memora_admin/tasks/*`:
```bash
bench restart
```

## Running Tests

```bash
# FastAPI tests (use port 13001)
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/ -v

# Frappe sync tests
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.tests
```

## Rollback

If anything goes wrong, revert to shared Redis:
```bash
# Update .env back to 13000
sed -i 's|REDIS_URL=redis://127.0.0.1:13001|REDIS_URL=redis://127.0.0.1:13000|' /home/corex/aurevia-bench/apps/memora_admin/.env

# Remove site config key
bench --site x.conanacademy.com set-config redis_memora ""

# Restart services
pkill -f "uvicorn fastapi_app.main:app"
bench restart

# Self-healing handles the rest — all data rebuilds from MariaDB
```
