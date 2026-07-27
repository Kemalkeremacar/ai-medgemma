# Kural Önerileri Handoff

Bu klasör, Provizyon panelindeki **Kural Önerileri** ekranının read-only veri + UI paketidir.

Konum: `data/handoffs/kural-onerileri/`  
(Üst indeks: `../README.md`)

## Snapshot özeti
- Deterministik kural önerisi: 799 *(değişmez)*
- İşlem coverage kaydı: 2060
- Resmî evidence: 1391
- Tamamlanan AI paketi: **3722** · `sourceState=complete` *(2026-07-27 final: 1639 → 3722)*
- Aşama: `crosswalk_adjudication=1416`, `rule_synthesis=799`, `proposal_rescue=1507`
- Durum: `accepted=2828`, `blocked=892`, `call_or_parse_error=2`
- AI kural hipotezi (kart): **637** (`rule_synthesis` + `accepted` + `outcome=proposal`)
- `proposal_rescue`: 1507 · hepsi `insufficient_evidence` (yeni öneri sayısı değildir)

`accepted` insan onayı değildir; yalnız şema / canonical constraint doğrulamasının geçtiğini gösterir.

## İlk adımlar
1. Bütünlük: `python3 scripts/validate_handoff.py`
2. Test: `python3 -m unittest discover -s app/tests -v`
3. Panel: `http://127.0.0.1:8020/dashboard/kural-onerileri`
4. Bağımsız demo: `python3 app/server.py --host 127.0.0.1 --port 8080`

Override: `PROVIZYON_RULE_PROPOSAL_HANDOFF_ROOT`

## Veri katmanları
- `data/base/`: Deterministik proposal, coverage, review template, schema ve kaynak manifesti.
- `data/snapshot/engine-proposals.partial-enriched.json`: Ana UI girdisi (AI sentezleri owner’a ekli).
- `data/snapshot/engine-proposals.ai-partial-results.json`: Paket bazında sanitize AI sonuçları.
- `data/snapshot/demo-sample.json`: Küçük örneklem.
- `restricted/`: Ham model cevapları (varsayılan kapalı; UI’da servis edilmez).

## Güvenlik
- DB / SQL / otomatik onay / apply yok.
- Resmî evidence ile AI hipotezi ekranda ayrı.
- Demo kararları yalnız tarayıcı localStorage / JSON export.
