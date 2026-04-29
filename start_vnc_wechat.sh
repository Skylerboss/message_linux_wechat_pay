#!/usr/bin/env bash

set -u

VNC_USER="${VNC_USER:-sky}"
VNC_DISPLAY="${VNC_DISPLAY:-:1}"
VNC_GEOMETRY="${VNC_GEOMETRY:-1280x720}"
VNC_DEPTH="${VNC_DEPTH:-24}"
VNC_PASSWORD_FILE="${VNC_PASSWORD_FILE:-/home/sky/.vnc/passwd}"
XAUTHORITY_FILE="${XAUTHORITY_FILE:-/home/sky/.Xauthority}"
DBUS_SESSION_BUS_ADDRESS_VALUE="${DBUS_SESSION_BUS_ADDRESS_VALUE:-unix:path=/run/user/1000/bus}"
NOTIFY_DAEMON_BIN="${NOTIFY_DAEMON_BIN:-/usr/lib/notification-daemon/notification-daemon}"
WECHAT_BIN="${WECHAT_BIN:-/opt/wechat/wechat}"
VNC_HOME="${VNC_HOME:-/home/sky}"

log() {
  printf '[start_vnc_wechat] %s\n' "$*"
}

run_as_user() {
  sudo -u "$VNC_USER" env \
    HOME="$VNC_HOME" \
    USER="$VNC_USER" \
    LOGNAME="$VNC_USER" \
    DISPLAY="$VNC_DISPLAY" \
    XAUTHORITY="$XAUTHORITY_FILE" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS_VALUE" \
    bash -c "$1"
}

ensure_vnc_password() {
  if [[ ! -f "$VNC_PASSWORD_FILE" ]]; then
    log "ERROR: VNC password file not found: $VNC_PASSWORD_FILE"
    return 1
  fi
}

restart_vnc() {
  log "Restarting TightVNC on $VNC_DISPLAY"
  run_as_user "tightvncserver -kill $VNC_DISPLAY >/dev/null 2>&1 || true"
  run_as_user "tightvncserver $VNC_DISPLAY -geometry $VNC_GEOMETRY -depth $VNC_DEPTH"
}

ensure_notification_daemon() {
  if [[ ! -x "$NOTIFY_DAEMON_BIN" ]]; then
    log "WARNING: notification-daemon not found: $NOTIFY_DAEMON_BIN"
    return 0
  fi

  log "Ensuring notification-daemon is running"
  run_as_user "pgrep -f '$NOTIFY_DAEMON_BIN' >/dev/null || nohup '$NOTIFY_DAEMON_BIN' >/tmp/notification-daemon.log 2>&1 &"
}

ensure_wechat_running() {
  if [[ ! -x "$WECHAT_BIN" ]]; then
    log "WARNING: WeChat binary not found: $WECHAT_BIN"
    return 0
  fi

  log "Ensuring WeChat is running"
  run_as_user "pgrep -f '$WECHAT_BIN' >/dev/null || nohup '$WECHAT_BIN' >/tmp/wechat-linux.log 2>&1 &"
}

show_status() {
  log "Current VNC processes:"
  ps -ef | grep -E 'Xtightvnc|Xvnc|tightvncserver' | grep -v grep || true
  log "Current WeChat processes:"
  ps -ef | grep -i wechat | grep -v grep || true
}

main() {
  ensure_vnc_password || exit 1
  restart_vnc || exit 1
  ensure_notification_daemon
  ensure_wechat_running
  show_status
  log "Done. You can now connect VNC to ${VNC_DISPLAY#*:} (usually port 590${VNC_DISPLAY#*:})."
}

main "$@"
