# DGX Komutları
Aşağıdaki komutlar handoff klasörünün DGX üzerinde kopyalandığı dizinde çalıştırılır.

## 1. Bütünlük doğrulama
```bash
cd /path/to/dgx-rule-proposal-demo-handoff
python3 scripts/validate_handoff.py
```

## 2. Snapshot özetini kontrol etme
```bash
python3 -m json.tool data/snapshot/demo-summary.json >/dev/null
python3 -m json.tool data/snapshot/engine-proposals.ai-progress.snapshot.json
```

## 3. Agent'a görev verme
DGX agent'ın çalışma dizinini bu klasör yapın ve `DGX_AGENT_PROMPT.md` dosyasının tamamını görev talimatı olarak verin. Agent önce manifest doğrulamasını çalıştırmalı, uygulamayı yalnız `app/` altında üretmelidir.

## 4. Agent tesliminden sonra beklenen test
```bash
python3 -m unittest discover -s app/tests -v
```

## 5. Uygulamayı güvenli yerel başlatma
```bash
python3 app/server.py --host 127.0.0.1 --port 8080
```

## 6. Ham cevap incelemesini bilinçli açma
```bash
python3 app/server.py --host 127.0.0.1 --port 8080 --enable-raw
```

Uzak erişim gerekiyorsa `0.0.0.0` yalnız DGX firewall/VPN erişimi sınırlandıktan sonra kullanılmalıdır:
```bash
python3 app/server.py --host 0.0.0.0 --port 8080
```

Bu handoff hiçbir inference çağrısı başlatmaz. Full çalışma kendi checkpoint'i üzerinden bağımsız olarak devam eder.
