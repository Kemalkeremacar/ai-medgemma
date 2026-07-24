"""vLLM + MedGemma entegrasyon yardımcıları.

Bu proje bir "RAG service" barındırmıyor. `pipeline/*.py` script'leri, bu paketteki:
- `defaults.py`: varsayılan GPTQ yolu ve endpoint
- `medgemma.env`: makineye özel URL/model/port (çoğu zaman tek düzenleme burada)
- `config.py`: env + medgemma.env → `Settings`
- `vllm_client.py`: OpenAI-uyumlu vLLM HTTP istemcisi

Sunucu: `./serve_medgemma.sh` (aynı `medgemma.env` değerlerini kullanır).
"""
