# Shadow / Validate / Yayın — Ürün Backlog

Kaynak: yönetici mesajları + [`YONETICI_DURUM_VE_SONRAKI_ADIMLAR.md`](../YONETICI_DURUM_VE_SONRAKI_ADIMLAR.md).

Şemalar: [`expert_decision.schema.json`](../../provizyon/config/expert_decision.schema.json),
[`rule_draft.schema.json`](../../provizyon/config/rule_draft.schema.json).

## İlkeler (değişmez)

- `auto_apply` her zaman `false`.
- Canlı runtime yazımı yalnızca `human_admin_approval_present=true` + yayın kapısı sonrası.
- Shadow canlı `PASS/FAIL/REVIEW` sonucunu değiştirmez; ayrı kanalda kaydeder.
- MedGemma çıktısı doğrudan kural değildir.

## Tamamlanan (görünürlük / validate — A)

- Bundle kökü: `data/handoffs/urun-hikayesi/`
- Read-only API: `/shadow/review-reduction/{summary,decision-register,703790}`
- Dashboard **Ürün Hikâyesi**: karar defteri + düzeltilmiş H40 metrikleri
- Portable CLI: `python provizyon/scripts/validate_shadow_handoff.py`
  (`--schema-check` ile `expert_decision` / `rule_draft` JSON Schema)
- **SG-2 observer henüz yok** (canlı yanında gölge log üretilmez; overlay kapalı)

## Backlog

### SG-1 — `rule_draft` validate CLI

- **Kısmen:** `provizyon/scripts/validate_shadow_handoff.py` handoff + safety bayrakları
  ve opsiyonel schema-check yapar.
- Kalan: runtime kural çakışması / ICD önek biçimi derin kontrolü; tam `rule_draft`
  yayın öncesi kapı.

### SG-2 — Shadow runner (overlay)

- Henüz yok.
- `governance.enabled=true` iken paralel değerlendirme.
- Canlı motor kararı aynen kalır.
- Shadow sonucu: log JSONL ve/veya ayrı Qdrant preview collection (production findings’e yazılmaz).

### SG-3 — Pilot metrikleri (703790 varsayılan eşikler)

| Metrik | Eşik |
|---|---|
| Gözlem süresi | ≥ 30 gün |
| 703790 REVIEW örnekleri | ≥ 100 |
| H40 (include) eşleşmesi | ≥ 50 |
| İnsan kararıyla uyuşmazlık | ≤ %2 |
| Adaylarda deterministik FAIL | 0 |

Eşik aşılırsa otomatik stop önerisi (SG-5).

### SG-4 — Yayın kapısı

Sıra zorunlu:

1. `status=validated`
2. Shadow metrikleri yeşil
3. Uzman `live_apply_approved` (veya ayrı yönetim imzası)
4. `human_admin_approval_present=true`
5. Sürümlü runtime kaydı + audit (kim, ne zaman, hangi draft_id)

### SG-5 — Rollback

1. Shadow gözlemciyi kapat (`enabled=false`)
2. Shadow aday işareti üretimini durdur
3. Varsa yalnız shadow/preview kayıtlarını kaldır
4. Canlı `REVIEW_REQUIRED` (veya önceki runtime) değişmediğini doğrula

### SG-6 — Uzman UI (opsiyonel)

- `expert_decision` formu
- Aday kovaları: öncelik / kod sorunu / manuel / shadow / beklet
- 703790 örneği ile ilk kullanım

## İlk pilot: 703790

- Örnek uzman formu: [`expert_decision.703790.example.json`](../../provizyon/config/expert_decision.703790.example.json)
- Proposal (bu kutu): `data/handoffs/urun-hikayesi/artifacts/review_reduction_703790_shadow_policy_proposal_20260720/`
- Mevcut durum: overlay kapalı; canlı değişmedi; görünürlük/validate hazır; SG-2 observer yok
