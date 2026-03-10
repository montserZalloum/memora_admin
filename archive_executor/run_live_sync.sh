#!/usr/bin/env bash
# Wrapper script for the live sync executor cron job.
# Sources environment variables from /etc/memora-archive.env, then runs
# the live sync executor inside the dedicated venv.
#
# Usage:
#   chmod +x run_live_sync.sh
#   # Add to crontab:
#   5 3 * * * /home/corex/aurevia-bench/apps/memora_admin/archive_executor/run_live_sync.sh
#
# The env file must exist at /etc/memora-archive.env (or override ENV_FILE below).
# See .env.archive.example for required variables.

set -euo pipefail

ENV_FILE="${MEMORA_ARCHIVE_ENV_FILE:-/etc/memora-archive.env}"
VENV_PYTHON="${MEMORA_ARCHIVE_VENV:-/opt/memora-archive/venv/bin/python}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Source environment file
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "[ERROR] env file not found: $ENV_FILE" >&2
    exit 1
fi

# Run executor from app root so archive_executor package is importable
cd "$APP_DIR"
exec "$VENV_PYTHON" -m archive_executor.live_sync
