# M4Trust AI Service — production image (ADR-007 §9, §15, §18).
#
# Tek image, iki rol (README "İki process rolü"): varsayilan komut ai-api'yi
# calistirir; ai-worker deploy'unda start command Railway'de
# `python -m app.worker` olarak override edilir. Boylece iki servis ayni
# image'i, ayni bagimlilik kilidini paylasir (surum kaymasi riski olmaz).
#
# Multi-stage: derleme bagimliliklari (build-essential) final image'a sizmaz.
#
# Base image digest ile pinlenmis (Berke review #8, 18 Temmuz 2026): sadece
# tag ("python:3.14-slim") kullanmak, ayni tag'in zaman icinde farkli bir
# image'a isaret etmesine izin verir (upstream yeniden yayinlarsa). Digest'i
# yenilemek icin: `docker pull python:3.14-slim && docker inspect --format
# '{{index .RepoDigests 0}}' python:3.14-slim`.

FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS builder
WORKDIR /app

# cp314 wheel'i olmayan paketler icin (bu Python surumu henuz yeni) source
# build'e dusebilir; bu yuzden derleme araclari builder asamasinda bulunur.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6
WORKDIR /app

# tesseract-ocr: OCR fallback (ADR-002 §3.1); tur+eng gerceklestirilen dil
# ciftidir (bkz. app/pipeline/document_extraction/text_extraction.py).
# tesseract-ocr paketiyle eng.traineddata zaten gelir, tur ayrica gerekir.
# libglib2.0-0/libgomp1: opencv-python-headless (video frame ornekleme)
# import zincirinin ihtiyac duydugu paylasimli kutuphaneler.
# curl: container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-tur \
        libglib2.0-0 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY app/ ./app
# Sadece runtime'da okunan schema'lar kopyalanir (ADR-007 §15 minimum image);
# openapi/asyncapi/examples/scripts sadece gelistirme zamaninda kullanilir.
COPY contracts/schemas/ ./contracts/schemas
# Legal RAG: yalniz onceden uretilmis embedding'ler kopyalanir (legal_rag.py'nin
# okudugu tek sey); raw/ (ham kanun PDF/Markdown) ve chunks/ (ara adim) ile
# scripts/ (build-time araclari) runtime'da gerekmez, image'a girmez.
COPY legal_corpus/embeddings/ ./legal_corpus/embeddings

RUN useradd --create-home --uid 10001 appuser
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Railway PORT'u environment'tan verir; local default 8000'dir (bkz. README).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-8000}/health/live" || exit 1

# Varsayilan: ai-api. ai-worker icin start command'i `python -m app.worker`
# ile override et (Railway: Service Settings -> Custom Start Command).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
