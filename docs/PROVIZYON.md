# Provizyon Değerlendirme Uygulaması — Tam Dokümantasyon

Bu belge, `MIMARI.md` içindeki **3 numaralı sistem** olan Provizyon Değerlendirme
uygulamasının her ayrıntısını anlatır.

> **Bu sistem ne değildir?** MedGemma Gateway (`medgemma_gateway/`, port 8080)
> değildir. Gateway yalnızca modele güvenli geçittir; **karar üretmez**.
> Provizyon ise bir provizyonu baştan sona değerlendirip **nihai karar** üretir.
> İkisinin ortak noktası yalnızca aynı MedGemma model sunucusunu (`:8000`)
> kullanmalarıdır. Ayrıntı: [`docs/gateway/PROVIZYONDAN_AYRI.md`](./gateway/PROVIZYONDAN_AYRI.md).

### Ürün konumu

Provizyon motoru, Vakıf modelindeki **Kurumsal Policy ve Provizyon Motoru**
ürünüdür (deterministik SUT/HUV/ICD + belge). Pipeline içindeki MedGemma adımı
**Klinik Yerindelik Asistanı** katmanına aittir: tavsiye/risk üretir; tek başına
bağlayıcı ödeme kararı değildir. Nihai canlı sonuç `merge_decisions` + kural
katmanlarından gelir. Ürün ayrımı ve yönetici durumu:
[`YONETICI_DURUM_VE_SONRAKI_ADIMLAR.md`](./YONETICI_DURUM_VE_SONRAKI_ADIMLAR.md) ·
[`MIMARI.md`](./MIMARI.md).

Uzman kararı / kural taslağı şemaları:
[`config/expert_decision.schema.json`](./config/expert_decision.schema.json),
[`config/rule_draft.schema.json`](./config/rule_draft.schema.json).

---

## 1. Kısa özet

| | |
|---|---|
| **Amaç** | Provizyonu alır → belgeleri/kuralları/MedGemma'yı çalıştırır → **karar** verir |
| **Kod kökü** | `provizyon/` |
| **Ana paket** | `provizyon/provizyon_engine/` |
| **API portu** | `8020` |
| **Yönetim** | `./svc start\|stop\|restart\|status\|logs provizyon` |
| **Bağımlılıklar** | Redis, Qdrant, TEI, MedGemma (vLLM), MSSQL (DB intake), Tesseract |

Üç native süreç + dış servisler:

```
Watcher ──┐
DB/API ───┼──► Redis kuyruk (provizyon:jobs) ──► Worker(lar)
          │                                         │
          │                                         ▼
          │                              Orchestrator pipeline
          │                                         │
          │                    ┌────────────────────┼────────────────────┐
          │                    ▼                    ▼                    ▼
          │               Belgeler/OCR          Kural katmanları      MedGemma
          │                    │                    │                    │
          │                    └────────────────────┼────────────────────┘
          │                                         ▼
          │                              merge_decisions + risk
          │                                         │
          │                    ┌────────────────────┼────────────────────┐
          │                    ▼                    ▼                    ▼
          │            Redis sonuç          JSONL audit         Qdrant findings
          │                    │
          └────────────────────┴──► API / Dashboard (:8020)
```

---

## 2. Dizin yapısı

```
provizyon/
├── config/
│   ├── provizyon.env                      # runtime ayarlar (run_*.sh source eder)
│   ├── document_requirements.json         # zorunlu evrak kod/prefix haritası
│   ├── document_requirements.example.json
│   └── provizyon-{api,watcher,worker,worker@}.service
├── provizyon_engine/                      # ana Python paketi
│   ├── api.py                             # FastAPI (8020)
│   ├── worker.py                          # Redis kuyruk tüketicisi
│   ├── orchestrator.py                    # pipeline (v4)
│   ├── decision.py                        # merge_decisions
│   ├── risk_normalizer.py                 # risk seviyesi / decision_type
│   ├── models.py                          # ProvizyonJob, JobResult, enum'lar
│   ├── settings.py                        # tüm env vars
│   ├── db.py, deidentify.py
│   ├── intake/                            # watcher, folder_intake, db_intake, popup_parser
│   ├── documents/                         # classify, OCR, extract, match, requirement…
│   ├── engines/                           # diagnosis, sut_diagnosis, sut_rules, diagnosis_payment
│   ├── medgemma/                          # client, clinical_eval, direct
│   ├── persistence/                       # results_store, qdrant_findings…
│   ├── queue/                             # redis_queue
│   └── static/                            # dashboard, copilot, yonetici HTML
├── lib/
│   ├── diagnosis_rules/                   # HUV / SUT tanı checker
│   ├── sut_engine/                        # SUTEvaluator, embedding, qdrant_store
│   └── unified_catalog/                   # advise(), retriever
├── data/generated/                        # kurallar, kataloglar (JSON/JSONL)
├── demo/fixtures/                         # demo-* test işleri
├── scripts/                               # bulk, audit, demo scriptleri
├── tests/
├── run_api.sh / run_worker.sh / run_watcher.sh
├── requirements.txt
└── .env                                   # MSSQL kimlik bilgileri (dotenv)
```

İlgili kök dizinler (repo dışı `provizyon/` altında değil):

| Yol | Kullanım |
|---|---|
| `data/intake/` | Watcher'ın izlediği klasör kökü |
| `data/documents/` | İndirilen / çözümlenen belgeler |
| `data/provizyon_work/` | Vision/OCR ara görselleri |
| `logs/provizyon-*.log` / `*.pid` | Süreç log ve PID dosyaları |
| `logs/provizyon-results.jsonl` | Sonuç audit logu |

---

## 3. Üç süreç: API, Worker, Watcher

| Süreç | Başlatma | Modül | Port | PID dosyası | Log |
|---|---|---|---|---|---|
| **API + Dashboard** | `run_api.sh start` | `uvicorn provizyon_engine.api:app` | **8020** | `logs/provizyon-api.pid` | `logs/provizyon-api.log` |
| **Worker** | `run_worker.sh start [id]` veya `scale N` | `python -m provizyon_engine.worker` | — | `logs/provizyon-worker-{id}.pid` | `logs/provizyon-worker-{id}.log` |
| **Watcher** | `run_watcher.sh start` | `python -m provizyon_engine.intake.watcher` | — | `logs/provizyon-watcher.pid` | `logs/provizyon-watcher.log` |

Hepsi `config/provizyon.env` dosyasını source eder; öncelik: **ortam değişkeni > provizyon.env > settings.py varsayılanları**.

### 3.1 API

- FastAPI uygulaması; sonuç sorgulama, enqueue, intake, analytics, sistem sağlığı.
- Health: `GET http://127.0.0.1:8020/health`
- Açılışta hafif bir **8010 → 8020** HTTP 307 redirect sunucusu da başlayabilir (eski port uyumu).
- Systemd: `config/provizyon-api.service` → `run_api.sh start-foreground`

### 3.2 Worker

- Redis'ten iş çeker (`BRPOPLPUSH`), `ProvizyonOrchestrator.run(job)` çalıştırır, sonucu yazar.
- `PROVIZYON_WORKER_ID` (varsayılan `1`) → kendi processing listesi: `provizyon:processing:{id}`
- `PROVIZYON_WORKERS=N` ile `./svc start provizyon-worker` → `run_worker.sh scale N`
- Her worker ayrı processing listesi kullanır → **çift işleme olmaz**
- Başlangıçta `reclaim_stale`: kendi processing listesindeki yarım kalmış işleri geri alır
- Hata → retry; `MAX_RETRIES` (3) aşılırsa → `provizyon:dead` (dead letter)

### 3.3 Watcher

- `data/intake/` altını periyodik tarar; hazır klasörleri kuyruğa ekler
- CLI: `--once` (tek tarama), `--reset-seen` (Redis seen setini temizle)
- **Daemon'dır** (sürekli çalışır). DB intake ise daemon değildir (aşağıda).

---

## 4. Konfigürasyon

### 4.1 `config/provizyon.env` (aktif ayarlar)

| Değişken | Anlamı | Örnek / varsayılan |
|---|---|---|
| `PROVIZYON_REDIS_URL` | Redis bağlantısı | `redis://127.0.0.1:6379/0` |
| `PROVIZYON_QUEUE_NAME` | Bekleyen iş listesi | `provizyon:jobs` |
| `PROVIZYON_MAX_RETRIES` | Fail → retry → dead | `3` |
| `PROVIZYON_WORKERS` | Paralel worker sayısı | `1` |
| `PROVIZYON_DOCUMENT_ROOT` | Belge kökü | `…/data/documents` |
| `PROVIZYON_WORK_DIR` | Ara görsel kökü | `…/data/provizyon_work` |
| `PROVIZYON_INTAKE_WATCH_DIR` | Watcher kökü | `…/data/intake` |
| `PROVIZYON_INTAKE_POLL_SECONDS` | Tarama aralığı | `10` |
| `PROVIZYON_INTAKE_STABLE_SECONDS` | Klasör "hazır" bekleme | `15` |
| `PROVIZYON_TESSERACT_CMD` / `OCR_*` | OCR ayarları | `tur+eng`, DPI 400… |
| `PROVIZYON_MEDGEMMA_BASE_URL` | vLLM OpenAI API | `http://127.0.0.1:8000/v1` |
| `PROVIZYON_MEDGEMMA_MODEL` | Model id/yolu | GPTQ checkpoint |
| `PROVIZYON_MEDGEMMA_VISION_MODE` | `auto` \| `on` \| `off` | `auto` |
| `PROVIZYON_VISION_MAX_IMAGES` | 0 = sınırsız | `0` |
| `PROVIZYON_QDRANT_URL` | Qdrant | `:6333` |
| `PROVIZYON_TEI_URL` | Embedding | `:8002` |
| `PROVIZYON_FINDINGS_COLLECTION` | Hasta bulguları | `patient_findings` |
| `PROVIZYON_DIAGNOSIS_COLLECTION` | HUV tanı kuralları | `huv_diagnosis_rules` |
| `PROVIZYON_SUT_DIAGNOSIS_COLLECTION` | SUT tanı kuralları | `sut_diagnosis_rules` |
| `PROVIZYON_DIAGNOSIS_PROCEDURE_COLLECTION` | Ödeme eğilimi | `diagnosis_procedure_pilot` |
| `PROVIZYON_ENABLE_DIAGNOSIS_PAYMENT_SIGNAL` | Adım 9b aç/kapa | `1` |
| `PROVIZYON_API_HOST` / `PORT` | API dinleme | `0.0.0.0:8020` |

### 4.2 Sadece `settings.py` / `.env` üzerinden

| Değişken | Anlamı |
|---|---|
| `PROVIZYON_PROCESSING_QUEUE` | `provizyon:processing` |
| `PROVIZYON_DEAD_LETTER_QUEUE` | `provizyon:dead` |
| `PROVIZYON_RESULT_PREFIX` | `provizyon:result:` |
| `PROVIZYON_RECENT_KEY` | `provizyon:recent` |
| `PROVIZYON_INTAKE_SEEN_KEY` | `provizyon:intake:seen` |
| `PROVIZYON_RESULT_TTL` | Sonuç TTL (varsayılan 7 gün) |
| `PROVIZYON_MEDGEMMA_TIMEOUT` | 900 sn |
| `PROVIZYON_MEDGEMMA_MAX_TOKENS` | 4096 |
| `PROVIZYON_ENABLE_PATIENT_CONTEXT` / `SIMILAR_CASES` | MedGemma öncesi hasta bağlamı |
| `PROVIZYON_SUT_RULES` / `SUT_INDEX` / `SUT_OUT_DIR` | SUT katalog yolları |
| `MSSQL_*` | DB intake (`.env` dosyasından; şifre burada) |

### 4.3 `config/document_requirements.json`

Zorunlu evrak kontrolünde kullanılır (`documents.requirement.check_requirement`):

- Exact kod: `"510070": true` → belge zorunlu
- Prefix: `"prefix:02.1": true` → en uzun eşleşen prefix kazanır
- `_` ile başlayan anahtarlar yorum satırıdır (yok sayılır)
- Genelde cerrahi prefix'ler `true`, lab/muayene `false`

---

## 5. Intake — iş nasıl girer?

Provizyon **üç yolla** kuyruğa girebilir. Hepsi sonunda aynı Redis kuyruğuna düşer.

### 5.1 Klasör izleme (Watcher) — sürekli daemon

1. Her `INTAKE_POLL_SECONDS` (10 sn) `INTAKE_WATCH_DIR` altındaki alt klasörleri tarar.
2. Redis set `provizyon:intake:seen` içindeyse atlar (tekrar enqueue yok).
3. Klasörde adı `PopupPage` içeren bir PDF olmalı (`find_popup_pdf`).
4. Klasörün son değişiklik zamanı ≥ `INTAKE_STABLE_SECONDS` (15 sn) olmalı
   (dosya kopyası bitene kadar bekler).
5. `folder_intake.build_job_from_folder`:
   - Popup PDF parse (`popup_parser.parse_popup_pdf`)
   - Belgeleri topla (`collect_documents`)
   - `ProvizyonJob` oluştur
6. `RedisQueue.enqueue` → seen set'e ekle

```
data/intake/<klasör>/
  ├── …PopupPage….pdf     ← zorunlu
  └── diğer belgeler…
```

### 5.2 DB intake — tetiklemeli (daemon değil)

- Kaynak: MSSQL view `dbo.S_VW_PROVIZYON_AI`
- Bekleyen durum: `ProvizyonDurumId = 5` (`AI_REVIEW_STATUS_ID` — AI incelemesi bekleyen)
- Alanlar: `TaniBilgileri` / `IslemBilgileri` / `BelgeBilgileri` (`kod|ad…` formatı, `<~>` ile birleşik)
- Belgeler: `OPENROWSET(BULK …)` ile indirilir → `DOCUMENT_ROOT/{ProvizyonId}/`
- Çalıştırma:
  - CLI: `python -m provizyon_engine.intake.db_intake <id> --enqueue`
  - CLI toplu: `… --pending --enqueue`
  - API: `POST /provizyon/intake-db`
- **Sürekli DB dinleyen bir servis yoktur**; elle / cron / API ile tetiklenir.
- `MSSQL_*` (özellikle şifre) `.env` içinde tanımlı olmalı.

### 5.3 API ile doğrudan enqueue

- `POST /provizyon/enqueue` — hazır `ProvizyonJob` JSON'u kuyruğa
- `POST /provizyon/process-sync` — kuyruğa koymadan orkestratörü **inline** çalıştırır, sonucu döner
- `POST /provizyon/intake-folder` — klasör parse; isteğe bağlı enqueue

### 5.4 Demo fixtures

`demo/fixtures/` + `demo/fixture_loader.py`:

| Fixture | Amaç |
|---|---|
| `demo-huv-mismatch` | HUV + tanı uyumsuzluğu |
| `demo-huv-orange-review` | Manuel/orange inceleme |
| `demo-huv-review-supported` | review_required ama belgeler destekli → düşük risk |
| `demo-sut-530090-e11` | SUT 530090 + E11 → düşük risk |
| `demo-sut-530090-k350` | Alternatif tanı |
| `demo-sut-530090-no-dx` | Tanı eksik |
| `demo-huv-missing-icd` | (eksik / boş olabilir) |

Canlı iş ID'leri: sayısal **veya** `demo-` / `DEMO-` prefix (`RedisQueue.is_live_job_id`).

---

## 6. Redis kuyruk mekanizması

### 6.1 Anahtarlar

| Anahtar | Tip | Amaç |
|---|---|---|
| `provizyon:jobs` | LIST | Bekleyen işler (LPUSH / BRPOPLPUSH) |
| `provizyon:processing` | LIST | Eski/base processing |
| `provizyon:processing:{worker_id}` | LIST | Worker başına in-flight |
| `provizyon:dead` | LIST | Dead letter (retry aşıldı) |
| `provizyon:result:{job_id}` | STRING | Sonuç JSON (+ TTL ~7 gün) |
| `provizyon:attempts:{job_id}` | STRING | Deneme sayacı / aktif claim (NX) |
| `provizyon:recent` | LIST | Son ~50 job ID |
| `provizyon:intake:seen` | SET | Watcher'ın işlediği klasör yolları |

### 6.2 Zarflama (envelope)

```json
{
  "job_id": "…",
  "payload": { /* ProvizyonJob */ },
  "attempts": 0,
  "enqueued_at": "…",
  "envelope_id": "…"
}
```

### 6.3 Güvenilir kuyruk akışı

1. Enqueue: `LPUSH provizyon:jobs` + `attempts` NX (aktif iş varsa duplicate engellenir)
2. Worker: `BRPOPLPUSH jobs → processing:{id}` (atomik claim)
3. Başarı: `ack` → processing'den `LREM` + sonuç yaz
4. Hata: `attempts++`; ≤ MAX → tekrar jobs'a; > MAX → `provizyon:dead`
5. Restart: worker kendi `processing:{id}` listesini reclaim eder

### 6.4 Kuyruk istatistiği

`GET /queue/stats` → pending / processing / dead derinlikleri  
`GET /queue/recent` → son iş özetleri

> **Dikkat:** Dashboard'da "bekleyen" görünmesi = Redis'te gerçek kuyruk derinliği
> demek değildir. Panel, sonucu yazılmamış eski kayıtları varsayılan `queued`
> gösterebilir. Gerçek derinlik için `/queue/stats` kullanın.

---

## 7. ProvizyonJob — kuyruğa giren veri modeli

`models.ProvizyonJob` (özet alanlar):

| Alan | Anlamı |
|---|---|
| `provizyon_id` | İş kimliği (zorunlu) |
| `hasta_id`, `tc_kimlik`, `patient_name` | Hasta kimliği |
| `yas`, `cinsiyet` | Demografi (`erkek` / `kadin` / `bilinmiyor`) |
| `facility_level`, `institution_name` | Kurum / seviye |
| `huv_codes`, `sut_codes` | İşlem kodları |
| `code_family` | HUV / SUT / both vb. |
| `diagnosis_code_source` | `huv` \| `sut` \| `both` — hangi tanı motoru çalışır |
| `diagnoses` | Tanı listesi (ICD vb.) |
| `procedures` | `ProcedureInput` listesi (code, code_type, name…) |
| `documents` | `DocumentInput` listesi (path, doc_type…) |
| `ozel_sorular` | MedGemma'ya özel sorular (opsiyonel) |

`extra="allow"` → bilinmeyen alanlar da taşınabilir.

---

## 8. Orchestrator pipeline (v4) — adım adım

Giriş: `ProvizyonOrchestrator.run(job)` → `_run_pipeline`.

`OrchestratorConfig` bayrakları: `enable_diagnosis`, `enable_sut_diagnosis`,
`enable_sut_rules`, `enable_medgemma`, `enable_diagnosis_payment`,
`enable_persistence`, `enable_patient_context`, `use_qdrant_rag`, `include_vision`.

| Adım | Ne yapılır | Erken çıkış / not |
|---|---|---|
| **2–3** | Belgeleri çöz (`FilesystemDocumentSource`) → `extract_document` → `ocr_document` → tür/title/cinsiyet zenginleştirme | — |
| **4** | `match_documents` → katman `belge_hasta` | **FAIL** → `yanlis_hasta_belgesi` (RAG belgeler yazılmaz) |
| **5** | `check_requirement` → `zorunlu_evrak` | **FAIL** → `evrak_eksik` |
| **6** | `check_diagnoses` (HUV) — `diagnosis_code_source` ∈ {huv, both} | — |
| **6b** | `check_sut_diagnoses` — ∈ {sut, both} | — |
| **7** | `check_sut_rules` | FAIL → sonra merge'te manuel inceleme |
| **8** | MedGemma `evaluate_clinical` | Atlanır: belge yok / analiz başarısız / **tanı FAIL** |
| — | `scan_extracted_documents` (iade/red sinyalleri) | Auto-approve'u engelleyebilir |
| **9** | `merge_decisions` | Nihai `KararDurumu` |
| **9b** | Tanı–işlem ödeme eğilimi (opsiyonel) | UYGUN/low_risk'i escalate edebilir; hard red'i yumuşatmaz |
| **10** | `_finalize` → `JobStatus.DONE` + `PatientFindingsWriter.write` | Redis + JSONL + Qdrant |

---

## 9. Belge (documents) katmanı

| Modül | Giriş | Görev |
|---|---|---|
| `source` | `FilesystemDocumentSource.resolve_all` | Yolları `DocumentRef`'e çevir |
| `extract` | `extract_document` | PDF/görsel/metin → sayfalar, gömülü görseller |
| `ocr` | `ocr_document` | Tesseract; kalite skoru; deskew/denoise; `ocr_cache` |
| `classify` | `classify_document`, `refine_doc_types`, `enrich_document_titles` | epikriz / rapor / fatura… |
| `patient_match` | `match_documents` | match / mismatch / uncertain / exempt → PASS/FAIL/REVIEW |
| `procedure_match` | `extract_codes_from_text` | Metinden HUV/SUT kod çıkarma (yardımcı) |
| `requirement` | `check_requirement` | JSON + SUT required_document → eksikse FAIL |
| `prepare` | `build_evidence_package` | MedGemma için sayfa seçimi, resize, metin kanıtı |
| `rejection_signals` | `scan_extracted_documents` | "iade", "red" vb. ifadeler → manuel zorlama |

OCR önemli ayarlar: `OCR_ALL_PAGES=1`, DPI 400, `tur+eng`, min kalite `0.35`,
deskew ±6°, denoise açık, binarize varsayılan kapalı.

---

## 10. Kural motorları (engines)

### 10.1 HUV tanı — `engines/diagnosis.py` → `check_diagnoses`

- Katman adı: `tani_kurali`
- Qdrant: `huv_diagnosis_rules`
- Lib: `diagnosis_rules.provision_diagnosis_checker.evaluate_provision`
- TZH meta kodları (`TZH.*`) atlanır
- Sonuç eşlemesi:
  - `not_payable_by_diagnosis` → **FAIL**
  - `review_required` → **REVIEW**
  - `allowed` → **PASS**
- FAIL detay bayrakları: `missing_diagnosis` → nihai `tani_eksik`; `diagnosis_mismatch` → `tani_uyumsuz`

### 10.2 SUT tanı — `engines/sut_diagnosis.py` → `check_sut_diagnoses`

- Katman: `sut_tani_kurali`
- Qdrant: `sut_diagnosis_rules`
- Lib: `evaluate_sut_provision` (+ yaş/cinsiyet)
- `not_payable_by_sut_diagnosis` → **FAIL**

### 10.3 SUT kuralları — `engines/sut_rules.py` → `check_sut_rules`

- Katman: `sut_kurali`
- `unified_catalog.unified_advisor.advise` + `sut_rules_merged.json` / index / unified catalog
- Erken SKIP: yalnızca TZH, yerel HUV→SUT eşlemesi yok
- FAIL → merge'te **manuel_inceleme** (hard red tanı gibi değil)

### 10.4 Tanı–işlem ödeme eğilimi — `engines/diagnosis_payment.py` (adım 9b)

- Katman gerekçesi: `diagnosis_payment_tendency`
- Gereksinim: `institution_name` + tanılar + işlem kod/adı
- TEI + Qdrant `diagnosis_procedure_pilot`; sıkı post-filter
- Kırmızı/turuncu sinyaller UYGUN / low_risk'i `manuel_inceleme` / orange'a yükseltebilir
- Mevcut hard red kararları **yumuşatılmaz**
- `PROVIZYON_ENABLE_DIAGNOSIS_PAYMENT_SIGNAL=0` ile kapatılır

---

## 11. MedGemma entegrasyonu (pipeline içi)

| Modül | Rol | Ne zaman |
|---|---|---|
| `medgemma/clinical_eval.evaluate_clinical` | Klinik JSON değerlendirme | Orchestrator adım 8 |
| `medgemma/client.MedGemmaVisionClient` | OpenAI uyumlu multimodal chat → `:8000/v1` | clinical_eval (ve direct) |
| `medgemma/direct.py` | Bağımsız vision / sigorta batch | **Pipeline dışı** — Redis/Qdrant/karar yazmaz |

**Atlanma koşulları (adım 8):**

- Config'de MedGemma kapalı
- Belge yok
- Extract/OCR tamamen boş
- HUV veya SUT **tanı katmanı FAIL**

**Çıktı (`MedGemmaClinicalOutput`):**

| Alan | Tip |
|---|---|
| `islem_belge_destekli` | bool? |
| `tani_belge_destekli` | bool? |
| `yas_cinsiyet_uygun` | bool? |
| `klinik_celiski` | bool? |
| `eksik_evrak` | bool? |
| `manuel_inceleme_gerekli` | bool |
| `guven` | `high` \| `medium` \| `low` |
| `gerekce` | str |
| `ozel_soru_cevaplari` | liste |

İstemci: vision mode `auto|on|off`; context overflow'da vision tier'ları; JSON mode + parse repair.
Hasta bağlamı (opsiyonel): `patient_context.load_patient_context` — geçmiş / benzer vakalar MedGemma öncesi.

> Provizyon, MedGemma'ya **doğrudan** `http://127.0.0.1:8000/v1` üzerinden gider.
> Gateway'i (`:8080`) kullanmaz. Bu yüzden gateway'in kuyruk/SLA/API-key
> koruması provizyon trafiğini kapsamaz (kaynak paylaşımı riski — `MIMARI.md`).

---

## 12. Karar birleştirme ve risk

### 12.1 `merge_decisions` öncelik sırası

1. `belge_hasta` FAIL → `yanlis_hasta_belgesi`
2. `zorunlu_evrak` FAIL → `evrak_eksik`
3. `tani_kurali` / `sut_tani_kurali` FAIL → `tani_eksik` veya `tani_uyumsuz`
4. MedGemma sert negatifler → `klinik_uyumsuzluk` / `evrak_eksik` / `belge_kaniti_yetersiz`
5. Rejection signals → `manuel_inceleme`
6. `sut_kurali` FAIL → `manuel_inceleme`
7. Belge analizi başarısız → `belge_analizi_tamamlanamadi`
8. MedGemma katmanı INSUFFICIENT → `ai_yorumu_bekleniyor`
9. REVIEW katmanları + MedGemma bayrakları — bazı güvenli override'lar:
   - Yalnız belge-hasta REVIEW veya tanı REVIEW + yüksek güven MedGemma → `uygun` olabilir
10. Aksi halde → `uygun`

Sonra `normalize_provision_risk` → `decision_type` + `risk_level` + `risk_reasons`.

### 12.2 Enum değerleri

**`KararDurumu` (nihai karar):**

`uygun` · `tani_eksik` · `tani_uyumsuz` · `evrak_eksik` · `yanlis_hasta_belgesi` ·
`klinik_uyumsuzluk` · `belge_kaniti_yetersiz` · `manuel_inceleme` ·
`ai_yorumu_bekleniyor` · `belge_analizi_tamamlanamadi`

**`JobStatus`:** `queued` · `processing` · `done` · `failed`

**`LayerStatus`:** `pass` · `fail` · `review` · `insufficient` · `skipped`

**`DecisionType`:** `automatic_defensible` · `manual_review` · `low_risk`

**`RiskLevel`:** `red` · `orange` · `yellow` · `green` · `blue` · `gray`

**Karar → risk eşlemesi (özet):**

| Karar grubu | decision_type | risk |
|---|---|---|
| Tanı uyumsuz / yanlış hasta / klinik | `automatic_defensible` | red |
| Evrak eksik / belge kanıtı | — | yellow |
| Manuel inceleme | `manual_review` | orange |
| AI bekleniyor / analiz tamamlanamadı | — | gray |
| Uygun | `low_risk` | green |

### 12.3 `JobResult` yapısı

- `nihai_karar`, `gerekce`, `decision_type`, `risk_level`, `risk_reasons[]`
- Katman sonuçları: `belge_hasta`, `zorunlu_evrak`, `tani_kurali`, `sut_tani_kurali`, `sut_kurali`, `medgemma`
- `warnings`, `raw` (denetim), `started_at` / bitiş zamanları

---

## 13. Kalıcılık (persistence)

### 13.1 Redis sonuç

`ResultStore.store_result` → `provizyon:result:{id}` (TTL ~7 gün)

### 13.2 JSONL audit

`logs/provizyon-results.jsonl` — her tamamlanan iş için satır eklenir

### 13.3 Qdrant `patient_findings`

`PatientFindingsWriter.write` — `allow_document_rag` iken katmanlar yazılır:

`nihai_karar`, `belge_hasta`, `zorunlu_evrak`, `tani_kurali`, `sut_tani_kurali`,
`sut_kurali`, `medgemma` (+ TEI embedding)

Yanlış hasta belgesi durumunda yalnızca `nihai_karar` yazılır.

Payload örnek alanlar: `provizyon_id`, `hasta_id`, `tc_kimlik`, `layer`, `status`,
`nihai_karar`, `institution_name`, `facility_level`, `yas_grubu`, `cinsiyet`.

---

## 14. API uçları (port 8020)

| Method | Path | Amaç |
|---|---|---|
| POST | `/provizyon/enqueue` | `ProvizyonJob` kuyruğa ekle |
| POST | `/provizyon/process-sync` | Orkestratörü senkron çalıştır; sonucu dön |
| POST | `/provizyon/intake-folder` | Klasör parse; opsiyonel enqueue |
| POST | `/provizyon/intake-db` | MSSQL'den çek (id veya pending); opsiyonel enqueue |
| DELETE | `/provizyon/{provizyon_id}` | Redis sonuç + recent girişini sil |
| GET | `/provizyon/{provizyon_id}` | Tam sonuç (risk_reasons sıralı) |
| GET | `/queue/stats` | pending / processing / dead derinlikleri |
| GET | `/queue/recent?limit=` | Son iş özetleri (≤50) |
| GET | `/health` | Redis + temel config |
| GET | `/analytics/findings` | patient_findings istatistikleri (~2 dk cache) |
| GET | `/system/health` | MedGemma / Qdrant / TEI / Redis / workers / GPU-RAM + katalog |
| GET | `/system/logs?service=&lines=` | Log kuyruğu |
| GET | `/`, `/dashboard` | `dashboard.html` |
| GET | `/dashboard/demo` | `dashboard_demo.html` |
| GET | `/copilot`, `/dashboard/copilot` | `copilot.html` |
| GET | `/yonetici`, `/dashboard/yonetici` | `yonetici.html` |

---

## 15. Dashboard / arayüzler

`provizyon_engine/static/` (no-cache header ile sunulur):

| Dosya | UI |
|---|---|
| `dashboard.html` | Ana kontrol paneli (kuyruk, sonuçlar, sistem) |
| `dashboard_demo.html` | Demo / eski panel |
| `copilot.html` | Uzman co-pilot |
| `yonetici.html` | Yönetici / kural motoru raporu |

Hepsi yukarıdaki JSON API'leri çağırır.

---

## 16. Dış bağımlılıklar

| Servis | Adres | Provizyon'da kullanım |
|---|---|---|
| **Redis** | `:6379` (docker) | İş kuyruğu, sonuç, seen, recent |
| **Qdrant** | `:6333` (docker) | Tanı kuralları, SUT katalog, findings, ödeme sinyali |
| **TEI bge-m3** | `:8002` (docker) | Embedding (yazma/arama) |
| **MedGemma vLLM** | `:8000/v1` (native) | Klinik değerlendirme |
| **MSSQL** | `.env` host | DB intake view + belge blob |
| **Tesseract** | yerel CLI | OCR |
| **Open WebUI** | `:3000` | Pipeline için **gerekli değil**; `/system/health` probe eder |

Python paketleri: `requirements.txt` (fastapi, uvicorn, redis, pyodbc, pymupdf,
pytesseract, qdrant-client, openai, httpx, pydantic…).

---

## 17. Yönetim komutları

Repo kökünden (`GemmaApp/`):

```bash
./svc status provizyon                 # api + worker + watcher
./svc start|stop|restart provizyon
./svc start|stop|restart provizyon-api|provizyon-worker|provizyon-watcher
./svc logs provizyon-api|provizyon-worker|provizyon-watcher
./svc config provizyon-api             # provizyon.env içeriği

# veya doğrudan scriptler:
provizyon/run_api.sh {start|stop|restart|status|start-foreground}
provizyon/run_worker.sh {start [id]|stop [id|all]|scale N|status}
provizyon/run_watcher.sh {start|stop|once|status}
```

`./svc start all` sırası: medgemma → hazır ol → qdrant/webui/redis → tei → provizyon*.

Alias'lar: `prov`, `prov-api`, `watcher`, vb. (`svc` içindeki `_map_name`).

---

## 18. Sistemd (opsiyonel)

`config/` altında unit dosyaları:

- `provizyon-api.service`
- `provizyon-worker.service` / `provizyon-worker@.service`
- `provizyon-watcher.service`

Normal işletimde `./svc` + `run_*.sh` yeterlidir; systemd kalıcı servis için.

---

## 19. Testler ve scriptler

- `tests/` — classify, intake, OCR, orchestrator, medgemma client/direct, risk_normalizer, rejection_signals, red regression…
- `scripts/` — bulk historical prefill, pipeline audit, demo payer trend, contract outputs, DGX shadow review, `validate_shadow_handoff.py`, `medgemma_direct.py`…
- Review-reduction handoff (read-only): `data/handoffs/review_reduction_dgx_transfer_bundle_20260709/` → API `/shadow/review-reduction/*`, panel Model & Hikâye

---

## 20. Sık karışan noktalar

1. **Gateway'e istek atmak provizyon değerlendirmesi başlatmaz.** Provizyon akışını yalnızca watcher / db intake / API enqueue + worker yürütür.
2. **Dashboard "queued" ≠ Redis'te bekleyen iş.** Gerçek derinlik: `GET /queue/stats`.
3. **MedGemma Gateway ile ortak kod/DB/kuyruk yok.** Ortak kaynak yalnızca vLLM `:8000`.
4. **Watcher sürekli; DB intake tetiklemeli.** DB tarafında daemon yoktur.
5. **Tanı FAIL olursa MedGemma adımı atlanır** (gereksiz GPU harcamasını önlemek için).
6. **Worker sayısı artırılırken** her biri ayrı `processing:{id}` kullanır; `PROVIZYON_WORKERS` + `scale N`.

---

## 21. Zihinsel model (tek bakışta)

```
Intake (watcher | DB | API)
        ↓
Redis: provizyon:jobs
        ↓
Worker → Orchestrator v4
   │
   ├─ Belgeler: extract → OCR → classify → hasta eşle → zorunlu evrak
   ├─ Kurallar: HUV tanı → SUT tanı → SUT kuralları
   ├─ MedGemma klinik değerlendirme (+ opsiyonel hasta bağlamı)
   ├─ Rejection signals
   ├─ merge_decisions → risk_normalizer
   └─ (+ opsiyonel diagnosis_payment escalate)
        ↓
Redis result + JSONL audit + Qdrant patient_findings
        ↓
API :8020 / Dashboard / Copilot / Yönetici
```

Bu belge `MIMARI.md` içindeki **3) Provizyon Değerlendirme** satırının ayrıntılı açılımıdır.
Gateway (2) için: [`docs/gateway/`](./gateway/).
Genel harita: [`MIMARI.md`](./MIMARI.md).
