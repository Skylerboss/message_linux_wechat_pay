#!/usr/bin/env bash

set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() {
  printf '[start_dbus_only] %s\n' "$*"
}

main() {
  cd "$PROJECT_DIR" || exit 1

  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    log "ERROR: Python interpreter not found: ${PYTHON_BIN}"
    exit 1
  fi

  if ! command -v dbus-monitor >/dev/null 2>&1; then
    log "ERROR: dbus-monitor command not found"
    exit 1
  fi

  log "Using Python: $(command -v "$PYTHON_BIN")"
  log "DISPLAY=${DISPLAY:-<empty>}"
  log "DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-<empty>}"

  if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
    log "ERROR: DBUS_SESSION_BUS_ADDRESS is empty"
    exit 1
  fi

  log "Starting DBus listener only..."
  exec "$PYTHON_BIN" dbus_listener.py
}

main "$@"
