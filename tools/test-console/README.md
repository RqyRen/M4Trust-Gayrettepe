# M4Trust Test Console

Yerel, tek dosyalık bir web arayüzü: dosya yükle, gerçek pipeline'ı (RabbitMQ'ya hiç
girmeden, `app.pipeline.*` modüllerini doğrudan çağırarak) çalıştır, sonucu tarayıcıda
gör. Production servisinin bir parçası değil, `contracts/` veya `app/`'a hiçbir etkisi yok.

## Ne yapar

1. **Doküman çıkarımı** — PDF/DOCX yükle → dosya MinIO'ya (`docker-compose`'daki yerel
   instance) yüklenir, presigned URL üretilir, gerçek `document_extraction.pipeline.run()`
   çağrılır (gerçek indirme + hash + OCR + PII maskeleme + **gerçek GPT-5.4** + legalBasis).
   Sonuç: taraflar, kurallar (legalBasis rozeti dahil), uyarılar.
2. **Video analizi — fotoğraf ile hızlı test** — JPG/PNG yükle → doğrudan
   `roboflow_client.detect_objects`/`detect_damage` çağrılır (**gerçek Roboflow**, 2 model),
   tespitler fotoğrafın üstüne kutu olarak çizilir. RabbitMQ/MinIO'ya hiç gerek yok, en hızlı
   görsel kontrol yolu.
3. **Video analizi — tam pipeline** — gerçek bir MP4/WebM yükle → gerçek
   `video_analysis.pipeline.run()` (indirme + frame örnekleme + Roboflow + canonical
   aggregation). Bounding box göstermez (canonical sonuç bunu taşımıyor, ADR-002 §8.1) —
   sadece observation/anomaly/summary. Görsel kutu kontrolü için 2. modu kullan.

## Çalıştırma

```powershell
.venv\Scripts\python.exe -m pip install -r tools/test-console/requirements.txt
.venv\Scripts\python.exe tools/test-console/app.py
```

`http://localhost:8080` adresini aç. Docker'daki `m4trust-minio`'nun ayakta olması gerekir
(`docker compose up -d`).

**Her "Çalıştır" tıklaması gerçek, ücretli bir API çağrısı yapar** (OpenAI ve/veya
Roboflow) — sayfadaki uyarı etiketleri hangi modun ne kadar maliyetli olduğunu gösterir.
