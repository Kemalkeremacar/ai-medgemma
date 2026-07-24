# Generated Runtime Artifacts

Bu klasor, kural motorlarinin calisma zamaninda kullandigi
derlenmiş JSON/JSONL dosyalarini icerir.

## Icerik

### `diagnosis_rules/runtime/`
- `huv_diagnosis_runtime_lookup.json` -- 8,050 HUV tani kurali (code -> rule index)
- `huv_diagnosis_runtime_rules.jsonl` -- Tum HUV kurallari (satirlik JSON)
- `huv_diagnosis_auto_rules.jsonl/.csv` -- Otomatik karar verilebilen kurallar
- `huv_diagnosis_review_queue.jsonl/.csv` -- Manuel inceleme gerektiren kurallar
- `huv_diagnosis_runtime_summary.json` -- Istatistik ozet

### `sut_diagnosis_rules/ek2b/runtime/`
- `sut_diagnosis_runtime_lookup.json` -- 7,058 SUT tani kurali (EK-2B kaynakli)
- `sut_diagnosis_runtime_rules.jsonl` -- Tum SUT kurallari
- `sut_diagnosis_auto_rules.jsonl/.csv` -- Otomatik karar verilebilen
- `sut_diagnosis_review_queue.jsonl/.csv` -- Manuel inceleme gerektiren
- `sut_diagnosis_runtime_summary.json` -- Istatistik ozet

### `sut_diagnosis_rules/ek2b/qdrant_backfill/`
- `backfill_summary.json` -- Qdrant collection'a yukleme raporu

### `unified_catalog_final_medgemma/`
- `unified_catalog.jsonl` -- Birlesik HUV+SUT katalog (offline / geri acma icin)
- `huv_sut_crosswalk.jsonl` -- HUV <-> SUT kod eslestirmeleri (dosya durur)
- `huv_note_rules.jsonl` -- HUV notlarina dayali kurallar

> **Runtime (2026-07):** Provizyon degerlendirmede HUV→SUT crosswalk
> varsayilan **kapali** (`PROVIZYON_ENABLE_HUV_SUT_CROSSWALK=0`).
> HUV tani ve dogrudan SUT kurallari ayri calisir; bu dosyalar silinmez.
> Belgesiz akis: `docs/provizyon/BELGESIZ_AKIS.md`.

### Root dosyalar
- `sut_rules_merged.json` -- 6,551 SUT islem kurali (frekans, es-fatura vb.)
- `sut_index_core.json` -- SUT kod indeksi

## Yeniden Uretim

Bu dosyalar harici kaynaklardan (SUT EK-2B dokumanlari, HUV listeleri)
derlenmistir. Uretim scriptleri bu repo'da degildir.
Mevcut dosyalar dogrudan kullanilabilir; degisiklik icin
lib/diagnosis_rules/ altindaki checker modulleriyle test edilmelidir.
