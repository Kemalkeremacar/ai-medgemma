# Belgesiz Provizyon Değerlendirme Akışı

Yeni / yeniden kuyruğa alınan **belgesiz** işler (`documents_mode=skipped_full_pipeline`)
aşağıdaki pipeline ile değerlendirilir.

**Önemli (2026-07):** Runtime **HUV→SUT eşleştirmesi kapalıdır**.
HUV kuralları ve SUT kuralları **ayrı** çalışır. Katalog dosyası
(`huv_sut_crosswalk.jsonl`) diskte durur; canlı değerlendirmede HUV kodu
SUT’a çevrilmez. Geri açmak: `PROVIZYON_ENABLE_HUV_SUT_CROSSWALK=1` + API/worker restart.

İlgili: [`PROVIZYON.md`](../PROVIZYON.md) · bayrak: `OrchestratorConfig.enable_huv_sut_crosswalk`

---

## Baştan sona diyagram

```mermaid
flowchart TD
  intake["1. Giriş<br/>DB / API üstveri<br/>yaş · cinsiyet · ICD · HUV/SUT"]
  queue["2. Redis kuyruk"]
  worker["3. Worker · Orchestrator"]

  intake --> queue --> worker

  worker --> skipBelge["4. Belge–Hasta<br/>SKIPPED"]
  worker --> skipEvrak["5. Zorunlu Evrak<br/>SKIPPED"]

  skipBelge --> branch{Kod ailesi?}
  skipEvrak --> branch

  branch -->|HUV / both| huvTani["6. HUV tanı kuralı<br/>tani_kurali<br/>Qdrant huv_diagnosis_rules"]
  branch -->|SUT / both| sutTani["6b. SUT tanı kuralı<br/>sut_tani_kurali<br/>Qdrant sut_diagnosis_rules"]
  branch -->|yalnız HUV| noSutTani["6b. SUT tanı SKIP<br/>HUV provizyonu"]
  branch -->|yalnız SUT| noHuvTani["6. HUV tanı SKIP<br/>SUT provizyonu"]

  huvTani --> sutIslem
  sutTani --> sutIslem
  noSutTani --> sutIslem
  noHuvTani --> sutIslem

  sutIslem{"7. SUT işlem kuralı<br/>sut_kurali"}
  sutIslem -->|doğrudan SUT kodu var| sutEval["SUTEvaluator<br/>advise allow_huv_crosswalk=false"]
  sutIslem -->|yalnız HUV| sutSkip["SKIP<br/>huv_sut_crosswalk_disabled"]

  sutEval --> medgemma
  sutSkip --> medgemma

  medgemma["8. MedGemma<br/>belgesiz prompt<br/>üstveri + kural özeti · belge alanları null"]
  medgemma --> merge["9. merge_decisions + risk"]
  merge --> out["10. Sonuç<br/>Redis · JSONL · Qdrant findings"]

  xwalkOff[["HUV→SUT runtime eşleştirme KAPALI<br/>köprü yok · kurallar ayrı"]]
  xwalkOff -.-> sutIslem
```

---

## Katman özeti (belgesiz)

| Adım | Katman | Belgesiz davranış |
|---|---|---|
| 4 | `belge_hasta` | **SKIPPED** (hata değil) |
| 5 | `zorunlu_evrak` | **SKIPPED** (hata değil); HUV→SUT ile evrak aranmaz |
| 6 | `tani_kurali` | HUV kodu varsa **çalışır** |
| 6b | `sut_tani_kurali` | Doğrudan SUT kodu varsa **çalışır** |
| 7 | `sut_kurali` | Doğrudan SUT → **çalışır**; yalnız HUV → **SKIP** (`huv_sut_crosswalk_disabled`) |
| 8 | `medgemma` | **Çalışır** (görsel yok; metin/üstveri) |
| 9 | merge | Soft/skip REVIEW + belgesiz `medium\|high` → uygun yolu açık |

---

## Ne bozulmaz / ne değişmez

- HUV ICD tanı motoru aynı
- SUT ICD tanı + SUT işlem kuralları (doğrudan SUT) aynı
- MedGemma, persistence, risk birleştirme aynı
- Sadece HUV kodunu SUT’a çevirip SUT kuralına/zorunlu-evraka sokma yolu kapalı

Eski bitmiş sonuçlar diskte eski değerlendirmeyi tutar; yeni davranış için
yeniden enqueue gerekir.
