#!/usr/bin/env bash
# run_analytics_export.sh — wrapper for analytics_exporter CLI.
#
# Loads environment variables from .env if present, then invokes
#   python3 -m analytics_exporter
#
# Suitable for cron or supervisor invocation from the repo root.
#
# Usage:
#   ./run_analytics_export.sh                     # full/auto export
#   ANALYTICS_DATASETS=practice_log ./run_analytics_export.sh
#   ANALYTICS_MODE=full ./run_analytics_export.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env if it exists alongside this script
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "${SCRIPT_DIR}/.env"
    set +a
fi

exec python3 -m analytics_exporter "$@"
