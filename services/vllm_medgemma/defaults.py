"""MedGemma / vLLM için tek kaynak varsayılanlar.

Üretim varsayılanı: **GPTQ** checkpoint `/raid/monassist1/medgemma_model_gptq_w4`
ve API **http://127.0.0.1:8000**. Makineye özel değerler `medgemma.env` ile tutulur
(ortam değişkeni tanımlıysa o önceliklidir).

Ağırlık dosyaları bu repoda tutulmaz; `DEFAULT_VLLM_MODEL` yerel disk yoludur.
`config.load_settings()` ve `serve_medgemma.sh` bu modülle uyumludur.

GPU: vLLM, uygun CUDA sürücüsü ve GPU varsa varsayılan olarak **CUDA** ile çalışır.
Bu dosyada ayrı bir "GPU açık/kapalı" anahtarı yok; çoğu kurulumda `serve_medgemma.sh`
yeterlidir. Belirli bir karta sabitlemek için ortamda `CUDA_VISIBLE_DEVICES` kullanın
(ör. `CUDA_VISIBLE_DEVICES=0`).
"""

from __future__ import annotations

# İstemci → çalışan API (OpenAI uyumlu)
DEFAULT_VLLM_BASE_URL = "http://127.0.0.1:8000"

# Sunucuda `vllm serve` ile verilen model id (çoğunlukla checkpoint dizini)
DEFAULT_VLLM_MODEL = "/raid/monassist1/medgemma_model_gptq_w4"

DEFAULT_VLLM_TIMEOUT_SECONDS = 120
DEFAULT_ANALYZE_MAX_TOKENS = 900

# `serve_medgemma.sh` içinde kullanılan önerilen venv (vLLM CLI)
DEFAULT_MEDGAMMA_VENV = "/home/monassist1/medgamma_env"

DEFAULT_VLLM_SERVE_HOST = "127.0.0.1"
DEFAULT_VLLM_SERVE_PORT = 8000
