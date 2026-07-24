#!/usr/bin/env bash
# MedGemma Gateway başlatma scripti.
# Kendi sanal ortamını (venv) oluşturur, bağımlılıkları kurar ve servisi çalıştırır.
# Mevcut projeye/venv'lere dokunmaz — tamamen bağımsızdır.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

VENV="${GATEWAY_VENV:-$HERE/.venv}"
ENV_FILE="$HERE/gateway.env"

# gateway.env varsa yükle.
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "UYARI: $ENV_FILE bulunamadı. gateway.env.example dosyasını kopyalayın."
fi

# venv kur.
if [[ ! -d "$VENV" ]]; then
  echo "Sanal ortam oluşturuluyor: $VENV"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet -r "$HERE/requirements.txt"

HOST="${GATEWAY_HOST:-0.0.0.0}"
PORT="${GATEWAY_PORT:-8080}"

if [[ -z "${GATEWAY_API_KEY:-}" ]]; then
  echo "UYARI: GATEWAY_API_KEY boş. Servis 503 dönecek; gateway.env içine bir anahtar girin."
fi

echo "MedGemma Gateway başlıyor → http://$HOST:$PORT (upstream: ${GATEWAY_UPSTREAM_URL:-http://127.0.0.1:8000/v1})"
exec "$VENV/bin/uvicorn" app:app --host "$HOST" --port "$PORT"
