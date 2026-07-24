# MedGemma Gateway

> Kod: [`medgemma_gateway/`](../../medgemma_gateway/) · Bu belge dokümantasyon kopyasıdır (`docs/gateway/`).

Mevcut vLLM/MedGemma sunucusunun (port 8000) önünde duran **bağımsız** bir API
katmanı. Mevcut projeye hiç dokunmaz; silinse bile mevcut sistem etkilenmez.

## Ne işe yarar?

- Başka bir makineden **ağ üzerinden** güvenli çağrı yapmayı sağlar.
- **API key (Bearer token)** ile korumalıdır.
- **OpenAI uyumlu** geçiş yapar: `/v1/chat/completions`, `/v1/completions`, `/v1/models`.
- **Kolaylık endpoint'i** `/degerlendir`: kendi JSON yapınızı (`Prompt` + veri) olduğu
  gibi kabul eder, sarmalamayı kendisi yapar ve size direkt JSON sonuç döner.
- İstemci model yolunu bilmek zorunda değildir (varsayılan model otomatik eklenir).

```
[Başka makine] --(HTTPS/HTTP + API key)--> [Gateway :8080] --> [vLLM :8000]
```

## Kurulum ve çalıştırma

```bash
# Repo kökünden:
cd medgemma_gateway
cp gateway.env.example gateway.env
# gateway.env içine güçlü bir API key girin:
#   GATEWAY_API_KEY=$(openssl rand -hex 32)
./run.sh
```

`run.sh` kendi sanal ortamını (`.venv`) kurar; sistemdeki diğer venv'lere dokunmaz.

## Ayarlar (gateway.env)

| Değişken | Açıklama | Varsayılan |
|---|---|---|
| `GATEWAY_HOST` | Dinlenen adres | `0.0.0.0` |
| `GATEWAY_PORT` | Dinlenen port | `8080` |
| `GATEWAY_API_KEY` | İstemcinin göndereceği Bearer token | *(boş — zorunlu)* |
| `GATEWAY_UPSTREAM_URL` | Arkadaki vLLM adresi | `http://127.0.0.1:8000/v1` |
| `GATEWAY_DEFAULT_MODEL` | Varsayılan model | `/raid/monassist1/medgemma_model_gptq_w4` |
| `GATEWAY_TIMEOUT_SECONDS` | İstek zaman aşımı | `900` |

## Başka makineden çağırma

```bash
curl http://<sunucu-ip>:8080/v1/chat/completions \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Merhaba, kısa bir test."}]}'
```

Python (OpenAI SDK) ile:

```python
from openai import OpenAI

client = OpenAI(base_url="http://<sunucu-ip>:8080/v1", api_key="<API_KEY>")
resp = client.chat.completions.create(
    model="x",  # gateway varsayılanı kullanır; herhangi bir değer olabilir
    messages=[{"role": "user", "content": "Merhaba"}],
)
print(resp.choices[0].message.content)
```

## Birden çok API key (isimli)

Anahtarlar `api_keys.txt` dosyasında tutulur; her satır bir anahtar:

```
<key>:<isim>
```

- `isim` log'da "kimin çağırdığı" olarak görünür.
- Yeni anahtar: `openssl rand -hex 32`
- Bir anahtarı iptal etmek: satırı silip `systemctl --user restart medgemma-gateway.service`.
- Alternatif: tek anahtar için `gateway.env` içinde `GATEWAY_API_KEY` de kullanılabilir.

## İstek kaydı (log)

Her istek `GATEWAY_LOG_FILE` (varsayılan `logs/gateway_requests.jsonl`) dosyasına
JSONL olarak yazılır. **Varsayılan olarak yalnızca meta veri** yazılır (tıbbi
içerik değil): zaman, istemci ismi, model, durum kodu, süre, token sayıları.

- Kapatmak: `GATEWAY_LOG_REQUESTS=0`
- Prompt/cevap içeriğini de yazmak (dikkat, tıbbi veri): `GATEWAY_LOG_CONTENT=1`

Örnek satır:

```json
{"ts":"...","client":"makine-1","path":"/v1/chat/completions","model":"...","status":200,"prompt_tokens":19,"completion_tokens":4,"total_tokens":23,"duration_ms":368}
```

## Rate limit

Anahtar başına dakikada en fazla istek: `GATEWAY_RATE_LIMIT_PER_MIN`.
`0` = sınırsız (varsayılan/kapalı). Aşılırsa `429` döner.

## Kuyruk (eşzamanlılık sınırı)

Gateway, aynı anda upstream'e giden istek sayısını sınırlar; fazlası sırada bekler.

- `GATEWAY_MAX_CONCURRENCY` (varsayılan 4): aynı anda işlenen istek sayısı.
- `GATEWAY_QUEUE_TIMEOUT_SECONDS` (varsayılan 300): kuyrukta bekleme sınırı; aşılırsa 503.
- Anlık durum: `GET /health` → `queue.active` / `queue.waiting`.

## Kalıcı servis (systemd --user, sudo'suz)

Otomatik başlar (makine reboot sonrası dahil, linger açıkken) ve çökerse yeniden başlar.

```bash
# İlk kurulum: önce venv'i hazırla (bir kez)
./run.sh   # Ctrl+C ile durdurabilirsin; amaç .venv'i oluşturmak

# Servisi kur ve başlat
loginctl enable-linger "$USER"
mkdir -p ~/.config/systemd/user
cp medgemma-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now medgemma-gateway.service

# Yönetim
systemctl --user status medgemma-gateway.service
systemctl --user restart medgemma-gateway.service
systemctl --user stop medgemma-gateway.service
journalctl --user -u medgemma-gateway.service -f   # canlı log
```

Ayar (`gateway.env`) değiştirdikten sonra: `systemctl --user restart medgemma-gateway.service`.

## Sağlık kontrolü

```bash
curl http://<sunucu-ip>:8080/health   # kimlik doğrulama gerektirmez
```

## Notlar

- Servis çalışması için arkadaki vLLM'in (`./svc start medgemma`) çalışıyor olması gerekir.
- Ağa açık olduğundan `GATEWAY_API_KEY` mutlaka doldurulmalıdır; boşsa servis 503 döner.
