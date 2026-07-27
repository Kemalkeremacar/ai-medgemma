# Kural Önerileri Demo Uygulaması

Bu uygulama, handoff klasöründeki dondurulmuş DGX snapshot verilerini **read-only** okuyan on-prem bir inceleme demosudur. Yeni bir web sayfası olarak `app/` altında çalışır; Provizyon veya üretim API’sine bağlı değildir.

## Kurulum / çalıştırma

Bağımlılık yoktur (Python 3 standart kütüphane).

```bash
cd /path/to/data/handoffs/kural-onerileri
python3 scripts/validate_handoff.py
python3 -m unittest discover -s app/tests -v
python3 app/server.py --host 127.0.0.1 --port 8080
```

Tarayıcı: `http://127.0.0.1:8080/`

Provizyon paneline gömülü erişim (ayrı sunucu gerekmez):

- Menü: **Kural Önerileri**
- Doğrudan: `http://127.0.0.1:8020/dashboard/kural-onerileri`
- API köprüsü: `/rule-proposal-demo/api/...`
- Ham cevap: `PROVIZYON_RULE_PROPOSAL_ENABLE_RAW=1` (API yeniden başlatılmalı)

Ham (doğrulanmamış) model cevaplarını bağımsız sunucuda açmak için:

```bash
python3 app/server.py --host 127.0.0.1 --port 8080 --enable-raw
```

## Veri kaynakları

| Kullanım | Dosya |
|----------|--------|
| Dashboard sayaçları | `data/snapshot/demo-summary.json` |
| Ana UI girdisi | `data/snapshot/engine-proposals.partial-enriched.json` |
| AI paket sonuçları | `data/snapshot/engine-proposals.ai-partial-results.json` (3722 paket; final `complete`) |
| Schema / base | `data/base/*` |
| Ham cevaplar (opt-in) | `restricted/engine-proposals.ai-raw-responses.json` |

Final AI dağılımı: `rule_synthesis=799` (637 hipotez kartı), `crosswalk_adjudication=1416`, `proposal_rescue=1507` (hepsi `insufficient_evidence`). Deterministik öneri sayısı **799** kalır.

Öneri detayında **3. AI kural hipotezi** katmanı yalnız `rule_synthesis` sonuçlarını gösterir. Liste filtresi: `hasAi=1` (finalde 799 önerinin tümü AI değerlendirmeli).

Kaynak JSON/CSV dosyaları değiştirilmez.

## Ekranlar

1. Özet — sayaçlar + uzman rehberi (`YARDIM_UZMAN.md`)  
2. Öneriler listesi — arama, filtre (liste tipi HUV/SUT), pagination  
3. Öneri detayı — kanıt / önerilen alanlar / örnek kural taslağı  
4. Kararlarım — local storage + JSON export  
5. Öneri AI — uzman sohbeti (Provizyon API: kural motoru / HUV-SUT / provizyon / MedGemma)

HUV↔SUT crosswalk karşılaştırması bu demoda **yoktur**; HUV ve SUT kuralları ayrı incelenir.  
Kolon başlıklarında kısa tooltip vardır; ayrıntı Özet sekmesindedir.  


## Güvenlik sınırları

- Veritabanına yazma, SQL, model çağrısı, otomatik onay/apply yok  
- `accepted` = teknik doğrulama geçti; insan onayı değil  
- Demo kararları yalnız tarayıcı local storage / export JSON  
- `restricted/` static web root’ta değil; yalnız `--enable-raw` ile API  
- Varsayılan bind: `127.0.0.1`  

## API özeti

- `GET /api/summary`
- `GET /api/proposals?...` (`listeTipi=HUV|SUT` dahil)
- `GET /api/proposals/{id}`
- `GET /api/proposals/{id}/example-rules`
- `GET /api/help`
- `GET /api/raw/{packetId}` (yalnız `--enable-raw`; debug)
