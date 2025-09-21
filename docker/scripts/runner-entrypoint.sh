#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  if [ -n "${VNC_PID:-}" ]; then
    kill "$VNC_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [ "${RUNNER_VNC_LEGACY:-0}" = "1" ]; then
  /usr/local/bin/vnc-start.sh &
  VNC_PID=$!
fi

exec python -m camoufox_runner
