# Expert demo handoff paketleri

İki panel ekranı aynı kök altında tutulur:

| Klasör | Panel menü | API |
|--------|------------|-----|
| `kural-onerileri/` | **Kural Önerileri** | `/dashboard/kural-onerileri`, `/rule-proposal-demo/api/...` |
| `urun-hikayesi/` | **Ürün Hikâyesi** (shadow) | `/shadow/review-reduction/*` |
| `lab-not-required/` | (ekran yok; örnek politika paketi) | expert_decision örnekleri |

Override:
- `PROVIZYON_RULE_PROPOSAL_HANDOFF_ROOT` → `kural-onerileri` kökü
- `PROVIZYON_SHADOW_HANDOFF_ROOT` → `urun-hikayesi` kökü

Validate:
- `python data/handoffs/kural-onerileri/scripts/validate_handoff.py`
- `python provizyon/scripts/validate_shadow_handoff.py`

Not: Bazı dondurulmuş dosyalarda (`DGX_TRANSFER_MANIFEST.json`, Qdrant contract) SUT/Windows **provenance** path’leri kasıtlı bırakılmıştır; canlı kök bu dizindir.

Her iki paket de read-only’dir; canlı kural / DB / Qdrant yazmaz.
