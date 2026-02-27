# Repository Guidelines

## Project Structure & Module Organization
- `memora_admin/`: Frappe app entrypoint (hooks, tasks, public assets, API glue).
- `memora_admin/memora_admin/`: core domain package (DocTypes under `doctype/`, reports, services, and app-level tests in `tests/`).
- `fastapi_app/`: FastAPI sidecar (`api/`, `services/`, `models/`, `core/`, `middleware/`, `tests/`).
- `docs/`: operational and feature documentation.
- `specs/`: implementation specs and task plans used during delivery.

## Build, Test, and Development Commands
- `pre-commit install`: enable local quality gates.
- `pre-commit run --all-files`: run Ruff, formatting, ESLint, Prettier, and basic file checks.
- `python3 -m pytest fastapi_app/tests/ -v`: run FastAPI test suite.
- `bench --site <site> run-tests --app memora_admin`: run Frappe/DocType test suite.
- `bench --site <site> run-tests --app memora_admin --module memora_admin.tests.test_sync_wallets`: run one Frappe test module.
- `uvicorn fastapi_app.main:app --host 127.0.0.1 --port 8002 --reload`: run FastAPI locally.

## Coding Style & Naming Conventions
- Python/JS/HTML/CSS use tabs for indentation (`.editorconfig`); JSON uses 2 spaces.
- Ruff is the primary Python linter/formatter (`[tool.ruff]` in `pyproject.toml`), with double quotes preferred.
- Keep naming explicit and domain-based: `*_service.py`, `test_*.py`, endpoint files by feature (`profile.py`, `voucher.py`).
- Follow existing module boundaries: API layer in `api/`, business logic in `services/`, schema/models in `models/`.

## Testing Guidelines
- FastAPI tests use `pytest` (+ `pytest-asyncio`) in `fastapi_app/tests`.
- Frappe tests use `FrappeTestCase` and run through `bench run-tests`.
- Use `test_*.py` naming and keep tests close to the subsystem they validate.
- Useful markers: `slow`, `integration`, `characterization` (see `pyproject.toml`).
- Security audit flow (project-specific):
  - `bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.tests.test_security_audit`
  - `pkill -f "uvicorn fastapi_app.main:app"` then wait 2-3s and verify: `curl http://127.0.0.1:8002/api/v1/health/live`
  - `bench restart` (required after `voucher.py` worker-side changes)

## Commit & Pull Request Guidelines
- Prefer Conventional Commit style seen in history: `feat(scope): ...`, `fix(scope): ...`, `refactor(scope): ...`.
- Avoid placeholder commit subjects (for example `--`); use clear, imperative summaries.
- PRs should include:
  - concise problem/solution description,
  - linked issue/spec when available,
  - test evidence (exact commands run),
  - screenshots/video for UI changes (`memora_admin/public/js` or Desk pages).

## Security & Configuration Tips
- Copy from `.env.example`; do not commit secrets from `.env`.
- Redis and site config changes must be documented in PR notes when touching caching/sync paths.
