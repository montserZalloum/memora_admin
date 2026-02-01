# Server Migration Guide

This guide documents how to migrate the FastAPI sidecar and Redis to a separate server for improved performance and scalability.

## Current Architecture

```
┌─────────────────────────────────────────┐
│            Single Server                │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐ │
│  │  Nginx  │──│ Frappe  │──│  Redis  │ │
│  │  :80    │  │  :8000  │  │  :6379  │ │
│  └────┬────┘  └─────────┘  └────┬────┘ │
│       │                         │      │
│       │       ┌─────────┐       │      │
│       └───────│ FastAPI │───────┘      │
│               │  :8001  │              │
│               └─────────┘              │
└─────────────────────────────────────────┘
```

## Target Architecture

```
┌─────────────────────────────┐     ┌─────────────────────────────┐
│       Web Server            │     │       Game Server           │
│  ┌─────────┐  ┌─────────┐  │     │  ┌─────────┐  ┌─────────┐  │
│  │  Nginx  │──│ Frappe  │  │     │  │ FastAPI │──│  Redis  │  │
│  │  :80    │  │  :8000  │  │     │  │  :8001  │  │  :6379  │  │
│  └────┬────┘  └─────────┘  │     │  └─────────┘  └─────────┘  │
│       │                    │     │                             │
│       │     Internal LAN   │     │                             │
│       └────────────────────┼─────┼─────────────────────────────┘
│                            │     │
└────────────────────────────┘     └─────────────────────────────┘
```

## Prerequisites

Before starting the migration:

1. **New Server Provisioned**
   - Same OS version recommended (Ubuntu 22.04)
   - Python 3.11+ installed
   - Redis 7.0+ installed
   - Internal network access to web server

2. **Downtime Window**
   - Estimated: 30-60 minutes
   - Schedule during low-traffic period

3. **Backups**
   - Redis RDB/AOF backup
   - Current .env file backup
   - nginx configuration backup

4. **Network Access**
   - Firewall rules allow traffic between servers
   - Internal IP addresses known

## Migration Steps

### Phase 1: Prepare New Server

#### 1.1 Install Dependencies

```bash
# On new game server
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip redis-server nginx

# Create virtual environment
python3.11 -m venv /opt/memora/venv
source /opt/memora/venv/bin/activate

# Install FastAPI dependencies
pip install -r /path/to/requirements.txt
```

#### 1.2 Configure Redis

Edit `/etc/redis/redis.conf`:

```conf
# Bind to internal interface (replace with actual internal IP)
bind 10.0.0.2 127.0.0.1

# Enable password (recommended)
requirepass your-strong-redis-password

# Persistence (optional, depends on requirements)
save 900 1
save 300 10
save 60 10000
```

```bash
sudo systemctl restart redis-server
```

#### 1.3 Deploy FastAPI Application

```bash
# Copy application code
rsync -avz /home/corex/aurevia-bench/apps/memora_admin/fastapi_app/ \
    game-server:/opt/memora/fastapi_app/

# Copy and update .env
scp /home/corex/aurevia-bench/apps/memora_admin/.env game-server:/opt/memora/.env
```

Update `.env` on game server:
```
REDIS_URL=redis://:your-strong-redis-password@127.0.0.1:6379/0
```

#### 1.4 Create Systemd Service

Create `/etc/systemd/system/memora-fastapi.service`:

```ini
[Unit]
Description=Memora FastAPI Game API
After=network.target redis-server.service

[Service]
Type=exec
User=www-data
Group=www-data
WorkingDirectory=/opt/memora
Environment="PATH=/opt/memora/venv/bin"
EnvironmentFile=/opt/memora/.env
ExecStart=/opt/memora/venv/bin/uvicorn fastapi_app.main:app \
    --host 0.0.0.0 \
    --port 8001 \
    --workers 4 \
    --proxy-headers

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable memora-fastapi
sudo systemctl start memora-fastapi
```

#### 1.5 Verify Game Server

```bash
# Test FastAPI locally
curl http://127.0.0.1:8001/api/v1/health/live
curl http://127.0.0.1:8001/api/v1/health/ready

# Test from web server (replace with actual IP)
curl http://10.0.0.2:8001/api/v1/health/live
```

### Phase 2: Configure Network

#### 2.1 Firewall Rules

On game server:
```bash
# Allow FastAPI from web server only
sudo ufw allow from 10.0.0.1 to any port 8001

# Allow Redis from web server (for Frappe access)
sudo ufw allow from 10.0.0.1 to any port 6379
```

#### 2.2 Update Frappe Redis Config

On web server, update Frappe's `common_site_config.json`:

```json
{
  "redis_cache": "redis://:your-strong-redis-password@10.0.0.2:6379/0",
  "redis_queue": "redis://:your-strong-redis-password@10.0.0.2:6379/1"
}
```

Then restart Frappe:
```bash
bench restart
```

### Phase 3: Update Nginx

#### 3.1 Modify Upstream

On web server, update nginx upstream to point to game server:

```nginx
upstream memora-fastapi {
    server 10.0.0.2:8001 fail_timeout=0;
    keepalive 32;
}
```

#### 3.2 Test and Reload

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### Phase 4: Migrate Redis Data

#### 4.1 Export from Old Redis

```bash
# On old server
redis-cli BGSAVE
# Wait for save to complete
redis-cli LASTSAVE
# Copy RDB file
scp /var/lib/redis/dump.rdb game-server:/tmp/
```

#### 4.2 Import to New Redis

```bash
# On game server
sudo systemctl stop redis-server
sudo cp /tmp/dump.rdb /var/lib/redis/
sudo chown redis:redis /var/lib/redis/dump.rdb
sudo systemctl start redis-server
```

### Phase 5: Cutover

1. **Stop FastAPI on old server** (if running)
2. **Verify nginx routing** to new server
3. **Monitor logs** on both servers
4. **Test all endpoints** via public URL

## Verification Checklist

- [ ] FastAPI health endpoints respond via public URL
- [ ] Redis connection verified via /api/v1/health/ready
- [ ] Frappe can connect to new Redis
- [ ] No 502 errors in nginx logs
- [ ] Request IDs flow through correctly
- [ ] Response times within acceptable range

## Rollback Procedure

If issues occur, rollback to single-server setup:

### Immediate Rollback

1. **Revert nginx upstream**
   ```nginx
   upstream memora-fastapi {
       server 127.0.0.1:8001 fail_timeout=0;
       keepalive 32;
   }
   ```

2. **Start local FastAPI** (if stopped)
   ```bash
   # On web server
   uvicorn fastapi_app.main:app --port 8001 &
   ```

3. **Reload nginx**
   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

4. **Revert Frappe Redis config** (if changed)
   - Edit `common_site_config.json` to use local Redis
   - Run `bench restart`

### Full Rollback

1. Restore Redis data from backup
2. Revert all configuration changes
3. Restart all services
4. Verify functionality

## Post-Migration

- [ ] Remove FastAPI from web server (optional)
- [ ] Update monitoring to include game server
- [ ] Document new architecture in team wiki
- [ ] Update deployment scripts

## Troubleshooting

### FastAPI Cannot Connect to Redis

1. Check firewall rules: `sudo ufw status`
2. Verify Redis binding: `sudo netstat -tlnp | grep 6379`
3. Test connection: `redis-cli -h 10.0.0.2 -a password ping`

### High Latency After Migration

1. Check network latency: `ping 10.0.0.2`
2. Consider switching to Unix socket if same machine
3. Tune Redis connection pool size

### Frappe Redis Errors

1. Verify Redis URL in `common_site_config.json`
2. Check Redis password in URL
3. Test: `redis-cli -h 10.0.0.2 -a password ping`
