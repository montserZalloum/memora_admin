# Technology Stack

**Analysis Date:** 2026-02-01

## Languages

**Primary:**
- Python 3.10+ - Core application logic and backend services
- JavaScript (ES2022) - Frontend UI and client-side interactions
- JSON - DocType schemas, configuration files

**Secondary:**
- Jinja2 - Server-side templating (Frappe framework)
- SCSS/CSS - Styling and theming

## Runtime

**Environment:**
- Python 3.10.12
- Node.js (via Frappe/bench)
- Redis (cache and job queue)

**Package Manager:**
- pip - Python package management
- npm - JavaScript dependencies (managed via Frappe bench)

## Frameworks

**Core:**
- Frappe Framework 15.0+ - Full-stack web framework with:
  - ORM and database abstraction (Document model)
  - Built-in admin UI (Desk)
  - WebSocket support (socketio)
  - Job queue system
  - Background task scheduling

**Testing:**
- Python unittest (standard library) - Backend tests
- Frappe test framework - Integration tests
- Pre-commit hooks integration

**Build/Dev:**
- Ruff - Python linting and formatting
- Prettier - JavaScript/Vue code formatting
- ESLint - JavaScript linting
- Pre-commit - Git hooks for code quality
- Bench CLI - Development and deployment tool

## Key Dependencies

**Critical:**
- frappe - Main framework (version managed by bench, ~15.0.0)
  - Provides ORM, API, admin UI, task scheduling
- erpnext - Enterprise resource planning module (available in bench)
  - Financial and operational data models

**Infrastructure:**
- redis-server - Caching and job queue backend
  - Cache instance: port 13000 (localhost)
  - Queue instance: configured separately

## Configuration

**Environment:**
- Python settings: `pyproject.toml`
  - Ruff configuration with linting rules
  - Import sorting enabled (isort plugin)
  - Formatting: tabs for indentation, double quotes
- Editor configuration: `.editorconfig`
  - Python/JS files: tab indentation, 4 spaces rendered as tab
  - JSON files: 2-space indentation
  - Line length: 99 characters for Python

**Build:**
- Frappe bench system - Manages Python environment and dependencies
- Project root: `/home/corex/aurevia-bench/`
- App root: `/home/corex/aurevia-bench/apps/memora_admin/`
- Virtual environment: `/home/corex/aurevia-bench/env/`

**Code Style Config:**
- `.eslintrc` - JavaScript linting rules
- `.pre-commit-config.yaml` - Git pre-commit hooks
- `pyproject.toml` - Ruff config with import sorting, formatting rules

## Platform Requirements

**Development:**
- Python 3.10+
- Node.js (bundled with Frappe)
- Redis server
- Frappe bench CLI
- Git with pre-commit hooks support

**Production:**
- Deployment: Frappe bench with:
  - Web server: Gunicorn (via `bench serve`)
  - WebSocket server: Node.js socketio
  - Background workers: Python background job workers
  - Task scheduler: Frappe scheduler
  - Caching: Redis
- Database: MariaDB or MySQL (configured via Frappe)
- File storage: Local filesystem or cloud provider (AWS S3/Cloudflare R2 configured via Memora Settings)

## App Architecture

**Memora Admin App:**
- Type: Frappe custom app
- Package location: `memora_admin` Python package
- Entry point: Frappe document types (DocTypes)
- Config: `pyproject.toml` with app metadata

**Related Apps in Bench:**
- `frappe` - Core framework
- `erpnext` - Enterprise modules
- `memora` - Primary Memora application
- `posawesome` - POS system
- `corex_*` - Custom Corex modules

---

*Stack analysis: 2026-02-01*
