#!/bin/zsh
set -eu

SCRIPT_DIR=${0:A:h}
cd "$SCRIPT_DIR"

exec python3 -m uvicorn app.server:app --host 127.0.0.1 --port "${PI_SWIMLANE_PORT:-8791}"

