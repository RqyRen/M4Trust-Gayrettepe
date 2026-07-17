"""ai-api: internal operational HTTP surface (ADR-002 §21, ADR-007 §29-31).

Bu process SADECE operasyonel endpoint'ler sunar. AI processing burada
calismaz; senkron inference endpoint'i (POST /extract, /analyze, /chat ...)
kesinlikle yoktur (ADR-002 §21.5). AI isleri ai-worker tarafinda RabbitMQ
uzerinden asenkron yurur.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.config import get_settings
from app.operational import build_capabilities, build_contracts

settings = get_settings()

app = FastAPI(
    title="M4Trust AI Service (ai-api)",
    version=settings.service_version,
    description="Internal operational endpoints only. No synchronous inference (ADR-002 §21.5).",
)


@app.get("/health/live", tags=["health"])
def health_live() -> dict:
    """Liveness: process ayakta mi? (ADR-007 §30)

    Dis bagimliliga (RabbitMQ, object storage, AI provider) BAKMAZ; aksi
    halde gecici bir dis sorun butun process'i bosuna restart ettirir.
    """
    return {"status": "UP"}


@app.get("/health/ready", tags=["health"])
def health_ready() -> dict:
    """Readiness: servis trafik kabul edebilir mi? (ADR-007 §31)

    ai-api icin operasyonel endpoint'lerin calisabilirligi yeterlidir.
    Ileride gerekli dis bagimlilik kontrolleri buraya component olarak eklenir.
    """
    return {"status": "UP"}


@app.get("/internal/v1/capabilities", tags=["internal"])
def capabilities() -> dict:
    """Desteklenen job turleri ve schema version'lari (ADR-002 §21.3)."""
    return build_capabilities(settings)


@app.get("/internal/v1/contracts", tags=["internal"])
def contracts() -> list[dict]:
    """Contract metadata ve checksum bilgisi (ADR-002 §21.4)."""
    return build_contracts()
