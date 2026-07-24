# DGX Kural Önerileri Demo Handoff
Bu klasör 2026-07-23T14:51:17.670856+00:00 anında, full DGX çalışması devam ederken alınmış dondurulmuş bir snapshot'tır.

## Snapshot özeti
- Deterministik kural önerisi: 799
- İşlem coverage kaydı: 2060
- Resmî evidence: 1391
- Tamamlanan AI paketi: 297
- Aşama dağılımı: {"crosswalk_adjudication": 297}
- Durum dağılımı: {"accepted": 161, "blocked": 136}

Bu snapshot'taki AI sonuçları yalnız tamamlanmış aşamaları temsil eder. `accepted`, insan onayı değil; yalnız şema ve canonical constraint doğrulamasının geçtiğini gösterir.

## İlk adımlar
1. Bütünlük kontrolünü çalıştırın:
   `python3 scripts/validate_handoff.py`
2. DGX agent'ın çalışma dizinini bu klasör yapın.
3. Agent'a `DGX_AGENT_PROMPT.md` dosyasını görev metni olarak verin.
4. Komut özeti için `COMMANDS.md` dosyasını kullanın.

## Veri katmanları
- `data/base/`: Deterministik proposal, coverage, review template, schema ve kaynak manifesti.
- `data/snapshot/engine-proposals.partial-enriched.json`: AI sentezleri owner kayıtlarına eklenmiş ana UI girdisi.
- `data/snapshot/engine-proposals.ai-partial-results.json`: Paket bazında doğrulanmış/sanitize AI sonuçları.
- `data/snapshot/demo-sample.json`: Hızlı ekran geliştirme için küçük accepted/blocked örneklemi.
- `restricted/engine-proposals.ai-raw-responses.json`: Ham ve doğrulanmamış model cevapları. Varsayılan UI/static assets içine konulmaz.

## Değişmez güvenlik sınırları
- Veritabanına yazma, SQL üretme/çalıştırma, otomatik onay ve kural uygulama yoktur.
- Resmî evidence ile AI hipotezi ekranda ayrı gösterilir.
- Kaynak JSON/CSV dosyaları değiştirilmez.
- Demo kararları yalnız tarayıcı local storage'ında tutulur veya ayrı JSON olarak dışa aktarılır.
- `restricted/` varsayılan olarak web üzerinden servis edilmez.
