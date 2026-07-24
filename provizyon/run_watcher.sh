#!/usr/bin/env bash
# Provizyon Klasör İzleyici — başlat / durdur / durum
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv/bin/python"
PID_FILE="/home/monassist1/GemmaApp/logs/provizyon-watcher.pid"
LOG_FILE="/home/monassist1/GemmaApp/logs/provizyon-watcher.log"
ENV_FILE="$ROOT/config/provizyon.env"

[[ -x "$VENV" ]] || VENV="python3"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a && . "$ENV_FILE" && set +a
fi

cmd="${1:-status}"

case "$cmd" in
  start-foreground)
    exec "$VENV" -m provizyon_engine.intake.watcher
    ;;
  start)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Provizyon watcher zaten çalışıyor (pid $(cat "$PID_FILE"))"
      exit 0
    fi
    mkdir -p "$(dirname "$LOG_FILE")"
    # Log dosyasını Python RotatingFileHandler yönetir.
    nohup "$VENV" -m provizyon_engine.intake.watcher >/dev/null 2>&1 &
    echo $! >"$PID_FILE"
    sleep 1
    echo "Provizyon watcher başlatıldı (pid $(cat "$PID_FILE")) log=$LOG_FILE"
    ;;
  stop)
    if [[ -f "$PID_FILE" ]]; then
      kill "$(cat "$PID_FILE")" 2>/dev/null || true
      rm -f "$PID_FILE"
    fi
    echo "Provizyon watcher durduruldu"
    ;;
  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;
  once)
    exec "$VENV" -m provizyon_engine.intake.watcher --once
    ;;
  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "running pid=$(cat "$PID_FILE")"
    else
      echo "stopped"
    fi
    ;;
  *)
    echo "Kullanım: $0 {start|start-foreground|stop|restart|once|status}"
    exit 1
    ;;
esac
