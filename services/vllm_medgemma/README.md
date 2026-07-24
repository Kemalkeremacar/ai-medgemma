# vllm_medgemma

Bu dizin **model ağırlıklarını** içermez; yalnızca çalışan vLLM sunucusuna bağlanan istemci kodu ve sunucuyu başlatma yardımcıları bulunur.

## Sabit kurulum (GPTQ + 8000)

Üretim varsayılanı:

- **Model:** `/raid/monassist1/medgemma_model_gptq_w4`
- **API (bu makinedeki istemci):** `http://127.0.0.1:8000`
- **Sunucu dinleme:** `medgemma.env` içinde `VLLM_SERVE_HOST=0.0.0.0` — LAN’daki başka cihazlar `http://<sunucu-ip>:8000` ile bağlanır. Güvenlik duvarında 8000/TCP açın veya sadece güvenilen ağlarda kullanın.

Tek yerden değiştirmek için **`medgemma.env`** dosyasını düzenleyin. `load_settings()` ve `serve_medgemma.sh` bunu otomatik okur. Öncelik: **ortam değişkeni > `medgemma.env` > `defaults.py`**.

Yeni ortam için şablon: `medgemma.env.example` → `medgemma.env` kopyalayın.

## Sunucuyu başlatma

Proje kökünden:

```bash
chmod +x services/vllm_medgemma/serve_medgemma.sh
./services/vllm_medgemma/serve_medgemma.sh
```

`medgemma.env` varsayılan olarak `VLLM_SERVE_HOST=0.0.0.0` kullanır; yalnızca localhost istiyorsanız `127.0.0.1` yapın.

Ek `vllm serve` bayrakları komut satırının sonuna eklenebilir.

### GPU (CUDA)

vLLM varsayılan olarak **GPU (CUDA)** kullanır. Tek kart: `CUDA_VISIBLE_DEVICES=0` (ortam veya `medgemma.env`).

## İzleme (canlı takip)

Sunucu `medgemma.env` içindeki host/port’ta çalışırken (ör. **0.0.0.0:8000** dinleme, yerel tarayıcı için **127.0.0.1:8000/docs**):

| Ne | Adres / komut |
|----|------------------|
| API dokümantasyonu (Swagger) | Tarayıcı: `http://127.0.0.1:8000/docs` |
| Sağlık | `curl -s http://127.0.0.1:8000/health` |
| Yüklü model | `curl -s http://127.0.0.1:8000/v1/models` |
| Prometheus metrikleri (ham metin) | `http://127.0.0.1:8000/metrics` |
| GPU kullanımı | `watch -n 2 nvidia-smi` |

`VLLM_SERVE_HOST=0.0.0.0` iken aynı ağdaki başka bir bilgisayardan `http://<sunucu-ip>:8000/docs` kullanılabilir. Yalnızca bu makineye kısıtlamak için `127.0.0.1` kullanın.

Konsol çıktısını dosyaya da yazdırmak için (önerilen yol: `./svc` kullanın):

```bash
# Önerilen: svc üzerinden başlatma (loglar otomatik logs/medgemma.log'a yazılır)
./svc start medgemma

# Manuel başlatma (log yolu proje ile tutarlı olsun):
./services/vllm_medgemma/serve_medgemma.sh 2>&1 | tee -a logs/medgemma.log
```

### Bellek (RAM / birleşik bellek) neden dolu görünür?

NVIDIA **GB10** gibi kartlarda GPU ve sistem belleği **paylaşımlı** olabilir. vLLM, modelin desteklediği çok uzun bağlam için **büyük KV önbelleği** ayırır; `max_model_len` yüksekse `free` ve görev yöneticisi “yüzde 98 RAM” gösterebilir — bu çoğu zaman **vLLM’in kasıtlı ayırımı**, ikinci bir model süreci değil.

`medgemma.env` içindeki **`VLLM_SERVE_EXTRA_ARGS`** ile `--max-model-len` ve `--gpu-memory-utilization` sınırlandırılır. Değişiklikten sonra sunucuyu **yeniden başlatın** (`serve_medgemma.sh`). Çok uzun bağlam gerekiyorsa bu değerleri artırın.

## Pipeline (istemci)

StageA/StageB ekstra env vermeden `medgemma.env` ile aynı `VLLM_BASE_URL` ve `VLLM_MODEL` değerlerini kullanır.

## Dosyalar

| Dosya | Açıklama |
|--------|-----------|
| `medgemma.env` | URL, GPTQ, port, venv, `VLLM_SERVE_EXTRA_ARGS` (bellek) |
| `defaults.py` | Kod içi yedek varsayılanlar (`medgemma.env` yoksa) |
| `config.py` | Ortam + `medgemma.env` → `Settings` |
| `vllm_client.py` | `/v1/chat/completions` HTTP istemcisi |
| `serve_medgemma.sh` | vLLM sunucusunu bu ayarlarla başlatır |
| `medgemma.env.example` | Boş/yeni kurulum şablonu |
