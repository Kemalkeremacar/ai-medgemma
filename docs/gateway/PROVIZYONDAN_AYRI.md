# MedGemma Gateway, Provizyon Uygulamasından Ayrıdır

Bu belge, `GemmaApp` altında yan yana duran **iki bağımsız sistemi** ve aralarındaki
tek bağlantı noktasını netleştirmek için yazılmıştır. Kafa karışıklığının ana
sebebi ikisinin de "MedGemma" kelimesini kullanmasıdır; oysa yaptıkları iş
tamamen farklıdır.

---

## Kısa cevap

- **Provizyon Değerlendirme Uygulaması** → büyük iş akışı. Provizyonu alır,
  kurallara göre değerlendirir, **karar üretir**.
- **MedGemma Gateway** (`medgemma_gateway/`) → ince bir **geçit (proxy)**. Dışarıdan
  gelen isteği alır, MedGemma modeline iletir, cevabı geri döner. **Karar üretmez,
  provizyon mantığı bilmez.**
- **Ortak olan tek şey:** ikisi de aynı MedGemma model sunucusuna (vLLM · port 8000)
  gider. Başka hiçbir bağlantıları yoktur; biri kapanınca diğeri etkilenmez.

---

## Şema

```
                          ┌──────────────────────────────┐
                          │   MedGemma model sunucusu     │
                          │   vLLM · port 8000            │  ← TEK paylaşılan kaynak
                          └───────────────┬──────────────┘
                     ┌────────────────────┴─────────────────────┐
                     │                                           │
   ┌─────────────────┴──────────────────┐        ┌──────────────┴───────────────┐
   │  A) PROVİZYON DEĞERLENDİRME         │        │  B) MEDGEMMA GATEWAY          │
   │     (büyük iş akışı)                │        │     (ince geçit / proxy)      │
   │                                     │        │                               │
   │  • API + Dashboard   (port 8020)    │        │  • FastAPI          (port 8080)│
   │  • Worker (kuyruğu tüketir)         │        │  • API key doğrulama          │
   │  • Watcher (klasör izler)           │        │  • Eşzamanlılık kuyruğu       │
   │  • Redis (kuyruk)                   │        │  • İstek loglama              │
   │  • Qdrant + TEI (RAG/embedding)     │        │  • /v1/chat/completions       │
   │  • MSSQL (provizyon kaynağı)        │        │    → isteği MedGemma'ya iletir │
   │                                     │        │                               │
   │  intake → kural katmanları → KARAR  │        │  istek al → ilet → cevabı dön │
   └─────────────────────────────────────┘        └───────────────────────────────┘
        MedGemma'yı kendi içinden,                   MedGemma'yı düz geçişle
        değerlendirmenin bir parçası olarak          kullanır; başka hiçbir şey
        kullanır.                                    bilmez.
```

---

## A) Provizyon Değerlendirme Uygulaması

Asıl iş mantığının olduğu yer. Bir provizyonu baştan sona değerlendirir:
belge–hasta eşleşmesi, zorunlu evrak, HUV/SUT tanı-işlem kuralları ve MedGemma
klinik değerlendirmesi → **nihai karar** (uygun / manuel inceleme / red vb.).

Bileşenleri:

| Bileşen | Görev | Port / Yer |
|---|---|---|
| API + Dashboard | Sonuç sorgulama, panel | 8020 |
| Worker | Redis kuyruğundaki işleri işler | — (`run_worker.sh`) |
| Watcher | Klasöre düşen provizyonları kuyruğa ekler | — (`run_watcher.sh`) |
| Redis | İş kuyruğu | 6379 |
| Qdrant / TEI | Kural/kanıt arama (RAG) | 6333 / 8002 |
| MSSQL | Provizyon veri kaynağı | dış sunucu |

### İş nasıl giriyor? (intake) — iki yol var

Uygulama provizyonu **iki farklı kaynaktan** alabilir:

1. **Klasör dinleme (folder intake) — sürekli çalışan izleyici**
   - `run_watcher.sh start` ile çalışır (`provizyon_engine.intake.watcher`).
   - `data/intake/` altındaki her alt klasörü ~10 sn'de bir tarar.
   - İçinde "Hizmet Döküm Formu" (PopupPage) PDF'i olan ve dosya kopyası bitmiş
     (stabil) klasörleri iş'e çevirip Redis kuyruğuna ekler.
   - Aynı klasörü tekrar eklememek için Redis'te "seen" seti tutar.

2. **DB'den çekme (db intake) — komutla/tetiklemeyle**
   - `provizyon_engine.intake.db_intake` ile MSSQL `dbo.S_VW_PROVIZYON_AI` view'inden okur.
   - Tek provizyon: `python -m provizyon_engine.intake.db_intake <id> --enqueue`
   - Bekleyen toplu: `python -m provizyon_engine.intake.db_intake --pending --enqueue`
     (ProvizyonDurumId = 5, yani "AI incelemesi bekleyen").
   - **Bu bir daemon değildir**; sürekli DB dinleyen bir servis yoktur. Elle veya
     zamanlanmış görevle (cron vb.) çalıştırılır. DB bağlantısı için `MSSQL_*`
     ayarlarının (özellikle şifrenin) tanımlı olması gerekir.

> Özet: **klasör tarafı sürekli dinler (watcher)**, **DB tarafı çekmeli/tetiklemelidir (komut)**.

---

## B) MedGemma Gateway (`medgemma_gateway/`)

Provizyon uygulamasıyla **hiçbir ortak kodu, veritabanı veya kuyruğu yoktur.**
Tek işi: başka uygulamaların/makinelerin MedGemma modelini güvenli ve düzenli
şekilde kullanabilmesi.

- Girdi: OpenAI uyumlu `/v1/chat/completions` isteği (metin ve/veya görsel).
- Yaptığı: API key doğrular → eşzamanlılık kuyruğuna alır → MedGemma'ya (8000)
  iletir → cevabı olduğu gibi döner. İsteğe bağlı log tutar.
- Bilmediği: provizyon kuralları, Redis kuyruğu, Qdrant, MSSQL, karar mantığı —
  hiçbiri. Sadece "istek gelir, modele gider, cevap döner".

Kullanım ve çağırma örnekleri için: `README.md` ve `CAGIRMA_KILAVUZU.md`.

---

## Sık karışan noktalar

- **"Dashboard'da bekleyen provizyonlar var" ≠ kuyrukta iş var.** Panel, sonucu
  yazılmamış eski kayıtları varsayılan olarak `queued` gösterebilir; gerçek kuyruk
  derinliği `GET /queue/stats` ile görülür.
- **Gateway'e istek atmak provizyon değerlendirmesi başlatmaz.** Gateway sadece
  modele soru sorar; provizyon akışını yalnızca A sistemi (watcher/db intake +
  worker) yürütür.
- **İki sistem birbirini bozmaz.** Ortak nokta yalnızca 8000'deki modeldir; o da
  eşzamanlı birden çok isteği (continuous batching) karşılayabilir.
