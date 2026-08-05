# Kural Önerileri — Kısa Yardım

Bu ekran, sistemin ürettiği **kural adaylarını** incelemeniz içindir.  
Seçtiğiniz kayıtlar otomatik olarak canlı kurala yazılmaz.

---

## Ne yapmalıyım?

1. **Öneriler** listesinde Öncelik **A** ve kanıtı olanlara bakın.  
2. Detayda sıra: **Kanıt → Önerilen alanlar → Örnek metin**.  
3. İsterseniz not alın veya **Öneri AI** ile sorun.  
4. Asıl kuralı kurumunuzun resmi sürecinde tanımlayın.

---

## HUV ve SUT tamamen ayrı

Birlikte ödenmez kuralı **aynı liste + aynı sözleşme** içindedir:

- Kurum HUV çalışıyorsa: A (HUV) yapıldıysa, sözleşmedeki B (HUV) birlikte ödenmez.  
- Kurum SUT çalışıyorsa: aynı mantık SUT–SUT.  
- HUV işlemi ile SUT kodunu tek birlikte-ödenmez kuralında birleştirmezsiniz.

Örnek metin ve hedef listesi buna göre üretilir. Kayıtta yalnız SUT hedefleri varsa bu bir **HUV kuralı olarak onaylanamaz**; ayrı SUT adayı olarak not edilir.

---

## Liste alanları

| Alan | Anlamı |
|------|--------|
| **Öneri** | Liste + işlem kodu + kural tipi |
| **Tip** | Süre/frekans, birlikte ödenmez veya yaş |
| **Öncelik** | A önce, B normal, C sonra |
| **Kanıt** | Resmî alıntı sayısı (0 ise dayanak zayıf) |
| **Durum** | Tam / Kısmi; uyarı varsa kısa açıklama |

Detayda: **hedef işlemler (aynı liste)**, **ayrı SUT izi (peer değil)**, **eşleme / sinyal notu**.

---

## Ne zaman beklemeliyim?

- Kanıt = 0  
- Durum **Kısmi** ve aynı listede hedef yok  
- “HUV–SUT karışımı engellendi” / çapraz liste uyarısı

---

## Yapmayın

- Kanıt okumadan kural girmeyin.  
- Örnek metni olduğu gibi canlıya yapıştırmayın.  
- Buradaki notu resmi onay sanmayın.  
- HUV kuralına SUT hedef kodu yazmayın.
