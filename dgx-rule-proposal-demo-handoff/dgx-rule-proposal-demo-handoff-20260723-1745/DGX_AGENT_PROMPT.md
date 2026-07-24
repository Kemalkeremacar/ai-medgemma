# DGX Agent Görevi: Kural Önerileri Demo Ekranı
Bu klasördeki dondurulmuş verilerden tamamen on-prem, Türkçe ve insan-inceleme odaklı bir web demo uygulaması oluştur.

## Başlamadan önce
1. Çalışma dizinin bu handoff klasörü olsun.
2. `python3 scripts/validate_handoff.py` çalıştır ve doğrulama başarısızsa dur.
3. `START_HERE.md`, `data/snapshot/demo-summary.json` ve JSON schema dosyasını incele.
4. `data/` ve `restricted/` altındaki kaynak dosyaları değiştirme.

## Uygulama sınırı
- Yeni uygulamayı yalnız `app/` altında oluştur.
- Tercihen Python 3 standard library backend ve dependency-free HTML/CSS/JavaScript kullan. Mevcut onaylı bir framework zaten kuruluysa kullanılabilir; internetten paket/CDN indirme.
- Varsayılan bind adresi `127.0.0.1`, port parametreyle değiştirilebilir olsun.
- Veritabanı, SQL, model çağrısı, Qdrant, otomatik onay veya otomatik apply kesinlikle yok.
- Kaynak verileri read-only aç. Kullanıcı not/kararlarını local storage'da tut ve ayrı demo JSON olarak export et.
- Büyük JSON dosyasını her sayfa değişiminde tarayıcıya tamamen gönderme; backend tarafında indeksle, sayfala ve filtrele.

## Zorunlu ekranlar
1. **Dashboard**
   - Deterministik öneri, coverage, evidence ve tamamlanan AI paket sayaçları.
   - Stage/status dağılımları.
   - “Partial snapshot” ve mevcut aşama sınırlaması görünür uyarısı.
2. **Kural önerileri listesi**
   - Proposal ID, işlem kod/adı, kural tipi, A/B/C öncelik, completeness, evidence sayısı.
   - Arama, kural tipi/öncelik/quality flag filtresi ve pagination.
3. **Kural önerisi detayı**
   - Deterministik önerilen alanlar, işlemler, tanılar, engine signals ve mevcut-kural karşılaştırması.
   - Resmî evidence alıntısı, kaynak dosya/satır/locator bilgisi.
   - Varsa doğrulanmış AI synthesis: outcome, proposedFields, rationale, evidenceGaps, expertQuestions.
   - Deterministik içerik, resmî evidence ve AI hipotezini görsel olarak kesin ayır.
4. **AI değerlendirmeleri**
   - Stage, accepted/blocked, owner ve hata nedenleri.
   - `accepted` etiketini “Teknik doğrulamayı geçti”; `blocked` etiketini “Güvenlik kontrolünde engellendi” olarak göster.
   - Accepted cevabın `proposal`, `no_change` veya `insufficient_evidence` olabileceğini göster.
5. **Crosswalk değerlendirmeleri**
   - HUV işlemi, candidate crosswalk'lar, seçilen crosswalk, gerekçe ve review recommendation.
6. **Uzman demo kararı**
   - `approve`, `edit`, `reject`, `needs_more_evidence` taslak seçenekleri.
   - “Demo taslağıdır; gerçek onay değildir” uyarısı.
   - Kaynak dosyayı değiştirme; local storage ve JSON export kullan.

## Ham cevap ekranı
- `restricted/engine-proposals.ai-raw-responses.json` varsayılan olarak yüklenmez ve static web root altında bulunmaz.
- Yalnız açıkça `--enable-raw` parametresi verilirse backend üzerinden erişilebilir olsun.
- Her ham cevabın üzerinde “DOĞRULANMAMIŞ MODEL ÇIKTISI — KURAL DEĞİLDİR” uyarısı göster.
- Ham cevap ile structured/accepted synthesis'i yan yana karşılaştırma imkânı ver.
- API key, authorization header veya secret benzeri değerleri hiçbir log/ekranda gösterme.

## UX beklentileri
- Türkçe, masaüstü odaklı, sade kurumsal arayüz.
- Evidence-first tasarım: uzman kararından önce kaynak ve gerekçeyi görünür yap.
- Status renkleri tek başına anlam taşımamalı; metin etiketi de bulunmalı.
- JSON alanlarını ham dump etmek yerine kural tipine uygun okunabilir kart/tablo olarak göster.
- Eksik AI aşamalarını “henüz işlenmedi” olarak göster; veri uydurma.

## Teslim ve doğrulama
- `app/README.md`: kurulum/çalıştırma, veri kaynakları, güvenlik sınırları.
- `app/server.py`: `--host`, `--port`, `--enable-raw` seçenekleri.
- Backend ve kritik UI veri dönüşümleri için tests.
- Test ve başlatma komutlarını çalıştır; sonuçları kısa bir teslim notunda bildir.
- Handoff root'undaki manifest veya veri dosyalarını değiştirme.
