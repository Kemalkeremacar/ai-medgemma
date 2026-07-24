# Kural Önerileri — Uzman Yardım Rehberi

Bu ekran, sistemin ürettiği **kural adaylarını** incelemeniz içindir.  
Buradan seçtiğiniz kayıtlar **otomatik olarak canlı kural motoruna yazılmaz**. Asıl kural tanımı, kurumunuzun resmi kural süreçlerinde sizin onayınızla yapılır.

Bu sayfa size şunu sağlar: *“Hangi liste (HUV veya SUT) ve hangi işlem için, hangi tipte, hangi kanıta dayanarak kural tasarlamalıyım?”*

---

## 1. Başlamadan önce bilmeniz gerekenler

1. **Partial snapshot:** Ekran dondurulmuş bir kesittir; veri zamanla değişmez.
2. **HUV ve SUT ayrıdır:** Bu demoda HUV↔SUT eşleştirme (crosswalk) yoktur. HUV işlemi için kural ile SUT işlemi için kural **ayrı adaylar** olarak incelenir.
3. **İki ana katmanı ayırın:**
   - **Deterministik öneri** → kural motorunun ürettiği aday alanlar  
   - **Resmî evidence** → kaynak alıntısı (asıl dayanak)
4. **Örnek kural önerileri** butonu otomatik Türkçe taslak üretir; yayın onayı değildir.
5. Ekrandaki **Uzman kararı** yalnızca demo notudur.

---

## 2. Menüler

| Menü | Ne zaman kullanırsınız? |
|------|-------------------------|
| **Dashboard** | Genel sayılar, partial uyarı, HUV/SUT ayrı bakış hatırlatması |
| **Kural önerileri** | Ana çalışma alanı: adayları süzün, seçin, detaya girin |
| **Uzman kararları** | Bu tarayıcıda tuttuğunuz demo notlarınız |
| **Yardım** | Bu rehber |

---

## 3. “Kural önerileri” listesi — başlıkların anlamı

### Proposal ID
Benzersiz aday kimliği. Takip ve not için kullanın.

### İşlem
Kuralın bağlandığı işlem kodu ve adı.

### Liste
| Değer | Anlamı |
|-------|--------|
| **HUV** | Bu aday bir **HUV** işlemi kuralı olarak değerlendirilir |
| **SUT** | Bu aday bir **SUT** işlemi kuralı olarak değerlendirilir |

HUV kaydını SUT’a “çevirerek” tek kural yapmaya çalışmayın; listeleri ayrı tutun.

### Kural tipi
| Değer | Anlamı |
|-------|--------|
| **Süre / frekans** | Adet, periyot, süre sınırı |
| **Birlikte ödenmez** | Birlikte faturalanma / çakışma engeli |
| **Yaş** | Yaş aralığı / yaş koşulu |

### Öncelik
- **A** → önce bakın  
- **B** → normal  
- **C** → düşük  

Öncelik, “kesin doğru” demez.

### Completeness
- **complete** → alanlar görece tam  
- **partial** → eksik/kısmi; önce boşluklara bakın  

### Evidence
Resmî alıntı sayısı. **0** ise dayanak zayıf; **1+** ise detayda alıntıyı okuyun.

### Quality flags
Motor dikkat bayrakları. Doluysa kaydı şüpheli kabul edin.

---

## 4. Hangi kayıtlara kural tasarımına girebilirsiniz?

### Öncelikli aday
- Öncelik **A** (veya sizin için kritik **B**)
- Evidence **≥ 1** ve alıntı işlemi destekliyor
- Completeness uygun / eksikler sizin için net
- Quality flag’leri bilinçli değerlendirdiniz
- Liste tipi (HUV veya SUT) kurum sürecinizdeki doğru liste

### Beklet / ek kanıt
- Evidence = 0  
- Locator/alıntı eksik  
- Partial + kritik alanlar boş  
- Flag yoğun (`explicit_frequency_fields_not_parsed` vb.)

---

## 5. Kural tanımlayacaksanız adımlar

1. **Dashboard** — partial ve “HUV/SUT ayrı” uyarısını okuyun.  
2. **Listeyi süzün** — Öncelik A + Liste tipi (HUV veya SUT) + kural tipi.  
3. **Detay sırası:** Deterministik alanlar → Resmî evidence → (isteğe bağlı) Örnek kural önerileri butonu.  
4. **Demo karar** — Onayla / Düzenle / Reddet / Ek kanıt (yalnızca not).  
5. **Resmi kurala geçerken** — doğru liste tipi, işlem, alanlar, evidence uyumu.

### Örnek kural butonu
Detayda **“Bu kayıt için örnek kural önerilerini göster”**:
- Kayıttaki alanlardan Türkçe taslak üretir.
- Tutarlılık skoru gösterir.
- Evidence ile çelişiyorsa kullanmayın.

---

## 6. Yapmayın

- Evidence okumadan kural girmeyin.  
- HUV adayını SUT kuralı gibi (veya tersi) tek potada eritmeyin.  
- Örnek kural taslağını olduğu gibi canlıya yapıştırmayın.  
- Demo kararını production onayı sanmayın.

---

## 7. Kısa özet

1. Liste tipini seçin: **HUV ayrı / SUT ayrı**.  
2. Öncelik A + Evidence ile süzün.  
3. Sıra: **Deterministik → Evidence → Örnek taslak**.  
4. Flag varsa bilinçli çöz / bekle.  
5. Asıl kuralı resmi süreçte tanımlayın.
