#!/usr/bin/env bash
# Provizyon Orkestratör API — başlat / durdur / durum
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv/bin/python"
APP_DIR="$ROOT"
PID_FILE="/home/monassist1/GemmaApp/logs/provizyon-api.pid"
LOG_FILE="/home/monassist1/GemmaApp/logs/provizyon-api.log"
ENV_FILE="$ROOT/config/provizyon.env"
PORT="${PROVIZYON_API_PORT:-8020}"

[[ -x "$VENV" ]] || VENV="python3"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a && . "$ENV_FILE" && set +a
fi

cmd="${1:-status}"

case "$cmd" in
  start-foreground)
    exec "$VENV" -m uvicorn provizyon_engine.api:app --app-dir "$APP_DIR" --host 0.0.0.0 --port "$PORT"
    ;;
  start)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Provizyon API zaten çalışıyor (pid $(cat "$PID_FILE"))"
      exit 0
    fi
    mkdir -p "$(dirname "$LOG_FILE")"
    # Şişkin access log'u tek yedekle döndür (legacy birikmesin)
    if [[ -f "$LOG_FILE" ]] && [[ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 524288 ]]; then
      mv -f "$LOG_FILE" "${LOG_FILE}.1" 2>/dev/null || true
    fi
    rm -f "${LOG_FILE}".legacy.* 2>/dev/null || true
    nohup "$VENV" -m uvicorn provizyon_engine.api:app \
      --app-dir "$APP_DIR" --host 0.0.0.0 --port "$PORT" \
      >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    sleep 1
    curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && echo "Provizyon API başlatıldı :$PORT (pid $(cat "$PID_FILE"))"
    ;;
  stop)
    if [[ -f "$PID_FILE" ]]; then
      kill "$(cat "$PID_FILE")" 2>/dev/null || true
      rm -f "$PID_FILE"
    fi
    fuser -k "$PORT/tcp" 2>/dev/null || true
    echo "Provizyon API durduruldu"
    ;;
  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;
  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "running pid=$(cat "$PID_FILE")"
      curl -s "http://127.0.0.1:$PORT/health" | python3 -m json.tool 2>/dev/null || true
    else
      echo "stopped"
    fi
    ;;
  *)
    echo "Kullanım: $0 {start|start-foreground|stop|restart|status}"
    exit 1
    ;;
esac
