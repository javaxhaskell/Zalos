#!/usr/bin/env bash
# AgentForge durable local dev server — survives terminal / agent session exit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="${AGENTFORGE_DEV_PIDFILE:-/tmp/agentforge-dev.pid}"
LOGFILE="${AGENTFORGE_DEV_LOGFILE:-/tmp/agentforge-dev.log}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"

if command -v pnpm >/dev/null 2>&1; then
  NODE_PM=pnpm
else
  NODE_PM=npm
fi

port_pids() {
  local port="$1"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null | sort -u || true
}

daemon_pid() {
  if [[ -f "$PIDFILE" ]]; then
    cat "$PIDFILE"
  fi
}

daemon_alive() {
  local pid
  pid="$(daemon_pid || true)"
  [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null
}

stop_port_listeners() {
  local port="$1"
  local pids
  pids="$(port_pids "$port")"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null || true
    sleep 1
    pids="$(port_pids "$port")"
    if [[ -n "$pids" ]]; then
      # shellcheck disable=SC2086
      kill -KILL $pids 2>/dev/null || true
    fi
  fi
}

stop_children() {
  local pid="$1"
  local child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    stop_children "$child"
    kill -TERM "$child" 2>/dev/null || true
  done
}

cmd_start() {
  if daemon_alive; then
    echo "AgentForge dev daemon already running (PID $(daemon_pid), log: $LOGFILE)"
    return 0
  fi

  rm -f "$PIDFILE"

  local api_pids web_pids
  api_pids="$(port_pids "$API_PORT")"
  web_pids="$(port_pids "$WEB_PORT")"
  if [[ -n "$api_pids" || -n "$web_pids" ]]; then
    echo "Ports ${API_PORT}/${WEB_PORT} already in use. Stop them first: make down"
    exit 1
  fi

  if [[ ! -f "$ROOT/.env" ]]; then
    echo "Missing $ROOT/.env — run: cp .env.example .env"
    exit 1
  fi

  mkdir -p "$(dirname "$LOGFILE")"
  : >>"$LOGFILE"

  echo "Starting AgentForge dev daemon (log: $LOGFILE) ..."
  nohup env ROOT="$ROOT" NODE_PM="$NODE_PM" API_PORT="$API_PORT" WEB_PORT="$WEB_PORT" \
    bash "$ROOT/scripts/dev-daemon-inner.sh" >>"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"

  sleep 2
  if ! daemon_alive; then
    echo "Daemon failed to start. Last log lines:"
    tail -n 40 "$LOGFILE" || true
    rm -f "$PIDFILE"
    exit 1
  fi

  echo "AgentForge dev daemon started (PID $(daemon_pid))"
  echo "  API  http://127.0.0.1:${API_PORT}/health"
  echo "  Web  http://127.0.0.1:${WEB_PORT}/"
  echo "  Logs tail -f $LOGFILE"
}

cmd_stop() {
  local pid
  pid="$(daemon_pid || true)"

  if [[ -n "${pid:-}" ]]; then
    stop_children "$pid"
    kill -TERM "$pid" 2>/dev/null || true
    sleep 1
    kill -KILL "$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
  fi

  stop_port_listeners "$API_PORT"
  stop_port_listeners "$WEB_PORT"

  echo "AgentForge dev daemon stopped"
}

cmd_status() {
  if daemon_alive; then
    echo "running  PID=$(daemon_pid)  log=$LOGFILE"
  else
    echo "stopped  (no live daemon PID)"
    rm -f "$PIDFILE" 2>/dev/null || true
  fi

  local api_pids web_pids
  api_pids="$(port_pids "$API_PORT")"
  web_pids="$(port_pids "$WEB_PORT")"
  echo "api:${API_PORT}  listeners=${api_pids:-none}"
  echo "web:${WEB_PORT}  listeners=${web_pids:-none}"
}

cmd_health() {
  local api_code web_code
  api_code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${API_PORT}/health" 2>/dev/null || echo "000")"
  web_code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${WEB_PORT}/" 2>/dev/null || echo "000")"
  echo "API http://127.0.0.1:${API_PORT}/health -> HTTP ${api_code}"
  echo "Web http://127.0.0.1:${WEB_PORT}/ -> HTTP ${web_code}"
  if [[ "$api_code" == "200" && "$web_code" == "200" ]]; then
    return 0
  fi
  return 1
}

cmd_logs() {
  tail -f "$LOGFILE"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") {start|stop|status|health|logs}

  start   migrate + API + web in background (nohup)
  stop    stop daemon and free ports ${API_PORT}/${WEB_PORT}
  status  show daemon PID and port listeners
  health  curl API /health and web /
  logs    tail -f $LOGFILE
EOF
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  health) cmd_health ;;
  logs) cmd_logs ;;
  *)
    usage
    exit 1
    ;;
esac
