#!/usr/bin/env bash
# Servis (systemd) için hafif başlatıcı: bağımlılık KURMAZ, sadece uvicorn'u çalıştırır.
# Kurulum için önce ./run.sh bir kez çalıştırılmış (venv hazır) olmalıdır.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

VENV="${GATEWAY_VENV:-$HERE/.venv}"
ENV_FILE="$HERE/gateway.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

HOST="${GATEWAY_HOST:-0.0.0.0}"
PORT="${GATEWAY_PORT:-8080}"

exec "$VENV/bin/uvicorn" app:app --host "$HOST" --port "$PORT"
