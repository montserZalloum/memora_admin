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

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **memora_admin** (19503 symbols, 32742 relationships, 293 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/memora_admin/context` | Codebase overview, check index freshness |
| `gitnexus://repo/memora_admin/clusters` | All functional areas |
| `gitnexus://repo/memora_admin/processes` | All execution flows |
| `gitnexus://repo/memora_admin/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
