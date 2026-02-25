### Memora Admin

Memora

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app memora_admin
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/memora_admin
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit

---

## Redis Hardening Deployment Guide

Memora uses a **dedicated Redis instance** (port 13001) separate from Frappe's cache Redis (port 13000). This prevents `bench clear-cache` from wiping game data (wallets, progress, sessions, dirty sets).

### Architecture Overview

```
┌─────────────────────────────┐    ┌─────────────────────────────┐
│  Frappe Redis (port 13000)  │    │  Memora Redis (port 13001)  │
│  • bench cache              │    │  • Player wallets           │
│  • session data             │    │  • Progress bitmaps         │
│  • queue/scheduler          │    │  • Dirty sets & buffers     │
│  • bench clear-cache → FLUSH│    │  • Leaderboards             │
│                             │    │  • AOF persistence          │
└─────────────────────────────┘    └─────────────────────────────┘
         ↕                                    ↕
     Frappe ORM                      FastAPI + Frappe tasks
```

### Prerequisites

- Redis server binary installed (`/usr/bin/redis-server`)
- `redis` user/group exists on the system
- Port 13001 available
- Sufficient disk space for AOF files (~2x Redis memory usage)

### Step 1: Create Redis Configuration

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

### Step 2: Create Data Directory

```bash
sudo mkdir -p /var/lib/redis-memora
sudo chown redis:redis /var/lib/redis-memora
sudo chmod 750 /var/lib/redis-memora
```

### Step 3: Create Systemd Service

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

### Step 4: Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable redis-memora
sudo systemctl start redis-memora
```

### Step 5: Update Application Configuration

```bash
# Update FastAPI .env
cd /path/to/bench/apps/memora_admin
sed -i 's|REDIS_URL=redis://127.0.0.1:13000|REDIS_URL=redis://127.0.0.1:13001|' .env

# Update Frappe site config
cd /path/to/bench
bench --site your-site set-config redis_memora "redis://127.0.0.1:13001"
```

### Step 6: Migration (Flush-Then-Switch)

Perform a zero-downtime migration from shared Redis to the dedicated instance:

```bash
# 1. Flush all dirty data to MariaDB BEFORE switching
cd /path/to/bench
bench --site your-site execute memora_admin.tasks.sync.sync_dirty_progress
bench --site your-site execute memora_admin.tasks.sync.sync_dirty_wallets
bench --site your-site execute memora_admin.tasks.sync.flush_interaction_buffer

# 2. Restart services (after updating config in Step 5)
pkill -f "uvicorn fastapi_app.main:app"
sleep 3
bench restart

# 3. Self-healing handles the rest — all caches auto-rebuild from MariaDB
```

### Step 7: Verification

```bash
# Check Redis service is running
sudo systemctl status redis-memora

# Test connectivity
redis-cli -p 13001 ping
# Expected: PONG

# Verify AOF persistence
redis-cli -p 13001 INFO persistence | grep aof_enabled
# Expected: aof_enabled:1

# Verify memory eviction policy
redis-cli -p 13001 CONFIG GET maxmemory-policy
# Expected: volatile-ttl

# Verify FastAPI health
curl http://127.0.0.1:8002/api/v1/health/live
# Expected: {"status":"alive","api_version":"v1"}

# Verify Redis health endpoint
curl http://127.0.0.1:8002/api/v1/health/redis | python3 -m json.tool

# Canary test: bench clear-cache must NOT affect Memora data
redis-cli -p 13001 SET memora:test:canary alive
bench --site your-site clear-cache
redis-cli -p 13001 GET memora:test:canary
# Expected: "alive"
redis-cli -p 13001 DEL memora:test:canary
```

### Step 8: Monitoring Setup

Monitoring is built-in via two mechanisms:

1. **Health endpoint** (`GET /api/v1/health/redis`): Returns real-time Redis metrics — memory usage, buffer length, dirty set sizes, AOF status. No authentication required.
2. **Scheduled monitor** (every 5 minutes): Logs metrics to Frappe error log with threshold-based alerting:
   - WARNING: memory >80% or dirty sets >1000
   - CRITICAL: interaction buffer >10000

### Dev vs Production Configuration

| Setting | Dev | Production | Notes |
|---------|-----|-----------|-------|
| `maxmemory` | 128mb | 512mb–1gb | Scale with user count |
| `maxmemory-policy` | volatile-ttl | volatile-ttl | Evict keys with shortest TTL first |
| `tcp-backlog` | 511 (default) | 1024 | Higher for production traffic |
| `timeout` | 0 (disabled) | 300 | Close idle connections in production |
| `appendonly` | yes | yes | AOF persistence always on |
| `appendfsync` | everysec | everysec | 1-second data loss window max |
| `aof-use-rdb-preamble` | yes | yes | Faster AOF rewrite/recovery |

### TTL Policy Summary

Keys with TTL are evictable under memory pressure (`volatile-ttl` policy). Protected keys (no TTL) are never evicted.

| Key | TTL | Protected? |
|-----|-----|-----------|
| `wallet:{player}` | 48h | No — self-heals from MariaDB |
| `progress:{user}:{subj}:v{ver}` | 48h | No — self-heals from MariaDB |
| `access:{player}` | 24h | No — self-heals from MariaDB |
| `plan:{plan}:free_subjects` | 12h | No — rebuilt by periodic task |
| `dirty:progress` | None | **Yes** — data loss if evicted |
| `dirty:wallets` | None | **Yes** — data loss if evicted |
| `buffer:interactions` | None | **Yes** — data loss if evicted |
| `lb:alltime*` | None | **Yes** — permanent rankings |

### Rollback

If anything goes wrong, revert to shared Redis:

```bash
# Revert .env
sed -i 's|REDIS_URL=redis://127.0.0.1:13001|REDIS_URL=redis://127.0.0.1:13000|' /path/to/bench/apps/memora_admin/.env

# Remove site config key
bench --site your-site set-config redis_memora ""

# Restart services
pkill -f "uvicorn fastapi_app.main:app"
bench restart

# Self-healing handles the rest — all data rebuilds from MariaDB
```
