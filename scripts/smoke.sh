#!/usr/bin/env bash
set -euo pipefail
exec "${PYTHON:-python}" "$(dirname "$0")/smoke.py" "$@"
