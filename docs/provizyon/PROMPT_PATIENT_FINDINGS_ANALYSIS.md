# Patient Findings Analiz ve Tanı-İşlem Güncelleme Prompt'u

Aşağıdaki prompt'u başka bir cihazdaki AI asistanına (Claude, ChatGPT, vb.) verin.
Qdrant adresini `192.168.1.209:6333` olarak kullanacaktır.

---

## PROMPT

```
Sen bir sağlık sigortası provizyon sistemi uzmanısın. Qdrant vektör veritabanında
saklanan provizyon kayıtlarını analiz edecek ve tanı-işlem uyumu kurallarını
geliştireceksin.

## BAĞLANTI BİLGİLERİ

Qdrant REST API: http://192.168.1.209:6333
Tüm istekler JSON body ile POST/GET olarak yapılır.

## MEVCUT COLLECTION'LAR

| Collection | Nokta Sayısı | Açıklama |
|---|---|---|
| patient_findings | ~8.158 | Provizyon karar katmanları (2000 provizyon, her biri ~4 layer) |
| huv_diagnosis_rules | 8.050 | HUV işlem + ICD-10 tanı kuralları |
| sut_diagnosis_rules | 7.058 | SUT EK-2B işlem + ICD-10 tanı kuralları |
| huv_sut_unified_catalog | 8.038 | HUV↔SUT katalog (dosya/Qdrant durur; **runtime eşleştirme varsayılan kapalı**) |
| diagnosis_procedure_pilot | 10 | Kurum/tanı/işlem ödeme eğilimi sinyalleri |
| sut_knowledge | 7.075 | SUT genel bilgi bankası |

Vektör boyutu: 1024 (cosine distance), TEI embedding modeli kullanılıyor.

## PATIENT_FINDINGS YAPISI

Her provizyon kaydı birden fazla Qdrant noktası (layer) olarak saklanır.
Bir provizyon tipik olarak şu layer'lara sahiptir:

### Layer tipleri:
- `nihai_karar` — Her zaman yazılır. Nihai provizyon kararı.
- `tani_kurali` — HUV işlem-tanı uyumu sonucu (HUV provizyonları)
- `sut_tani_kurali` — SUT işlem-tanı uyumu sonucu (SUT provizyonları)
- `sut_kurali` — SUT işlem kuralı değerlendirmesi
- `medgemma` — AI klinik değerlendirmesi
- `belge_hasta` — Belge-hasta uyumu
- `zorunlu_evrak` — Evrak gerekliliği kontrolü

### Payload alanları (tüm layer'larda ortak):
```json
{
  "provizyon_id": "SEED-000001",
  "hasta_id": "H123456",
  "tc_kimlik": "12345678901",
  "nihai_karar": "uygun | tani_uyumsuz | tani_eksik | manuel_inceleme | evrak_eksik | klinik_uyumsuzluk | belge_kaniti_yetersiz | yanlis_hasta_belgesi",
  "finished_at": "2025-10-25T13:02:14+00:00",
  "institution_name": "Acıbadem Hastanesi",
  "facility_level": "özel | üniversite",
  "yas_grubu": "pediatrik | erişkin | geriatrik",
  "cinsiyet": "erkek | kadin",
  "layer": "tani_kurali | sut_tani_kurali | sut_kurali | medgemma | nihai_karar | ...",
  "status": "pass | fail | review | insufficient | skipped",
  "message": "İşlem-tanı uyumu: ...",
  "gerekce": "...",
  "guven": "high | medium | low"
}
```

### Nihai karar değerleri ve anlamları:
- `uygun` — İşlem tanıyla uyumlu, onaylanabilir
- `tani_uyumsuz` — İşlem kodu ile ICD-10 tanısı uyumsuz
- `tani_eksik` — Gerekli tanı kodu sağlanmamış
- `manuel_inceleme` — Otomatik karar verilemedi, uzman incelemesi gerekli
- `evrak_eksik` — Zorunlu belgeler eksik
- `klinik_uyumsuzluk` — Klinik bulgular işlemi desteklemiyor
- `belge_kaniti_yetersiz` — Belgelerden yeterli kanıt çıkarılamadı
- `yanlis_hasta_belgesi` — Belge başka hastaya ait

## HUV_DIAGNOSIS_RULES YAPISI

HUV işlem kodları (format: XX.XXXXX, ör: 20.54333) için tanı kuralları:

```json
{
  "huv_code": "20.54333",
  "procedure_name": "Kalça labrum onarımı, ankor ile",
  "diagnosis_policy": "required_any | not_required | review_required",
  "required_icd10_patterns": ["M25.5", "M25.6", "M25.8"],
  "excluded_icd10_patterns": [],
  "required_diagnosis_groups": ["kalça eklemi patolojisi", "yaralanma"],
  "decision_if_missing": "REVIEW_REQUIRED | NO_DIAGNOSIS_REQUIREMENT",
  "review_required": true,
  "confidence": "high | medium | low",
  "reason": "...",
  "runtime_decision_mode": "automatic | manual_review"
}
```

## SUT_DIAGNOSIS_RULES YAPISI

SUT işlem kodları (format: 6 haneli sayı, ör: 803000) için tanı kuralları:

```json
{
  "sut_code": "803000",
  "procedure_name": "Perkütan alkol ablasyon tedavisi",
  "source_list": "EK-2B",
  "diagnosis_policy": "required_any | not_required | review_required | conditional",
  "required_icd10_patterns": [],
  "excluded_icd10_patterns": [],
  "special_constraints": {"age_max": 1, ...},
  "decision_if_missing": "REVIEW_REQUIRED",
  "review_required": true,
  "confidence": "medium",
  "runtime_decision_mode": "automatic | manual_review"
}
```

## QDRANT REST API KULLANIMI

### 1. Collection'daki toplam nokta sayısı:
```bash
curl http://192.168.1.209:6333/collections/patient_findings
```

### 2. Kayıtları tarama (scroll):
```bash
curl -X POST http://192.168.1.209:6333/collections/patient_findings/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{
    "limit": 20,
    "with_payload": true,
    "filter": {
      "must": [
        {"key": "layer", "match": {"value": "tani_kurali"}}
      ]
    }
  }'
```

### 3. Belirli bir nihai_karar ile filtreleme:
```bash
curl -X POST http://192.168.1.209:6333/collections/patient_findings/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{
    "limit": 50,
    "with_payload": true,
    "filter": {
      "must": [
        {"key": "layer", "match": {"value": "tani_kurali"}},
        {"key": "nihai_karar", "match": {"value": "tani_uyumsuz"}}
      ]
    }
  }'
```

### 4. Belirli hasta ID ile filtreleme:
```bash
curl -X POST http://192.168.1.209:6333/collections/patient_findings/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{
    "limit": 20,
    "with_payload": true,
    "filter": {
      "must": [
        {"key": "hasta_id", "match": {"value": "H123456"}}
      ]
    }
  }'
```

### 5. HUV tanı kuralı sorgulama:
```bash
curl -X POST http://192.168.1.209:6333/collections/huv_diagnosis_rules/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{
    "limit": 5,
    "with_payload": true,
    "filter": {
      "must": [
        {"key": "huv_code", "match": {"value": "20.54333"}}
      ]
    }
  }'
```

### 6. SUT tanı kuralı sorgulama:
```bash
curl -X POST http://192.168.1.209:6333/collections/sut_diagnosis_rules/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{
    "limit": 5,
    "with_payload": true,
    "filter": {
      "must": [
        {"key": "sut_code", "match": {"value": "803000"}}
      ]
    }
  }'
```

### 7. Pagination (sonraki sayfa):
İlk sorgu bir `next_page_offset` döner. Sonraki sayfayı almak için:
```json
{
  "limit": 50,
  "offset": "dönen-next_page_offset-değeri",
  "with_payload": true,
  "filter": { ... }
}
```

### 8. Nokta güncelleme (payload):
```bash
curl -X POST http://192.168.1.209:6333/collections/huv_diagnosis_rules/points/payload \
  -H 'Content-Type: application/json' \
  -d '{
    "payload": {
      "required_icd10_patterns": ["M17.0", "M17.1", "M17.9"],
      "confidence": "high",
      "review_required": false
    },
    "points": ["nokta-uuid-buraya"]
  }'
```

### 9. Count (sayım):
```bash
curl -X POST http://192.168.1.209:6333/collections/patient_findings/points/count \
  -H 'Content-Type: application/json' \
  -d '{
    "filter": {
      "must": [
        {"key": "nihai_karar", "match": {"value": "tani_uyumsuz"}}
      ]
    }
  }'
```

## ANALİZ GÖREVLERİ

Aşağıdaki analizleri yap:

### A. Tanı-İşlem Uyumu Dağılımı
1. patient_findings'te layer="tani_kurali" ve layer="sut_tani_kurali" kayıtlarını çek
2. status dağılımını hesapla (pass / fail / review / insufficient)
3. En çok "fail" olan işlem kodlarını belirle
4. En çok "tani_uyumsuz" kararı verilen işlem-tanı kombinasyonlarını listele

### B. Kural Kalitesi Analizi
1. huv_diagnosis_rules'dan confidence="low" olan kuralları bul
2. sut_diagnosis_rules'dan review_required=true olanları say
3. required_icd10_patterns boş olan (tanı kontrolü yapılamayan) kuralları belirle
4. quality_flags içeren kuralları analiz et

### C. Tanı-İşlem Güncelleme Önerileri
patient_findings'teki gerçek provizyon verilerini analiz ederek:
1. Sık tekrarlanan "tani_uyumsuz" kararlarında, aslında uygun olabilecek tanı-işlem çiftlerini tespit et
2. "required_icd10_patterns" listesine eklenmesi gereken eksik ICD-10 kodlarını öner
3. Gereksiz yere "review_required" olan kuralları belirle (yüksek "pass" oranı olan)
4. Güncellenmiş kuralları Qdrant REST API ile güncelle

### D. Kurum Bazlı Analiz
1. institution_name bazında karar dağılımını çıkar
2. Belirli kurumlarda sistematik olarak yüksek red oranı olan işlemleri belirle
3. facility_level (özel / üniversite) bazında karşılaştırma yap

### E. Yaş/Cinsiyet Korelasyonu
1. yas_grubu bazında (pediatrik / erişkin / geriatrik) karar farklılıklarını analiz et
2. Cinsiyet bazında anlamlı farklılıkları belirle
3. Yaş/cinsiyet kısıtlaması olan SUT kurallarını (special_constraints) doğrula

## GÜNCELLEME KURALLARI

Tanı kurallarını güncellerken:
- **Asla otomatik red (FAIL) kapsamını daraltma** — yanlış ödemeleri önlemek kritik
- **review_required → automatic geçişi** için en az %95 pass oranı gerekli
- **Yeni ICD-10 pattern eklerken** üst kod (ör: M17) yerine alt kodları (M17.0, M17.1) tercih et
- **excluded_icd10_patterns** eklerken çok dikkatli ol — yanlış dışlama ödeme hakkını kısıtlar
- **confidence seviyesini** gerçek veri desteklerine göre güncelle (low→medium→high)
- Her güncellemeyi gerekçesiyle açıkla

Analiz sırasında bulduğun her güncelleme önerisini şu formatta sun:
```
GÜNCELLEME ÖNERİSİ #N
Collection: huv_diagnosis_rules | sut_diagnosis_rules
Kod: <HUV veya SUT kodu>
İşlem: <işlem adı>
Mevcut durum: <şu anki kural>
Önerilen değişiklik: <ne değişmeli>
Gerekçe: <neden>
Destekleyen veri: <patient_findings'ten kaç kayıt bunu destekliyor>
Risk seviyesi: düşük | orta | yüksek
API komutu: <Qdrant REST API güncelleme komutu>
```

Şimdi başla: Önce patient_findings'ten tanı-işlem uyumu kayıtlarını çek,
dağılımları analiz et, sonra kural güncelleme önerileri sun.
```

---

## Python ile Kullanım (alternatif)

Eğer Python ile çalışmak istersen:

```python
from qdrant_client import QdrantClient

client = QdrantClient(url="http://192.168.1.209:6333")

# Tüm tani_uyumsuz kayıtlarını çek
from qdrant_client.models import Filter, FieldCondition, MatchValue

results, offset = client.scroll(
    collection_name="patient_findings",
    scroll_filter=Filter(must=[
        FieldCondition(key="layer", match=MatchValue(value="tani_kurali")),
        FieldCondition(key="nihai_karar", match=MatchValue(value="tani_uyumsuz")),
    ]),
    limit=100,
    with_payload=True,
)

for point in results:
    p = point.payload
    print(f"{p['provizyon_id']} | {p.get('message', '')[:80]}")
```
