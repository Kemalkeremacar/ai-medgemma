# Restricted Raw Responses
`engine-proposals.ai-raw-responses.json` snapshot anındaki ham model cevaplarını içerir.

- Bu cevaplar doğrulanmamış olabilir ve `blocked` kayıtlarında canonical sınırları aşan içerik taşıyabilir.
- Dosya public/static web root'a kopyalanmaz.
- Yalnız analyst/debug görünümünde ve açık `--enable-raw` seçeneğiyle kullanılır.
- Ham çıktı resmî evidence, onaylanmış kural veya accepted synthesis değildir.
- Otomatik redaction uygulanmıştır; yine de dosya hassas çalışma artefaktı olarak korunur.
