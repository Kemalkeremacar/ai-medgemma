# MedGemma API — Başka Makineden Çağırma Kılavuzu

MedGemma modeli ağ üzerinden bir API olarak sunulmaktadır. Aşağıdaki bilgilerle
herhangi bir makineden istek atıp cevap alabilirsiniz.

## Bağlantı bilgileri

| | |
|---|---|
| **Adres (URL)** | `http://192.168.1.209:8080/v1` |
| **Endpoint** | `POST /v1/chat/completions` |
| **Kimlik doğrulama** | `Authorization: Bearer <API_KEY>` |
| **API Key** | `610e3f28ce586a5b6f859daedaf1111ef5cff11f42d80e1ae0f19a7dc1418f7e` |
| **Format** | OpenAI uyumlu |

> API Key gizli tutulmalıdır. Sadece yetkili kişilerle paylaşın.
> İstek atacak makinenin `192.168.1.209` sunucusuna ağ erişimi olmalıdır (aynı ağ / VPN).

## 1) En basit örnek (metin) — curl

```bash
curl http://192.168.1.209:8080/v1/chat/completions \
  -H "Authorization: Bearer 610e3f28ce586a5b6f859daedaf1111ef5cff11f42d80e1ae0f19a7dc1418f7e" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Merhaba, kısa bir test."}]}'
```

## 2) Python (OpenAI kütüphanesi ile)

```bash
pip install openai
```

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.1.209:8080/v1",
    api_key="610e3f28ce586a5b6f859daedaf1111ef5cff11f42d80e1ae0f19a7dc1418f7e",
)

resp = client.chat.completions.create(
    model="medgemma",  # herhangi bir değer olabilir; sunucu doğru modeli kullanır
    messages=[{"role": "user", "content": "Merhaba"}],
)
print(resp.choices[0].message.content)
```

## 3) Belge/görsel gönderme (tek istekte metin + görseller)

Görseller base64 olarak `data:` URI şeklinde gönderilir. Tek istekte metin ve
birden çok görsel birlikte gönderilebilir.

```python
import base64
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.1.209:8080/v1",
    api_key="610e3f28ce586a5b6f859daedaf1111ef5cff11f42d80e1ae0f19a7dc1418f7e",
)

def to_uri(path):
    b = base64.b64encode(open(path, "rb").read()).decode()
    return f"data:image/jpeg;base64,{b}"

resp = client.chat.completions.create(
    model="medgemma",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "Bu belgeleri değerlendir."},
            {"type": "image_url", "image_url": {"url": to_uri("belge1.jpg")}},
            {"type": "image_url", "image_url": {"url": to_uri("belge2.jpg")}},
        ],
    }],
    max_tokens=1024,
)
print(resp.choices[0].message.content)
```

> PDF gönderecekseniz önce sayfaları görsele (JPEG/PNG) çevirmeniz gerekir.

## 4) Provizyon bilgilerini (JSON) + prompt gönderip JSON cevap alma

Yapılandırılmış veri (ör. provizyon bilgileri) **en üst seviyeye konmaz**; prompt ile
birlikte bir `user` mesajının `content`'i içine metin olarak yazılır. `response_format`
ile cevabın **geçerli JSON** olması garanti edilir.

```python
import json
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.1.209:8080/v1",
    api_key="610e3f28ce586a5b6f859daedaf1111ef5cff11f42d80e1ae0f19a7dc1418f7e",
)

sistem_prompt = (
    "Sen deneyimli ve şüpheci bir sağlık sigortası suistimal tespit uzmanısın. "
    "Her işlemi 0-100 güvenilirlik skoruyla değerlendir. "
    "Yanıtı YALNIZCA şu JSON formatında ver: "
    '{"skor": <0-100>, "risk": "<dusuk|orta|yuksek>", '
    '"gerekce": "<kisa aciklama>", "kirmizi_bayraklar": ["..."]}'
)

provizyon = {
    "hastaYas": 45,
    "cinsiyet": "Erkek",
    "tanilar": ["O82 Sezaryen ile doğum"],
    "islemler": ["619.100 Sezaryen ameliyatı", "704.010 Genel anestezi"],
}

resp = client.chat.completions.create(
    model="medgemma",
    messages=[
        {"role": "system", "content": sistem_prompt},
        {"role": "user", "content": "Provizyon bilgileri:\n" + json.dumps(provizyon, ensure_ascii=False)},
    ],
    temperature=0.1,
    max_tokens=1024,
    response_format={"type": "json_object"},
)

sonuc = json.loads(resp.choices[0].message.content)  # temiz JSON
print(sonuc["skor"], sonuc["risk"], sonuc["gerekce"])
```

Örnek dönen cevap:

```json
{"skor": 0, "risk": "yuksek", "gerekce": "Erkek hastada sezaryen mümkün değildir.", "kirmizi_bayraklar": ["Cinsiyet Uyumsuzluğu"]}
```

> İpucu: İstediğiniz JSON alanlarını prompt içinde açıkça belirtin; model her seferinde
> aynı yapıda döner. Tutarlılık için `temperature: 0.1` önerilir.

## 5) En kolay yol: kendi JSON yapınızı olduğu gibi gönderme (`/degerlendir`)

OpenAI formatına (`messages`) sarmakla uğraşmak istemiyorsanız, `POST /degerlendir`
endpoint'ini kullanın. Kendi JSON yapınızı **olduğu gibi** gönderirsiniz; tek şart
gövdede bir **`Prompt`** alanı olması. Gateway şunu otomatik yapar:

- `Prompt` → sistem talimatı olur.
- Geri kalan tüm alanlar (`hastaAd`, `tanilar`, `islemler`, ...) değerlendirilecek
  veri olarak modele iletilir.
- Cevap **doğrudan** modelin ürettiği JSON nesnesi olarak döner (skor, risk, ...).
- (Opsiyonel) gövdeye `temperature` ve `max_tokens` ekleyebilirsiniz; varsayılan
  `temperature=0`, `max_tokens=1024`.

### curl

```bash
curl http://192.168.1.209:8080/degerlendir \
  -H "Authorization: Bearer 610e3f28ce586a5b6f859daedaf1111ef5cff11f42d80e1ae0f19a7dc1418f7e" \
  -H "Content-Type: application/json" \
  -d '{
        "Prompt": "Sen şüpheci bir suistimal uzmanısın. Yanıtı YALNIZCA şu JSON ile ver: {\"skor\":<0-100>,\"risk\":\"<dusuk|orta|yuksek>\",\"ozet\":\"...\",\"detayli_analiz\":\"...\"}",
        "hastaAd": "Ayşe Yılmaz",
        "hastaYas": 34,
        "hastaCinsiyet": "Kadın",
        "tanilar": [{"ICD10Kod":"Z34.0","TaniAd":"Normal ilk gebeliğin denetlenmesi"}],
        "islemler": [{"IslemKod":"804.470","IslemAd":"Obstetrik ultrasonografi"}]
      }'
```

### Python (kütüphane gerekmez)

```python
import json, urllib.request

body = {
    "Prompt": "Sen şüpheci bir suistimal uzmanısın. Yanıtı YALNIZCA şu JSON ile ver: "
              '{"skor":<0-100>,"risk":"<dusuk|orta|yuksek>","ozet":"...","detayli_analiz":"..."}',
    "hastaAd": "Ayşe Yılmaz",
    "hastaYas": 34,
    "hastaCinsiyet": "Kadın",
    "brans": "Kadın Hastalıkları ve Doğum",
    "tanilar": [{"ICD10Kod": "Z34.0", "TaniAd": "Normal ilk gebeliğin denetlenmesi"}],
    "islemler": [{"IslemKod": "804.470", "IslemAd": "Obstetrik ultrasonografi"}],
}

req = urllib.request.Request(
    "http://192.168.1.209:8080/degerlendir",
    data=json.dumps(body).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer 610e3f28ce586a5b6f859daedaf1111ef5cff11f42d80e1ae0f19a7dc1418f7e",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=600) as r:
    sonuc = json.load(r)   # zaten temiz JSON: {"skor":..., "risk":..., ...}

print(sonuc["skor"], sonuc["risk"])
```

Örnek dönen cevap:

```json
{"skor": 95, "risk": "dusuk", "ozet": "...", "detayli_analiz": "..."}
```

> `/degerlendir` ile `/v1/chat/completions` aynı modele, aynı kuyruğa gider; tek fark
> `/degerlendir`'in sarmalamayı sizin yerinize yapıp size direkt JSON döndürmesidir.
> Görsel/belge de göndermek isterseniz 3. bölümdeki `/v1/chat/completions` yolunu kullanın.

## Bilmeniz gerekenler

- **Cevap süresi:** İstek karmaşıklığına göre birkaç saniye ile birkaç dakika arasında sürebilir. Uzun istekler için zaman aşımı 30 dakikadır.
- **Eşzamanlılık:** Aynı anda en fazla 4 istek işlenir; fazlası otomatik sıraya alınır.
- **Görsel sınırı:** Sabit bir sayı sınırı yoktur; toplam boyut modelin bağlam penceresiyle (64K token, görsel başına ~265 token) sınırlıdır — pratikte tek istekte onlarca sayfa gönderilebilir.

## Sağlık kontrolü (servis ayakta mı?)

```bash
curl http://192.168.1.209:8080/health
```

`{"status":"ok", ...}` dönerse servis çalışıyordur.
