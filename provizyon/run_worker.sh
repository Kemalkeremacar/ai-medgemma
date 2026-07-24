#!/usr/bin/env bash
# Provizyon Orkestratör Worker — başlat / durdur / durum (çoklu worker destekli)
#
# Kullanım:
#   ./run_worker.sh start [id]        # tek worker başlat (id varsayılan 1)
#   ./run_worker.sh stop [id|all]     # bir worker'ı veya tümünü durdur
#   ./run_worker.sh restart [id]
#   ./run_worker.sh status            # tüm worker'ların durumu
#   ./run_worker.sh scale <N>         # tam olarak N worker çalışır hale getir
#   ./run_worker.sh start-foreground [id]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv/bin/python"
LOG_DIR="/home/monassist1/GemmaApp/logs"
ENV_FILE="$ROOT/config/provizyon.env"

[[ -x "$VENV" ]] || VENV="python3"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a && . "$ENV_FILE" && set +a
fi

pid_file() { echo "$LOG_DIR/provizyon-worker-$1.pid"; }
log_file() { echo "$LOG_DIR/provizyon-worker-$1.log"; }

is_running() {
  local pf; pf="$(pid_file "$1")"
  [[ -f "$pf" ]] && kill -0 "$(cat "$pf")" 2>/dev/null
}

running_ids() {
  shopt -s nullglob
  for pf in "$LOG_DIR"/provizyon-worker-*.pid; do
    local id; id="$(basename "$pf" .pid)"; id="${id#provizyon-worker-}"
    if is_running "$id"; then echo "$id"; fi
  done
}

start_one() {
  local id="$1"
  if is_running "$id"; then
    echo "Worker $id zaten çalışıyor (pid $(cat "$(pid_file "$id")"))"
    return 0
  fi
  mkdir -p "$LOG_DIR"
  # Log dosyasını Python RotatingFileHandler yönetir (shell >> yok → çift yazım yok).
  # Eski şişkin log varsa bir kez arşivle.
  local lf; lf="$(log_file "$id")"
  if [[ -f "$lf" ]] && [[ "$(stat -c%s "$lf" 2>/dev/null || echo 0)" -gt 2097152 ]]; then
    mv -f "$lf" "${lf}.1" 2>/dev/null || true
  fi
  rm -f "${lf}".legacy.* "$LOG_DIR/provizyon-worker-${id}.console.log" 2>/dev/null || true
  # Uygulama logunu RotatingFileHandler yazar; stdout/stderr'i yut (çift dosya yok).
  PROVIZYON_WORKER_ID="$id" nohup "$VENV" -m provizyon_engine.worker \
    >/dev/null 2>&1 &
  echo $! >"$(pid_file "$id")"
  sleep 1
  echo "Worker $id başlatıldı (pid $(cat "$(pid_file "$id")")) log=$lf"
}

stop_one() {
  local id="$1" pf; pf="$(pid_file "$id")"
  if [[ -f "$pf" ]]; then
    kill "$(cat "$pf")" 2>/dev/null || true
    rm -f "$pf"
    echo "Worker $id durduruldu"
  else
    echo "Worker $id zaten durmuş"
  fi
}

cmd="${1:-status}"
arg="${2:-1}"

case "$cmd" in
  start-foreground)
    export PROVIZYON_WORKER_ID="$arg"
    exec "$VENV" -m provizyon_engine.worker
    ;;
  start)
    start_one "$arg"
    ;;
  stop)
    if [[ "$arg" == "all" ]]; then
      for id in $(running_ids); do stop_one "$id"; done
      # PID dosyası kalmış ama süreç ölmüşse temizle
      shopt -s nullglob
      for pf in "$LOG_DIR"/provizyon-worker-*.pid; do rm -f "$pf"; done
      echo "Tüm worker'lar durduruldu"
    else
      stop_one "$arg"
    fi
    ;;
  restart)
    stop_one "$arg"
    sleep 1
    start_one "$arg"
    ;;
  scale)
    n="$arg"
    if ! [[ "$n" =~ ^[0-9]+$ ]] || [[ "$n" -lt 0 ]]; then
      echo "scale için pozitif tamsayı verin: $0 scale <N>"; exit 1
    fi
    # Fazla worker'ları durdur (id > N)
    for id in $(running_ids); do
      if [[ "$id" =~ ^[0-9]+$ ]] && [[ "$id" -gt "$n" ]]; then stop_one "$id"; fi
    done
    # Eksikleri başlat (1..N)
    for ((i=1; i<=n; i++)); do start_one "$i"; done
    echo "Ölçeklendi: $n worker hedeflendi"
    ;;
  status)
    found=0
    for id in $(running_ids); do
      echo "running id=$id pid=$(cat "$(pid_file "$id")")"
      found=1
    done
    if [[ "$found" -eq 0 ]]; then echo "stopped"; fi
    ;;
  *)
    echo "Kullanım: $0 {start [id]|stop [id|all]|restart [id]|scale N|status|start-foreground [id]}"
    exit 1
    ;;
esac
