# M4Trust AI Service (FastAPI)

M4Trust platformunun **AI capability servisi**. Bu repo Spring Boot Core Platform'dan (Berke) ayrı geliştirilir ve
ayrı deploy edilir. İki servis arasında **kod değil, contract paylaşılır** (bkz. `contracts/`).

> Mimari otorite: `m4trust-spring-front-prod` reposundaki ADR-001 … ADR-009. Bu servis o kararlara tabidir.

## Sorumluluk sınırı (ADR-001, ADR-002)

Bu servis bir **internal capability service**'tir. Business kararı vermez, business state değiştirmez.

**Yapar:**
- Doküman metin çıkarımı (PDF/DOCX, gerekirse OCR), normalizasyon, hassas veri maskeleme
- RAG + LLM tabanlı yapılandırılmış veri çıkarımı
- Video / görsel analizi (nesne-olay tespiti, advisory anomaly)
- Model-native çıktıyı **canonical M4Trust payload**'ına çevirme + schema validation
- Teknik metadata, retry, teknik hata → stable error contract

**Yapmaz (ADR-002 §28):**
- Spring PostgreSQL'e bağlanmaz, transaction / Deal state değiştirmez
- Ratification, payment, settlement, dispute kararı vermez
- Frontend'e public API sunmaz
- Model-native sonucu canonical yerine göndermez
- Contract'ı tek taraflı değiştirmez

## İletişim modeli (ADR-002)

Senkron inference **yoktur**. Tüm AI işleri RabbitMQ üzerinden asenkron yürür:

```
Spring → ai.job.requested.v1 → RabbitMQ → [AI Worker] → ai.job.completed.v1 / failed.v1 → RabbitMQ → Spring
```

Job türleri: `DOCUMENT_EXTRACTION`, `VIDEO_ANALYSIS`.

## İki process rolü (ADR-001 §21, ADR-007 §9)

| Rol | Görev |
| --- | --- |
| `ai-worker` | RabbitMQ command queue'larını tüketir, pipeline'ı çalıştırır, result event basar. Asıl iş. |
| `ai-api` | Sadece operasyonel HTTP: `/health/live`, `/health/ready`, `/internal/v1/capabilities`, `/internal/v1/contracts`. Inference endpoint'i **yok**. |

`ai-worker`'ın ek bir altyapı bağımlılığı var: **Redis** — job idempotency/lease store
(ADR-002 §17.1), cooperative cancellation intent'leri ve operasyonel sayaçlar için. ADR-001
§21'in deployment birimleri listesinde ayrıca adı geçmiyor; kapsamı ADR-001 §4.2'deki
"teknik çalışma verisi" altına giriyor (business state değil), meşru ama ayrıca not
edilmesi iyi (Fable 5 mimari denetimi, 20 Temmuz 2026).

## Klasör yapısı

```
app/
  main.py                 ai-api (health + capabilities)
  worker.py               ai-worker (RabbitMQ consumer entrypoint)
  config.py               env-based ayarlar (host/port hard-code edilmez)
  messaging/              consumer, publisher, topology (exchange/queue/routing key)
  contracts/              envelope modeli, JSON Schema validation, error code'lar
  pipeline/
    document_extraction/  indir → hash → parse → OCR → mask → RAG → LLM → canonical
    video_analysis/       indir → hash → frame analiz → canonical
  storage/                presigned URL indirme + SHA-256 doğrulama
  common/                 idempotency, retry, structured logging
contracts/                Spring ile paylaşılan JSON Schema + örnekler (senkron kopya)
tests/                    mock-first minimum kritik testler
```

## Geliştirme yol haritası

| Adım | İçerik |
| --- | --- |
| 0 | Foundation: iskelet + README + contracts (bu commit) |
| 1 | Contract katmanı: envelope + JSON Schema validation + capabilities endpoint |
| 2 | Messaging iskeleti: RabbitMQ consumer/publisher + fake pipeline ile uçtan uca event akışı |
| 3 | Duplicate + retry + failure: idempotency, retry, stable error contract |
| 4 | Document extraction (mock canonical result) |
| 5 | Video analysis (mock canonical result) |
| 6 | Gerçek AI entegrasyonu: RAG + LLM + OCR + video model (contract sabit kalır) |

## Local çalıştırma

```powershell
# 1) Altyapı (RabbitMQ + MinIO + Redis)
docker compose up -d

# 2) Bağımlılıklar
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3) ai-api (operasyonel endpoint'ler)
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# 4) ai-worker (RabbitMQ command tüketici)
.venv\Scripts\python.exe -m app.worker
```

RabbitMQ management UI: http://localhost:15672 · MinIO console: http://localhost:9001

Testler:

```powershell
.venv\Scripts\python.exe -m pytest tests/ -q
```

## Production image

Tek `Dockerfile`, iki rol. Varsayilan komut `ai-api`'yi calistirir (`uvicorn app.main:app`,
Railway'in verdigi `$PORT`'u dinler). `ai-worker` deploy'unda Railway'de
**Custom Start Command** `python -m app.worker` olarak ayarlanir — ayni image,
ayni bagimlilik kilidi, tek surum kaymasi riski yok.

Image `tesseract-ocr` (tur+eng dil paketleri) icerir, non-root kullanici (`appuser`)
olarak calisir, ve `/health/live` uzerinden Docker `HEALTHCHECK` yapar.

```powershell
docker build -t m4trust-ai-service .

# ai-api
docker run -p 8000:8000 --env-file .env m4trust-ai-service

# ai-worker (ayni image, farkli komut)
docker run --env-file .env m4trust-ai-service python -m app.worker
```

## Contracts senkronu

`contracts/` klasörü Spring reposundaki ortak sözleşmenin kopyasıdır. Değiştiğinde her iki repoda güncellenir.
Contract değişikliği önce, kod sonra (ADR-002 §25).

Yerel doğrulama:

```powershell
python -m pip install -r contracts/requirements.txt
python contracts/scripts/validate_contracts.py
```
