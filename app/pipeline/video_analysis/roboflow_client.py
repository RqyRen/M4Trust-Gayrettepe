"""Roboflow serverless inference API istemcisi (ADR-002 SS3.2 "Nesne veya
olay tespiti, Confidence uretimi, Advisory anomaly uretimi").

Roboflow'un resmi `inference-sdk` paketi bu ortamdaki Python surumunu
desteklemedigi icin (SS Python <3.13 sinirlamasi), dogrudan REST API'ye
hafif bir httpx istemcisi ile baglanilir (ADR-007 SS15 "minimum runtime
dependency"). Wire format canli olarak dogrulandi: base64 JPEG POST,
JSON `predictions[]` cevabi.

Model-native cevap (Roboflow ham JSON'u) yalniz bu modul icinde tuketilir;
canonical'a donusum aggregation.py'de yapilir (ADR-002 SS8.1, SS10).
"""
from __future__ import annotations

import base64

import httpx

from app.config import Settings
from app.contracts.errors import ErrorCode, PipelineFailure


def _infer(image_jpeg: bytes, *, model_id: str, settings: Settings) -> list[dict]:
    """Tek bir goruntuyu belirtilen Roboflow modeline gonderir.

    Doner: prediction listesi (her biri x/y/width/height/confidence/class icerir).
    Firlatir: PipelineFailure — provider hatalarinda uygun stable kod ile.
    """
    if not settings.roboflow_api_key:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "video model provider is not configured",
            details={"dependency": "roboflow", "reason": "missing configuration"},
        )

    url = f"{settings.roboflow_api_url}/{model_id}"
    image_b64 = base64.b64encode(image_jpeg)

    try:
        response = httpx.post(
            url,
            params={"api_key": settings.roboflow_api_key, "confidence": int(settings.roboflow_min_confidence * 100)},
            content=image_b64,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=settings.roboflow_timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_TIMEOUT,
            "video model provider did not respond before the timeout",
            details={"dependency": "roboflow", "reason": "timeout"},
        ) from exc
    except httpx.HTTPError as exc:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "video model provider connection failed",
            details={"dependency": "roboflow", "reason": "connection error"},
        ) from exc

    if response.status_code in (401, 403):
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "video model provider rejected the request credentials",
            details={"dependency": "roboflow", "reason": "authentication error"},
        )
    if response.status_code >= 500:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "video model provider is temporarily unavailable",
            details={"dependency": "roboflow", "reason": "server error"},
        )
    if response.status_code >= 400:
        # Model-native hata mesaji disari sizdirilmaz (ADR-002 SS12.3).
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "video model provider rejected the request",
            details={"dependency": "roboflow", "reason": "request rejected"},
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "video model provider returned an unparseable response",
            details={"dependency": "roboflow", "reason": "invalid response"},
        ) from exc

    return payload.get("predictions", [])


def detect_objects(image_jpeg: bytes, settings: Settings) -> list[dict]:
    """Lojistik/sayim modeli (ADR §3.2 'Nesne veya olay tespiti')."""
    return _infer(image_jpeg, model_id=settings.roboflow_logistics_model_id, settings=settings)


def detect_damage(image_jpeg: bytes, settings: Settings) -> list[dict]:
    """Hasarli paket / anomaly modeli (ADR §3.2 'Advisory anomaly uretimi')."""
    return _infer(image_jpeg, model_id=settings.roboflow_damage_model_id, settings=settings)
