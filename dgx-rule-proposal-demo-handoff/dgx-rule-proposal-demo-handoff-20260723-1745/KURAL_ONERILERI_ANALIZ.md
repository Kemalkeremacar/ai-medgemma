# Kural Önerileri Sayfası — Analiz

**Tarih:** 2026-07-23  
**Kapsam:** DGX handoff snapshot demosu + Provizyon paneline gömülü “Kural Önerileri” sayfası  
**Durum:** Read-only inceleme demosu (üretim kural motoruna yazmaz)

---

## 1. Özet

DGX üzerinde üretilen **dondurulmuş** bir kural önerisi paketinden, uzmanların insan-inceleme yapabileceği on-prem bir web sayfası kuruldu. Sayfa:

1. Deterministik motorun ürettiği kural önerilerini listeler ve detaylar.
2. Resmî kaynak evidence’ı (SUT/HUV alıntıları) gösterir.
3. HUV↔SUT crosswalk karşılaştırması **kullanılmaz**; HUV ve SUT kural adayları ayrı incelenir.
4. Uzmanın demo kararını (`approve` / `edit` / `reject` / `needs_more_evidence`) tarayıcıda saklar; kaynak dosyaya ve veritabanına yazmaz.

Provizyon sol menüsündeki **Kural Önerileri** sekmesi bu demoyu iframe ile sunar (`/dashboard/kural-onerileri`).

---

## 2. Ne yapıldı? (Teknik teslim)

### 2.1 Handoff verisi
Kaynak paket: `dgx-rule-proposal-demo-handoff-20260723-1745` (zip + açılmış klasör).

| Metrik | Değer |
|--------|------:|
| Deterministik kural önerisi | 799 |
| İşlem coverage | 2060 |
| Resmî evidence | 1391 |
| Engine signal | 2322 |
| Tamamlanan AI paketi | 297 |
| AI aşaması | `crosswalk_adjudication` (297) |
| AI status | accepted 161 / blocked 136 |

Snapshot, full DGX run **henüz bitmeden** alınmıştır (`sourceState: running`). Bu yüzden AI sonuçları partial’dır.

### 2.2 Demo uygulama (`app/`)
Handoff’un istediği gibi uygulama yalnız `app/` altında üretildi:

| Dosya / klasör | İşlev |
|----------------|--------|
| `app/server.py` | Bağımsız stdlib sunucu (`--host`, `--port`, `--enable-raw`) |
| `app/data_store.py` | Büyük JSON’u bellek içi indeks; arama, filtre, sayfalama |
| `app/static/` | Türkçe UI (HTML/CSS/JS, CDN yok) |
| `app/tests/` | Backend / API testleri |
| `app/README.md` | Kurulum ve güvenlik notları |

**Zorunlu ekranlar:** Dashboard, kural listesi, öneri detayı, AI değerlendirmeleri, crosswalk, uzman demo kararı.

### 2.3 Provizyon entegrasyonu
| Parça | İşlev |
|-------|--------|
| `provizyon_engine/rule_proposal_handoff.py` | Handoff `DataStore` köprüsü (lazy load) |
| `provizyon_engine/api.py` | `/dashboard/kural-onerileri`, `/rule-proposal-demo/api/...` |
| `static/dashboard.html` | Sol menü + iframe gömme |

Ayrı 808x sunucusu zorunlu değildir; Provizyon API (8020) üzerinden erişilir.

### 2.4 Güvenlik sınırları (bilinçli tasarım)
- Veritabanına / Qdrant’a yazma yok
- SQL üretimi / çalıştırma yok
- Canlı model çağrısı yok
- Otomatik onay / apply yok
- Kaynak JSON/CSV değiştirilmez
- `restricted/` ham cevaplar varsayılan kapalı
- Demo kararları: `localStorage` + JSON export

---

## 3. Sayfa ne işe yarıyor? (İş akışı)

```text
Deterministik motor önerileri (799)
        +
Resmî evidence (alıntı / locator)
        +
AI crosswalk hipotezi (partial, 297 paket)
        ↓
Uzman ekranda inceler
        ↓
Demo kararı (localStorage) — gerçek onay değildir
```

### Detay katmanları (bilerek ayrıldı)
1. **Deterministik:** kural tipi, öncelik A/B/C, proposedFields, engine signals, mevcut kural karşılaştırması  
2. **Resmî evidence:** kaynak dosya, satır, locator, doğrulanmış alıntı  
3. **AI hipotezi:** outcome, rationale, evidenceGaps, expertQuestions, seçilen crosswalk  

Bu ayrım, AI çıktısının “onaylı kural” gibi okunmasını engellemek içindir.

### Etiket anlamları
| Etiket | Anlamı |
|--------|--------|
| Teknik doğrulamayı geçti (`accepted`) | Şema / canonical kontrol geçti; **insan onayı değil** |
| Güvenlik kontrolünde engellendi (`blocked`) | Constraint ihlali; structured sonuç güvenilmez |
| AI kural hipotezi (`proposal`) | Model değişiklik öneriyor |
| Değişiklik önermiyor (`no_change`) | Model mevcut hali uygun buluyor |
| Kanıt yetersiz (`insufficient_evidence`) | Model ek kanıt istiyor |
| Henüz işlenmedi | Bu snapshot’ta AI aşaması yok |

---

## 4. Faydalar

### 4.1 İnceleme ve şeffaflık
- 799 öneri tek ekranda aranabilir / filtrelenebilir; büyük JSON tarayıcıya toptan basılmaz.
- Evidence-first: uzman, karar vermeden önce kaynak alıntıyı görür.
- Deterministik vs AI görsel olarak ayrıldığı için “model söyledi = kural” riski azalır.

### 4.2 Operasyonel güvenlik
- Read-only: yanlışlıkla production kuralına / DB’ye yazma yolu yok.
- Ham model çıktısı kapalı tutulabilir; sızdırma yüzeyi daraltılır.
- On-prem, CDN/internet bağımlılığı yok.

### 4.3 Ürün / demo değeri
- Provizyon içinde tek tıkla gösterilebilir; müşteri / yönetim / uzman sunumu için hazır yüzey.
- Crosswalk adjudication sonuçlarını (HUV↔SUT) erken aşamada tartışılabilir hale getirir.
- Demo karar export’u ile “uzman ne derdi?” senaryoları toplanabilir (eğitim / süreç tasarımı).

### 4.4 Mühendislik
- Handoff paketi taşınabilir; `validate_handoff.py` + testler ile bütünlük kontrolü var.
- Stdlib sunucu + mevcut Provizyon köprüsü: ek ağır stack gerekmedi.
- Partial snapshot uyarısı UI’da açık; veri eksikliği gizlenmez.

### 4.5 Risk azaltma (sürece katkı)
- Blocked paketler ve hata kodları görünür; kör apply yerine “önce temizle” kültürünü destekler.
- Expert questions / evidence gaps, insan review checklist’i üretir.

---

## 5. Zararlar / riskler / dezavantajlar

### 5.1 Yanlış güven riski (en kritik)
- Ekran “kural motoru paneli” gibi durduğu için `accepted` veya yeşil durum **onay sanılabilir**.
- AI hipotezi ikna edici metin üretebilir; evidence zayıf olsa bile uzmanı yönlendirebilir (automation bias).
- Demo kararı (`approve`) gerçek apply değildir ama süreçte “onaylandı” diye not düşülebilir.

**Mitigasyon (mevcut):** uyarı metinleri, katman ayrımı, “demo taslağıdır” bandı.  
**Kalan risk:** eğitim / prosedür olmadan kullanıcı yine yanlış yorumlayabilir.

### 5.2 Partial / eski veri riski
- Snapshot full run bitmeden alınmıştır; AI yalnız crosswalk aşamasındadır.
- Deterministik 799 önerinin çoğunda AI sentezi “henüz işlenmedi” olabilir — eksik sandığı için yanlış red/onay eğilimi.
- Full run ilerledikçe bu sayfa **otomatik güncellenmez**; yeni handoff gelene kadar eski kesit kalır.

### 5.3 Kapsam darlığı
- Şu an AI değeri ağırlıkla **crosswalk adjudication** üzerinedir; süre / birlikte-ödenmez / yaş kural alanlarının AI ile zenginleştirilmesi bu snapshot’ta tamamlanmış sayılmaz.
- Mevcut kural karşılaştırması çoğu kayıtta `new` / boş bağlam olabilir; “eski kuralı iyileştirme” hissi zayıf kalabilir.

### 5.4 Karar kalıcılığı zayıf
- Demo kararlar `localStorage`’da: tarayıcı temizliği / başka makine / başka kullanıcı ile kaybolur veya paylaşılmaz.
- Çok kullanıcılı resmi review workflow değildir (atama, audit trail, imza yok).

### 5.5 Performans / operasyon
- İlk istekte ~12MB enriched JSON bellek içine yüklenir; API sürecinde bellek kullanımı artar.
- Provizyon API ile aynı process’te yaşar; ağır kullanımda API latency’sine etki edebilir (şu an okuma ağırlıklı, kabul edilebilir).

### 5.6 Bilgi güvenliği
- Pakette resmî tarife / kural metinleri ve (opt-in ile) ham model cevapları vardır.
- `restricted/` açılırsa doğrulanmamış / constraint aşan içerik ekrana gelebilir.
- Export edilen demo karar JSON’u dışarı sızdırılırsa süreç içi niyetler görünür olur (düşük–orta hassasiyet).

### 5.7 Ürün karmaşası
- Provizyon’da ek menü maddesi: operasyon ekranları ile demo/inceleme ekranı yan yana; kullanıcı “canlı karar ekranı” sanabilir.
- Çift navigasyon (Provizyon menü + demo iç menü) bilişsel yük ekler.

### 5.8 Bakım maliyeti
- Yeni DGX handoff gelince path / env / yeniden yükleme gerekir; aksi halde eski snapshot ile demo yapılır.
- Handoff `app/` ile Provizyon köprüsü iki yüzey: UI değişikliği iki yerde düşünülmeli (aslında UI handoff’ta tek; köprü ince).

---

## 6. Fayda–zarar dengesi (kısa hüküm)

| Soru | Cevap |
|------|--------|
| Üretim kuralı uygulamak için uygun mu? | **Hayır** |
| Uzman / ürün / güvenlik incelemesi için faydalı mı? | **Evet** |
| En büyük fayda | Evidence + deterministik + AI’yi ayırarak şeffaf review |
| En büyük zarar | Partial veriyi / AI hipotezini “onaylı kural” sanmak |

**Sonuç:** Sayfa, **karar destekli inceleme demosu** olarak değerlidir; **otomatik veya resmi kural yayın kanalı** değildir. Faydası, insanı bilgilendirip tartışmayı hızlandırmasıdır. Zararı, yanlış güven ve partial snapshot’tan kaynaklanan eksik/yanlış yorumdur.

---

## 7. Kullanım önerileri

1. Sunum ve review’da her açılışta “partial snapshot / accepted ≠ onay” cümlesini hatırlatın.  
2. Karar verirken önce **resmî evidence**, sonra deterministik alanlar, en son AI hipotezi okunsun.  
3. Blocked kayıtları “kötü kural” değil “güvenlik filtresi tuttu” diye ele alın.  
4. Demo `approve` çıktısını production change-request olarak kullanmayın; ayrı resmi süreç olsun.  
5. Full DGX run bitince yeni handoff ile sayfayı yenileyin; bu kesiti güncel gerçeklik sanmayın.  
6. Ham cevap (`--enable-raw` / `PROVIZYON_RULE_PROPOSAL_ENABLE_RAW`) yalnız analist oturumunda açılsın.

---

## 8. Erişim

| Yol | Adres |
|-----|--------|
| Provizyon menü | `http://127.0.0.1:8020/dashboard` → **Kural Önerileri** |
| Doğrudan | `http://127.0.0.1:8020/dashboard/kural-onerileri` |
| Bağımsız sunucu | `python3 app/server.py --host 127.0.0.1 --port 8080` |
| Bu analiz | `KURAL_ONERILERI_ANALIZ.md` (handoff kökü) |

---

## 9. İlgili dosyalar

- Handoff görev: `DGX_AGENT_PROMPT.md`, `START_HERE.md`
- Veri özeti: `data/snapshot/demo-summary.json`
- Uygulama: `app/`
- Provizyon köprü: `provizyon/provizyon_engine/rule_proposal_handoff.py`
