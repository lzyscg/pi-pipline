#!/bin/zsh
set -eu

SCRIPT_DIR=${0:A:h}
cd "$SCRIPT_DIR"

PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi
export PI_SWIMLANE_HOST=127.0.0.1
exec "$PYTHON" -m uvicorn app.server:app --host 127.0.0.1 --port "${PI_SWIMLANE_PORT:-8791}"
