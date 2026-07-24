# Yönetici Durumu — Ne Yaptık / Ne Karşılıyoruz / Ne Yapabiliriz

Bu belge, yönetici mesajlarındaki üç katmanlı modeli (klinik AI + deterministik
policy + uzman) mevcut sistemle eşleştirir; yöneticiye verilecek cevabı,
703790 uzman brifingini, uzman karar şemasını ve shadow/yayın backlog’unu
tek yerde toplar.

İlgili belgeler: [`MIMARI.md`](./MIMARI.md) · [`PROVIZYON.md`](./PROVIZYON.md) ·
şemalar: [`expert_decision.schema.json`](../provizyon/config/expert_decision.schema.json),
[`rule_draft.schema.json`](../provizyon/config/rule_draft.schema.json)

---

## 1. Yöneticiye kısa cevap (kopyalanabilir)

> İki ayrı yerindeliği net ayırıyoruz: MedGemma klinik yorum üretir; canlı
> ödeme/mevzuat kararı deterministik kural motorundadır. Şimdiye kadar
> SUT/HUV/ICD listelerini aranabilir/kural haline getirdik, geçmiş
> veriden adayları çıkardık, AI’yı canlı karar verici yapmadık. Canlı
> değerlendirmede HUV→SUT kod köprüsü kapalıdır: HUV ve SUT kuralları ayrı
> çalışır (katalog dosyası durur, runtime eşleştirme yok). 703790
> örneğinde model geniş ICD önerirken tıbbi kod açıklamasında hata yaptı;
> bu yüzden öneriyi reddedip yalnız H40 için kapalı shadow proposal
> hazırladık — canlı REVIEW_REQUIRED değişmedi. Bundan sonraki adım daha
> fazla model değil: yetkili medikal-policy uzmanının H40 kapsamını
> netleştirmesi, ardından taslak → shadow pilot → yönetim onayı → kontrollü
> yayın. Altyapı tarafında MedGemma ve provizyon motoru çalışır durumda;
> gateway için istemci SLA’sı (300 sn / 504) tanımlandı.

### İki sepet (karıştırılmamalı)

| Sepet | İçerik |
|---|---|
| **1 — Kurumsal bilgi + policy** | Listeler, Qdrant kural hafızası, deterministik HUV/SUT motorları (runtime HUV→SUT köprüsü kapalı), review_queue, 703790 kapalı shadow proposal |
| **2 — Çalışma ortamı (GemmaApp)** | vLLM, gateway SLA, provizyon API/worker/watcher, Redis/Qdrant/TEI, mimari dokümanlar |

---

## 2. Üç katman — ne karşılıyoruz?

| Katman | Rol | Durum |
|---|---|---|
| **Klinik yerindelik** | MedGemma tavsiye / risk | Kısmen (pipeline + gateway/WebUI) |
| **Kurumsal policy motoru** | SUT/HUV/ICD deterministik karar | Büyük ölçüde (Provizyon) |
| **Uzman + shadow + yayın** | Yapılandırılmış onay → kural | Kısmen (703790 paket + backlog) |

**Ürün dili (sabit):**

1. **Klinik Yerindelik Asistanı** — MedGemma ağırlıklı; çıktı tavsiye ve risk işareti; canlı bağlayıcı karar değil.
2. **Kurumsal Policy ve Provizyon Motoru** — deterministik; SUT/HUV/ICD/belge; açıklanabilir sonuç.
3. **Uzman insan** — belirsizlik / yeni kural; yapılandırılmış karar → taslak → shadow → yayın.

---

## 3. Gereksinim matrisi (özet)

### A) Karşılıyoruz / yapılmış

- Geçmiş analiz, REVIEW yoğunluğu, auto vs review_queue
- Canlı karar deterministik; AI ≠ canlı kural
- Katalog / runtime kurallar / Qdrant (hafıza, karar verici değil)
- 703790: AI geniş öneri reddi → H40-only; overlay `enabled=false`; canlı değişmedi
- Gateway SLA 300 sn / HTTP 504 (istemci loglama sözleşmesi)

### B) Kısmen

- Klinik input paketi (lab/ilaç/geçmiş her vakada tam değil)
- Shadow proposal var; canlı yanında koşan ölçüm ürünü yok
- Aday kovaları veri tarafında; uzman UI yok

### C) Sonraki aşamalar

1. 703790 uzman değerlendirmesi (aşağı §4)
2. Yapılandırılmış uzman kararı → kural taslağı (§5 + schema dosyaları)
3. Validate + shadow pilot + yayın/rollback (§6 backlog)
4. Sürekli öğrenme döngüsünün ürünleşmesi

---

## 4. Aşama 1 — 703790 uzman brifingi

### 4.1 İşlem

- **Kod:** 703790 — Nerve Fiber Analyzer (NFA)
- **Canlı davranış (değişmedi):** `REVIEW_REQUIRED` / otomatik onay yok
- **Shadow önerisi:** yalnız **H40\*** (glokom); `enabled=false`, `apply_ready=false`, `auto_apply=false`

### 4.2 Paket konumu (bu GemmaApp kutusu)

Canonical DGX transfer bundle (açılmış):

```
data/handoffs/urun-hikayesi/
```

703790 düzeltilmiş proposal (bundle içi):

```
data/handoffs/urun-hikayesi/artifacts/review_reduction_703790_shadow_policy_proposal_20260720/
```

Portable validate (Windows SUT path gerekmez):

```
python provizyon/scripts/validate_shadow_handoff.py
```

Read-only API (Provizyon :8020):

- `GET /shadow/review-reduction/summary`
- `GET /shadow/review-reduction/decision-register`
- `GET /shadow/review-reduction/703790`

Panel: Dashboard → **Ürün Hikâyesi** (karar defteri + H40 özeti).  
Override: `PROVIZYON_SHADOW_HANDOFF_ROOT`.

SUT ortamındaki orijinal yollar (tarihsel referans; bu kutuda açılmış paket `data/handoffs/urun-hikayesi/`):

```
SUT/generated/shadow_quality_gate/review_reduction_703790_shadow_policy_proposal_20260720/
SUT/generated/dgx_handoff/review_reduction_dgx_transfer_bundle_20260709.zip
```

### 4.3 Uzmanddan istenen kararlar

Uzman aşağıdaki alanları doldurmalı (şema: `expert_decision.schema.json`):

1. H40 grubu 703790 için uygun mu? (evet / hayır / daralt)
2. Hariç tutulacak H40 alt kodları
3. İlave edilmesi gereken tanılar (varsa)
4. İstisnalar / özel durumlar
5. Eksik tanıda karar (`REVIEW_REQUIRED` vb.)
6. Gerekçe ve resmi/kurumsal dayanak
7. Güven düzeyi
8. Shadow pilot’a izin veriliyor mu?
9. Kararı veren kişi, yetki, tarih

### 4.4 Counterfactual özet (canlı onay değil)

| Metrik | Değer |
|---|---|
| Geçmiş 703790 REVIEW | 212 |
| H40 eşleşmesi | 146 (~%68,9) |
| Teorik tam çözüm | 7 |
| Kısmi çözüm | 139 |
| H40 dışı (manuel kalır) | 66 |

### 4.5 Sunum kontrol listesi

- [ ] Proposal HTML + manifest paketten açıldı
- [ ] Validator PASS kanıtı gösterildi (yazma yok, case ID yok)
- [ ] MedGemma geniş liste hatası (göz→kulak) anlatıldı
- [ ] Canlı runtime’ın değişmediği doğrulandı
- [ ] Uzman `expert_decision` JSON’unu doldurdu / imzaladı
- [ ] Shadow açılacaksa yönetim bilgilendirildi (henüz apply değil)

---

## 5. Uzman kararı → kural taslağı

Makine sözleşmeleri:

| Dosya | Amaç |
|---|---|
| [`expert_decision.schema.json`](../provizyon/config/expert_decision.schema.json) | Uzmanın doldurduğu yapılandırılmış karar |
| [`rule_draft.schema.json`](../provizyon/config/rule_draft.schema.json) | AI/insan tarafından üretilen aday kural paketi |
| [`expert_decision.703790.example.json`](../provizyon/config/expert_decision.703790.example.json) | 703790 için boş/örnek form |

Akış: uzman JSON → (model veya editör) `rule_draft` → validate → shadow → yönetim onayı → runtime.

**Kural:** Taslak veya MedGemma çıktısı hiçbir zaman doğrudan canlı runtime’a yazılmaz.

---

## 6. Ürün backlog — shadow / validate / yayın

Öncelik sırası (teknik işler):

| ID | İş | Kabul kriteri |
|---|---|---|
| SG-1 | `rule_draft` validate CLI | Sözdizimi, şema, mevcut kural çakışması, güvenlik bayrakları |
| SG-2 | Shadow runner (overlay) | Canlı kararı değiştirmez; shadow sonucu ayrı log/Qdrant preview |
| SG-3 | Pilot metrikleri | 30 gün / ≥100×703790 REVIEW / ≥50×H40 / uyuşmazlık ≤%2 / 0 deterministik FAIL |
| SG-4 | Yayın kapısı | `human_admin_approval_present=true` + sürüm + audit kaydı olmadan runtime yazılmaz |
| SG-5 | Rollback | Overlay kapat; shadow yazmayı durdur; canlı REVIEW_REQUIRED doğrula |
| SG-6 | Uzman UI (opsiyonel) | `expert_decision` formu + kuyruk kovaları |

703790 shadow açılış önerisi (yönetici metniyle aynı): gözlem ≥30 gün; yeterli örnek; insanla ≤%2 uyuşmazlık; H40 adaylarında deterministik FAIL yok.

---

## 7. Sonraki somut adımlar (iş)

1. **Hemen:** §1 cevabını yöneticiye ilet; §4 brifing + paket ile medikal-policy uzmanına git.
2. **Uzman sonrası:** `expert_decision` → `rule_draft` → SG-1 validate.
3. **Uygunsa:** SG-2/SG-3 shadow pilot; rapor → yönetim.
4. **Onay sonrası:** SG-4 kontrollü yayın; SG-5 rollback hazır.
5. **Ürün dili:** Klinik Asistan vs Policy Motor — `MIMARI.md` / `PROVIZYON.md` / Yönetici paneli.
