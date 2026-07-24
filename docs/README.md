# GemmaApp — Dokümantasyon

Tüm bilgilendirme / mimari / ürün belgeleri burada toplanır. Kod klasörlerinde
yalnızca kısa yönlendirme bırakılır.

## Genel

| Belge | İçerik |
|---|---|
| [MIMARI.md](./MIMARI.md) | Servis haritası, ayrım, ürün dili |
| [PROVIZYON.md](./PROVIZYON.md) | Provizyon değerlendirme sistemi (sistem #3) |
| [YONETICI_DURUM_VE_SONRAKI_ADIMLAR.md](./YONETICI_DURUM_VE_SONRAKI_ADIMLAR.md) | Yönetici cevabı, 703790, sonraki adımlar |

## Gateway (sistem #2)

| Belge | İçerik |
|---|---|
| [gateway/README.md](./gateway/README.md) | Kurulum, env, çalıştırma |
| [gateway/CAGIRMA_KILAVUZU.md](./gateway/CAGIRMA_KILAVUZU.md) | Dış istemci çağrı örnekleri |
| [gateway/PROVIZYONDAN_AYRI.md](./gateway/PROVIZYONDAN_AYRI.md) | Gateway ≠ Provizyon ayrımı |

## Provizyon (ürün / backlog)

| Belge | İçerik |
|---|---|
| [provizyon/SHADOW_PUBLISH_BACKLOG.md](./provizyon/SHADOW_PUBLISH_BACKLOG.md) | Shadow / validate / yayın backlog |
| [provizyon/PROMPT_PATIENT_FINDINGS_ANALYSIS.md](./provizyon/PROMPT_PATIENT_FINDINGS_ANALYSIS.md) | Patient findings analiz notları |

## Kod konumları (belge değil)

- Provizyon motoru: `provizyon/`
- Gateway kodu: `medgemma_gateway/`
- vLLM servisi: `services/vllm_medgemma/`
- Servis yöneticisi: `./svc`
- Review-reduction handoff: `data/handoffs/review_reduction_dgx_transfer_bundle_20260709/`
- Validate: `python provizyon/scripts/validate_shadow_handoff.py`
