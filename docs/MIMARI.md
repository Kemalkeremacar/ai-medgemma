# GemmaApp — Mimari ve Servisler

Bu belge, `GemmaApp` altında yan yana duran sistemlerin **ayrı ve bağımsız projeler**
olduğunu ve birbirlerine hangi mekanizmayla bağlandıklarını netleştirir.

> Kafa karışıklığının ana sebebi birçok bileşenin "MedGemma / Gemma" kelimesini
> paylaşmasıdır. Oysa bunlar farklı yaşam döngüsüne, koda ve amaca sahip
> **bağımsız servislerdir**. Aralarındaki tek gerçek bağ **ağ (HTTP) üzerinden**dir;
> ortak kod/veritabanı paylaşmazlar.

---

## 1. Özet: bunlar ayrı projelerdir

| # | Proje / Servis | Görev | Port | Nasıl yönetilir |
|---|---|---|---|---|
| 1 | **MedGemma / vLLM** | LLM model sunucusu (OpenAI uyumlu) | `8000` | `./svc` (native process) |
| 2 | **MedGemma Gateway** | İnce güvenli geçit (proxy) + SLA/kuyruk | `8080` | **Ayrı** (`run.sh` / systemd) |
| 3 | **Provizyon Değerlendirme** | Provizyon iş akışı → karar üretir | `8020` (+worker/watcher) | `./svc` (native process) |
| 4 | **Qdrant** | Vektör DB (RAG/kanıt arama) | `6333/6334` | `./svc` (docker) |
| 5 | **TEI (bge-m3)** | Embedding servisi | `8002` | `./svc` (docker) |
| 6 | **Open WebUI** | Sohbet arayüzü (model denemek için) | `3000` | `./svc` (docker) |
| 7 | **Redis** | Provizyon iş kuyruğu | `6379` | `./svc` (docker) |

**Ortak paylaşılan tek kaynak:** MedGemma model sunucusu (vLLM · `8000`).
Onun dışında bu servisler birbirinden bağımsızdır — biri kapanınca diğerleri
(ortak kaynağa ihtiyaç duymadıkları sürece) çalışmaya devam eder.

### Ürün dili — iki yetenek + uzman

Vakıf karar destek modeli **tek bir “AI her şeyi yapar” ürünü değildir**.
İki yetenek ve bir insan katmanı vardır:

| Ürün / katman | Ne yapar | Bağlayıcı canlı karar mı? |
|---|---|---|
| **Klinik Yerindelik Asistanı** | MedGemma ile klinik yorum, risk/tavsiye (gateway, WebUI, provizyon klinik katmanı) | **Hayır** — olasılıksal görüş |
| **Kurumsal Policy ve Provizyon Motoru** | SUT/HUV/ICD, belge, deterministik kurallar ([`PROVIZYON.md`](./PROVIZYON.md)) | **Evet** — aynı girdi → aynı sonuç |
| **Uzman insan** | Belirsizlik / yeni kural; yapılandırılmış karar → shadow → kontrollü yayın | Onay sonrası kurala dönüşür |

> Yapay zekâ hastanın klinik tablosunu yorumlar; kural motoru kurumun onaylanmış
> politikasını uygular; uzman insan bu ikisi arasındaki belirsizlikleri çözerek
> yeni kurumsal bilgiyi sisteme kazandırır.

Durum, yönetici cevabı, 703790 brifing, şemalar ve shadow backlog:
→ [`YONETICI_DURUM_VE_SONRAKI_ADIMLAR.md`](./YONETICI_DURUM_VE_SONRAKI_ADIMLAR.md)

 ---

## 2. Şema — kim kime bağlanıyor

```
                    ┌───────────────────────────────────────────┐
                    │   MedGemma model sunucusu (vLLM)           │
                    │   :8000  ·  OpenAI uyumlu /v1              │  ← TEK paylaşılan kaynak
                    │   model: /raid/.../medgemma_model_gptq_w4  │     (max-num-seqs=4)
                    └───────▲───────────────▲───────────────▲────┘
                            │               │               │
             HTTP /v1       │               │ HTTP /v1      │ HTTP /v1
                            │               │               │
   ┌────────────────────────┴──┐  ┌─────────┴────────┐  ┌───┴──────────────┐
   │ 2) MedGemma Gateway :8080  │  │ 3) Provizyon      │  │ 6) Open WebUI    │
   │    (bağımsız proxy)        │  │    Değerlendirme  │  │    :3000         │
   │  • API key + SLA + kuyruk  │  │  • API :8020      │  │  (sohbet arayüzü)│
   │  • /v1/chat, /degerlendir  │  │  • Worker         │  └──────────────────┘
   └────────────▲───────────────┘  │  • Watcher        │
                │                   │       │  │  │      │
         dış istemci                │       │  │  │      │
        (ör. makine-1)              │       ▼  ▼  ▼      │
                                    │  Redis Qdrant TEI  │
                                    │ :6379 :6333 :8002  │
                                    │  + MSSQL (dış DB)  │
                                    └────────────────────┘
```

- **Gateway (2)**, **Provizyon (3)** ve **Open WebUI (6)** aynı vLLM'e (`8000`)
  **birbirinden habersiz**, doğrudan HTTP ile gider.
- **Provizyon (3)** ayrıca kendi altyapısını kullanır: Redis (kuyruk), Qdrant + TEI
  (RAG), MSSQL (provizyon kaynağı).
- **Gateway (2)** provizyonun hiçbir şeyini (Redis/Qdrant/MSSQL/karar mantığı)
  **bilmez**; sadece "istek al → modele ilet → cevabı dön" yapar.

---

## 3. Servis servis mekanizma

### 1) MedGemma / vLLM  (`:8000`)
- **Nedir:** GPTQ MedGemma modelini `vllm serve` ile sunan OpenAI uyumlu API.
- **Kod/ayar:** `services/vllm_medgemma/` (`serve_medgemma.sh`, `medgemma.env`, `defaults.py`).
- **Eşzamanlılık:** `--max-num-seqs 4` (aynı anda 4 sekans), 64K bağlam, prefix cache.
- **Rolü:** Tüm diğer tüketicilerin ortak beyni. Continuous batching ile birden
  çok isteği aynı anda karşılayabilir.
- **Yönetim:** `./svc start|stop|restart|logs medgemma`.

### 2) MedGemma Gateway  (`:8080`) — bu proje ayrıdır
- **Nedir:** vLLM'in önünde duran ince, güvenli **geçit (reverse proxy)**.
- **Kod/ayar:** `medgemma_gateway/` (`app.py`, `gateway.env`, `api_keys.txt`).
  Kendi `.venv`'i vardır; provizyon ile **ortak kod/DB/kuyruk yoktur.**
- **Ne yapar:**
  - API key doğrulama (isimli anahtarlar → log'da "kim çağırdı").
  - **Eşzamanlılık kuyruğu** (`GATEWAY_MAX_CONCURRENCY=4`, vLLM ile uyumlu).
  - **SLA / toplam deadline** (`GATEWAY_SLA_SECONDS=300`): istek gateway'e ulaştığı
    andan yanıta kadar toplam süre bütçesi. Kuyruk + upstream bunu aşarsa istek
    **HTTP 504** döner ve upstream isteği iptal edilir (GPU slotu serbest kalır).
  - İstek loglama (`logs/gateway_requests.jsonl`, varsayılan sadece meta veri).
  - Uçlar: `/v1/chat/completions`, `/v1/completions`, `/degerlendir`, `/health`.
- **Yönetim:** `./svc` **kapsamında değildir** — ayrı başlatılır: `medgemma_gateway/run.sh`
  (veya `medgemma-gateway.service`). Böylece diğer servisler restart edilse bile
  gateway'in trafiği/oturumu etkilenmez.
- **Ayrıntı:** [`docs/gateway/README.md`](./gateway/README.md), [`CAGIRMA_KILAVUZU.md`](./gateway/CAGIRMA_KILAVUZU.md), [`PROVIZYONDAN_AYRI.md`](./gateway/PROVIZYONDAN_AYRI.md).

### 3) Provizyon Değerlendirme  (`:8020` + worker + watcher)
- **Nedir:** Asıl iş mantığı. Bir provizyonu baştan sona değerlendirir
  (belge–hasta eşleşmesi, zorunlu evrak, HUV/SUT tanı-işlem kuralları, MedGemma
  klinik değerlendirmesi) → **nihai karar** (uygun / manuel inceleme / red).
- **Kod/ayar:** `provizyon/` (`provizyon_engine/`, `config/provizyon.env`).
- **Bileşenler:**
  - **API + Dashboard** (`:8020`) — sonuç sorgulama ve panel.
  - **Worker** — Redis kuyruğundaki işleri işler (`run_worker.sh`).
  - **Watcher** — `data/intake/` klasörünü izler, yeni provizyonu kuyruğa ekler
    (`run_watcher.sh`).
- **Bağlandığı yerler:** vLLM (`:8000/v1`), Redis (`:6379`), Qdrant (`:6333`),
  TEI (`:8002`), MSSQL (dış sunucu).
- **Yönetim:** `./svc start|stop|restart provizyon` (veya `provizyon-api` /
  `provizyon-worker` / `provizyon-watcher` tek tek).
- **Tam ayrıntı (intake, pipeline, Redis anahtarları, API, karar enum'ları):**
  → [`PROVIZYON.md`](./PROVIZYON.md)

### 4) Qdrant  (`:6333/6334`)
- Vektör veritabanı; provizyonun RAG/kanıt aramasında kullanılır.
- Docker; `docker-compose.yml` + `services/qdrant/qdrant.env`.

### 5) TEI — bge-m3  (`:8002`)
- Metinleri embedding'e çevirir (Qdrant'a yazmak/aramak için).
- Docker (CPU-ARM64 imajı); `services/tei_bge_m3/`.

### 6) Open WebUI  (`:3000`)
- MedGemma modelini elle denemek için sohbet arayüzü.
- Docker; `OPENAI_API_BASE_URL=http://host.docker.internal:8000/v1` ile doğrudan
  vLLM'e bağlanır. Provizyon/gateway ile ilgisi yoktur.

### 7) Redis  (`:6379`)
- Provizyon worker/API arasında iş kuyruğu.
- Docker; `docker-compose.yml`.

---

## 4. Bağımsızlık — biri kapanırsa ne olur?

- **Gateway kapanır** → dış istemciler (ör. `makine-1`) model çağıramaz; ama
  provizyon ve Open WebUI vLLM'e doğrudan gittiği için **çalışmaya devam eder.**
- **Provizyon kapanır** → değerlendirme akışı durur; gateway ve Open WebUI etkilenmez.
- **vLLM (8000) kapanır** → üç tüketici de (gateway, provizyon, Open WebUI) model
  çağrılarında hata alır. **Tek gerçek ortak bağımlılık budur.**
- **Qdrant / TEI / Redis kapanır** → yalnızca provizyon iş akışı etkilenir.

> Not: Gateway, provizyon ve Open WebUI aynı vLLM'i paylaştığı için **yük/kuyruk
> düzeyinde birbirini etkiler** (aynı 4 eşzamanlılık slotunu kullanırlar).
> Bu bir veri karışması değil, **kaynak paylaşımıdır**.

---

## 5. Yönetim özeti

```bash
# svc kapsamındaki her şey (medgemma, qdrant, tei, webui, redis, provizyon-*)
./svc status
./svc start all
./svc restart provizyon
./svc logs medgemma

# Gateway AYRIDIR — svc ile yönetilmez:
cd medgemma_gateway && bash run.sh          # başlat
# veya systemd: systemctl start medgemma-gateway
```

| Yönetim yolu | Servisler |
|---|---|
| `./svc` (native) | medgemma (vLLM), provizyon-api / -worker / -watcher |
| `./svc` (docker-compose) | qdrant, tei, open-webui, redis |
| **Ayrı** (`run.sh` / systemd) | **medgemma_gateway** |
