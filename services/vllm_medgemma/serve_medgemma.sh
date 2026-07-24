#!/usr/bin/env bash
# GPTQ MedGemma — `medgemma.env` host/port + isteğe bağlı VLLM_SERVE_EXTRA_ARGS
# Ortam değişkeni > bu dizindeki medgemma.env > defaults.py (Python)
# vLLM varsayılanı: CUDA GPU. Kart: CUDA_VISIBLE_DEVICES=0
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/medgemma.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/medgemma.env"
  set +a
fi
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$ROOT"

_VENV="${MEDGAMMA_VENV:-/home/monassist1/medgamma_env}"
_PY="${_VENV}/bin/python3"
[[ -x "$_PY" ]] || _PY=python3

mapfile -t _DEF < <("$_PY" -c "
from services.vllm_medgemma.defaults import (
    DEFAULT_MEDGAMMA_VENV,
    DEFAULT_VLLM_MODEL,
    DEFAULT_VLLM_SERVE_HOST,
    DEFAULT_VLLM_SERVE_PORT,
)
for x in (
    DEFAULT_MEDGAMMA_VENV,
    DEFAULT_VLLM_MODEL,
    DEFAULT_VLLM_SERVE_HOST,
    str(DEFAULT_VLLM_SERVE_PORT),
):
    print(x)
")

VENV="${MEDGAMMA_VENV:-${_DEF[0]}}"
MODEL="${VLLM_MODEL:-${_DEF[1]}}"
HOST="${VLLM_SERVE_HOST:-${_DEF[2]}}"
PORT="${VLLM_SERVE_PORT:-${_DEF[3]}}"
_SERVE_EXTRA="${VLLM_SERVE_EXTRA_ARGS-}"

# medgemma.env içindeki VLLM_BASE_URL vb. bizim Pipeline ayarlarıdır; vLLM 0.19+
# bilinmeyen VLLM_* ortamında uyarı verir. Süreçe geçmeden temizle.
unset VLLM_BASE_URL VLLM_MODEL VLLM_TIMEOUT_SECONDS \
  VLLM_SERVE_EXTRA_ARGS VLLM_SERVE_HOST VLLM_SERVE_PORT || true

# Belleği düşürmek için `medgemma.env` içinde örn.:
# VLLM_SERVE_EXTRA_ARGS="--max-model-len 16384 --gpu-memory-utilization 0.88"
# shellcheck disable=SC2086
exec "${VENV}/bin/vllm" serve "${MODEL}" --host "${HOST}" --port "${PORT}" ${_SERVE_EXTRA} "$@"
