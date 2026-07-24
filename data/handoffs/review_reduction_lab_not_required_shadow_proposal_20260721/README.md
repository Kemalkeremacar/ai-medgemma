# Rutin HUV Lab — Shadow Policy Proposal (2026-07-21)

Belgesiz 100'lük provizyon koşusundan üretilmiş **shadow-only** aday paket.
Canlı runtime'ı değiştirmez (`enabled=false`, `auto_apply=false`).

## Ne öneriyor?

Faz-1'deki 12 sık lab HUV kodu için `diagnosis_policy: not_required`
(tanı aranmaz → shadow PASS adayı). Gerekçe: batch'te bu kodlar zaten
`diagnosis_required=false` + motor metni "tanı gerektirmez" iken
`review_required` yüzünden manuel kalıyor.

## Dosyalar

| Dosya | İçerik |
|---|---|
| `LAB_SHADOW_POLICY_PROPOSAL.json` | Ana proposal + safety |
| `LAB_CANDIDATE_REGISTER.json/.csv` | Kod frekans / ICD özeti |
| `LAB_POLICY_OVERLAY_DRAFTS.json` | not_required overlay taslakları |
| `expert_decision.*.example.json` | Uzman formları (boş/imzasız) |
| `rule_draft.huv_03.10886.example.json` | İV ayrı ICD-scoped taslak |
| `LAB_SHADOW_MONITORING_PLAN.json` | Pilot eşikleri |
| `LAB_SHADOW_ROLLBACK_MANIFEST.json` | Geri alma adımları |
| `LAB_GOVERNANCE_REVIEW.txt` | Kısa brifing |

## Uzman formu

```bash
python provizyon/scripts/validate_shadow_handoff.py --schema-check \
  --expert-decision provizyon/config/expert_decision.huv_routine_lab_phase1.example.json
```

Config kopyaları:
- `provizyon/config/expert_decision.huv_routine_lab_phase1.example.json`
- `provizyon/config/expert_decision.huv_34.53153.example.json` (CBC)

## B ile ilişki

Bu paket **A (policy)**. Belgesiz AI medium override **B** ayrı iş; canlı kural yazmaz.
