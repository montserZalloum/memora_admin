# External Integrations

**Analysis Date:** 2026-02-01

## APIs & External Services

**Content Delivery & Storage:**
- AWS S3 - Cloud object storage for assets
  - Configuration: Memora Settings DocType
  - Credentials: `access_key`, `secret_key` fields (stored as passwords)
- Cloudflare R2 - Alternative cloud storage provider
  - Configuration: Selectable via `storage_provider` setting in Memora Settings
  - Auth: Same access/secret key structure

**Internal Memora APIs:**
- Frappe REST API - Built-in API for all DocTypes
  - Authentication: Frappe session cookies and token-based auth
  - Endpoints generated automatically for each DocType

## Data Storage

**Databases:**
- MariaDB/MySQL (via Frappe ORM)
  - Client: Frappe ORM (mysqlclient/PyMySQL)
  - Connection: Configured via Frappe bench (`sites/[sitename]/site_config.json`)
  - Models: All DocTypes defined in `memora_admin/memora_admin/doctype/*/`

**File Storage:**
- Primary: Local filesystem
  - Fallback mode enabled by default (`local_fallback_mode` = 1)
  - Fallback trigger: When CDN is unavailable
- Secondary: Cloud storage (AWS S3 or Cloudflare R2)
  - CDN Base URL: Configurable via Memora Settings
  - Configuration: `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json`
  - Signed URLs: 4-hour expiry by default (configurable)

**Caching:**
- Redis (cache instance): `redis-server` on localhost:13000
  - Purpose: Session caching, frequently accessed data
  - Client: Frappe's built-in Redis client
- Redis (queue instance): Separate Redis instance
  - Purpose: Background job queue and task scheduling

## Authentication & Identity

**Auth Provider:**
- Custom Frappe authentication
  - Implementation: Frappe's built-in user management and session system
  - Session storage: Database + Redis cache
  - Token-based auth: Generated API tokens for service-to-service auth

**User Management:**
- Frappe User DocType - Standard user management
- Roles and Permissions system via DocType permissions
  - Configured in `memora_admin/memora_admin/doctype/*/memora_*.json`
  - System Manager role required for Memora Settings modification

## Monitoring & Observability

**Error Tracking:**
- Not detected - No explicit error tracking service (Sentry, DataDog, etc.) configured

**Logs:**
- Frappe logging system
  - Approach: Application logs to files and database
  - Log files: `/home/corex/aurevia-bench/logs/`
  - Access log file: `logs/worker.log`, `logs/worker.error.log`

**Background Jobs:**
- Frappe task scheduler and job queue
  - Configuration: Scheduled events can be defined in `memora_admin/hooks.py`
  - Workers: `bench worker` process handles async tasks
  - Scheduler: `bench schedule` process handles time-based tasks

## CI/CD & Deployment

**Hosting:**
- Frappe bench deployment
  - Development: `bench serve` on port 8001
  - Services: Web, Redis cache, Redis queue, WebSocket (socketio), scheduler, workers
  - Procfile configuration: `/home/corex/aurevia-bench/Procfile`

**CI Pipeline:**
- Pre-commit hooks - Git hooks for code quality
  - Tools: Ruff (Python), ESLint, Prettier
  - Configuration: `.pre-commit-config.yaml`
  - Managed via: `pre-commit install` command

**Deployment Workflow:**
- Bench CLI commands:
  - `bench get-app [repo-url]` - Install app
  - `bench install-app memora_admin` - Activate app
  - `bench migrate` - Run database migrations
  - `bench build` - Compile assets
  - `bench serve` - Start development server

## Environment Configuration

**Required env vars:**
- Database credentials (via Frappe site config):
  - `db_name`, `db_user`, `db_password`, `db_host`
- Redis connection info (auto-configured by bench):
  - Redis cache port: 13000
- Frappe secret key: Set in `sites/[sitename]/site_config.json`

**Secrets location:**
- Frappe site config: `sites/[sitename]/site_config.json`
- Memora Settings DocType for runtime configuration:
  - AWS S3 credentials: `access_key`, `secret_key` (Password fields)
  - Storage provider selection: `storage_provider` (AWS S3 or Cloudflare R2)

**Runtime Settings (Memora Settings DocType):**
- CDN configuration:
  - `cdn_enabled`: Enable/disable CDN usage
  - `cdn_base_url`: Base URL for CDN assets
  - `local_fallback_mode`: Fallback to local storage when CDN unavailable (default: enabled)
  - `storage_provider`: Select between AWS S3 or Cloudflare R2
  - `json_version`: Content version for cache busting
  - `signed_url_expiry_hours`: Signed URL validity (default: 4 hours)
- Content sync:
  - `batch_interval_minutes`: Batch processing interval (default: 5)
  - `batch_threshold`: Batch size threshold (default: 50)
- Gamification:
  - `default_max_hearts`: Player health points (default: 5)
  - `xp_per_heart`: XP awarded per remaining heart (default: calculated)
  - `base_lesson_xp`: XP for first lesson completion (default: calculated)
  - `replay_xp`: XP for replaying completed lessons (default: calculated)
- Security:
  - `max_devices_per_player`: Device limit per user (default: 2)
  - `session_timeout_days`: Session expiry (default: 30 days)
- FSRS Engine (spaced repetition):
  - `fsrs_weights`: Algorithm weights configuration
  - `request_retention_days`: How long to keep interaction logs

## Webhooks & Callbacks

**Incoming:**
- Not detected - No webhook receivers configured

**Outgoing:**
- Frappe hooks can be configured in `memora_admin/memora_admin/hooks.py` for:
  - Document events (`doc_events`)
  - Request handlers (`before_request`, `after_request`)
  - Job handlers (`before_job`, `after_job`)
  - Current state: All webhook configurations are commented out

## DocType Integrations

**Document Types Defined:**
- `memora_admin/memora_admin/doctype/memora_settings/` - Configuration singleton
- `memora_admin/memora_admin/doctype/memora_player_profile/` - Player data
- `memora_admin/memora_admin/doctype/memora_player_subscription/` - Subscription management
- `memora_admin/memora_admin/doctype/memora_lesson/` - Learning content
- `memora_admin/memora_admin/doctype/memora_achievement/` - Player achievements
- `memora_admin/memora_admin/doctype/memora_sync_log/` - Data synchronization logs
- Additional doctypes: Major, Unit, Subject, Grade, Track, Topic, Season, Plan Overrider, etc.

**Frappe Integration Points:**
- Document model inheritance: All DocTypes extend `frappe.model.document.Document`
  - Located: `memora_admin/memora_admin/doctype/*/memora_*.py`
- Automatic REST API generation for all DocTypes
- Built-in form UI generation from JSON schemas

---

*Integration audit: 2026-02-01*
