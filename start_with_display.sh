#!/usr/bin/env bash

set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_LOG_FILE="${PROJECT_DIR}/wechat_pay.log"
LOG_FILE="${PROJECT_DIR}/start_with_display.log"
PID_FILE="${PROJECT_DIR}/wechat_pay.pid"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DISPLAY_CANDIDATES=(":1.0" ":1" ":0.0" ":0")
XAUTHORITY_CANDIDATES=("/home/sky/.Xauthority" "/root/.Xauthority")
LOG_MAX_SIZE_MB="${LOG_MAX_SIZE_MB:-20}"
LOG_BACKUP_COUNT="${LOG_BACKUP_COUNT:-5}"

log() {
  printf '[start_with_display] %s\n' "$*"
}

get_log_max_bytes() {
  local size_mb="${LOG_MAX_SIZE_MB:-20}"

  if [[ ! "$size_mb" =~ ^[0-9]+$ ]] || [[ "$size_mb" -le 0 ]]; then
    size_mb=20
  fi

  printf '%s\n' $((size_mb * 1024 * 1024))
}

get_log_backup_count() {
  local backup_count="${LOG_BACKUP_COUNT:-5}"

  if [[ ! "$backup_count" =~ ^[0-9]+$ ]] || [[ "$backup_count" -lt 1 ]]; then
    backup_count=5
  fi

  printf '%s\n' "$backup_count"
}

rotate_log_file() {
  local log_file="$1"
  local max_bytes backup_count file_size index

  [[ -n "$log_file" ]] || return 0
  [[ -f "$log_file" ]] || return 0

  max_bytes="$(get_log_max_bytes)"
  backup_count="$(get_log_backup_count)"
  file_size="$(wc -c < "$log_file" 2>/dev/null || printf '0')"

  [[ "$file_size" =~ ^[0-9]+$ ]] || file_size=0
  if [[ "$file_size" -lt "$max_bytes" ]]; then
    return 0
  fi

  if [[ -f "${log_file}.${backup_count}" ]]; then
    rm -f "${log_file}.${backup_count}"
  fi

  for ((index=backup_count-1; index>=1; index--)); do
    if [[ -f "${log_file}.${index}" ]]; then
      mv -f "${log_file}.${index}" "${log_file}.$((index + 1))"
    fi
  done

  mv -f "$log_file" "${log_file}.1"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [start|stop|restart|status|foreground] [main.py args...]

Commands:
  start       Start service in background (default)
  stop        Stop running service
  restart     Restart service in background
  status      Show service status
  foreground  Run service in foreground
EOF
}

show_menu() {
  printf '\n'
  printf '==============================\n'
  printf ' Linux WeChat Pay Service Menu\n'
  printf '==============================\n'
  printf '1) Start background service\n'
  printf '2) Stop service\n'
  printf '3) Restart service\n'
  printf '4) Show status\n'
  printf '5) Run in foreground\n'
  printf '0) Exit\n'
  printf '\n'
}

run_interactive_menu() {
  local choice

  while true; do
    show_menu
    read -r -p 'Please choose [0-5]: ' choice

    case "$choice" in
      1)
        start_background
        ;;
      2)
        stop_service
        ;;
      3)
        stop_service
        start_background
        ;;
      4)
        show_status
        ;;
      5)
        run_foreground
        return $?
        ;;
      0)
        log 'Exit.'
        return 0
        ;;
      *)
        log 'Invalid choice, please enter 0-5.'
        ;;
    esac

    printf '\n'
    read -r -p 'Press Enter to continue...' _
  done
}

get_main_script() {
  printf '%s/main.py\n' "$PROJECT_DIR"
}

read_pid() {
  [[ -f "$PID_FILE" ]] || return 1

  local pid
  pid="$(tr -d '[:space:]' < "$PID_FILE" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$pid"
}

is_pid_running() {
  local pid="$1"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

remove_pid_file() {
  [[ -f "$PID_FILE" ]] && rm -f "$PID_FILE"
}

is_service_running() {
  local pid
  pid="$(read_pid 2>/dev/null || true)"

  if [[ -z "$pid" ]]; then
    return 1
  fi

  if is_pid_running "$pid"; then
    return 0
  fi

  log "Removing stale PID file: ${PID_FILE}"
  remove_pid_file
  return 1
}

cleanup_old_processes() {
  local main_script pids current_pid
  main_script="$(get_main_script)"
  current_pid="${1:-}"
  pids="$(pgrep -f "$main_script" 2>/dev/null || true)"

  if [[ -z "$pids" ]]; then
    return 0
  fi

  if [[ -n "$current_pid" ]]; then
    pids="$(printf '%s\n' "$pids" | grep -vx "$current_pid" || true)"
    [[ -z "$pids" ]] && return 0
  fi

  log "Found existing main.py process(es): $(printf '%s' "$pids" | tr '\n' ' ')"
  log "Stopping old process(es) before startup..."

  while read -r pid; do
    [[ -n "$pid" ]] || continue
    kill "$pid" 2>/dev/null || true
  done <<< "$pids"

  sleep 1
}

find_display_from_who() {
  who 2>/dev/null | while read -r user _ _ display _; do
    case "$display" in
      "(:"*")")
        display="${display#(}"
        display="${display%)}"
        printf '%s|%s\n' "$user" "$display"
        return 0
        ;;
    esac
  done
}

find_xauthority_for_user() {
  local user="$1"

  if [[ -z "$user" ]]; then
    return 1
  fi

  if [[ "$user" == "root" ]]; then
    [[ -f /root/.Xauthority ]] && printf '/root/.Xauthority\n' && return 0
    return 1
  fi

  local home_dir
  home_dir="$(getent passwd "$user" | cut -d: -f6 2>/dev/null)"
  [[ -n "$home_dir" && -f "$home_dir/.Xauthority" ]] && printf '%s/.Xauthority\n' "$home_dir" && return 0
  return 1
}

use_display_candidate() {
  local candidate="$1"

  [[ -z "$candidate" ]] && return 1

  export DISPLAY="$candidate"
  log "Trying fallback DISPLAY=${DISPLAY}"
  return 0
}

ensure_xauthority_hint() {
  if [[ -n "${XAUTHORITY:-}" && -f "${XAUTHORITY}" ]]; then
    return 0
  fi

  local candidate
  for candidate in "${XAUTHORITY_CANDIDATES[@]}" "$HOME/.Xauthority"; do
    if [[ -f "$candidate" ]]; then
      export XAUTHORITY="$candidate"
      log "Using fallback XAUTHORITY=${XAUTHORITY}"
      return 0
    fi
  done

  return 1
}

ensure_display_context() {
  if [[ -n "${DISPLAY:-}" ]]; then
    log "Using existing DISPLAY=${DISPLAY}"
    if [[ -n "${XAUTHORITY:-}" ]]; then
      log "Using existing XAUTHORITY=${XAUTHORITY}"
    else
      ensure_xauthority_hint || true
    fi
    return 0
  fi

  local detected detected_user detected_display detected_xauth candidate
  detected="$(find_display_from_who | head -n 1)"

  if [[ -n "$detected" ]]; then
    detected_user="${detected%%|*}"
    detected_display="${detected##*|}"
    detected_xauth="$(find_xauthority_for_user "$detected_user")"

    export DISPLAY="$detected_display"
    if [[ -n "$detected_xauth" ]]; then
      export XAUTHORITY="$detected_xauth"
    else
      ensure_xauthority_hint || true
    fi

    log "Detected desktop session user=${detected_user} DISPLAY=${DISPLAY}"
    if [[ -n "${XAUTHORITY:-}" ]]; then
      log "Detected XAUTHORITY=${XAUTHORITY}"
    else
      log "WARNING: Could not auto-detect XAUTHORITY for user ${detected_user}"
    fi
    return 0
  fi

  for candidate in "${DISPLAY_CANDIDATES[@]}"; do
    use_display_candidate "$candidate"
    ensure_xauthority_hint || true
    return 0
  done

  log "ERROR: No desktop session detected from 'who', and no fallback DISPLAY could be applied."
  log "Please run this script inside the logged-in desktop user session, or export DISPLAY/XAUTHORITY manually."
  return 1
}

check_python() {
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    log "ERROR: Python interpreter not found: ${PYTHON_BIN}"
    return 1
  fi

  log "Using Python: $(command -v "$PYTHON_BIN")"
}

check_screen_capture() {
  "$PYTHON_BIN" - <<'PY'
from PIL import ImageGrab

try:
    image = ImageGrab.grab()
    print(f"SCREEN_CHECK_OK size={image.size}")
except Exception as exc:
    print(f"SCREEN_CHECK_FAILED {exc}")
    raise
PY
}

check_ocr_runtime() {
  cd "$PROJECT_DIR" || return 1

  "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import hashlib
import re
import sys

project_dir = Path('.')
config_path = project_dir / 'config.yaml'
scanner_path = project_dir / 'ocr_scanner.py'

if not config_path.exists():
    print('OCR_CHECK_FAILED missing config.yaml')
    raise SystemExit(1)

if not scanner_path.exists():
    print('OCR_CHECK_FAILED missing ocr_scanner.py')
    raise SystemExit(1)

config_text = config_path.read_text(encoding='utf-8')
scanner_text = scanner_path.read_text(encoding='utf-8')
scanner_hash = hashlib.sha1(scanner_text.encode('utf-8')).hexdigest()[:12]

engine_match = re.search(r'engine:\s*"?([a-zA-Z0-9_]+)"?', config_text)
engine = engine_match.group(1) if engine_match else 'unknown'

errors = []

if 'def _extract_payment_info' in scanner_text and 'self.STRONG_AMOUNT_PATTERNS' in scanner_text and 'STRONG_AMOUNT_PATTERNS =' not in scanner_text:
    errors.append('ocr_scanner.py uses STRONG_AMOUNT_PATTERNS but definition is missing')

if '累计金额' not in scanner_text:
    errors.append('ocr_scanner.py does not contain accumulated-amount exclusion rules')

if engine == 'easyocr':
    try:
        import easyocr  # noqa: F401
    except Exception:
        errors.append('config engine=easyocr but easyocr is not installed')

if engine == 'tesseract':
    try:
        import pytesseract  # noqa: F401
    except Exception:
        errors.append('config engine=tesseract but pytesseract is not installed')

if errors:
    print('OCR_CHECK_FAILED')
    print(f'- file={scanner_path}')
    print(f'- sha1={scanner_hash}')
    for item in errors:
        print(f'- {item}')
    raise SystemExit(1)

print(f'OCR_CHECK_OK engine={engine} sha1={scanner_hash}')
PY
}

check_dbus_runtime() {
  cd "$PROJECT_DIR" || return 1

  "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import re
import shutil

project_dir = Path('.')
config_path = project_dir / 'config.yaml'

if not config_path.exists():
    print('DBUS_CHECK_FAILED missing config.yaml')
    raise SystemExit(1)

config_text = config_path.read_text(encoding='utf-8')
enabled_match = re.search(r'dbus:\s*(?:\n|\r\n)(?:[ \t]+.*\n|\r\n)*?[ \t]+enabled:\s*(true|false)', config_text, re.IGNORECASE)
dbus_enabled = enabled_match.group(1).lower() == 'true' if enabled_match else False

if not dbus_enabled:
    print('DBUS_CHECK_SKIPPED dbus.enabled=false')
    raise SystemExit(0)

errors = []
if shutil.which('dbus-monitor') is None:
    errors.append('dbus-monitor command not found')

if not __import__('os').environ.get('DBUS_SESSION_BUS_ADDRESS'):
    errors.append('DBUS_SESSION_BUS_ADDRESS is empty')

try:
    import dbus  # noqa: F401
    from dbus.mainloop.glib import DBusGMainLoop  # noqa: F401
    from gi.repository import GLib  # noqa: F401
except Exception as exc:
    errors.append(f'python dbus runtime unavailable: {exc}')

if errors:
    print('DBUS_CHECK_FAILED')
    for item in errors:
        print(f'- {item}')
    raise SystemExit(1)

print('DBUS_CHECK_OK enabled=true')
PY
}

start_app() {
  cd "$PROJECT_DIR" || return 1

  printf '%s\n' "$$" > "$PID_FILE"

  log "Starting Linux WeChat Pay system..."
  log "Project directory: ${PROJECT_DIR}"
  log "Launcher log file: ${LOG_FILE}"
  log "Application log file: ${APP_LOG_FILE}"
  log "PID file: ${PID_FILE}"

  exec "$PYTHON_BIN" main.py "$@"
}

start_background() {
  if is_service_running; then
    log "Service is already running with PID $(read_pid)"
    return 0
  fi

  remove_pid_file
  log "Starting service in background..."
  rotate_log_file "$LOG_FILE"
  log "Background log: ${LOG_FILE}"
  log "Application log: ${APP_LOG_FILE}"

  APP_LOG_FILE="$APP_LOG_FILE" WECHAT_PAY_DISABLE_STREAM_LOG=1 nohup "$0" foreground "$@" >> "$LOG_FILE" 2>&1 &
  local child_pid=$!
  sleep 2

  if is_pid_running "$child_pid"; then
    log "Service started in background, launcher PID=${child_pid}"
    log "Use '$(basename "$0") status' to check status"
    return 0
  fi

  log "ERROR: Failed to start service in background. Check ${LOG_FILE}"
  return 1
}

stop_service() {
  local pid waited

  if ! is_service_running; then
    log "Service is not running"
    remove_pid_file
    return 0
  fi

  pid="$(read_pid)"
  log "Stopping service PID=${pid}"
  kill "$pid" 2>/dev/null || true

  waited=0
  while is_pid_running "$pid" && [[ "$waited" -lt 10 ]]; do
    sleep 1
    waited=$((waited + 1))
  done

  if is_pid_running "$pid"; then
    log "Process did not exit in time, sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
  fi

  remove_pid_file
  log "Service stopped"
}

show_status() {
  if is_service_running; then
    log "Service is running with PID $(read_pid)"
    log "Launcher log file: ${LOG_FILE}"
    log "Application log file: ${APP_LOG_FILE}"
    return 0
  fi

  log "Service is not running"
  return 1
}

run_foreground() {
  check_python || exit 1
  cleanup_old_processes "$$" || exit 1
  ensure_display_context || exit 1

  log "Running screen capture pre-check..."
  if ! check_screen_capture; then
    log "ERROR: Screen capture pre-check failed."
    log "Current DISPLAY=${DISPLAY:-<empty>} XAUTHORITY=${XAUTHORITY:-<empty>}"
    log "Please make sure this script runs inside the desktop session that shows WeChat."
    exit 1
  fi

  log "Skipping OCR runtime self-check (OCR disabled in current payment flow)."

  log "Running DBus runtime self-check..."
  if ! check_dbus_runtime; then
    log "ERROR: DBus runtime self-check failed."
    log "If you only want OCR fallback, set dbus.enabled=false in config.yaml."
    exit 1
  fi

  start_app "$@"
}

main() {
  local command="${1:-}"

  if [[ -z "$command" ]]; then
    run_interactive_menu
    return $?
  fi

  case "$command" in
    start)
      shift || true
      start_background "$@"
      ;;
    stop)
      stop_service
      ;;
    restart)
      shift || true
      stop_service
      start_background "$@"
      ;;
    status)
      show_status
      ;;
    foreground)
      shift || true
      run_foreground "$@"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      start_background "$@"
      ;;
  esac
}

main "$@"
