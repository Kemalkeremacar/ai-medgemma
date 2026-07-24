"""Pipeline'ın vLLM bağlantı ayarlarını yükler.

Script'ler (StageA/StageB) çalışan vLLM server'a HTTP ile bağlanır.
`medgemma.env` (bu paket dizininde) varsa ve ilgili anahtar OS'ta yoksa oradan
yüklenir; sonra ortam değişkenleri okunur. Öncelik: **ortam > medgemma.env > defaults.py**.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from .defaults import (
    DEFAULT_ANALYZE_MAX_TOKENS,
    DEFAULT_VLLM_BASE_URL,
    DEFAULT_VLLM_MODEL,
    DEFAULT_VLLM_TIMEOUT_SECONDS,
)


def _apply_medgemma_env_file() -> None:
    """Yalnızca henüz tanımlı olmayan anahtarları `medgemma.env` ile doldurur."""

    p = Path(__file__).resolve().parent / "medgemma.env"
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


class Settings(BaseModel):
    """Runtime ayarları (env → Settings)."""

    # vLLM / OpenAI-uyumlu endpoint (örn: http://127.0.0.1:8000)
    vllm_base_url: str = DEFAULT_VLLM_BASE_URL

    # vLLM server tarafındaki model adı/path'i.
    vllm_model: str = DEFAULT_VLLM_MODEL

    # HTTP timeout (saniye). Uzun JSON üretiminde daha uzun gerekebilir.
    vllm_timeout_seconds: int = DEFAULT_VLLM_TIMEOUT_SECONDS

    # LLM completion token bütçesi (uzun JSON için).
    analyze_max_tokens: int = DEFAULT_ANALYZE_MAX_TOKENS


def load_settings() -> Settings:
    _apply_medgemma_env_file()

    def getenv(name: str, default: str | None = None) -> str | None:
        v = os.getenv(name)
        return v if v is not None else default

    def getint(name: str, default: int) -> int:
        v = os.getenv(name)
        if v is None or v.strip() == "":
            return default
        return int(v)

    # Ortam / medgemma.env anahtarları:
    # - VLLM_BASE_URL
    # - VLLM_MODEL
    # - VLLM_TIMEOUT_SECONDS
    # - ANALYZE_MAX_TOKENS
    return Settings(
        vllm_base_url=getenv("VLLM_BASE_URL", DEFAULT_VLLM_BASE_URL)
        or DEFAULT_VLLM_BASE_URL,
        vllm_model=getenv("VLLM_MODEL", DEFAULT_VLLM_MODEL) or DEFAULT_VLLM_MODEL,
        vllm_timeout_seconds=getint("VLLM_TIMEOUT_SECONDS", DEFAULT_VLLM_TIMEOUT_SECONDS),
        analyze_max_tokens=getint("ANALYZE_MAX_TOKENS", DEFAULT_ANALYZE_MAX_TOKENS),
    )

